"""Global recording hotkeys; native registration, never a keyboard logger."""
from __future__ import annotations

import asyncio
import ctypes
import os
from pathlib import Path
import sys
import time
import uuid

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QSocketNotifier, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication

START_BINDINGS = (("start", "R", "Start screen recording for the current page"),)
BINDINGS = (("pause", "P", "Pause/resume screen recording"),
            ("stop", "S", "Stop screen recording and review"))
PORTAL_APP_ID = "app.stillpoint.StillPoint"
DEFAULT_DESCRIPTION = "Ctrl+Alt+Shift+P: pause/resume · Ctrl+Alt+Shift+S: stop/review"


class RecordingHotkeys(QObject):
    activated = Signal(str)
    ready = Signal()
    statusChanged = Signal(str)

    def __init__(self, parent=None, *, bindings=BINDINGS):
        super().__init__(parent)
        self._bindings = tuple(bindings)
        self.status = "Global shortcuts not registered"
        self._backend = None
        self._last_activation = {}
        self._open = False

    def start(self):
        if self._open:
            self.ready.emit()
            return
        self._open = True
        self.status = "Registering global shortcuts…"
        self.statusChanged.emit(self.status)
        try:
            platform = QGuiApplication.platformName().lower()
            if sys.platform == "win32":
                self._backend = _WindowsHotkeys(self._dispatch, self._bindings)
            elif sys.platform == "darwin":
                self._backend = _MacHotkeys(self._dispatch, self._bindings)
            elif platform.startswith("wayland"):
                self._backend = _PortalHotkeys(QCoreApplication.instance(), bindings=self._bindings)
                self._backend.activated.connect(self._dispatch)
                self._backend.registered.connect(self._registered)
                self._backend.start()
                return
            elif platform == "xcb":
                self._backend = _X11Hotkeys(self._dispatch, self, self._bindings)
            else:
                raise RuntimeError("this desktop has no supported global-shortcut backend")
            labels = {"start": "start recording", "pause": "pause/resume", "stop": "stop/review"}
            description = " · ".join(f"Ctrl+Alt+Shift+{key}: {labels[action]}" for action, key, _ in self._bindings)
            self._registered(description.replace("Ctrl+Alt", "Control+Option") if sys.platform == "darwin" else description)
        except Exception as exc:
            self.close()
            self._registered(f"Global shortcuts unavailable: {exc}. Use the recording bar or tray.")

    def _registered(self, status):
        if "unavailable" in status.lower() or "were revoked" in status.lower():
            self.close()
        self.status = status
        self.statusChanged.emit(status)
        self.ready.emit()

    def _dispatch(self, action):
        if not self._open or action not in {binding[0] for binding in self._bindings}:
            return
        now = time.monotonic()
        if now - self._last_activation.get(action, -1) < 0.4:
            return
        self._last_activation[action] = now
        self.activated.emit(action)

    def close(self):
        self._open = False
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._last_activation.clear()


class _X11Hotkeys:
    def __init__(self, callback, parent, bindings=BINDINGS):
        from Xlib import X, XK, display, error
        self._X = X
        self._display = display.Display()
        self._root = self._display.screen().root
        self._keys = {}
        self._grabs = []
        self._notifier = None
        self._callback = callback
        self._down = set()
        try:
            numlock_code = self._display.keysym_to_keycode(XK.string_to_keysym("Num_Lock"))
            numlock_mask = 0
            for modifier, codes in enumerate(self._display.get_modifier_mapping()):
                if numlock_code and numlock_code in codes:
                    numlock_mask |= 1 << modifier
            ignored = {0, X.LockMask, numlock_mask, X.LockMask | numlock_mask}
            modifiers = X.ControlMask | X.Mod1Mask | X.ShiftMask
            for action, key, _description in bindings:
                code = self._display.keysym_to_keycode(XK.string_to_keysym(key.lower()))
                if not code:
                    raise RuntimeError(f"cannot find the {key} key on this keyboard")
                self._keys[code] = action
                for lock_mask in ignored:
                    caught = error.CatchError(error.BadAccess)
                    mask = modifiers | lock_mask
                    self._root.grab_key(code, mask, False, X.GrabModeAsync, X.GrabModeAsync, onerror=caught)
                    self._display.sync()
                    if caught.get_error() is not None:
                        raise RuntimeError(f"Ctrl+Alt+Shift+{key} is already in use")
                    self._grabs.append((code, mask))
            self._notifier = QSocketNotifier(self._display.fileno(), QSocketNotifier.Read, parent)
            self._notifier.activated.connect(self._events)
        except Exception:
            self.close()
            raise

    def _events(self, *_args):
        # X11 represents auto-repeat as adjacent release/press events with the
        # same timestamp. Retain the held-key state across those pairs.
        events = []
        while self._display.pending_events():
            events.append(self._display.next_event())
        for index, event in enumerate(events):
            if event.type == self._X.KeyRelease:
                following = events[index + 1] if index + 1 < len(events) else None
                repeat = (following is not None and following.type == self._X.KeyPress
                          and following.detail == event.detail and following.time == event.time)
                if not repeat:
                    self._down.discard(event.detail)
            elif event.type == self._X.KeyPress and event.detail in self._keys:
                if event.detail not in self._down:
                    self._down.add(event.detail)
                    self._callback(self._keys[event.detail])

    def close(self):
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._display is not None:
            for code, mask in self._grabs:
                self._root.ungrab_key(code, mask)
            self._display.sync()
            self._display.close()
            self._display = None


