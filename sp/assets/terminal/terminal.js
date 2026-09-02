(function () {
  "use strict";

  let initialOptions = {};
  try {
    initialOptions = JSON.parse(new URLSearchParams(window.location.search).get("options") || "{}");
  } catch (_) {
    initialOptions = {};
  }
  const defaultTheme = {
    background: "#111318",
    foreground: "#e5e7eb",
    cursor: "#ffffff",
    cursorAccent: "#111318",
    selectionBackground: "#365b7d"
  };
  let bridge = null;
  const terminal = new Terminal(Object.assign({
    allowProposedApi: false,
    convertEol: false,
    cursorBlink: true,
    cursorStyle: "block",
    cursorInactiveStyle: "block",
    fontFamily: "monospace",
    fontSize: 13,
    linkHandler: {
      activate: function (_event, uri) {
        if (bridge) {
          bridge.openExternalUrl(uri);
        }
      },
      allowNonHttpProtocols: false
    },
    minimumContrastRatio: 7,
    scrollback: 10000,
    theme: defaultTheme
  }, initialOptions, {
    theme: Object.assign({}, defaultTheme, initialOptions.theme || {})
  }));
  const fitAddon = new FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(document.getElementById("terminal"));

  let resizeTimer = null;

  function fitAndNotify() {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      try {
        fitAddon.fit();
        if (bridge) {
          bridge.resize(terminal.cols, terminal.rows);
        }
      } catch (_) {
        // The pane can report zero geometry briefly while being hidden.
      }
    }, 20);
  }

  terminal.onData(function (data) {
    if (bridge) {
      bridge.input(data);
    }
  });

  terminal.attachCustomKeyEventHandler(function (event) {
    if (event.type !== "keydown" || !bridge) {
      return true;
    }
    const copy = event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "c";
    const paste = event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "v";
    if (copy) {
      bridge.copyText(terminal.getSelection());
      return false;
    }
    if (paste) {
      bridge.requestPaste();
      return false;
    }
    const fontSmaller = event.ctrlKey && !event.altKey && !event.metaKey && event.key === "-";
    const fontLarger = event.ctrlKey && !event.altKey && !event.metaKey && event.key === "+";
    const newTerminal = event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey && event.key.toLowerCase() === "t";
    if (newTerminal) {
      bridge.requestNewTerminal();
      return false;
    }
    if (fontSmaller || fontLarger) {
      bridge.adjustFontSize(fontLarger ? 1 : -1);
      return false;
    }
    return true;
  });

  document.getElementById("terminal").addEventListener("wheel", function (event) {
    if (!event.ctrlKey || !bridge || event.deltaY === 0) {
      return;
    }
    event.preventDefault();
    bridge.adjustFontSize(event.deltaY < 0 ? 1 : -1);
  }, { passive: false });

  new ResizeObserver(fitAndNotify).observe(document.getElementById("terminal"));

  window.stillpointTerminal = {
    write: function (sequence, data) {
      terminal.write(data, function () {
        if (bridge) {
          bridge.acknowledgeOutput(sequence);
        }
      });
    },
    clear: function () { terminal.clear(); },
    focus: function () { terminal.focus(); },
    applyOptions: function (raw) {
      try {
        const options = JSON.parse(raw || "{}");
        Object.keys(options).forEach(function (key) {
          terminal.options[key] = options[key];
        });
        if (options.theme && options.theme.background) {
          document.documentElement.style.background = options.theme.background;
          document.body.style.background = options.theme.background;
          document.getElementById("terminal").style.background = options.theme.background;
        }
        fitAndNotify();
      } catch (_) {
        // Invalid options should not prevent terminal startup.
      }
    }
  };

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.terminalBridge;
    bridge.outputData.connect(window.stillpointTerminal.write);
    bridge.clearRequested.connect(window.stillpointTerminal.clear);
    bridge.focusRequested.connect(window.stillpointTerminal.focus);
    bridge.pasteData.connect(function (data) { terminal.paste(data); });
    bridge.optionsChanged.connect(window.stillpointTerminal.applyOptions);
    fitAddon.fit();
    bridge.ready(terminal.cols, terminal.rows);
    terminal.focus();
  });
}());
