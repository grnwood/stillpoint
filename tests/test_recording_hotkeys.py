from types import SimpleNamespace
import asyncio
import ctypes

import pytest
from PySide6.QtCore import QCoreApplication

from sp.app.ui import recording_hotkeys as hotkeys


def test_manager_registers_debounces_and_releases_native_bindings(qapp, monkeypatch):
    instances = []

    class Backend:
        def __init__(self, callback, parent, bindings=hotkeys.BINDINGS):
            self.callback = callback
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys.QGuiApplication, "platformName", lambda: "xcb")
    monkeypatch.setattr(hotkeys, "_X11Hotkeys", Backend)
    ticks = iter([1.0, 1.1, 1.6, 1.7])
    monkeypatch.setattr(hotkeys.time, "monotonic", lambda: next(ticks))
    manager = hotkeys.RecordingHotkeys()
    received, ready = [], []
    manager.activated.connect(received.append)
    manager.ready.connect(lambda: ready.append(True))
    manager.start()
    for action in ("pause", "pause", "pause", "stop"):
        instances[0].callback(action)
    assert received == ["pause", "pause", "stop"]
    assert ready == [True]
    assert "Ctrl+Alt+Shift+P" in manager.status
    manager.close()
    assert instances[0].closed
    instances[0].callback("stop")
    assert received == ["pause", "pause", "stop"]


def test_registration_conflict_is_reported_and_recording_can_continue(qapp, monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys.QGuiApplication, "platformName", lambda: "xcb")

    def unavailable(*args):
        raise RuntimeError("Ctrl+Alt+Shift+P is already in use")

    monkeypatch.setattr(hotkeys, "_X11Hotkeys", unavailable)
    manager = hotkeys.RecordingHotkeys()
    ready = []
    manager.ready.connect(lambda: ready.append(True))
    manager.start()
    assert ready == [True]
    assert "already in use" in manager.status
    assert "bar or tray" in manager.status
    assert not manager._open
    manager.close()


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("fail_second", [False, True])
def test_windows_registration_uses_no_repeat_and_rolls_back_conflicts(qapp, monkeypatch, fail_second):
    registered, unregistered = [], []

    def register(window, identifier, modifiers, key):
        registered.append((identifier, modifiers, key))
        return not (fail_second and len(registered) == 2)

    user32 = SimpleNamespace(
        RegisterHotKey=Function(register),
        UnregisterHotKey=Function(lambda window, identifier: unregistered.append(identifier)),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: user32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1409, raising=False)
    if fail_second:
        with pytest.raises(RuntimeError, match="could not be registered"):
            hotkeys._WindowsHotkeys(lambda action: None)
        assert unregistered == [0x5350]
    else:
        received = []
        backend = hotkeys._WindowsHotkeys(received.append)
        message = backend._msg_type()
        message.message = 0x0312
        message.wParam = 0x5351
        assert backend.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(message)) == (True, 0)
        assert received == ["stop"]
        backend.close()
        assert unregistered == [0x5350, 0x5351]
    assert registered == [(0x5350, 0x4007, ord("P")), (0x5351, 0x4007, ord("S"))]


@pytest.mark.parametrize("mode", ["recording", "start", "capture"])
def test_macos_uses_physical_control_and_ignores_held_key_repeats(qapp, monkeypatch, mode):
    registered, unregistered, removed = [], [], []
    kind = [5]  # Actual Carbon kEventHotKeyPressed, not the release event.
    identifier_to_deliver = [{"start": 3, "capture": 4, "recording": 1}[mode]]

    def register(code, modifiers, identifier, target, options, output):
        registered.append((code, modifiers, identifier.id))
        output._obj.value = 100 + identifier.id
        return 0

    def install(target, callback, count, specs, data, output):
        assert count == 2
        assert [specs[index].eventKind for index in range(count)] == [5, 6]
        output._obj.value = 99
        return 0

    def parameter(event, name, type_, actual, size, size_out, output):
        output._obj.signature = int.from_bytes(b"StPr", "big")
        output._obj.id = identifier_to_deliver[0]
        return 0

    carbon = SimpleNamespace(
        GetApplicationEventTarget=Function(lambda: 77),
        RegisterEventHotKey=Function(register),
        UnregisterEventHotKey=Function(lambda ref: unregistered.append(ref.value)),
        InstallEventHandler=Function(install),
        RemoveEventHandler=Function(lambda ref: removed.append(ref.value)),
        GetEventParameter=Function(parameter),
        GetEventKind=Function(lambda event: kind[0]),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda path: carbon)
    received = []
    bindings = {"start": hotkeys.START_BINDINGS, "recording": hotkeys.BINDINGS,
                "capture": (("capture", "Alt+Shift+S", "Capture screen region"),)}[mode]
    backend = hotkeys._MacHotkeys(received.append, bindings)
    expected = {"start": [(15, 4096 | 2048 | 512, 3)],
                "capture": [(1, 2048 | 512, 4)],
                "recording": [(35, 4096 | 2048 | 512, 1), (1, 4096 | 2048 | 512, 2)]}[mode]
    assert registered == expected
    backend._event(None, None, None)
    backend._event(None, None, None)
    kind[0] = 6
    backend._event(None, None, None)
    kind[0] = 5
    backend._event(None, None, None)
    expected_action = {"start": "start", "capture": "capture", "recording": "pause"}[mode]
    assert received == [expected_action, expected_action]
    # Release and press again: the binding stays live after repeated captures.
    for _ in range(3):
        kind[0] = 6
        backend._event(None, None, None)
        kind[0] = 5
        backend._event(None, None, None)
    assert received == [expected_action] * 5
    identifier_to_deliver[0] = 1 if mode in ("start", "capture") else 3
    assert backend._event(None, None, None) == -9874
    assert received == [expected_action] * 5
    backend.close()
    assert unregistered == {"start": [103], "capture": [104], "recording": [101, 102]}[mode]
    assert removed == [99]