class _WindowsHotkeys(QAbstractNativeEventFilter):
    def __init__(self, callback, bindings=BINDINGS):
        super().__init__()
        from ctypes import wintypes
        self._msg_type = wintypes.MSG
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._callback = callback
        self._ids = {}
        self._installed = False
        try:
            for action, key, _ in bindings:
                identifier = {"pause": 0x5350, "stop": 0x5351, "start": 0x5352}[action]
                if not self._user32.RegisterHotKey(None, identifier, 0x4000 | 0x1 | 0x2 | 0x4, ord(key)):
                    raise RuntimeError(f"Ctrl+Alt+Shift+{key} could not be registered (error {ctypes.get_last_error()})")
                self._ids[identifier] = action
            QCoreApplication.instance().installNativeEventFilter(self)
            self._installed = True
        except Exception:
            self.close()
            raise

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = self._msg_type.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self._ids:  # WM_HOTKEY
                self._callback(self._ids[msg.wParam])
                return True, 0
        return False, 0

    def close(self):
        if self._installed:
            QCoreApplication.instance().removeNativeEventFilter(self)
            self._installed = False
        for identifier in self._ids:
            self._user32.UnregisterHotKey(None, identifier)
        self._ids.clear()


class _HotkeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventType(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _MacHotkeys:
    def __init__(self, callback, bindings=BINDINGS):
        self._carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon = self._carbon
        pointer = ctypes.c_void_p
        status = ctypes.c_int32
        self._handler = pointer()
        self._refs = []
        self._ids = set()
        self._callback = callback
        self._held = set()
        carbon.GetEventKind.argtypes = [pointer]
        carbon.GetEventKind.restype = ctypes.c_uint32
        carbon.GetApplicationEventTarget.restype = pointer
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotkeyID, pointer, ctypes.c_uint32, ctypes.POINTER(pointer)]
        carbon.RegisterEventHotKey.restype = status
        carbon.UnregisterEventHotKey.argtypes = [pointer]
        carbon.UnregisterEventHotKey.restype = status
        carbon.RemoveEventHandler.argtypes = [pointer]
        carbon.RemoveEventHandler.restype = status
        handler_type = ctypes.CFUNCTYPE(status, pointer, pointer, pointer)
        self._handler_callback = handler_type(self._event)
        carbon.InstallEventHandler.argtypes = [pointer, handler_type, ctypes.c_uint32, ctypes.POINTER(_EventType), pointer, ctypes.POINTER(pointer)]
        carbon.InstallEventHandler.restype = status
        carbon.GetEventParameter.argtypes = [pointer, ctypes.c_uint32, ctypes.c_uint32, pointer, ctypes.c_uint32, pointer, pointer]
        carbon.GetEventParameter.restype = status
        try:
            specs = (_EventType * 2)(_EventType(int.from_bytes(b"keyb", "big"), 6),
                                     _EventType(int.from_bytes(b"keyb", "big"), 7))
            target = carbon.GetApplicationEventTarget()
            result = carbon.InstallEventHandler(target, self._handler_callback, 2, specs, None, ctypes.byref(self._handler))
            if result:
                raise RuntimeError(f"could not install macOS hotkey handler ({result})")
            # Carbon uses the physical Control key, not Qt's Command mapping.
            for action, key, _ in bindings:
                identifier, keycode = {"pause": (1, 35), "stop": (2, 1), "start": (3, 15)}[action]  # ANSI P / S / R
                ref = pointer()
                result = carbon.RegisterEventHotKey(keycode, 4096 | 2048 | 512,
                    _HotkeyID(int.from_bytes(b"StPr", "big"), identifier), target, 0, ctypes.byref(ref))
                if result:
                    raise RuntimeError(f"Control+Option+Shift+{key} is unavailable ({result})")
                self._refs.append(ref)
                self._ids.add(identifier)
        except Exception:
            self.close()
            raise

    def _event(self, _next, event, _data):
        identifier = _HotkeyID()
        result = self._carbon.GetEventParameter(event, int.from_bytes(b"----", "big"),
            int.from_bytes(b"hkid", "big"), None, ctypes.sizeof(identifier), None, ctypes.byref(identifier))
        if (result == 0 and identifier.signature == int.from_bytes(b"StPr", "big")
                and identifier.id in self._ids):
            if self._carbon.GetEventKind(event) == 7:
                self._held.discard(identifier.id)
            elif identifier.id not in self._held:
                self._held.add(identifier.id)
                self._callback({1: "pause", 2: "stop", 3: "start"}.get(identifier.id, ""))
            return 0
        return -9874  # eventNotHandledErr

    def close(self):
        for ref in self._refs:
            self._carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        if self._handler.value:
            self._carbon.RemoveEventHandler(self._handler)
            self._handler = ctypes.c_void_p()


