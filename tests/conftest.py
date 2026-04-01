from __future__ import annotations

import os
import sys
import gc
from pathlib import Path
import httpx

# Configure Qt platform before importing any PySide modules.
# VS Code test workers can crash (SIGSEGV) when Qt picks a GUI backend
# incompatible with the runner environment.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure `import sp` works even when pytest is launched from `tests/`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
import shiboken6


def _flush_qt(app: QApplication, rounds: int = 2) -> None:
    """Drain queued Qt events, including DeferredDelete callbacks."""
    for _ in range(rounds):
        try:
            app.processEvents()
        except Exception:
            pass
        try:
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        except Exception:
            pass


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Make teardown deterministic to avoid interpreter-shutdown crashes.
    try:
        app.closeAllWindows()
    except Exception:
        pass
    try:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.clear()
    except Exception:
        pass
    _flush_qt(app, rounds=3)
    try:
        app.quit()
    except Exception:
        pass
    _flush_qt(app, rounds=2)
    # Force Qt object destruction while Python runtime is still fully alive.
    try:
        shiboken6.delete(app)
    except Exception:
        pass
    try:
        for wrapper in list(shiboken6.Shiboken.getAllValidWrappers()):
            try:
                shiboken6.Shiboken.invalidate(wrapper)
            except Exception:
                pass
    except Exception:
        pass
    if gc_was_enabled:
        gc.enable()
    gc.collect()


@pytest.fixture(autouse=True)
def _cleanup_toplevel_widgets(qapp: QApplication):
    """Close and delete any leaked widgets between tests."""
    yield
    try:
        widgets = list(qapp.topLevelWidgets())
    except Exception:
        widgets = []

    for widget in reversed(widgets):
        try:
            if hasattr(widget, "close"):
                widget.close()
        except Exception:
            pass
        try:
            if hasattr(widget, "deleteLater"):
                widget.deleteLater()
        except Exception:
            pass

    widgets.clear()
    _flush_qt(qapp, rounds=5)


@pytest.fixture(autouse=True)
def _cleanup_clipboard(qapp: QApplication):
    """Reset clipboard MIME ownership between tests to avoid Qt teardown crashes."""
    yield
    try:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.clear()
    except Exception:
        pass
    _flush_qt(qapp, rounds=1)


class _QtBot:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._widgets = []

    def addWidget(self, widget) -> None:
        self._widgets.append(widget)
        widget.show()
        self._app.processEvents()


@pytest.fixture
def qtbot(qapp: QApplication):
    bot = _QtBot(qapp)
    yield bot
    # Clean up widgets in reverse order
    for widget in reversed(bot._widgets):
        try:
            if hasattr(widget, 'close'):
                widget.close()
        except Exception:
            pass
    # Process events to complete cleanup
    try:
        qapp.processEvents()
    except Exception:
        pass
    # Delete widgets after processing events
    for widget in reversed(bot._widgets):
        try:
            if hasattr(widget, 'deleteLater'):
                widget.deleteLater()
        except Exception:
            pass
    bot._widgets.clear()
    _flush_qt(qapp, rounds=5)


class _TestHttpResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200, url: str = "http://localhost/test") -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


