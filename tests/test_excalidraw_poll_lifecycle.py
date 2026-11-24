from types import SimpleNamespace

import pytest


def response(path=None):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"path": path})


def test_navigation_poll_stops_when_last_editor_exits(main_window, monkeypatch):
    window = main_window
    live = SimpleNamespace(poll=lambda: None)
    exited = SimpleNamespace(poll=lambda: 0)
    window._excalidraw_processes = [exited, live]
    requests = []
    monkeypatch.setattr(window.http, "get", lambda path: requests.append(path) or response())
    window._ensure_excalidraw_open_page_poll()
    timer = window._excalidraw_open_page_timer
    assert timer.isActive()
    window._poll_excalidraw_open_page_request()
    assert requests == ["/api/excalidraw/open-page/next"]
    assert window._excalidraw_processes == [live]
    live.poll = lambda: 0
    window._poll_excalidraw_open_page_request()
    assert not timer.isActive()
    assert window._excalidraw_processes == []
    assert requests == ["/api/excalidraw/open-page/next"]
    # A queued timeout after stopping also must not issue another request.
    window._poll_excalidraw_open_page_request()
    assert len(requests) == 1
    window._excalidraw_processes = [SimpleNamespace(poll=lambda: None)]
    window._ensure_excalidraw_open_page_poll()
    assert timer.isActive()
    window._poll_excalidraw_open_page_request()
    assert len(requests) == 2
    timer.stop()


@pytest.mark.parametrize("closing,remote", [(True, False), (False, True)])
def test_navigation_poll_stops_during_main_shutdown_or_remote_switch(main_window, monkeypatch, closing, remote):
    window = main_window
    window._excalidraw_processes = [SimpleNamespace(poll=lambda: None)]
    monkeypatch.setattr(window.http, "get", lambda *a: pytest.fail("Unexpected API polling"))
    window._ensure_excalidraw_open_page_poll()
    window.setProperty("_sp_closing", closing)
    window._remote_mode = remote
    window._poll_excalidraw_open_page_request()
    assert not window._excalidraw_open_page_timer.isActive()
    window.setProperty("_sp_closing", False)
    window._remote_mode = False


def test_explicit_external_browser_navigation_is_preserved(main_window, monkeypatch):
    window = main_window
    window._excalidraw_browser_navigation = True
    window._excalidraw_processes = []
    requests = []
    monkeypatch.setattr(window.http, "get", lambda path: requests.append(path) or response())
    window._ensure_excalidraw_open_page_poll()
    window._poll_excalidraw_open_page_request()
    assert requests == ["/api/excalidraw/open-page/next"]
    window._excalidraw_open_page_timer.stop()