def test_portal_handles_early_response_and_rejects_permission_denial(qapp):
    from dbus_next import Message, MessageType
    portal = hotkeys._PortalHotkeys()
    portal._Message = Message
    portal._MessageType = MessageType

    class Bus:
        async def call(self, message):
            message.serial = 1
            portal._message(Message.new_signal("/request/test", "org.freedesktop.portal.Request", "Response", "ua{sv}", [1, {}]))
            return Message.new_method_return(message, "o", ["/request/test"])

    portal._bus = Bus()
    with pytest.raises(RuntimeError, match="cancelled or denied"):
        asyncio.run(portal._request("CreateSession", "a{sv}", [{}]))
    assert not portal._pending


@pytest.mark.parametrize("grant_both", [False, True])
@pytest.mark.parametrize("requested", [hotkeys.BINDINGS, hotkeys.START_BINDINGS])
def test_wayland_portal_uses_granted_bindings_and_disconnects_session(qapp, monkeypatch, grant_both, requested):
    from dbus_next import Message, MessageType, Variant
    portal = hotkeys._PortalHotkeys(bindings=requested)
    statuses, actions = [], []
    portal.registered.connect(statuses.append)
    portal.activated.connect(lambda action, _token: actions.append(action))

    class Bus:
        def __init__(self):
            self.handler = None
            self.disconnected = False
            self.calls = []

        async def connect(self):
            return self

        def add_message_handler(self, handler):
            self.handler = handler

        async def call(self, message):
            self.calls.append(message)
            message.serial = len(self.calls)
            message._marshall()  # Exercise the actual D-Bus argument signatures.
            if message.member in ("AddMatch", "Register"):
                return Message.new_method_return(message)
            path = "/request/" + message.member
            if message.member == "CreateSession":
                result = {"session_handle": Variant("s", "/session/test")}
            else:
                assert message.signature == "oa(sa{sv})sa{sv}"
                assert message.body[1][0][1]["preferred_trigger"].value == f"CTRL+ALT+SHIFT+{requested[0][1].lower()}"
                bindings = [[action, {"trigger_description": Variant("s", f"Custom {action} binding")}]
                            for action, _, _ in requested]
                if not grant_both:
                    bindings = bindings[:-1]
                result = {"shortcuts": Variant("a(sa{sv})", bindings)}
            self.handler(Message.new_signal(path, "org.freedesktop.portal.Request", "Response", "ua{sv}", [0, result]))
            return Message.new_method_return(message, "o", [path])

        def disconnect(self):
            self.disconnected = True

    bus = Bus()
    monkeypatch.setattr("dbus_next.aio.MessageBus", lambda: bus)

    async def run():
        task = asyncio.create_task(portal._run())
        for _ in range(30):
            if statuses:
                break
            await asyncio.sleep(0)
        assert statuses
        if grant_both:
            assert f"Custom {requested[0][0]} binding" in statuses[0]
            portal._message(Message.new_signal("/portal", "org.freedesktop.portal.GlobalShortcuts", "Activated", "osta{sv}", ["/session/other", "stop", 1, {}]))
            portal._message(Message.new_signal("/portal", "org.freedesktop.portal.GlobalShortcuts", "Activated", "osta{sv}", ["/session/test", requested[0][0], 1, {}]))
            assert actions == [requested[0][0]]
            task.cancel()
        else:
            assert "did not grant all requested" in statuses[0]
        await task
        assert bus.disconnected

    asyncio.run(run())


