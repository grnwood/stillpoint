import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import { Excalidraw, exportToBlob } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import "./styles.css";

const SAVE_DEBOUNCE_MS = 1200;
const PREVIEW_DEBOUNCE_MS = 2200;
const DEFAULT_AI_LIMITS = {
  max_json_bytes: 120 * 1024,
  max_elements: 250,
};
const AI_PHASE_LABELS = {
  idle: "",
  preparing: "Preparing context",
  analyzing: "Analyzing diagram",
  saving: "Saving summary",
  sending: "Sending chat",
  receiving: "Receiving AI response",
  processing: "Processing response",
  applying: "Applying drawing",
  exporting: "Exporting preview",
  complete: "Complete",
  failed: "Failed",
};

function readParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    path: params.get("path") || "",
    token: params.get("token") || "",
    filterPath: params.get("filter_path") || "",
  };
}

function apiHeaders(token) {
  const headers = { "content-type": "application/json" };
  if (token) {
    headers["x-local-ui-token"] = token;
  }
  return headers;
}

function cleanAppState(appState = {}) {
  const {
    collaborators,
    isLoading,
    errorMessage,
    openDialog,
    openPopup,
    openMenu,
    contextMenu,
    toast,
    ...rest
  } = appState;
  return rest;
}

function normalizeScene(scene = {}) {
  return {
    type: scene.type || "excalidraw",
    version: scene.version || 2,
    source: scene.source || "stillpoint",
    elements: Array.isArray(scene.elements) ? scene.elements : [],
    appState: cleanAppState(scene.appState || {}),
    files: scene.files || {},
  };
}

function sceneToInitialData(scene) {
  return {
    elements: scene.elements,
    appState: scene.appState,
    files: scene.files,
  };
}

function sceneStats(scene, limits = DEFAULT_AI_LIMITS) {
  const normalized = normalizeScene(scene || {});
  const jsonBytes = new TextEncoder().encode(JSON.stringify(normalized)).length;
  const elements = normalized.elements.length;
  return {
    jsonBytes,
    elements,
    overLimit: jsonBytes > limits.max_json_bytes || elements > limits.max_elements,
  };
}