class _PortalHotkeys(QThread):
    activated = Signal(str)
    registered = Signal(str)

    def __init__(self, parent=None, *, bindings=BINDINGS):
        super().__init__(parent)
        self._bindings = tuple(bindings)
        self._loop = None
        self._task = None
        self._session = None
        self._responses = {}
        self._pending = {}

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        bus = None
        try:
            from dbus_next import Message, MessageType, Variant
            from dbus_next.aio import MessageBus
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            if self.isInterruptionRequested():
                return
            bus = await MessageBus().connect()
            self._bus, self._Message, self._Variant = bus, Message, Variant
            self._MessageType = MessageType
            bus.add_message_handler(self._message)
            if not (os.environ.get("FLATPAK_ID") or os.environ.get("SNAP") or Path("/.flatpak-info").exists()):
                # New portal backends require an installed desktop identity for
                # unsandboxed clients. Older portals lack Registry entirely.
                reply = await bus.call(Message(destination="org.freedesktop.portal.Desktop",
                    path="/org/freedesktop/portal/desktop", interface="org.freedesktop.host.portal.Registry",
                    member="Register", signature="sa{sv}", body=[PORTAL_APP_ID, {}]))
                if (reply is not None and reply.message_type == MessageType.ERROR
                        and reply.error_name not in ("org.freedesktop.DBus.Error.UnknownMethod", "org.freedesktop.DBus.Error.UnknownInterface")):
                    raise RuntimeError("could not register StillPoint's desktop identity; install its Linux desktop entry")
            for interface in ("org.freedesktop.portal.Request", "org.freedesktop.portal.GlobalShortcuts", "org.freedesktop.portal.Session"):
                reply = await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                    body=[f"type='signal',sender='org.freedesktop.portal.Desktop',interface='{interface}'"]))
                self._check_reply(reply)
            reply = await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                body=["type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.freedesktop.portal.Desktop'"]))
            self._check_reply(reply)
            token = "stillpoint_" + uuid.uuid4().hex
            result = await self._request("CreateSession", "a{sv}", [{
                "handle_token": Variant("s", token), "session_handle_token": Variant("s", token)}])
            self._session = result["session_handle"].value
            shortcuts = [[action, {"description": Variant("s", description),
                          "preferred_trigger": Variant("s", f"CTRL+ALT+SHIFT+{key.lower()}")}]
                         for action, key, description in self._bindings]
            result = await self._request("BindShortcuts", "oa(sa{sv})sa{sv}",
                [self._session, shortcuts, "", {"handle_token": Variant("s", "bind_" + uuid.uuid4().hex)}])
            bound = result.get("shortcuts")
            entries = bound.value if bound is not None else []
            descriptions = {identifier: props.get("trigger_description") for identifier, props in entries}
            if not all(action in descriptions for action, _, _ in self._bindings):
                raise RuntimeError("the desktop did not grant all requested recording shortcuts")
            text = " · ".join(f"{descriptions[action].value if descriptions[action] else key}: {description}"
                              for action, key, description in self._bindings)
            self.registered.emit(text)
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            detail = "the desktop shortcut prompt timed out" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
            self.registered.emit(f"Global shortcuts unavailable: {detail}. Use the recording bar or tray.")
        finally:
            if bus is not None:
                bus.disconnect()  # Portal closes all sessions owned by this connection.
            self._loop = None
            self._task = None

    def _check_reply(self, reply):
        if reply is None or reply.message_type == self._MessageType.ERROR:
            raise RuntimeError(str(reply.body[0]) if reply is not None and reply.body else "portal request failed")

    async def _request(self, member, signature, body):
        reply = await self._bus.call(self._Message(destination="org.freedesktop.portal.Desktop",
            path="/org/freedesktop/portal/desktop", interface="org.freedesktop.portal.GlobalShortcuts",
            member=member, signature=signature, body=body))
        self._check_reply(reply)
        path = reply.body[0]
        if path in self._responses:
            response = self._responses.pop(path)
        else:
            future = asyncio.get_running_loop().create_future()
            self._pending[path] = future
            try:
                response = await asyncio.wait_for(future, timeout=120)
            finally:
                self._pending.pop(path, None)
        code, results = response
        if code:
            raise RuntimeError("global shortcut permission was cancelled or denied")
        return results

    def _message(self, message):
        if message.message_type != self._MessageType.SIGNAL:
            return
        if (message.interface == "org.freedesktop.DBus" and message.member == "NameOwnerChanged"
                and message.body[0] == "org.freedesktop.portal.Desktop" and message.body[1]):
            self.registered.emit("Global shortcuts unavailable: the desktop portal restarted. Use the recording bar or tray.")
            if self._task is not None:
                self._task.cancel()
        elif message.interface == "org.freedesktop.portal.Request" and message.member == "Response":
            future = self._pending.get(message.path)
            if future is not None and not future.done():
                future.set_result(message.body)
            else:
                self._responses[message.path] = message.body
        elif (message.interface == "org.freedesktop.portal.GlobalShortcuts" and message.member == "Activated"
              and self._session and message.body[0] == self._session):
            self.activated.emit(message.body[1])
        elif (message.interface == "org.freedesktop.portal.Session" and message.member == "Closed"
              and self._session and message.path == self._session):
            self.registered.emit("Global shortcuts were revoked by the desktop. Use the recording bar or tray.")
            if self._task is not None:
                self._task.cancel()

    def close(self):
        self.requestInterruption()
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        self.wait(3000)
        if not self.isRunning():
            self.deleteLater()


