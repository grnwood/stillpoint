"""Isolated Qt WebEngine worker that renders Mermaid source to SVG over JSONL."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication


def _asset_url() -> QUrl:
    roots: list[Path] = []
    if frozen_root := getattr(sys, "_MEIPASS", None):
        roots.extend((Path(frozen_root), Path(frozen_root) / "_internal"))
    roots.append(Path(__file__).resolve().parents[1])
    for root in roots:
        candidate = root / "assets" / "vendor" / "mermaid.min.js"
        if candidate.is_file():
            return QUrl.fromLocalFile(str(candidate))
    raise FileNotFoundError("Bundled Mermaid runtime was not found")


def _asset_base_url(asset_url: QUrl) -> QUrl:
    """Return a local directory URL without relying on Qt enum overloads."""
    asset_path = Path(asset_url.toLocalFile())
    return QUrl.fromLocalFile(str(asset_path.parent) + os.sep)


class RequestInput(QObject):
    received = Signal(object)
    closed = Signal()

    def start(self) -> None:
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in sys.stdin:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self.received.emit(payload)
        self.closed.emit()


class ResultBridge(QObject):
    delivered = Signal()

    @Slot(str)
    def deliver(self, payload: str) -> None:
        try:
            print(payload, flush=True)
        finally:
            self.delivered.emit()


class RenderController(QObject):
    """Own all WebEngine calls on the QApplication thread."""

    def __init__(self, view) -> None:
        super().__init__()
        self.view = view
        self.pending: list[dict] = []
        self.busy = False
        self.page_ready = False

    @Slot(object)
    def receive(self, request: dict) -> None:
        self.pending.append(request)
        self.dispatch()

    @Slot()
    def delivered(self) -> None:
        self.busy = False
        self.dispatch()

    def set_ready(self) -> None:
        self.page_ready = True
        self.dispatch()

    def dispatch(self) -> None:
        if self.busy or not self.page_ready or not self.pending:
            return
        self.busy = True
        request = self.pending.pop(0)
        self.view.page().runJavaScript(
            f"spRender({json.dumps(request, ensure_ascii=False)})"
        )


def _shell_html(script_url: str) -> str:
    script_json = json.dumps(script_url)
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<script src={script_json}></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
window.__spReady = false;
new QWebChannel(qt.webChannelTransport, (channel) => {{
  window.stillpoint = channel.objects.stillpoint;
  window.__spReady = Boolean(window.mermaid && window.stillpoint);
}});
async function spRender(request) {{
  let response;
  try {{
    if (!window.__spReady || !window.mermaid) throw new Error('Bundled Mermaid runtime is not ready');
    mermaid.initialize({{
      startOnLoad: false,
      securityLevel: 'loose',
      theme: request.theme || 'neutral',
      flowchart: {{ htmlLabels: false }}
    }});
    const rendered = await mermaid.render(`sp-${{request.id}}`, request.source || '');
    response = {{ id: request.id, ok: true, svg: rendered.svg }};
  }} catch (error) {{
    response = {{
      id: request.id,
      ok: false,
      error: error && error.message ? error.message : String(error),
      details: error && error.stack ? error.stack : ''
    }};
  }}
  window.stillpoint.deliver(JSON.stringify(response));
}}
</script></head><body><div id="render-target"></div></body></html>"""


def main() -> int:
    os.environ.pop("SP_MERMAID_DISABLE_INPROCESS_WEBENGINE", None)
    from sp.app.ui.webengine_env import configure_linux_webengine_env

    configure_linux_webengine_env()
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv[:1])
    view = QWebEngineView()
    view.resize(1200, 800)
    bridge = ResultBridge()
    channel = QWebChannel(view.page())
    channel.registerObject("stillpoint", bridge)
    view.page().setWebChannel(channel)
    requests = RequestInput()
    controller = RenderController(view)
    ready_reported = False

    def loaded(ok: bool) -> None:
        if not ok:
            print(json.dumps({"type": "error", "error": "Renderer page failed to load"}), flush=True)
            return

        def checked(value) -> None:
            nonlocal ready_reported
            if bool(value):
                controller.set_ready()
                if not ready_reported:
                    ready_reported = True
                    print(json.dumps({"type": "ready"}), flush=True)
            else:
                QTimer.singleShot(
                    25,
                    lambda: view.page().runJavaScript("Boolean(window.__spReady)", checked),
                )

        view.page().runJavaScript("Boolean(window.__spReady)", checked)

    requests.received.connect(controller.receive)
    requests.closed.connect(app.quit)
    bridge.delivered.connect(controller.delivered)
    view.loadFinished.connect(loaded)
    requests.start()
    asset_url = _asset_url()
    view.setHtml(_shell_html(asset_url.toString()), _asset_base_url(asset_url))
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