@pytest.fixture
def main_window(qtbot, monkeypatch, tmp_path):
    from sp.app.ui.main_window import MainWindow
    from sp.app import config, indexer

    vault_root = tmp_path / "test_vault"
    page_a = vault_root / "PageA" / "PageA.md"
    child_1 = vault_root / "PageA" / "Child1" / "Child1.md"
    page_b = vault_root / "PageB" / "PageB.md"
    page_c = vault_root / "PageC" / "PageC.md"
    root_page = vault_root / "test_vault" / "test_vault.md"
    for path in (page_a, child_1, page_b, page_c, root_page):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n\nContent for {path.stem}\nMore content here.\n", encoding="utf-8")

    page_map = {
        "/PageA/PageA.md": page_a.read_text(encoding="utf-8"),
        "/PageA/Child1/Child1.md": child_1.read_text(encoding="utf-8"),
        "/PageB/PageB.md": page_b.read_text(encoding="utf-8"),
        "/PageC/PageC.md": page_c.read_text(encoding="utf-8"),
        "/test_vault/test_vault.md": root_page.read_text(encoding="utf-8"),
    }

    tree_state = [
        {
            "name": "PageA",
            "path": "/PageA",
            "open_path": "/PageA/PageA.md",
            "children": [
                {
                    "name": "Child1",
                    "path": "/PageA/Child1",
                    "open_path": "/PageA/Child1/Child1.md",
                    "children": [],
                }
            ],
        },
        {"name": "PageB", "path": "/PageB", "open_path": "/PageB/PageB.md", "children": []},
        {"name": "PageC", "path": "/PageC", "open_path": "/PageC/PageC.md", "children": []},
    ]

    def tree_payload() -> list[dict]:
        return [
            {
                "path": "/",
                "children": [dict(child) for child in tree_state],
            }
        ]

    class _WindowHttpClient:
        def post(self, path, json=None):
            if path == "/api/file/read":
                page_path = str((json or {}).get("path") or "")
                return _TestHttpResponse(
                    payload={"content": page_map.get(page_path, ""), "rev": 1, "mtime_ns": 1},
                    url=f"http://localhost{path}",
                )
            if path == "/api/tree/reorder":
                return _TestHttpResponse(
                    payload={"ok": True, "version": 2},
                    url=f"http://localhost{path}",
                )
            raise AssertionError(f"Unexpected POST path: {path}")

        def get(self, path, params=None):
            if path == "/api/vault/tree":
                return _TestHttpResponse(
                    payload={"tree": tree_payload(), "version": 1},
                    url=f"http://localhost{path}",
                )
            if path == "/api/vault/stats":
                return _TestHttpResponse(
                    payload={"folder_count": 0},
                    url=f"http://localhost{path}",
                )
            if path == "/api/vault/tree/expand-path":
                # Build segments dict from the tree state for each ancestor
                target = (params or {}).get("target", "/")
                segments = {"/": [dict(c) for c in tree_state]}
                parts = [p for p in target.strip("/").split("/") if p]
                current = "/"
                for part in parts:
                    current = f"/{part}" if current == "/" else f"{current}/{part}"
                    # Find children in tree_state for this path
                    for node in tree_state:
                        if node.get("path") == current:
                            segments[current] = node.get("children", [])
                            break
                return _TestHttpResponse(
                    payload={"segments": segments, "version": 1},
                    url=f"http://localhost{path}",
                )
            raise AssertionError(f"Unexpected GET path: {path}")

        def close(self) -> None:
            return None

    monkeypatch.setattr(config, "load_feature_remember_cursor_position_enabled", lambda: True)
    monkeypatch.setattr(config, "has_active_vault", lambda: False)
    monkeypatch.setattr(indexer, "index_page", lambda *args, **kwargs: False)

    window = MainWindow(api_base="http://localhost:5050")
    qtbot.addWidget(window)
    window.http = _WindowHttpClient()
    window.vault_root = str(vault_root)
    window.vault_root_name = vault_root.name
    monkeypatch.setattr(window.right_panel, "refresh_tasks", lambda *args, **kwargs: None)
    monkeypatch.setattr(window.right_panel, "refresh_calendar", lambda *args, **kwargs: None)
    monkeypatch.setattr(window.right_panel, "refresh_links", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_refresh_detached_link_panels", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_save_panel_visibility", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_update_active_page_chicklets", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_update_window_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_update_calendar_for_journal_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_capture_undo_snapshot", lambda *args, **kwargs: None)
    window._test_tree_state = tree_state
    window._populate_vault_tree()
    yield window
    try:
        window.close()
    except Exception:
        pass
    try:
        window.deleteLater()
    except Exception:
        pass
    del window
    _flush_qt(qapp, rounds=5)