class RecordingStartShortcut(QObject):
    """One persistent start binding per process, routed to its last active vault."""
    def __init__(self, app):
        super().__init__(app)
        self._windows = []
        self._last_window = None
        self.hotkeys = RecordingHotkeys(self, bindings=START_BINDINGS)
        self.hotkeys.activated.connect(self._activate)
        self.hotkeys.statusChanged.connect(self._status_changed)
        app.aboutToQuit.connect(self.hotkeys.close)
        app.focusChanged.connect(self._focus_changed)

    def register_window(self, window):
        import weakref
        from PySide6.QtCore import QTimer
        if not any(reference() is window for reference in self._windows):
            self._windows.append(weakref.ref(window))
        self._last_window = weakref.ref(window)
        if not self.hotkeys._open:
            QTimer.singleShot(0, self.hotkeys.start)

    def _live_windows(self):
        import shiboken6
        windows = [reference() for reference in self._windows]
        return [window for window in windows if window is not None and shiboken6.isValid(window)]

    def _focus_changed(self, _old, widget):
        import weakref
        while widget is not None:
            if any(widget is window for window in self._live_windows()):
                self._last_window = weakref.ref(widget)
                return
            widget = widget.parentWidget()

    def _status_changed(self, status):
        for window in self._live_windows():
            action = getattr(window, "_record_screen_action", None)
            if action is not None:
                action.setToolTip("Record the screen and attach it to the current page\n" + status)
            if "unavailable" in status.lower() and window.isActiveWindow():
                window.statusBar().showMessage(status, 8000)

    def _activate(self, action):
        if action != "start":
            return
        windows = self._live_windows()
        # R is start, never a toggle or an instruction to overwrite a draft.
        for window in windows:
            panel = getattr(getattr(window, "right_panel", None), "attachments_panel", None)
            recording = getattr(panel, "_screen_dialog", None)
            if recording is not None and recording._state in (
                    "waiting", "shortcuts", "countdown", "starting", "recording", "paused", "retrying", "stopping"):
                return
        target = self._last_window() if self._last_window is not None else None
        if target not in windows:
            target = windows[-1] if windows else None
        if target is not None:
            target._start_screen_recording_from_hotkey()


def register_recording_start_shortcut(window):
    app = QCoreApplication.instance()
    if app is None:
        return
    controller = getattr(app, "_stillpoint_recording_start_shortcut", None)
    if controller is None:
        controller = RecordingStartShortcut(app)
        app._stillpoint_recording_start_shortcut = controller
    controller.register_window(window)