def test_start_binding_is_registered_separately_from_pause_and_stop(qapp, monkeypatch):
    registrations, releases = [], []

    class Backend:
        def __init__(self, callback, parent, bindings):
            registrations.append(tuple(bindings))

        def close(self):
            releases.append(True)

    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys.QGuiApplication, "platformName", lambda: "xcb")
    monkeypatch.setattr(hotkeys, "_X11Hotkeys", Backend)
    starter = hotkeys.RecordingHotkeys(bindings=hotkeys.START_BINDINGS)
    recording = hotkeys.RecordingHotkeys()
    starter.start()
    recording.start()
    assert registrations == [hotkeys.START_BINDINGS, hotkeys.BINDINGS]
    recording.close()
    assert starter._open
    assert "Ctrl+Alt+Shift+R" in starter.status
    starter.close()
    assert len(releases) == 2


def test_windows_start_uses_its_own_hotkey_identifier(qapp, monkeypatch):
    registrations = []
    user32 = SimpleNamespace(
        RegisterHotKey=Function(lambda window, identifier, modifiers, key: registrations.append((identifier, key)) or True),
        UnregisterHotKey=Function(lambda *args: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: user32, raising=False)
    backend = hotkeys._WindowsHotkeys(lambda action: None, hotkeys.START_BINDINGS)
    assert registrations == [(0x5352, ord("R"))]
    backend.close()


def test_global_start_routes_to_last_focused_vault_and_ignores_active_recording(qapp, qtbot, monkeypatch):
    from PySide6.QtWidgets import QLineEdit, QWidget
    from PySide6.QtTest import QTest
    controller = hotkeys.RecordingStartShortcut(qapp)
    monkeypatch.setattr(controller.hotkeys, "start", lambda: None)
    called = []
    windows = []
    for name in ("first", "second"):
        window = QWidget()
        qtbot.addWidget(window)
        window.right_panel = SimpleNamespace(attachments_panel=SimpleNamespace(_screen_dialog=None))
        window._start_screen_recording_from_hotkey = lambda name=name: called.append(name)
        windows.append(window)
        controller.register_window(window)
    child = QLineEdit(windows[0])
    controller._focus_changed(None, child)
    controller._activate("start")
    assert called == ["first"]
    windows[1].right_panel.attachments_panel._screen_dialog = SimpleNamespace(_state="recording")
    controller._activate("start")
    assert called == ["first"]
    windows[1].right_panel.attachments_panel._screen_dialog = None
    controller._focus_changed(None, windows[1])
    controller._activate("start")
    assert called == ["first", "second"]
    controller.deleteLater()
    QTest.qWait(1)


def test_windows_capture_uses_alt_shift_without_control(qapp, monkeypatch):
    registered = []
    user32 = SimpleNamespace(
        RegisterHotKey=Function(lambda window, identifier, modifiers, key: registered.append((identifier, modifiers, key)) or True),
        UnregisterHotKey=Function(lambda *args: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: user32, raising=False)
    backend = hotkeys._WindowsHotkeys(lambda action: None, (("capture", "Alt+Shift+S", "Capture region"),))
    assert registered == [(0x5353, 0x4005, ord("S"))]
    backend.close()


def test_capture_portal_trigger_preserves_requested_modifiers():
    assert hotkeys._portal_trigger("Alt+Shift+S") == "ALT+SHIFT+s"
    assert hotkeys._portal_trigger("Ctrl+Alt+G") == "CTRL+ALT+g"


def test_windows_quick_capture_space_is_distinct_from_screenshot(qapp, monkeypatch):
    registered = []
    user32 = SimpleNamespace(
        RegisterHotKey=Function(lambda window, identifier, modifiers, key: registered.append((identifier, modifiers, key)) or True),
        UnregisterHotKey=Function(lambda *args: True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: user32, raising=False)
    screenshot = hotkeys._WindowsHotkeys(lambda action: None, (("capture", "Alt+Shift+S", "Capture region"),))
    quick_capture = hotkeys._WindowsHotkeys(lambda action: None, (("quick_capture", "Ctrl+Alt+Space", "Quick Capture"),))
    try:
        assert registered == [(0x5353, 0x4005, ord("S")), (0x5354, 0x4003, 0x20)]
        assert hotkeys._portal_trigger("Ctrl+Alt+Space") == "CTRL+ALT+space"
    finally:
        quick_capture.close()
        screenshot.close()
