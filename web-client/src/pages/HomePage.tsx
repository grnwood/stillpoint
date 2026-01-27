import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import boldIcon from '../assets/bold.svg';
import checkboxIcon from '../assets/checkbox.svg';
import italicIcon from '../assets/italic.svg';
import linkIcon from '../assets/link.svg';
import mindmapIcon from '../assets/mindmap.svg';
import addPageIcon from '../assets/addpage.svg';
import addImageIcon from '../assets/add-image.svg';
import cameraIcon from '../assets/camera.svg';
import microphoneIcon from '../assets/microphone.svg';
import { API_BASE_URL, APIError, apiClient } from '../lib/api';

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

const CACHE_PREFIX = 'stillpoint.page.cache.';
const SERVER_PREFIX = 'stillpoint.page.server.';

type HomePageProps = {
  headerLeft?: React.ReactNode;
  vaultAuthStatus?: 'active' | 'inactive' | 'unreachable';
  onLogout?: () => void;
};

export const HomePage: React.FC<HomePageProps> = ({ headerLeft, vaultAuthStatus = 'inactive', onLogout }) => {
  const [recentPages, setRecentPages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activePath, setActivePath] = useState('');
  const [content, setContent] = useState('');
  const [serverContent, setServerContent] = useState<string | null>(null);
  const [loadingPage, setLoadingPage] = useState(false);
  const [hydrating, setHydrating] = useState(false);
  const [currentRev, setCurrentRev] = useState<number | null>(null);
  const [serverRev, setServerRev] = useState<number | null>(null);
  const [currentMtime, setCurrentMtime] = useState<number | null>(null);
  const [serverMtime, setServerMtime] = useState<number | null>(null);
  const [conflict, setConflict] = useState(false);
  const [conflictContent, setConflictContent] = useState<string | null>(null);
  const [conflictRev, setConflictRev] = useState<number | null>(null);
  const [conflictMtime, setConflictMtime] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagMatches, setTagMatches] = useState<string[]>([]);
  const [tagQuery, setTagQuery] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [isRecordingAudio, setIsRecordingAudio] = useState(false);
  const [isRecordingPaused, setIsRecordingPaused] = useState(false);
  const [audioSupportError, setAudioSupportError] = useState('');
  const [audioPlayback, setAudioPlayback] = useState<{ src: string; label: string; playing: boolean; current: number; duration: number } | null>(null);
  const [recordElapsed, setRecordElapsed] = useState(0);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const contentTopRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const micInputRef = useRef<HTMLInputElement | null>(null);
  const audioRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<BlobPart[]>([]);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);
  const recordStartRef = useRef<number | null>(null);
  const recordTimerRef = useRef<number | null>(null);
  const recordAccumulatedRef = useRef(0);

  const cleanupRecordingState = () => {
    if (recordTimerRef.current) {
      window.clearInterval(recordTimerRef.current);
      recordTimerRef.current = null;
    }
    recordStartRef.current = null;
    recordAccumulatedRef.current = 0;
    setIsRecordingPaused(false);
    setRecordElapsed(0);
  };
  const saveTimeoutRef = useRef<number | null>(null);
  const contentRef = useRef(content);
  const serverContentRef = useRef(serverContent);
  const serverRevRef = useRef(serverRev);
  const serverMtimeRef = useRef(serverMtime);
  const toolbarButtonStyle: React.CSSProperties = {
    padding: '6px 10px',
    border: '1px solid #222',
    borderRadius: '6px',
    backgroundColor: '#222',
    color: '#fff',
    cursor: 'pointer'
  };
  const toolbarIconStyle: React.CSSProperties = {
    width: '18px',
    height: '18px',
    filter: 'brightness(0) invert(1)'
  };
  const stripImageParams = (src: string) => src.replace(/\{[^}]*\}\s*$/, '');
  const checkboxRegex = /^\s*\(( |x|X)\)\s+(.*)$/;

  const formatPagePath = (path: string) => {
    if (!path) return '';
    const parts = path.split('/').filter(Boolean);
    if (parts.length === 0) return '';
    const last = parts[parts.length - 1];
    parts[parts.length - 1] = last.endsWith('.txt') ? last.slice(0, -4) : last;
    return parts.join(':');
  };

  const pageName = activePath ? formatPagePath(activePath) : '';

  const toPageRelativePath = (attachmentPath: string) => {
    if (!attachmentPath || !activePath) return attachmentPath;
    const pageParts = activePath.replace(/^\/+/, '').split('/');
    pageParts.pop(); // remove filename
    const targetParts = attachmentPath.replace(/^\/+/, '').split('/');
    let i = 0;
    while (i < pageParts.length && i < targetParts.length && pageParts[i] === targetParts[i]) {
      i++;
    }
    const upCount = pageParts.length - i;
    const relParts: string[] = [];
    for (let u = 0; u < upCount; u++) relParts.push('..');
    relParts.push(...targetParts.slice(i));
    const rel = relParts.join('/');
    if (!rel) return './';
    if (!rel.startsWith('../')) return `./${rel}`;
    return rel;
  };

  // Helper to create a new page at the current level
  const handleCreateNewPage = async () => {
    const nameInput = window.prompt('Enter a page name');
    if (nameInput === null) return;
    const rawName = nameInput.trim();
    if (!rawName) {
      setError('Please provide a page name.');
      return;
    }
    const normalizedName = rawName
      .replace(/\\/g, '/')
      .replace(/\/+/g, '/')
      .replace(/^\/|\/$/g, '');
    const nameParts = normalizedName.split('/').filter(Boolean);
    const lastPart = nameParts.pop() || '';
    const baseName = lastPart.endsWith('.txt') ? lastPart.slice(0, -4).trim() : lastPart.trim();
    if (!baseName) {
      setError('Please provide a page name.');
      return;
    }

    setError('');
    let basePath = activePath || '/';
    if (basePath.endsWith('.txt')) basePath = basePath.slice(0, -4);
    if (basePath.endsWith('/')) basePath = basePath.slice(0, -1);
    const parent = basePath.split('/').slice(0, -1).join('/') || '';
    const inputParent = nameParts.join('/');
    const combinedParent = [parent, inputParent].filter(Boolean).join('/');
    const newPath = `${combinedParent ? combinedParent + '/' : ''}${baseName}/${baseName}.txt`;
    try {
      await apiClient.writePage(newPath, '', undefined);
      const pageInfo = await apiClient.readPage(newPath);
      setActivePath(newPath);
      setContent('');
      setIsEditing(true);
      setSaveStatus('idle');
      setServerContent(pageInfo.content ?? '');
      const nextRev = typeof pageInfo.rev === 'number' ? pageInfo.rev : null;
      const nextMtime = typeof pageInfo.mtime_ns === 'number' ? pageInfo.mtime_ns : null;
      setCurrentRev(nextRev);
      setServerRev(nextRev);
      setCurrentMtime(nextMtime);
      setServerMtime(nextMtime);
      setConflict(false);
      setConflictContent(null);
      setConflictRev(null);
      setConflictMtime(null);
      setAttachments([]);
    } catch (err: any) {
      setError(err.message || 'Failed to create new page');
    }
  };

  const handleImageButtonClick = () => {
    if (!activePath) {
      setError('Open or create a page before attaching images.');
      return;
    }
    imageInputRef.current?.click();
  };

  const handleCameraButtonClick = () => {
    if (!activePath) {
      setError('Open or create a page before attaching images.');
      return;
    }
    cameraInputRef.current?.click();
  };

  const handleMicButtonClick = () => {
    if (!activePath) {
      setError('Open or create a page before attaching audio.');
      return;
    }
    if (isRecordingAudio) {
      stopAudioRecording();
    } else {
      startAudioRecording();
    }
  };

  const downscaleImageIfNeeded = async (file: File): Promise<File> => {
    const MAX_DIMENSION = 640; // more aggressive cap
    const SCALE_FALLBACK = 0.3; // fallback even more aggressive
    const TARGET_SIZE = 0.5 * 1024 * 1024; // aim even smaller
    const MAX_SAFE_SIZE = 8 * 1024 * 1024; // warn if above this
    if (!file.type.startsWith('image/')) return file;
    if (file.size > MAX_SAFE_SIZE) {
      setError('Image is too large to process safely on this device. Please choose a smaller image.');
      return file;
    }
    try {
      const bitmap = await createImageBitmap(file);
      const { width, height } = bitmap;
      const longest = Math.max(width, height);
      const baseScale = longest > MAX_DIMENSION ? MAX_DIMENSION / longest : 1;
      const scale = Math.min(baseScale, SCALE_FALLBACK);
      const nextW = Math.max(1, Math.round(width * scale));
      const nextH = Math.max(1, Math.round(height * scale));
      if (file.size <= TARGET_SIZE && scale >= 1) {
        bitmap.close?.();
        return file;
      }
      const canvas = document.createElement('canvas');
      canvas.width = nextW;
      canvas.height = nextH;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        bitmap.close?.();
        return file;
      }
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close?.();
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.5)
      );
      // Release memory
      canvas.width = 1;
      canvas.height = 1;
      if (!blob) return file;
      return new File([blob], file.name, { type: 'image/jpeg', lastModified: Date.now() });
    } catch {
      setError('Failed to process image. Try a smaller one.');
      return file;
    }
  };

  const handleImagesSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length || !activePath) return;
    setError('');
    try {
      const processed: File[] = [];
      for (const file of files) {
        // Downscale large images to reduce memory pressure on mobile
        processed.push(await downscaleImageIfNeeded(file));
      }
      const result = await apiClient.attachFiles(activePath, processed);
      const attachmentPaths = result.attachments || [];
      if (!attachmentPaths.length) return;
      setAttachments((prev) => Array.from(new Set([...prev, ...attachmentPaths])));
      setContent((prev) => {
        const md = attachmentPaths.map((p) => `![](${toPageRelativePath(p)})`).join('\n');
        return prev ? `${prev}\n\n${md}\n` : `${md}\n`;
      });
    } catch (err: any) {
      setError(err.message || 'Failed to attach images');
    }
  };

  const handleAudioSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length || !activePath) return;
    await processAudioFiles(files);
  };

  const processAudioFiles = async (files: File[]) => {
    setError('');
    try {
      const result = await apiClient.attachFiles(activePath, files);
      const attachmentPaths = result.attachments || [];
      if (!attachmentPaths.length) return;
      setAttachments((prev) => Array.from(new Set([...prev, ...attachmentPaths])));
      setContent((prev) => {
        const md = attachmentPaths.map((p) => `[Audio clip](${toPageRelativePath(p)})`).join('\n');
        return prev ? `${prev}\n\n${md}\n` : `${md}\n`;
      });
    } catch (err: any) {
      setError(err.message || 'Failed to attach audio');
    }
  };

  const startAudioRecording = async () => {
    if (!activePath) return;
    try {
      const mediaDevices = navigator.mediaDevices;
      const mediaRecorderSupported = typeof (window as any).MediaRecorder !== 'undefined';
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== 'function' || !mediaRecorderSupported) {
        setAudioSupportError('Microphone was blocked. Try HTTPS/localhost or disable Brave shields; falling back to file picker.');
        micInputRef.current?.click();
        return;
      }
      setError('');
      setAudioSupportError('');
      const stream = await mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const preferred = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg'];
      const mimeType = preferred.find((t) => (window as any).MediaRecorder?.isTypeSupported?.(t)) || '';
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      audioRecorderRef.current = recorder;
      audioStreamRef.current = stream;
      audioChunksRef.current = [];
      recordStartRef.current = Date.now();
      recordAccumulatedRef.current = 0;
      setRecordElapsed(0);
      setIsRecordingPaused(false);
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };
      recorder.onstop = async () => {
        const chunks = audioChunksRef.current;
        audioChunksRef.current = [];
        await handleRecordingFinished(chunks, recorder.mimeType || mimeType || 'audio/webm');
      };
      recorder.start();
      setIsRecordingAudio(true);
      recordTimerRef.current = window.setInterval(() => {
        if (recordStartRef.current) {
          const elapsed = recordAccumulatedRef.current + ((Date.now() - recordStartRef.current) / 1000);
          setRecordElapsed(Math.floor(elapsed));
        }
      }, 500);
    } catch (err: any) {
      micInputRef.current?.click();
      setAudioSupportError('Mic recording failed. Use HTTPS/localhost or disable Brave shields; falling back to file picker.');
      setError(err?.message || 'Audio recording failed; falling back to file picker.');
    }
  };

  const handleRecordingFinished = async (chunks: BlobPart[], mimeType: string) => {
    const stream = audioStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      audioStreamRef.current = null;
    }
    audioRecorderRef.current = null;
    cleanupRecordingState();
    setIsRecordingAudio(false);
    if (!chunks.length) return;
    const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
    const file = new File([blob], `audio-${Date.now()}.webm`, { type: blob.type || 'audio/webm' });
    await processAudioFiles([file]);
  };

  const stopAudioRecording = async () => {
    const recorder = audioRecorderRef.current;
    if (recorder) {
      // If paused, ensure timer accumulation reflects paused time
      if (recorder.state === 'paused' && recordStartRef.current) {
        recordAccumulatedRef.current += (Date.now() - recordStartRef.current) / 1000;
        recordStartRef.current = null;
      }
      try {
        recorder.stop();
      } catch {
        await handleRecordingFinished([], recorder.mimeType || 'audio/webm');
      }
    } else {
      await handleRecordingFinished([], 'audio/webm');
    }
    cleanupRecordingState();
    setIsRecordingAudio(false);
  };

  const stopAudioPlayback = () => {
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current.currentTime = 0;
      audioElementRef.current = null;
    }
    setAudioPlayback(null);
  };

  const playAudio = (src: string, label: string) => {
    stopAudioPlayback();
    try {
      const audio = new Audio(src);
      audioElementRef.current = audio;
      const applyState = () => {
        setAudioPlayback({
          src,
          label,
          playing: !audio.paused,
          current: audio.currentTime || 0,
          duration: audio.duration && !Number.isNaN(audio.duration) ? audio.duration : 0
        });
      };
      audio.onloadedmetadata = applyState;
      audio.ontimeupdate = applyState;
      audio.onended = () => stopAudioPlayback();
      audio.play().then(() => {
        applyState();
      }).catch(() => {
        window.open(src, '_blank', 'noopener,noreferrer');
        stopAudioPlayback();
      });
    } catch (err) {
      setError((err as Error)?.message || 'Failed to play audio');
    }
  };

  const toggleAudioPlayback = () => {
    const audio = audioElementRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play().then(() => {
        setAudioPlayback((prev) => prev ? { ...prev, playing: true } : prev);
      }).catch(() => {
        setError('Unable to resume audio');
      });
    } else {
      audio.pause();
      setAudioPlayback((prev) => prev ? { ...prev, playing: false } : prev);
    }
  };

  const pauseAudioRecording = () => {
    const recorder = audioRecorderRef.current;
    if (recorder && recorder.state === 'recording') {
      recorder.pause();
      if (recordStartRef.current) {
        recordAccumulatedRef.current += (Date.now() - recordStartRef.current) / 1000;
      }
      recordStartRef.current = null;
      if (recordTimerRef.current) {
        window.clearInterval(recordTimerRef.current);
        recordTimerRef.current = null;
      }
      setIsRecordingPaused(true);
      setRecordElapsed(Math.floor(recordAccumulatedRef.current));
    }
  };

  const resumeAudioRecording = () => {
    const recorder = audioRecorderRef.current;
    if (recorder && recorder.state === 'paused') {
      recorder.resume();
      recordStartRef.current = Date.now();
      setIsRecordingPaused(false);
      recordTimerRef.current = window.setInterval(() => {
        if (recordStartRef.current) {
          const elapsed = recordAccumulatedRef.current + ((Date.now() - recordStartRef.current) / 1000);
          setRecordElapsed(Math.floor(elapsed));
        }
      }, 500);
    }
  };

  const resolveVaultPath = (basePath: string, src: string) => {
    if (!src) return '';
    if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('data:') || src.startsWith('blob:')) {
      return src;
    }
    const cleanSrc = src.replace(/\\/g, '/');
    if (cleanSrc.startsWith('/')) {
      return cleanSrc;
    }
    const baseParts = basePath.split('/').filter(Boolean);
    baseParts.pop();
    const combined = [...baseParts, ...cleanSrc.split('/')];
    const stack: string[] = [];
    for (const part of combined) {
      if (!part || part === '.') continue;
      if (part === '..') {
        stack.pop();
        continue;
      }
      stack.push(part);
    }
    return `/${stack.join('/')}`;
  };

  const buildFileUrl = (vaultPath: string) => {
    if (!vaultPath) return '';
    return `${API_BASE_URL}/api/file/raw?path=${encodeURIComponent(vaultPath)}`;
  };

  const resolveWikiTarget = (raw: string) => {
    const trimmed = raw.trim();
    const normalized = trimmed.startsWith(':') ? trimmed.slice(1) : trimmed;
    const clean = normalized.replace(/\\/g, '/');
    if (clean.startsWith('/')) {
      return clean;
    }
    return `/${clean}`;
  };

  const markdownComponents = {
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      if (!href) {
        return <span>{children}</span>;
      }
      const cleanedHref = href.trim();
      const isAudio = cleanedHref.match(/\.(wav|mp3|m4a|ogg|webm)$/i);
      if (isAudio) {
        const vaultPath = resolveAttachmentPath(cleanedHref);
        const audioSrc = buildFileUrl(vaultPath);
        const label = React.Children.toArray(children).map((c) => (typeof c === 'string' ? c : '')).join('') || 'Audio clip';
        return (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              playAudio(audioSrc, label);
            }}
            style={{
              background: 'none',
              border: '1px solid #ccc',
              padding: '6px 10px',
              borderRadius: '6px',
              cursor: 'pointer'
            }}
          >
            🎧 {children}
          </button>
        );
      }
      if (cleanedHref.startsWith(':') || cleanedHref.startsWith('/')) {
        const targetPath = resolveWikiTarget(cleanedHref);
        return (
          <button
            type="button"
            onClick={() => openPage(targetPath)}
            style={{
              background: 'none',
              border: 'none',
              padding: 0,
              color: '#7db7ff',
              textDecoration: 'underline',
              cursor: 'pointer'
            }}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={cleanedHref} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    img: ({ src, alt }: { src?: string; alt?: string }) => {
      const cleanedSrc = stripImageParams(src || '');
      const vaultPath = resolveAttachmentPath(cleanedSrc);
      const imageSrc = buildFileUrl(vaultPath);
      return (
        <img
          src={imageSrc}
          alt={alt || ''}
          loading="lazy"
          decoding="async"
          style={{ maxWidth: '100%', borderRadius: '6px', imageOrientation: 'from-image' }}
        />
      );
    },
  };

  const resolveAttachmentPath = (src: string) => {
    const normalized = resolveVaultPath(activePath, src);
    if (!normalized) return '';
    if (attachments.includes(normalized)) {
      return normalized;
    }
    const fileName = normalized.split('/').pop() || '';
    if (fileName) {
      const match = attachments.find((path) => path.endsWith(`/${fileName}`));
      if (match) {
        return match;
      }
    }
    return normalized;
  };

  useEffect(() => {
    loadRecent();
    loadTags();
  }, []);

  const loadRecent = async () => {
    try {
      const result = await apiClient.getRecent(10);
      setRecentPages(result.pages);
    } catch (error) {
      console.error('Failed to load recent pages:', error);
    } finally {
      setLoading(false);
    }
  };

  const runSearch = async (query: string) => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults([]);
      return;
    }
    setSearchLoading(true);
    try {
      const result = await apiClient.search(trimmed);
      setSearchResults(result.results || []);
    } catch (err: any) {
      setError(err.message || 'Search failed');
    } finally {
      setSearchLoading(false);
    }
  };

  const loadTags = async () => {
    try {
      const result = await apiClient.getTags();
      const extracted = result.tags.map((item) => item.tag);
      setTags(extracted);
    } catch (err) {
      console.error('Failed to load tags:', err);
    }
  };

  const updateTagMatches = (text: string, cursor: number) => {
    const prefix = text.slice(0, cursor);
    const match = prefix.match(/(^|\s)@([\w-]*)$/);
    if (!match) {
      setTagQuery(null);
      setTagMatches([]);
      return;
    }
    const query = match[2] || '';
    const filtered = tags.filter((tag) => tag.startsWith(query)).slice(0, 6);
    setTagQuery(query);
    setTagMatches(filtered);
  };

  const openPage = async (path: string) => {
    if (!path) return;
    setError('');
    setActivePath(path);
    setLoadingPage(true);
    setHydrating(true);
    setSaveStatus('idle');
    setServerContent(null);
    setCurrentRev(null);
    setServerRev(null);
    setCurrentMtime(null);
    setServerMtime(null);
    setConflict(false);
    setConflictContent(null);
    setConflictRev(null);
    setConflictMtime(null);
    setAttachments([]);
    setIsEditing(false);
    const cacheKey = `${CACHE_PREFIX}${path}`;
    const cached = localStorage.getItem(cacheKey);
    if (cached !== null) {
      setContent(cached);
    } else {
      setContent('');
    }
    try {
      const result = await apiClient.readPage(path);
      const nextRev = typeof result.rev === 'number' ? result.rev : null;
      const nextMtime = typeof result.mtime_ns === 'number' ? result.mtime_ns : null;
      setServerContent(result.content);
      setCurrentRev(nextRev);
      setServerRev(nextRev);
      setCurrentMtime(nextMtime);
      setServerMtime(nextMtime);
      if (cached === null) {
        setContent(result.content);
      }
      try {
        const attachmentResult = await apiClient.listAttachments(path);
        const attachmentPaths = (attachmentResult.attachments || []).map((item) => item.attachment_path);
        setAttachments(attachmentPaths);
      } catch (err) {
        console.error('Failed to load attachments:', err);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to load page');
    } finally {
      setLoadingPage(false);
      setHydrating(false);
      requestAnimationFrame(() => {
        contentTopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        if (editorRef.current) {
          editorRef.current.focus();
        }
      });
    }
  };


  const applyWrap = (before: string, after: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const selected = content.slice(start, end);
    const next = `${content.slice(0, start)}${before}${selected}${after}${content.slice(end)}`;
    setContent(next);
    const cursorStart = start + before.length;
    const cursorEnd = cursorStart + selected.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(cursorStart, cursorEnd);
    });
  };

  const insertAtCursor = (value: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const next = `${content.slice(0, start)}${value}${content.slice(end)}`;
    setContent(next);
    const cursor = start + value.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    });
  };

  const handleTagInsert = (tag: string) => {
    const textarea = editorRef.current;
    if (!textarea) return;
    const cursor = textarea.selectionStart || 0;
    const prefix = content.slice(0, cursor);
    const match = prefix.match(/(^|\s)@([\w-]*)$/);
    if (!match) return;
    const replaceStart = cursor - (match[2].length + 1);
    const next = `${content.slice(0, replaceStart)}${tag}${content.slice(cursor)}`;
    setContent(next);
    const newCursor = replaceStart + tag.length;
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(newCursor, newCursor);
    });
  };

  useEffect(() => {
    if (!activePath) return;
    if (hydrating || loadingPage) return;
    if (conflict) return;
    if (saveTimeoutRef.current) {
      window.clearTimeout(saveTimeoutRef.current);
    }
    const cacheKey = `${CACHE_PREFIX}${activePath}`;
    updateTagMatches(content, editorRef.current?.selectionStart || 0);
    saveTimeoutRef.current = window.setTimeout(async () => {
      try {
        setSaveStatus('saving');
        // For new pages (no rev/mtime), skip If-Match and write directly
        let latestRev = currentRev;
        let latestMtime = currentMtime;
        let isNewPage = latestRev === null && latestMtime === null;
        let ifMatchValue: string | undefined = undefined;
        if (!isNewPage) {
          try {
            const info = await apiClient.readPage(activePath);
            latestRev = typeof info.rev === 'number' ? info.rev : null;
            latestMtime = typeof info.mtime_ns === 'number' ? info.mtime_ns : null;
          } catch {}
          ifMatchValue = latestMtime !== null ? `mtime:${latestMtime}` : (latestRev !== null ? `rev:${latestRev}` : undefined);
        }
        const result = await apiClient.writePage(activePath, content, isNewPage ? undefined : ifMatchValue);
        localStorage.setItem(cacheKey, content);
        localStorage.setItem(`${SERVER_PREFIX}${activePath}`, content);
        setSaveStatus('saved');
        if (typeof result.rev === 'number') {
          setCurrentRev(result.rev);
          setServerRev(result.rev);
        }
        if (typeof result.mtime_ns === 'number') {
          setCurrentMtime(result.mtime_ns);
          setServerMtime(result.mtime_ns);
        }
        setServerContent(content);
      } catch (err: any) {
        if (err instanceof APIError && err.status === 409) {
          // On conflict, fetch latest server revision/mtime and content
          try {
            const info = await apiClient.readPage(activePath);
            if (info.content === content) {
              // Server already has our content; just adopt its metadata and mark saved.
              const nextRev = typeof info.rev === 'number' ? info.rev : null;
              const nextMtime = typeof info.mtime_ns === 'number' ? info.mtime_ns : null;
              setCurrentRev(nextRev);
              setServerRev(nextRev);
              setCurrentMtime(nextMtime);
              setServerMtime(nextMtime);
              setServerContent(content);
              setConflict(false);
              setConflictContent(null);
              setConflictRev(null);
              setConflictMtime(null);
              localStorage.setItem(cacheKey, content);
              localStorage.setItem(`${SERVER_PREFIX}${activePath}`, content);
              setSaveStatus('saved');
              return;
            }
            // True conflict: show conflict UI and stop retrying
            // Update local mtime/rev from server before showing conflict
            if (typeof info.rev === 'number') {
              setCurrentRev(info.rev);
              setServerRev(info.rev);
            }
            if (typeof info.mtime_ns === 'number') {
              setCurrentMtime(info.mtime_ns);
              setServerMtime(info.mtime_ns);
            }
            setConflict(true);
            setConflictRev(typeof info.rev === 'number' ? info.rev : null);
            setConflictMtime(typeof info.mtime_ns === 'number' ? info.mtime_ns : null);
            setConflictContent(typeof info.content === 'string' ? info.content : '');
            setSaveStatus('error');
            return;
          } catch (retryErr: any) {
            const detail = retryErr.detail as { current_rev?: number; current_mtime_ns?: number; current_content?: string } | undefined;
            setConflict(true);
            setConflictRev(typeof detail?.current_rev === 'number' ? detail?.current_rev : null);
            setConflictMtime(typeof detail?.current_mtime_ns === 'number' ? detail?.current_mtime_ns : null);
            setConflictContent(typeof detail?.current_content === 'string' ? detail.current_content : '');
            setSaveStatus('error');
            return;
          }
        }
        console.error('Save failed:', err);
        setSaveStatus('error');
      }
    }, 800);
    return () => {
      if (saveTimeoutRef.current) {
        window.clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [content, activePath, hydrating, loadingPage, conflict, currentRev]);

  useEffect(() => {
    contentRef.current = content;
    serverContentRef.current = serverContent;
    serverRevRef.current = serverRev;
    serverMtimeRef.current = serverMtime;
  }, [content, serverContent, serverRev, serverMtime]);

  useEffect(() => {
    if (!activePath) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await apiClient.readPage(activePath);
        if (cancelled) return;
        const nextRev = typeof result.rev === 'number' ? result.rev : null;
        const nextMtime = typeof result.mtime_ns === 'number' ? result.mtime_ns : null;
        const prevRev = serverRevRef.current;
        const prevMtime = serverMtimeRef.current;
        if (nextMtime !== null && prevMtime !== null) {
          if (nextMtime == prevMtime) {
            return;
          }
        } else if (nextRev === null || nextRev === prevRev) {
          return;
        }
        const localContent = contentRef.current;
        const currentServerContent = serverContentRef.current ?? '';
        if (localContent === result.content) {
          setServerContent(result.content);
          setCurrentRev(nextRev);
          setServerRev(nextRev);
          setCurrentMtime(nextMtime);
          setServerMtime(nextMtime);
          return;
        }
        if (localContent === currentServerContent) {
          setContent(result.content);
          setServerContent(result.content);
          setCurrentRev(nextRev);
          setServerRev(nextRev);
          setCurrentMtime(nextMtime);
          setServerMtime(nextMtime);
          return;
        }
        setConflict(true);
        setConflictRev(nextRev);
        setConflictMtime(nextMtime);
        setConflictContent(result.content);
        setSaveStatus('error');
      } catch (err) {
        console.error('Polling failed:', err);
      }
    };
    const intervalId = window.setInterval(poll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activePath]);

  const handleReloadFromServer = async () => {
    if (!activePath) return;
    let serverText = conflictContent;
    let nextRev = conflictRev;
    let nextMtime = conflictMtime;

    if (serverText === null || serverText === undefined) {
      try {
        const result = await apiClient.readPage(activePath);
        serverText = result.content;
        nextRev = typeof result.rev === 'number' ? result.rev : null;
        nextMtime = typeof result.mtime_ns === 'number' ? result.mtime_ns : null;
      } catch (err) {
        console.error('Failed to reload server version:', err);
        return;
      }
    }

    setContent(serverText ?? '');
    setServerContent(serverText ?? '');
    setCurrentRev(nextRev);
    setServerRev(nextRev);
    setCurrentMtime(nextMtime);
    setServerMtime(nextMtime);
    setConflict(false);
    setConflictContent(null);
    setConflictRev(null);
    setConflictMtime(null);
    setSaveStatus('idle');
  };

  const handleOverwriteServer = async () => {
    if (!activePath) return;
    setSaveStatus('saving');
    try {
      const ifMatchValue = conflict
        ? undefined
        : (currentMtime !== null
          ? `mtime:${currentMtime}`
          : (currentRev !== null ? `rev:${currentRev}` : undefined));
      const result = await apiClient.writePage(activePath, content, ifMatchValue);
      if (typeof result.rev === 'number') {
        setCurrentRev(result.rev);
        setServerRev(result.rev);
      }
      if (typeof result.mtime_ns === 'number') {
        setCurrentMtime(result.mtime_ns);
        setServerMtime(result.mtime_ns);
      }
      setServerContent(content);
      setConflict(false);
      setConflictContent(null);
      setConflictRev(null);
      setConflictMtime(null);
      setSaveStatus('saved');
    } catch (err: any) {
      if (err instanceof APIError && err.status === 409) {
        try {
          // Force overwrite without any precondition if the first attempt conflicted
          const forced = await apiClient.writePage(activePath, content, undefined);
          if (typeof forced.rev === 'number') {
            setCurrentRev(forced.rev);
            setServerRev(forced.rev);
          }
          if (typeof forced.mtime_ns === 'number') {
            setCurrentMtime(forced.mtime_ns);
            setServerMtime(forced.mtime_ns);
          }
          setServerContent(content);
          setConflict(false);
          setConflictContent(null);
          setConflictRev(null);
          setConflictMtime(null);
          setSaveStatus('saved');
          return;
        } catch (forceErr: any) {
          const detail = forceErr.detail as { current_rev?: number; current_mtime_ns?: number; current_content?: string } | undefined;
          setConflict(true);
          setConflictRev(typeof detail?.current_rev === 'number' ? detail?.current_rev : null);
          setConflictMtime(typeof detail?.current_mtime_ns === 'number' ? detail?.current_mtime_ns : null);
          setConflictContent(typeof detail?.current_content === 'string' ? detail.current_content : '');
          setSaveStatus('error');
          return;
        }
      }
      setSaveStatus('error');
    }
  };

  const toggleCheckboxLine = (lineIndex: number) => {
    const lines = content.split('\n');
    const line = lines[lineIndex] || '';
    const match = line.match(checkboxRegex);
    if (!match) return;
    const checked = match[1].toLowerCase() === 'x';
    lines[lineIndex] = line.replace(checkboxRegex, `(${checked ? ' ' : 'x'}) ${match[2]}`);
    setContent(lines.join('\n'));
  };

  const renderMarkdownBlocks = () => {
    if (!content.trim()) {
      return <ReactMarkdown components={markdownComponents}>*Empty page.*</ReactMarkdown>;
    }
    const transformWikiLinks = (text: string) => {
      return text.replace(/\[:([^\]|]+)\|([^\]]+)\]/g, (_match, target, label) => {
        const cleanedLabel = String(label).replace(/^:/, '');
        const targetPath = resolveWikiTarget(`:${target}`);
        return `[${cleanedLabel}](${targetPath})`;
      });
    };
    const blocks: React.ReactNode[] = [];
    const lines = content.split('\n');
    let buffer: string[] = [];
    const flushBuffer = (key: string) => {
      if (buffer.length === 0) return;
      blocks.push(
        <ReactMarkdown key={key} components={markdownComponents}>
          {transformWikiLinks(buffer.join('\n'))}
        </ReactMarkdown>
      );
      buffer = [];
    };
    lines.forEach((line, index) => {
      if (checkboxRegex.test(line)) {
        flushBuffer(`md-${index}`);
        const match = line.match(checkboxRegex);
        if (!match) return;
        const checked = match[1].toLowerCase() === 'x';
        blocks.push(
          <div
            key={`cb-${index}`}
            onDoubleClick={(event) => {
              event.stopPropagation();
              toggleCheckboxLine(index);
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '6px 0',
              cursor: 'pointer'
            }}
            title="Double tap to toggle"
          >
            <span style={{
              width: '18px',
              height: '18px',
              border: '1px solid #bbb',
              borderRadius: '4px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '12px',
              fontWeight: 600
            }}>
              {checked ? 'x' : ''}
            </span>
            <span>{match[2]}</span>
          </div>
        );
      } else {
        buffer.push(line);
      }
    });
    flushBuffer(`md-${lines.length}`);
    return blocks;
  };

  return (
    <div style={{ padding: 0, width: '100%', minHeight: '100vh', boxSizing: 'border-box' }}>
      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        multiple
        style={{ display: 'none' }}
        onChange={handleImagesSelected}
      />
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: 'none' }}
        onChange={handleImagesSelected}
      />
      <input
        ref={micInputRef}
        type="file"
        accept="audio/*"
        capture="user"
        style={{ display: 'none' }}
        onChange={handleAudioSelected}
      />
      <nav style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto 1fr',
        alignItems: 'center',
        gap: '12px',
        padding: '12px 24px',
        borderBottom: '1px solid #e5e7eb',
        backgroundColor: '#fff',
        position: 'sticky',
        top: 0,
        zIndex: 5
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', justifySelf: 'start' }}>
          {headerLeft}
          <button
            title="Create new page"
            onClick={handleCreateNewPage}
            style={{
              background: 'none',
              border: 'none',
              padding: 0,
              marginLeft: '6px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <img src={addPageIcon} alt="Add page" style={{ width: 24, height: 24 }} />
          </button>
        </div>
        <div style={{
          justifySelf: 'center',
          fontWeight: 600,
          textAlign: 'center'
        }}>
          {pageName || 'No page selected'}
        </div>
        <div style={{ justifySelf: 'end', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '14px', color: '#555' }}>
            <span
              className={`status-dot status-dot--${vaultAuthStatus}`}
              aria-label={`Vault auth status: ${vaultAuthStatus}`}
              title={`Vault auth status: ${vaultAuthStatus}`}
            />
            <span>Vault</span>
          </div>
          {onLogout && (
            <button
              onClick={onLogout}
              style={{
                padding: '8px 14px',
                backgroundColor: '#222',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer'
              }}
            >
              Logout
            </button>
          )}
        </div>
      </nav>
      {error && (
        <div style={{
          marginTop: '16px',
          padding: '12px',
          backgroundColor: '#fee',
          color: '#900',
          borderRadius: '6px'
        }}>
          {error}
        </div>
      )}
      {isRecordingAudio && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999
        }}>
          <style>
            {`@keyframes audioRecordBar {
                0% { height: 8px; }
                50% { height: 24px; }
                100% { height: 8px; }
              }`}
          </style>
          <div style={{
            background: '#fff',
            padding: '16px',
            borderRadius: '10px',
            boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
            width: '320px',
            maxWidth: '90%',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px'
          }}>
            <div style={{ fontWeight: 700 }}>Recording…</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', height: '28px' }}>
                {[0,1,2,3,4].map((i) => (
                  <div key={i} style={{
                    width: '6px',
                    background: '#dc2626',
                    borderRadius: '3px',
                    animation: isRecordingPaused ? 'none' : `audioRecordBar 0.8s ease-in-out ${i * 0.1}s infinite`,
                    height: '12px'
                  }} />
                ))}
              </div>
            <div style={{ fontSize: '14px', color: '#111' }}>{recordElapsed}s</div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button
                type="button"
                style={toolbarButtonStyle}
                onClick={isRecordingPaused ? resumeAudioRecording : pauseAudioRecording}
              >
                {isRecordingPaused ? 'Resume' : 'Pause'}
              </button>
              <button type="button" style={toolbarButtonStyle} onClick={stopAudioRecording}>
                Stop
              </button>
            </div>
          </div>
        </div>
      )}
      {audioPlayback && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999
        }}>
          <style>
            {`@keyframes audioBar {
                0% { height: 8px; }
                50% { height: 20px; }
                100% { height: 8px; }
              }`}
          </style>
          <div style={{
            background: '#fff',
            padding: '16px',
            borderRadius: '10px',
            boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
            width: '320px',
            maxWidth: '90%',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px'
          }}>
            <div style={{ fontWeight: 700 }}>{audioPlayback.label || 'Audio'}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', height: '24px' }}>
              {[0,1,2,3,4].map((i) => (
                <div key={i} style={{
                  width: '6px',
                  background: '#2563eb',
                  borderRadius: '3px',
                  animation: audioPlayback.playing ? `audioBar 0.9s ease-in-out ${i * 0.1}s infinite` : 'none',
                  height: '12px'
                }} />
              ))}
            </div>
            <div style={{
              height: '6px',
              borderRadius: '4px',
              background: '#e5e7eb',
              overflow: 'hidden'
            }}>
              <div style={{
                width: `${audioPlayback.duration ? Math.min(100, (audioPlayback.current / audioPlayback.duration) * 100) : 0}%`,
                height: '100%',
                background: '#2563eb',
                transition: 'width 0.1s linear'
              }} />
            </div>
            <input
              type="range"
              min={0}
              max={audioPlayback.duration || 0}
              step={0.1}
              value={audioPlayback.current}
              onChange={(e) => {
                const next = Number(e.target.value);
                const audio = audioElementRef.current;
                if (audio && !Number.isNaN(next)) {
                  audio.currentTime = next;
                  setAudioPlayback((prev) => prev ? { ...prev, current: next } : prev);
                }
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: '#374151' }}>
              <span>{Math.floor(audioPlayback.current)}s</span>
              <span>{audioPlayback.duration ? Math.floor(audioPlayback.duration) : 0}s</span>
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button type="button" style={toolbarButtonStyle} onClick={toggleAudioPlayback}>
                {audioPlayback.playing ? 'Pause' : 'Play'}
              </button>
              <button type="button" style={toolbarButtonStyle} onClick={stopAudioPlayback}>
                Stop
              </button>
            </div>
          </div>
        </div>
      )}

      <section style={{ marginTop: '24px' }}>
        <div style={{ marginBottom: '12px' }}>
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => {
              const next = e.target.value;
              setSearchQuery(next);
              runSearch(next);
            }}
            placeholder="Search pages..."
            style={{
              width: '100%',
              padding: '10px',
              fontSize: '16px',
              border: '1px solid #ddd',
              borderRadius: '4px'
            }}
          />
        </div>

        <section style={{ marginTop: '16px' }}>
          <h2>Search Results</h2>
          
          {searchQuery.trim() ? (
            searchLoading ? (
              <p>Searching...</p>
            ) : searchResults.length > 0 ? (
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {searchResults.map((page) => (
                  <li
                    key={page.path || page.page_id}
                    onClick={() => openPage(page.path)}
                    style={{ 
                      padding: '12px', 
                      marginBottom: '8px',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      cursor: 'pointer'
                    }}
                  >
                    <div style={{ fontWeight: 'bold' }}>{page.title || formatPagePath(page.path)}</div>
                    <div style={{ fontSize: '14px', color: '#666' }}>
                      {formatPagePath(page.path)}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p>No matching pages</p>
            )
          ) : (
            <p>Start typing to search.</p>
          )}
        </section>

        {activePath ? (
          <div style={{
            border: '1px solid #ddd',
            borderRadius: '8px',
            padding: '12px',
            marginTop: '16px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontWeight: 600 }}>{formatPagePath(activePath) || activePath}</div>
              <div style={{ fontSize: '13px', color: '#666' }}>
                {loadingPage
                  ? 'Loading...'
                  : serverContent !== null && serverContent !== content
                    ? 'Syncing'
                    : saveStatus === 'saving'
                      ? 'Saving...'
                      : saveStatus === 'saved'
                        ? 'Saved'
                        : saveStatus === 'error'
                          ? 'Save failed'
                          : 'Idle'}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
              <button
                type="button"
                style={{
                  padding: '6px 10px',
                  border: '1px solid #222',
                  borderRadius: '6px',
                  backgroundColor: isEditing ? '#f2f2f2' : '#222',
                  color: isEditing ? '#222' : '#fff',
                  cursor: 'pointer'
                }}
                onClick={() => setIsEditing(false)}
              >
                Read
              </button>
              <button
                type="button"
                style={{
                  padding: '6px 10px',
                  border: '1px solid #222',
                  borderRadius: '6px',
                  backgroundColor: isEditing ? '#222' : '#f2f2f2',
                  color: isEditing ? '#fff' : '#222',
                  cursor: 'pointer'
                }}
                onClick={() => setIsEditing(true)}
              >
                Edit
              </button>
            </div>
            <div ref={contentTopRef} />
            {conflict && (
              <div style={{
                marginTop: '8px',
                padding: '10px',
                backgroundColor: '#fdecea',
                borderRadius: '6px',
                color: '#611a15'
              }}>
                <div style={{ marginBottom: '8px' }}>
                  Server has a newer version. Reload to sync or overwrite with your local copy.
                </div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    style={toolbarButtonStyle}
                    onClick={handleReloadFromServer}
                  >
                    Reload server version
                  </button>
                  <button
                    type="button"
                    style={toolbarButtonStyle}
                    onClick={handleOverwriteServer}
                  >
                    Overwrite with local
                  </button>
                </div>
              </div>
            )}
            {audioSupportError && (
              <div style={{
                marginTop: '8px',
                padding: '10px',
                backgroundColor: '#fff4e5',
                borderRadius: '6px',
                border: '1px solid #f0c36d',
                color: '#7a4b00'
              }}>
                {audioSupportError}
              </div>
            )}
            {!isEditing ? (
              <div
                onDoubleClick={() => setIsEditing(true)}
                style={{
                marginTop: '12px',
                padding: '12px',
                border: '1px solid #eee',
                borderRadius: '6px',
                backgroundColor: '#0f1115',
                color: '#f2f2f2'
              }}>
                {renderMarkdownBlocks()}
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '12px' }}>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('**', '**')} aria-label="Bold">
                    <img src={boldIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('_', '_')} aria-label="Italic">
                    <img src={italicIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('[', '](url)')} aria-label="Link">
                    <img src={linkIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => insertAtCursor('( ) ')} aria-label="Checkbox">
                    <img src={checkboxIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={() => applyWrap('[[', ']]')} aria-label="Wiki link">
                    <img src={mindmapIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={handleImageButtonClick}>
                    <img src={addImageIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={handleCameraButtonClick}>
                    <img src={cameraIcon} alt="" style={toolbarIconStyle} />
                  </button>
                  <button type="button" style={toolbarButtonStyle} onClick={handleMicButtonClick}>
                    <img src={microphoneIcon} alt="" style={toolbarIconStyle} />
                  </button>
                </div>
                <textarea
                  ref={editorRef}
                  value={content}
                  onChange={(e) => {
                    setContent(e.target.value);
                    updateTagMatches(e.target.value, e.target.selectionStart || 0);
                  }}
                  onClick={(e) => updateTagMatches(e.currentTarget.value, e.currentTarget.selectionStart || 0)}
                  onKeyUp={(e) => updateTagMatches((e.target as HTMLTextAreaElement).value, (e.target as HTMLTextAreaElement).selectionStart || 0)}
                  rows={16}
                  style={{
                    width: '100%',
                    marginTop: '12px',
                    padding: '12px',
                    fontSize: '15px',
                    lineHeight: 1.5,
                    borderRadius: '6px',
                    border: '1px solid #222',
                    fontFamily: 'inherit',
                    backgroundColor: '#0f1115',
                    color: '#f2f2f2'
                  }}
                />
                {tagMatches.length > 0 && tagQuery !== null && (
                  <div style={{
                    marginTop: '8px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: '6px'
                  }}>
                    {tagMatches.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => handleTagInsert(`@${tag}`)}
                        style={{
                          padding: '6px 10px',
                          borderRadius: '14px',
                          border: '1px solid #ddd',
                          backgroundColor: '#f8f9fa',
                          cursor: 'pointer'
                        }}
                      >
                        @{tag}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <div style={{ textAlign: 'center', margin: '32px 0' }}>
            <p style={{ color: '#666', marginBottom: '18px' }}>No page loaded. Create a new page to get started.</p>
            <button
              type="button"
              onClick={handleCreateNewPage}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '10px 18px',
                fontSize: '16px',
                backgroundColor: '#222',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                boxShadow: '0 2px 8px rgba(0,0,0,0.07)'
              }}
            >
              <img src={addPageIcon} alt="Add page" style={{ width: 22, height: 22 }} />
              Create New Page
            </button>
          </div>
        )}

        <section style={{ marginTop: '16px' }}>
          <h2>Recent Pages</h2>
          
          {loading ? (
            <p>Loading...</p>
          ) : recentPages.length > 0 ? (
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {recentPages.map((page) => (
                <li
                  key={page.page_id}
                  onClick={() => openPage(page.path)}
                  style={{ 
                  padding: '12px', 
                  marginBottom: '8px',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  cursor: 'pointer'
                }}>
                  <div style={{ fontWeight: 'bold' }}>{page.title || formatPagePath(page.path)}</div>
                  <div style={{ fontSize: '14px', color: '#666' }}>
                    {formatPagePath(page.path)} • Rev {page.rev}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>No pages yet</p>
          )}
        </section>
      </section>
    </div>
  );
};
