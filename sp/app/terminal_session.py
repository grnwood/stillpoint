"""Cross-platform pseudo-terminal sessions used by the embedded terminal."""

from __future__ import annotations

import codecs
import os
import platform
import shlex
import shutil
import signal
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


OutputCallback = Callable[[str], None]
ExitCallback = Callable[[Optional[int], str], None]
ErrorCallback = Callable[[str], None]


class TerminalSessionError(RuntimeError):
    """Raised when a terminal backend cannot be created or operated."""


def available_shell_commands(system: Optional[str] = None) -> list[tuple[str, list[str]]]:
    """Return installed interactive shells suitable for the embedded PTY."""
    system = system or platform.system()
    candidates: list[str] = []
    if system == "Windows":
        for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell", "cmd.exe"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(resolved)
    else:
        configured = str(os.environ.get("SHELL") or "").strip()
        if configured:
            candidates.append(configured)
        try:
            for line in Path("/etc/shells").read_text(encoding="utf-8").splitlines():
                value = line.strip()
                if value and not value.startswith("#"):
                    candidates.append(value)
        except OSError:
            pass
        for name in ("bash", "zsh", "fish", "sh", "dash", "ksh"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(resolved)

    result: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        normalized = str(path.resolve())
        if normalized in seen:
            continue
        seen.add(normalized)
        name = path.name
        arguments = ["-NoLogo"] if system == "Windows" and name.lower().startswith("power") else []
        if system == "Windows" and name.lower().startswith("pwsh"):
            arguments = ["-NoLogo"]
        elif system != "Windows":
            arguments = ["-l"]
        result.append((f"{name} — {normalized}", [normalized, *arguments]))
    return result


def default_shell_command(system: Optional[str] = None) -> list[str]:
    """Return a conservative interactive shell command for *system*."""
    system = system or platform.system()
    if system == "Windows":
        pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        if pwsh:
            return [pwsh, "-NoLogo"]
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            return [powershell, "-NoLogo"]
        return [os.environ.get("COMSPEC") or "cmd.exe"]

    configured = str(os.environ.get("SHELL") or "").strip()
    if configured and Path(configured).is_file():
        return [configured, "-l"]
    if system == "Darwin" and Path("/bin/zsh").is_file():
        return ["/bin/zsh", "-l"]
    return ["/bin/sh", "-l"]


def parse_shell_command(executable: str = "", arguments: Optional[Sequence[str]] = None) -> list[str]:
    """Build a shell argv without interpolating a shell command string."""
    executable = str(executable or "").strip()
    if not executable:
        return default_shell_command()
    return [executable, *[str(arg) for arg in (arguments or [])]]


class TerminalSession:
    """Small callback-based interface shared by platform terminal backends."""

    def __init__(
        self,
        *,
        on_output: OutputCallback,
        on_exit: ExitCallback,
        on_error: ErrorCallback,
    ) -> None:
        self._on_output = on_output
        self._on_exit = on_exit
        self._on_error = on_error
        self._closed = False

    @property
    def running(self) -> bool:
        raise NotImplementedError

    @property
    def pid(self) -> Optional[int]:
        raise NotImplementedError

    def start(
        self,
        *,
        cwd: Path,
        argv: Sequence[str],
        environment: Mapping[str, str],
        rows: int,
        columns: int,
    ) -> None:
        raise NotImplementedError

    def write(self, data: str) -> None:
        raise NotImplementedError

    def resize(self, rows: int, columns: int) -> None:
        raise NotImplementedError

    def terminate(self) -> None:
        raise NotImplementedError

    def foreground_command_line(self) -> Optional[str]:
        """Return the PTY foreground process command when the backend exposes it."""
        return None


class PosixPtySession(TerminalSession):
    """A POSIX PTY session for Linux and macOS."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._master_fd: Optional[int] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self._closed

    @property
    def pid(self) -> Optional[int]:
        return int(self._process.pid) if self._process is not None else None

    def foreground_command_line(self) -> Optional[str]:
        fd = self._master_fd
        if fd is None or not self.running:
            return None
        try:
            foreground_pid = int(os.tcgetpgrp(fd))
        except (AttributeError, OSError):
            return None
        if foreground_pid <= 0:
            return None

        proc_root = Path("/proc") / str(foreground_pid)
        try:
            raw = (proc_root / "cmdline").read_bytes()
            arguments = [
                value.decode("utf-8", errors="replace")
                for value in raw.split(b"\0")
                if value
            ]
            if arguments:
                return shlex.join(arguments)
        except OSError:
            pass
        try:
            name = (proc_root / "comm").read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass

        # macOS and other POSIX systems generally lack /proc. Query only the
        # foreground process-group leader and bound the call tightly because
        # this method is used while opening a UI switcher.
        try:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(foreground_pid)],
                capture_output=True,
                text=True,
                timeout=0.25,
                check=False,
            )
            command = result.stdout.strip().splitlines()[0].strip()
            return command or None
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    @staticmethod
    def _set_winsize(fd: int, rows: int, columns: int) -> None:
        import fcntl
        import termios

        winsize = struct.pack("HHHH", max(1, rows), max(1, columns), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    def start(
        self,
        *,
        cwd: Path,
        argv: Sequence[str],
        environment: Mapping[str, str],
        rows: int,
        columns: int,
    ) -> None:
        if self.running:
            raise TerminalSessionError("The terminal session is already running.")
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise TerminalSessionError(f"Terminal working directory is unavailable: {cwd}")
        command = [str(item) for item in argv if str(item)]
        if not command:
            raise TerminalSessionError("No shell command was configured.")

        import fcntl
        import pty
        import termios

        master_fd, slave_fd = pty.openpty()
        self._set_winsize(slave_fd, rows, columns)
        child_env = {str(key): str(value) for key, value in environment.items()}
        child_env.setdefault("TERM", "xterm-256color")
        child_env.setdefault("COLORTERM", "truecolor")

        def _prepare_child() -> None:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        try:
            process = subprocess.Popen(
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(cwd),
                env=child_env,
                close_fds=True,
                preexec_fn=_prepare_child,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        self._closed = False
        self._master_fd = master_fd
        self._process = process
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"stillpoint-terminal-{process.pid}",
            daemon=True,
        )
        self._reader_thread.start()

    def _read_loop(self) -> None:
        import select

        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        reason = "exited"
        try:
            while not self._closed:
                fd = self._master_fd
                process = self._process
                if fd is None or process is None:
                    break
                readable, _, _ = select.select([fd], [], [], 0.1)
                if readable:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError as exc:
                        # Linux commonly reports EIO after the slave closes.
                        if getattr(exc, "errno", None) == 5:
                            break
                        raise
                    if not chunk:
                        break
                    text = decoder.decode(chunk)
                    if text:
                        self._on_output(text)
                if process.poll() is not None and not readable:
                    break
            tail = decoder.decode(b"", final=True)
            if tail:
                self._on_output(tail)
        except Exception as exc:
            if not self._closed:
                reason = "read error"
                self._on_error(str(exc))
        finally:
            process = self._process
            code: Optional[int] = None
            if process is not None:
                try:
                    code = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    code = process.poll()
            self._close_master()
            if not self._closed or code is not None:
                self._on_exit(code, reason)

    def _close_master(self) -> None:
        fd, self._master_fd = self._master_fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def write(self, data: str) -> None:
        if not data or not self.running or self._master_fd is None:
            return
        payload = data.encode("utf-8", errors="replace")
        with self._write_lock:
            offset = 0
            while offset < len(payload):
                try:
                    offset += os.write(self._master_fd, payload[offset:])
                except OSError as exc:
                    if not self._closed:
                        self._on_error(str(exc))
                    return

    def resize(self, rows: int, columns: int) -> None:
        if self._master_fd is None or self._closed:
            return
        try:
            self._set_winsize(self._master_fd, rows, columns)
        except OSError as exc:
            if not self._closed:
                self._on_error(str(exc))

    def terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass

            def _force_kill() -> None:
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

            threading.Thread(target=_force_kill, name="stillpoint-terminal-stop", daemon=True).start()
        self._close_master()


class WindowsConPtySession(TerminalSession):
    """Windows ConPTY session implemented through pywinpty."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._process = None
        self._reader_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

    @property
    def running(self) -> bool:
        try:
            return self._process is not None and self._process.isalive() and not self._closed
        except Exception:
            return False

    @property
    def pid(self) -> Optional[int]:
        try:
            return int(self._process.pid) if self._process is not None else None
        except Exception:
            return None

    def start(
        self,
        *,
        cwd: Path,
        argv: Sequence[str],
        environment: Mapping[str, str],
        rows: int,
        columns: int,
    ) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise TerminalSessionError(
                "Windows embedded terminal support requires the pywinpty package."
            ) from exc
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise TerminalSessionError(f"Terminal working directory is unavailable: {cwd}")
        command = [str(item) for item in argv if str(item)]
        if not command:
            raise TerminalSessionError("No shell command was configured.")
        child_env = {str(key): str(value) for key, value in environment.items()}
        child_env.setdefault("TERM", "xterm-256color")
        self._process = PtyProcess.spawn(
            command,
            cwd=str(cwd),
            env=child_env,
            dimensions=(max(1, rows), max(1, columns)),
        )
        self._closed = False
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"stillpoint-conpty-{self.pid or 'session'}",
            daemon=True,
        )
        self._reader_thread.start()

    def _read_loop(self) -> None:
        code: Optional[int] = None
        reason = "exited"
        try:
            while not self._closed and self._process is not None:
                try:
                    data = self._process.read(65536)
                except EOFError:
                    break
                if data:
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    self._on_output(str(data))
                elif not self._process.isalive():
                    break
            if self._process is not None:
                code = self._process.exitstatus
        except Exception as exc:
            if not self._closed:
                reason = "read error"
                self._on_error(str(exc))
        finally:
            if not self._closed or code is not None:
                self._on_exit(code, reason)

    def write(self, data: str) -> None:
        if not data or not self.running:
            return
        try:
            with self._write_lock:
                self._process.write(data)
        except Exception as exc:
            if not self._closed:
                self._on_error(str(exc))

    def resize(self, rows: int, columns: int) -> None:
        if not self.running:
            return
        try:
            self._process.setwinsize(max(1, rows), max(1, columns))
        except Exception as exc:
            if not self._closed:
                self._on_error(str(exc))

    def terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process is not None:
            try:
                self._process.close(force=True)
            except Exception:
                try:
                    self._process.terminate(force=True)
                except Exception:
                    pass


def create_terminal_session(
    *,
    on_output: OutputCallback,
    on_exit: ExitCallback,
    on_error: ErrorCallback,
    system: Optional[str] = None,
) -> TerminalSession:
    """Create the native terminal session backend for the current platform."""
    kwargs = {"on_output": on_output, "on_exit": on_exit, "on_error": on_error}
    if (system or platform.system()) == "Windows":
        return WindowsConPtySession(**kwargs)
    return PosixPtySession(**kwargs)


def command_display(argv: Sequence[str]) -> str:
    """Return a display-only command string; never use it for execution."""
    if platform.system() == "Windows":
        return subprocess.list2cmdline([str(arg) for arg in argv])
    return shlex.join([str(arg) for arg in argv])