function formatBytes(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${Math.round(bytes / 1024)} KB`;
}

function friendlyAiError(message) {
  const text = String(message || "AI request failed");
  if (/timed out|timeout/i.test(text)) {
    return `${text} The model may still be generating; try a smaller request, a faster model, or a longer AI chat read timeout.`;
  }
  if (/could not connect|connection|network|failed to fetch/i.test(text)) {
    return `${text} Check that the selected AI server is running and reachable.`;
  }
  return text;
}

function makeAiMessage(role, content, extra = {}) {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    role,
    content,
    ...extra,
  };
}

function aiChatHistory(messages, limit = 12) {
  return messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && !message.error)
    .map((message) => ({
      role: message.role,
      content: String(message.content || ""),
    }))
    .filter((message) => message.content.trim())
    .slice(-limit);
}

function selectedElementIdsFromAppState(appState = {}) {
  const selected = appState.selectedElementIds || {};
  return Object.keys(selected).filter((id) => selected[id]);
}

function stillpointLinkForElement(element) {
  return element?.customData?.stillpoint || null;
}

function bumpElement(element, patch) {
  return {
    ...element,
    ...patch,
    version: (element.version || 1) + 1,
    versionNonce: Math.floor(Math.random() * 2147483647),
    updated: Date.now(),
  };
}

function elementBottomRight(element, appState = {}) {
  const zoom = Number(appState.zoom?.value || appState.zoom || 1);
  const scrollX = Number(appState.scrollX || 0);
  const scrollY = Number(appState.scrollY || 0);
  const left = (Number(element.x || 0) + Number(element.width || 0) + scrollX) * zoom;
  const top = (Number(element.y || 0) + Number(element.height || 0) + scrollY) * zoom;
  if (!Number.isFinite(left) || !Number.isFinite(top)) {
    return null;
  }
  return { left, top };
}

function elementCenter(element) {
  if (!element) {
    return null;
  }
  return {
    x: Number(element.x || 0) + Number(element.width || 0) / 2,
    y: Number(element.y || 0) + Number(element.height || 0) / 2,
  };
}

function linkedElementNearPoint(elements, point, radius = 18) {
  if (!point) {
    return null;
  }
  const radiusSquared = radius * radius;
  let best = null;
  let bestDistance = Infinity;
  elements.forEach((element) => {
    if (!element || element.isDeleted || !stillpointLinkForElement(element)) {
      return;
    }
    const minX = Math.min(Number(element.x || 0), Number(element.x || 0) + Number(element.width || 0)) - radius;
    const maxX = Math.max(Number(element.x || 0), Number(element.x || 0) + Number(element.width || 0)) + radius;
    const minY = Math.min(Number(element.y || 0), Number(element.y || 0) + Number(element.height || 0)) - radius;
    const maxY = Math.max(Number(element.y || 0), Number(element.y || 0) + Number(element.height || 0)) + radius;
    if (point.x < minX || point.x > maxX || point.y < minY || point.y > maxY) {
      return;
    }
    const center = elementCenter(element);
    if (!center) {
      return;
    }
    const distance = (center.x - point.x) ** 2 + (center.y - point.y) ** 2;
    if (distance < bestDistance && distance < radiusSquared + (Number(element.width || 0) ** 2 + Number(element.height || 0) ** 2) / 4) {
      best = element;
      bestDistance = distance;
    }
  });
  return best;
}

function AiMessageContent({ message }) {
  if (message.role !== "assistant" || message.error) {
    return message.content;
  }
  return (
    <ReactMarkdown
      components={{
        a: ({ node, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer" />
        ),
      }}
    >
      {message.content}
    </ReactMarkdown>
  );
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Failed to read preview blob"));
    reader.readAsDataURL(blob);
  });
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      try {
        message = await response.text();
      } catch {
        // Keep the HTTP status fallback.
      }
    }
    throw new Error(message);
  }
  return response.json();
}

function App() {
  const { path, token, filterPath } = useMemo(readParams, []);
  const [initialData, setInitialData] = useState(null);
  const [status, setStatus] = useState("Loading");
  const [error, setError] = useState("");
  const [api, setApi] = useState(null);
  const [sceneAppState, setSceneAppState] = useState({});
  const [selectedElementId, setSelectedElementId] = useState("");
  const [pageLinkPanelOpen, setPageLinkPanelOpen] = useState(false);
  const [pageLinkUserCollapsed, setPageLinkUserCollapsed] = useState(true);
  const [pageLinkCollapsedElementId, setPageLinkCollapsedElementId] = useState("");
  const [stillPointOpening, setStillPointOpening] = useState(false);
  const [pageQuery, setPageQuery] = useState("");
  const [pageResults, setPageResults] = useState([]);
  const [pageSearchBusy, setPageSearchBusy] = useState(false);
  const [pageLinkError, setPageLinkError] = useState("");
  const [aiConfig, setAiConfig] = useState({
    enabled: false,
    servers: [],
    default_server: "",
    default_model: "",
    ...DEFAULT_AI_LIMITS,
  });
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const [aiServer, setAiServer] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiPromptHistory, setAiPromptHistory] = useState([]);
  const [aiPromptHistoryIndex, setAiPromptHistoryIndex] = useState(null);
  const [aiMode, setAiMode] = useState("draw");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiPhase, setAiPhase] = useState("idle");
  const [aiMessages, setAiMessages] = useState([]);
  const [undoScene, setUndoScene] = useState(null);
  const [summaryInfo, setSummaryInfo] = useState(null);
  const [stats, setStats] = useState(sceneStats(null));
  const latestSceneRef = useRef(null);
  const saveTimerRef = useRef(null);
  const previewTimerRef = useRef(null);
  const mountedRef = useRef(false);
  const loadedRef = useRef(false);

  const rememberAiPrompt = useCallback((prompt) => {
    const cleaned = prompt.trim();
    if (!cleaned) {
      return;
    }
    setAiPromptHistory((history) => {
      const withoutDuplicate = history.filter((item) => item !== cleaned);
      return [...withoutDuplicate, cleaned].slice(-50);
    });
    setAiPromptHistoryIndex(null);
  }, []);

  const saveScene = useCallback(
    async (scene) => {
      if (!path || !scene) {
        return;
      }
      setStatus("Saving");
      await requestJson("/api/excalidraw", {
        method: "PUT",
        headers: apiHeaders(token),
        body: JSON.stringify({ path, scene }),
      });
      setStatus("Saved");
    },
    [path, token],
  );

  const savePreview = useCallback(
    async (scene) => {
      if (!path || !scene || !scene.elements.length) {
        return;
      }
      try {
        const blob = await exportToBlob({
          elements: scene.elements,
          appState: {
            ...scene.appState,
            exportBackground: true,
            exportWithDarkMode: false,
            viewBackgroundColor: scene.appState?.viewBackgroundColor || "#ffffff",
          },
          files: scene.files,
          mimeType: "image/png",
        });
        const pngBase64 = await blobToDataUrl(blob);
        await requestJson("/api/excalidraw/preview", {
          method: "PUT",
          headers: apiHeaders(token),
          body: JSON.stringify({ path, png_base64: pngBase64 }),
        });
      } catch (err) {
        console.warn("[StillPoint Excalidraw] Preview export failed", err);
      }
    },
    [path, token],
  );

  const schedulePersist = useCallback(
    (scene) => {
      window.clearTimeout(saveTimerRef.current);
      window.clearTimeout(previewTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        saveScene(scene).catch((err) => {
          setError(err.message);
          setStatus("Save failed");
        });
      }, SAVE_DEBOUNCE_MS);
      previewTimerRef.current = window.setTimeout(() => {
        savePreview(scene);
      }, PREVIEW_DEBOUNCE_MS);
    },
    [savePreview, saveScene],
  );

  const applyScene = useCallback(
    (scene, nextStatus = "Unsaved") => {
      const normalized = normalizeScene(scene);
      const nextData = sceneToInitialData(normalized);
      if (api) {
        try {
          api.updateScene(nextData);
        } catch (err) {
          throw new Error(`Could not apply Excalidraw scene: ${err?.message || err}`);
        }
      }
      latestSceneRef.current = normalized;
      setStats(sceneStats(normalized, aiConfig));
      setInitialData(nextData);
      setSceneAppState(normalized.appState || {});
      setStatus(nextStatus);
      return normalized;
    },
    [api, aiConfig],
  );

  useEffect(() => {
    mountedRef.current = true;
    if (!path) {
      setError("Missing drawing path");
      setStatus("Load failed");
      return () => {
        mountedRef.current = false;
      };
    }
    requestJson(`/api/excalidraw?path=${encodeURIComponent(path)}`, {
      headers: apiHeaders(token),
    })
      .then((payload) => {
        if (!mountedRef.current) {
          return;
        }
        const scene = normalizeScene(payload.scene);
        latestSceneRef.current = scene;
        setStats(sceneStats(scene, aiConfig));
        setInitialData(sceneToInitialData(scene));
        setSceneAppState(scene.appState || {});
        setStatus("Saved");
        window.document.title = `Excalidraw - ${payload.title || path}`;
      })
      .catch((err) => {
        if (!mountedRef.current) {
          return;
        }
        setError(err.message);
        setStatus("Load failed");
      });
    return () => {
      mountedRef.current = false;
      window.clearTimeout(saveTimerRef.current);
      window.clearTimeout(previewTimerRef.current);
    };
  }, [aiConfig, path, token]);

  useEffect(() => {
    requestJson("/api/excalidraw/ai/config", {
      headers: apiHeaders(token),
    })
      .then((payload) => {
        const nextConfig = {
          ...DEFAULT_AI_LIMITS,
          ...payload,
          servers: Array.isArray(payload.servers) ? payload.servers : [],
        };
        setAiConfig(nextConfig);
        setAiServer(payload.default_server || nextConfig.servers[0]?.name || "");
        setAiModel(payload.default_model || nextConfig.servers[0]?.default_model || "");
        setStats(sceneStats(latestSceneRef.current, nextConfig));
      })
      .catch((err) => {
        console.warn("[StillPoint Excalidraw] Failed to load AI config", err);
      });
  }, [token]);

  const loadSummary = useCallback(async () => {
    if (!path) {
      return null;
    }
    const payload = await requestJson(`/api/excalidraw/summary?path=${encodeURIComponent(path)}`, {
      headers: apiHeaders(token),
    });
    setSummaryInfo(payload);
    return payload;
  }, [path, token]);

  useEffect(() => {
    loadSummary().catch((err) => {
      console.warn("[StillPoint Excalidraw] Failed to load summary", err);
    });
  }, [loadSummary]);

  useEffect(() => {
    if (!api || !initialData || loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    api.updateScene(initialData);
  }, [api, initialData]);

  const handleChange = useCallback(
    (elements, appState, files) => {
      if (!loadedRef.current) {
        return;
      }
      const scene = normalizeScene({ elements, appState, files });
      latestSceneRef.current = scene;
      setSceneAppState(appState || {});
      const selectedIds = selectedElementIdsFromAppState(appState);
      const nextSelectedElementId = selectedIds.length === 1 ? selectedIds[0] : "";
      if (nextSelectedElementId !== selectedElementId) {
        setPageLinkCollapsedElementId("");
      }
      setSelectedElementId(nextSelectedElementId);
      setStats(sceneStats(scene, aiConfig));
      setStatus("Unsaved");
      setError("");
      setSummaryInfo((current) => (current ? { ...current, stale: true } : current));
      schedulePersist(scene);
    },
    [aiConfig, schedulePersist, selectedElementId],
  );

  const handleManualSave = useCallback(() => {
    window.clearTimeout(saveTimerRef.current);
    const scene = latestSceneRef.current;
    saveScene(scene)
      .then(() => savePreview(scene))
      .catch((err) => {
        setError(err.message);
        setStatus("Save failed");
      });
  }, [savePreview, saveScene]);

  const selectedServer = aiConfig.servers.find((server) => server.name === aiServer) || aiConfig.servers[0];
  const aiModels = selectedServer?.models || [];
  const summaryReady = Boolean(summaryInfo?.exists && summaryInfo?.summary);
  const summaryStale = Boolean(summaryInfo?.stale);
  const summaryTitle = summaryInfo?.summary?.title || summaryInfo?.summary?.diagram_type || "summary ready";
  const drawDisabledReason = !aiConfig.enabled
    ? "AI chats are disabled"
    : stats.overLimit
      ? `Draw mode disabled over ${aiConfig.max_elements} elements or ${formatBytes(aiConfig.max_json_bytes)}`
      : "";
  const analyzeDisabledReason = !aiConfig.enabled
    ? "AI chats are disabled"
    : stats.overLimit
      ? `Analyze disabled over ${aiConfig.max_elements} elements or ${formatBytes(aiConfig.max_json_bytes)}`
      : "";
  const chatDisabledReason =
    !aiConfig.enabled
      ? "AI chats are disabled"
      : aiMode === "draw"
        ? drawDisabledReason
      : !summaryReady
          ? "Analyze the diagram first to create chat context"
          : "";
  const sceneElements = latestSceneRef.current?.elements || [];
  const selectedElement = sceneElements.find((element) => element.id === selectedElementId && !element.isDeleted) || null;
  const selectedStillPointLink = stillpointLinkForElement(selectedElement);
  const linkedElements = sceneElements.filter((element) => !element.isDeleted && stillpointLinkForElement(element));

  useEffect(() => {
    if (selectedStillPointLink && pageLinkCollapsedElementId !== selectedElement?.id) {
      setPageLinkPanelOpen(true);
      return;
    }
    if (!selectedElement && pageLinkUserCollapsed) {
      setPageLinkPanelOpen(false);
    }
  }, [pageLinkCollapsedElementId, pageLinkUserCollapsed, selectedElement, selectedStillPointLink]);

  const updateElementStillPointLink = useCallback(
    (elementId, link) => {
      const scene = latestSceneRef.current;
      if (!scene || !elementId) {
        return null;
      }
      const nextElements = scene.elements.map((element) => {
        if (element.id !== elementId) {
          return element;
        }
        const customData = { ...(element.customData || {}) };
        if (link) {
          customData.stillpoint = link;
        } else {
          delete customData.stillpoint;
        }
        return bumpElement(element, { customData });
      });
      const nextScene = normalizeScene({ ...scene, elements: nextElements });
      latestSceneRef.current = nextScene;
      if (api) {
        api.updateScene({
          elements: nextScene.elements,
          appState: nextScene.appState,
          files: nextScene.files,
        });
      }
      setStats(sceneStats(nextScene, aiConfig));
      setStatus("Unsaved");
      setError("");
      setPageLinkError("");
      setSummaryInfo((current) => (current ? { ...current, stale: true } : current));
      schedulePersist(nextScene);
      return nextScene.elements.find((element) => element.id === elementId) || null;
    },
    [aiConfig, api, schedulePersist],
  );

  const openStillPointPage = useCallback(
    async (pagePath) => {
      if (!pagePath) {
        return;
      }
      setPageLinkError("");
      setStillPointOpening(true);
      try {
        await requestJson("/api/excalidraw/open-page", {
          method: "POST",
          headers: apiHeaders(token),
          body: JSON.stringify({ path: pagePath, source_path: path }),
        });
      } finally {
        window.setTimeout(() => {
          setStillPointOpening(false);
        }, 700);
      }
    },
    [path, token],
  );

  const linkSelectedElementToPage = useCallback(
    (page) => {
      if (!selectedElementId || !page?.path) {
        return;
      }
      updateElementStillPointLink(selectedElementId, {
        page_path: page.path,
        page_title: page.title || page.path,
        linked_at: new Date().toISOString(),
      });
      setPageQuery("");
      setPageResults([]);
      setPageLinkPanelOpen(true);
      setPageLinkUserCollapsed(false);
      setPageLinkCollapsedElementId("");
    },
    [selectedElementId, updateElementStillPointLink],
  );

  useEffect(() => {
    if (!selectedElement || selectedStillPointLink || !pageLinkPanelOpen) {
      setPageResults([]);
      setPageSearchBusy(false);
      return;
    }
    const query = pageQuery.trim();
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setPageSearchBusy(true);
      const params = new URLSearchParams({
        q: query,
        limit: "12",
      });
      if (filterPath) {
        params.set("filter_path", filterPath);
      }
      requestJson(`/api/pages/search?${params.toString()}`, {
        headers: apiHeaders(token),
        signal: controller.signal,
      })
        .then((payload) => {
          setPageResults(Array.isArray(payload.pages) ? payload.pages : []);
          setPageLinkError("");
        })
        .catch((err) => {
          if (err.name === "AbortError") {
            return;
          }
          setPageResults([]);
          setPageLinkError(err.message);
        })
        .finally(() => {
          setPageSearchBusy(false);
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [filterPath, pageLinkPanelOpen, pageQuery, selectedElement, selectedStillPointLink, token]);

  useEffect(() => {
    if (!api?.onPointerUp) {
      return undefined;
    }
    return api.onPointerUp((activeTool, pointerDownState, event) => {
      if (!(pointerDownState?.withCmdOrCtrl || event?.ctrlKey || event?.metaKey) || pointerDownState?.drag?.hasOccurred) {
        return;
      }
      const hitElement =
        pointerDownState.hit?.element ||
        linkedElementNearPoint(latestSceneRef.current?.elements || [], pointerDownState.origin);
      const link = stillpointLinkForElement(hitElement);
      if (!link?.page_path) {
        return;
      }
      event?.preventDefault?.();
      event?.stopPropagation?.();
      if (hitElement?.id) {
        setSelectedElementId(hitElement.id);
        setPageLinkPanelOpen(true);
        setPageLinkUserCollapsed(false);
        setPageLinkCollapsedElementId("");
      }
      openStillPointPage(link.page_path).catch((err) => {
        setPageLinkError(err.message);
      });
    });
  }, [api, openStillPointPage]);

  const handleAiServerChange = useCallback(
    (event) => {
      const nextServer = event.target.value;
      setAiServer(nextServer);
      const server = aiConfig.servers.find((item) => item.name === nextServer);
      setAiModel(server?.default_model || server?.models?.[0] || "");
    },
    [aiConfig.servers],
  );

  const handleAiModeChange = useCallback((nextMode) => {
    if (aiBusy || nextMode === aiMode) {
      return;
    }
    setAiMode(nextMode);
    setError("");
  }, [aiBusy, aiMode]);

  const handleAiPromptKeyDown = useCallback(
    (event) => {
      if (!event.altKey) {
        if (aiPromptHistoryIndex !== null) {
          setAiPromptHistoryIndex(null);
        }
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        if (!aiBusy && aiPrompt.trim() && !chatDisabledReason) {
          event.currentTarget.form?.requestSubmit();
        }
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (!aiPromptHistory.length) {
          return;
        }
        const nextIndex =
          aiPromptHistoryIndex === null
            ? aiPromptHistory.length - 1
            : (aiPromptHistoryIndex - 1 + aiPromptHistory.length) % aiPromptHistory.length;
        setAiPromptHistoryIndex(nextIndex);
        setAiPrompt(aiPromptHistory[nextIndex]);
      }
    },
    [aiBusy, aiPrompt, aiPromptHistory, aiPromptHistoryIndex, chatDisabledReason],
  );

  const analyzeDiagramSummary = useCallback(
    async ({ announce = true } = {}) => {
      const scene = latestSceneRef.current;
      if (!scene || analyzeDisabledReason) {
        throw new Error(analyzeDisabledReason || "No diagram loaded");
      }
      window.clearTimeout(saveTimerRef.current);
      window.clearTimeout(previewTimerRef.current);
      setAiPhase("preparing");
      setStatus(AI_PHASE_LABELS.preparing);
      if (announce) {
        setAiMessages((messages) => [
          ...messages,
          makeAiMessage("system", `Preparing ${stats.elements} elements (${formatBytes(stats.jsonBytes)})`),
        ]);
      }
      await saveScene(scene);
      setAiPhase("analyzing");
      setStatus(AI_PHASE_LABELS.analyzing);
      if (announce) {
        setAiMessages((messages) => [
          ...messages,
          makeAiMessage("system", `Sending diagram to ${aiServer} / ${aiModel} for architecture summary`),
        ]);
      }
      const payload = await requestJson("/api/excalidraw/summary", {
        method: "POST",
        headers: apiHeaders(token),
        body: JSON.stringify({
          path,
          scene,
          server: aiServer,
          model: aiModel,
        }),
      });
      setSummaryInfo(payload);
      setAiPhase("saving");
      setStatus(AI_PHASE_LABELS.saving);
      if (announce) {
        const title = payload.summary?.title || payload.summary?.diagram_type || "summary";
        setAiMessages((messages) => [...messages, makeAiMessage("system", `Saved ${title} to ${payload.summary_path}`)]);
      }
      return payload;
    },
    [aiModel, aiServer, analyzeDisabledReason, path, saveScene, stats.elements, stats.jsonBytes, token],
  );

  const handleAnalyzeSummary = useCallback(async () => {
    if (aiBusy || analyzeDisabledReason) {
      return;
    }
    setAiBusy(true);
    setError("");
    try {
      await analyzeDiagramSummary({ announce: true });
      setAiPhase("complete");
      setStatus("Summary ready");
    } catch (err) {
      const message = friendlyAiError(err.message);
      setAiPhase("failed");
      setError(message);
      setStatus("AI failed");
      setAiMessages((messages) => [...messages, makeAiMessage("assistant", message, { error: true })]);
    } finally {
      setAiBusy(false);
      window.setTimeout(() => {
        setAiPhase("idle");
      }, 1200);
    }
  }, [aiBusy, analyzeDiagramSummary, analyzeDisabledReason]);

  const handleDrawSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      const prompt = aiPrompt.trim();
      const scene = latestSceneRef.current;
      if (!prompt || !scene || aiBusy || drawDisabledReason) {
        return;
      }
      window.clearTimeout(saveTimerRef.current);
      window.clearTimeout(previewTimerRef.current);
      setAiBusy(true);
      setAiPhase("preparing");
      setError("");
      setStatus(AI_PHASE_LABELS.preparing);
      setAiMessages((messages) => [
        ...messages,
        makeAiMessage("user", prompt),
        makeAiMessage("system", `Preparing ${stats.elements} elements (${formatBytes(stats.jsonBytes)}) for Draw mode`),
      ]);
      setAiPrompt("");
      rememberAiPrompt(prompt);
      try {
        const previous = normalizeScene(scene);
        await saveScene(previous);
        setAiPhase("sending");
        setStatus("Sending draw request");
        setAiMessages((messages) => [...messages, makeAiMessage("system", `Sending draw request to ${aiServer} / ${aiModel}`)]);
        setAiPhase("receiving");
        setStatus(AI_PHASE_LABELS.receiving);
        setAiMessages((messages) => [...messages, makeAiMessage("system", "Waiting for full Excalidraw scene JSON")]);
        const payload = await requestJson("/api/excalidraw/ai", {
          method: "POST",
          headers: apiHeaders(token),
          body: JSON.stringify({
            path,
            prompt,
            scene: previous,
            server: aiServer,
            model: aiModel,
          }),
        });
        setAiPhase("applying");
        setStatus(AI_PHASE_LABELS.applying);
        setAiMessages((messages) => [...messages, makeAiMessage("system", "Applying sanitized canvas response")]);
        const nextScene = applyScene(payload.scene, "AI drawing applied");
        setUndoScene(previous);
        setSummaryInfo((current) => (current ? { ...current, stale: true } : current));
        setAiPhase("saving");
        setStatus("Saving drawing");
        setAiMessages((messages) => [...messages, makeAiMessage("system", "Saving updated .excalidraw source")]);
        await saveScene(nextScene);
        setAiPhase("exporting");
        setStatus(AI_PHASE_LABELS.exporting);
        setAiMessages((messages) => [...messages, makeAiMessage("system", "Exporting PNG preview sidecar")]);
        await savePreview(nextScene);
        setAiPhase("complete");
        setStatus("AI drawing applied");
        setAiMessages((messages) => [
          ...messages,
          makeAiMessage("assistant", `Applied drawing update with ${payload.model || aiModel}`),
        ]);
      } catch (err) {
        const message = friendlyAiError(err.message);
        setAiPhase("failed");
        setError(message);
        setStatus("AI failed");
        setAiMessages((messages) => [...messages, makeAiMessage("assistant", message, { error: true })]);
      } finally {
        setAiBusy(false);
        window.setTimeout(() => {
          setAiPhase("idle");
        }, 1200);
      }
    },
    [aiBusy, aiModel, aiPrompt, aiServer, applyScene, drawDisabledReason, path, rememberAiPrompt, savePreview, saveScene, stats.elements, stats.jsonBytes, token],
  );

  const handleAnalyzeChatSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      const prompt = aiPrompt.trim();
      if (!prompt || aiBusy || chatDisabledReason) {
        return;
      }
      setAiBusy(true);
      setAiPhase("preparing");
      setError("");
      setStatus(AI_PHASE_LABELS.preparing);
      const history = aiChatHistory(aiMessages);
      setAiMessages((messages) => [...messages, makeAiMessage("user", prompt)]);
      setAiPrompt("");
      rememberAiPrompt(prompt);
      try {
        if (summaryStale) {
          setAiMessages((messages) => [
            ...messages,
            makeAiMessage("system", "Using stale summary context; refresh analysis when you are done editing"),
          ]);
        }
        setAiPhase("sending");
        setStatus(AI_PHASE_LABELS.sending);
        setAiMessages((messages) => [...messages, makeAiMessage("system", `Sending chat to ${aiServer} / ${aiModel}`)]);
        setAiPhase("receiving");
        setStatus(AI_PHASE_LABELS.receiving);
        setAiMessages((messages) => [...messages, makeAiMessage("system", "Waiting for an answer using the summary context")]);
        const payload = await requestJson("/api/excalidraw/chat", {
          method: "POST",
          headers: apiHeaders(token),
          body: JSON.stringify({
            path,
            prompt,
            history,
            server: aiServer,
            model: aiModel,
          }),
        });
        setAiPhase("processing");
        setStatus(AI_PHASE_LABELS.processing);
        setAiPhase("complete");
        setStatus("AI answered");
        setAiMessages((messages) => [
          ...messages,
          makeAiMessage("assistant", payload.reply || `No response content returned by ${payload.model || aiModel}`),
        ]);
      } catch (err) {
        const message = friendlyAiError(err.message);
        setAiPhase("failed");
        setError(message);
        setStatus("AI failed");
        setAiMessages((messages) => [...messages, makeAiMessage("assistant", message, { error: true })]);
      } finally {
        setAiBusy(false);
        window.setTimeout(() => {
          setAiPhase("idle");
        }, 1200);
      }
    },
    [aiBusy, aiMessages, aiModel, aiPrompt, aiServer, chatDisabledReason, path, rememberAiPrompt, summaryStale, token],
  );

  const handleAiSubmit = aiMode === "draw" ? handleDrawSubmit : handleAnalyzeChatSubmit;

  const handleUndoAi = useCallback(() => {
    if (!undoScene) {
      return;
    }
    window.clearTimeout(saveTimerRef.current);
    window.clearTimeout(previewTimerRef.current);
    const restored = applyScene(undoScene, "AI drawing undone");
    setUndoScene(null);
    setSummaryInfo((current) => (current ? { ...current, stale: true } : current));
    saveScene(restored)
      .then(() => savePreview(restored))
      .catch((err) => {
        setError(err.message);
        setStatus("Save failed");
      });
  }, [applyScene, savePreview, saveScene, undoScene]);

  if (error && !initialData) {
    return (
      <main className="sp-error">
        <section>
          <strong>{status}</strong>
          <p>{error}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="sp-excalidraw">
      <div
        className="sp-status"
        data-state={status.toLowerCase().replace(/\s+/g, "-")}
        data-stillpoint-opening={stillPointOpening ? "true" : "false"}
      >
        <button type="button" onClick={handleManualSave} disabled={!initialData}>
          Save
        </button>
        {aiConfig.enabled ? (
          <button type="button" onClick={() => setAiPanelOpen((open) => !open)} disabled={!initialData}>
            AI
          </button>
        ) : null}
        {selectedElement ? (
          <button
            type="button"
            className="sp-link-toggle-button"
            data-linked={selectedStillPointLink ? "true" : "false"}
            data-active={pageLinkPanelOpen ? "true" : "false"}
            data-working={stillPointOpening ? "true" : "false"}
            onClick={() => {
              const nextOpen = !pageLinkPanelOpen;
              setPageLinkPanelOpen(nextOpen);
              setPageLinkUserCollapsed(!nextOpen);
              setPageLinkCollapsedElementId(nextOpen ? "" : selectedElement.id);
            }}
          >
            {stillPointOpening ? "Opening..." : selectedStillPointLink ? "StillPoint" : "Link"}
          </button>
        ) : null}
        {undoScene && aiMode === "draw" ? (
          <button type="button" onClick={handleUndoAi}>
            Undo AI
          </button>
        ) : null}
        <span>{status}</span>
        {error ? <span className="sp-error-text">{error}</span> : null}
      </div>
      {aiPanelOpen && aiConfig.enabled ? (
        <aside className="sp-ai-panel" data-busy={aiBusy ? "true" : "false"}>
          <div className="sp-ai-mode-toggle" role="group" aria-label="AI mode">
            <button
              type="button"
              data-active={aiMode === "draw" ? "true" : "false"}
              onClick={() => handleAiModeChange("draw")}
              disabled={aiBusy}
            >
              Draw
            </button>
            <button
              type="button"
              data-active={aiMode === "analyze" ? "true" : "false"}
              onClick={() => handleAiModeChange("analyze")}
              disabled={aiBusy}
            >
              Analyze
            </button>
          </div>
          <div className="sp-ai-row">
            <select value={aiServer} onChange={handleAiServerChange} disabled={aiBusy}>
              {aiConfig.servers.map((server) => (
                <option key={server.name} value={server.name}>
                  {server.name}
                </option>
              ))}
            </select>
            <select value={aiModel} onChange={(event) => setAiModel(event.target.value)} disabled={aiBusy}>
              {aiModels.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </div>
          <div className="sp-ai-meter" data-over-limit={stats.overLimit ? "true" : "false"}>
            {stats.elements} elements · {formatBytes(stats.jsonBytes)}
          </div>
          {aiMode === "analyze" ? (
            <div className="sp-ai-summary" data-ready={summaryReady && !summaryStale ? "true" : "false"}>
              <span>
                {summaryReady
                  ? `Context: ${summaryTitle}${summaryStale ? " (stale)" : ""}`
                  : "Context: not summarized"}
              </span>
              <button type="button" onClick={handleAnalyzeSummary} disabled={aiBusy || Boolean(analyzeDisabledReason)}>
                {summaryReady ? "Refresh" : "Analyze"}
              </button>
            </div>
          ) : (
            <div className="sp-ai-summary" data-ready="true">
              <span>Draw mode: AI can replace the canvas scene</span>
              {undoScene ? (
                <button type="button" onClick={handleUndoAi} disabled={aiBusy}>
                  Undo
                </button>
              ) : null}
            </div>
          )}
          {aiBusy ? (
            <div className="sp-ai-progress" data-phase={aiPhase}>
              <span className="sp-ai-spinner" />
              <span>{AI_PHASE_LABELS[aiPhase] || "Working"}</span>
            </div>
          ) : null}
          {aiMode === "draw" && drawDisabledReason ? <div className="sp-ai-notice">{drawDisabledReason}</div> : null}
          {aiMode === "analyze" && analyzeDisabledReason ? <div className="sp-ai-notice">{analyzeDisabledReason}</div> : null}
          {aiMode === "analyze" && chatDisabledReason && chatDisabledReason !== analyzeDisabledReason ? (
            <div className="sp-ai-notice">{chatDisabledReason}</div>
          ) : null}
          <div className="sp-ai-messages">
            {aiMessages.map((message, index) => (
              <div
                className={`sp-ai-message sp-ai-message-${message.role}${message.error ? " is-error" : ""}`}
                key={message.id || `${message.role}-${index}`}
              >
                <AiMessageContent message={message} />
              </div>
            ))}
          </div>
          <form className="sp-ai-form" onSubmit={handleAiSubmit}>
            <textarea
              value={aiPrompt}
              onChange={(event) => setAiPrompt(event.target.value)}
              onKeyDown={handleAiPromptKeyDown}
              placeholder={
                aiMode === "draw"
                  ? "Ask AI to draw or change the canvas..."
                  : summaryReady
                    ? "Ask about this diagram..."
                    : "Ask about this diagram; StillPoint will summarize it first..."
              }
              disabled={aiBusy || Boolean(chatDisabledReason)}
              rows={4}
            />
            <button
              type="submit"
              className={aiBusy ? "is-working" : ""}
              disabled={aiBusy || !aiPrompt.trim() || Boolean(chatDisabledReason)}
            >
              {aiBusy ? (
                <>
                  <span className="sp-ai-spinner" />
                  <span>{AI_PHASE_LABELS[aiPhase] || "Working"}</span>
                </>
              ) : (
                "Send"
              )}
            </button>
          </form>
        </aside>
      ) : null}
      {selectedElement && pageLinkPanelOpen ? (
        <aside
          className="sp-page-link-panel"
          aria-label="StillPoint element link"
          data-ai-open={aiPanelOpen && aiConfig.enabled ? "true" : "false"}
          data-opening={stillPointOpening ? "true" : "false"}
        >
          <header>
            <strong>StillPoint</strong>
            <div className="sp-page-link-header-actions">
              <span>{selectedElement.type}</span>
              <button
                type="button"
                aria-label="Collapse StillPoint link panel"
                onClick={() => {
                  setPageLinkPanelOpen(false);
                  setPageLinkUserCollapsed(true);
                  setPageLinkCollapsedElementId(selectedElement.id);
                }}
              >
                -
              </button>
            </div>
          </header>
          {stillPointOpening ? (
            <div className="sp-page-link-opening">
              <span className="sp-ai-spinner" />
              <span>Opening in StillPoint</span>
            </div>
          ) : null}
          {selectedStillPointLink ? (
            <div className="sp-page-link-current">
              <button
                type="button"
                className="sp-page-link-title"
                title={selectedStillPointLink.page_path}
                onClick={() => openStillPointPage(selectedStillPointLink.page_path).catch((err) => setPageLinkError(err.message))}
              >
                {selectedStillPointLink.page_title || selectedStillPointLink.page_path}
              </button>
              <div className="sp-page-link-path">{selectedStillPointLink.page_path}</div>
              <div className="sp-page-link-actions">
                <button
                  type="button"
                  onClick={() => openStillPointPage(selectedStillPointLink.page_path).catch((err) => setPageLinkError(err.message))}
                >
                  Open
                </button>
                <button type="button" onClick={() => updateElementStillPointLink(selectedElement.id, null)}>
                  Unlink
                </button>
              </div>
            </div>
          ) : (
            <div className="sp-page-link-search">
              <input
                value={pageQuery}
                onChange={(event) => setPageQuery(event.target.value)}
                placeholder={filterPath ? "Link filtered page..." : "Link page..."}
              />
              <div className="sp-page-link-scope">{filterPath ? `Filtered: ${filterPath}` : "All pages"}</div>
              <div className="sp-page-link-results">
                {pageSearchBusy ? <div className="sp-page-link-empty">Searching</div> : null}
                {!pageSearchBusy && pageResults.length === 0 ? (
                  <div className="sp-page-link-empty">No matching pages</div>
                ) : null}
                {pageResults.map((page) => (
                  <button type="button" key={page.path} onClick={() => linkSelectedElementToPage(page)}>
                    <span>{page.title || page.path}</span>
                    <small>{page.path}</small>
                  </button>
                ))}
              </div>
            </div>
          )}
          {pageLinkError ? <div className="sp-page-link-error">{pageLinkError}</div> : null}
        </aside>
      ) : null}
      <div className="sp-linked-badges" aria-hidden="true">
        {linkedElements.map((element) => {
          const point = elementBottomRight(element, sceneAppState);
          if (!point) {
            return null;
          }
          return (
            <span
              className="sp-linked-badge"
              data-opening={stillPointOpening ? "true" : "false"}
              key={element.id}
              title={stillpointLinkForElement(element)?.page_path || "StillPoint link"}
              style={{
                left: `${point.left}px`,
                top: `${point.top}px`,
              }}
            />
          );
        })}
      </div>
      {initialData ? (
        <Excalidraw
          excalidrawAPI={setApi}
          initialData={initialData}
          onChange={handleChange}
        />
      ) : (
        <div className="sp-loading">Loading</div>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
