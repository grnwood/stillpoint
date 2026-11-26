"""Qt microphone capture and optional AAC encoding for voice attachments.

Capture always keeps a PCM WAV draft. Encoding and attachment saves never
replace that draft until the caller confirms a successful save.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import wave

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioBuffer, QAudioFormat, QAudioSource, QMediaCaptureSession,
    QMediaFormat, QMediaRecorder,
)


def pcm16(data: bytes, sample_format: QAudioFormat.SampleFormat) -> tuple[bytes, float]:
    """Convert device samples to little-endian signed PCM16 and measure peak."""
    if sample_format == QAudioFormat.UInt8:
        values = [(value - 128) / 128 for value in data]
    else:
        code = {QAudioFormat.Int16: "h", QAudioFormat.Int32: "i", QAudioFormat.Float: "f"}[sample_format]
        samples = array(code)
        samples.frombytes(data)
        # Device samples use native byte order; WAV output is little endian.
        divisor = {"h": 32768, "i": 2147483648, "f": 1}[code]
        values = [max(-1.0, min(1.0, value / divisor)) if math.isfinite(value) else 0.0 for value in samples]
    peak = max((abs(value) for value in values), default=0.0)
    output = array("h", (max(-32768, min(32767, round(value * 32768))) for value in values))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes(), peak


class VoiceRecorder(QObject):
    updated = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.frames = 0
        self.peak = 0.0
        self.format = QAudioFormat()
        self.path: Path | None = None
        self._source = None
        self._input = None
        self._writer = None
        self._tail = b""

    @property
    def duration_ms(self) -> int:
        return self.frames * 1000 // max(1, self.format.sampleRate())

    def start(self, device, path: Path) -> None:
        if self.state != "idle":
            return
        if device.isNull():
            self.failed.emit("No microphone is available. Connect one and try again.")
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self.format = fmt
        self.path = path
        try:
            self._writer = wave.open(str(path), "wb")
            self._writer.setnchannels(fmt.channelCount())
            self._writer.setsampwidth(2)
            self._writer.setframerate(fmt.sampleRate())
            self._source = QAudioSource(device, fmt, self)
            self._source.stateChanged.connect(self._device_state_changed)
            self.state = "recording"
            self._input = self._source.start()
            if self._input is None:
                raise RuntimeError("The microphone could not be opened. Check microphone permissions and whether another app is using it.")
            self._input.readyRead.connect(self._read)
        except (OSError, RuntimeError, wave.Error) as exc:
            self._fail(str(exc))
        self.updated.emit()

    def _device_state_changed(self, _state) -> None:
        if self.state in ("recording", "paused") and self._source.error().value != 0:
            self._fail("Microphone recording stopped unexpectedly. Check the input device and microphone permissions. Your captured audio is retained.")

    def _read(self) -> None:
        if self._input is None or self.state != "recording":
            return
        try:
            data = self._tail + bytes(self._input.readAll())
            frame_bytes = self.format.bytesPerFrame()
            length = len(data) - len(data) % frame_bytes
            self._tail = data[length:]
            if length:
                audio, peak = pcm16(data[:length], self.format.sampleFormat())
                if (self.frames * self.format.channelCount() * 2 + len(audio)) > 0xFFFFFF00:
                    raise OSError("The WAV recording size limit was reached. Save this recording and start a new one.")
                self._writer.writeframesraw(audio)
                self.frames += length // frame_bytes
                self.peak = max(self.peak, peak)
        except (OSError, ValueError, wave.Error) as exc:
            self._fail(f"Could not write the recording: {exc}")

    def pause(self) -> None:
        if self.state == "recording":
            self._read()
            if self.state != "recording":
                return
            self.state = "paused"
            self._source.suspend()
            self.peak = 0.0
            self.updated.emit()

    def resume(self) -> None:
        if self.state == "paused":
            # Exclude any queued samples from the paused interval.
            self._input.readAll()
            self._tail = b""
            self.state = "recording"
            self._source.resume()
            self.updated.emit()

    def stop(self) -> None:
        if self.state not in ("recording", "paused"):
            return
        if self.state == "recording":
            self._read()
            if self.state != "recording":
                return
        self.state = "stopped"
        try:
            self._close_capture()
        except (OSError, wave.Error) as exc:
            self.failed.emit(f"Could not finalize the recording: {exc}. The draft is retained at {self.path}.")
        self.updated.emit()

    def _close_capture(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
            self._source = None
        self._input = None
        if self._writer is not None:
            writer, self._writer = self._writer, None
            writer.close()
        self.peak = 0.0

    def _fail(self, message: str) -> None:
        self.state = "stopped" if self.frames else "idle"
        try:
            self._close_capture()
        except (OSError, wave.Error):
            pass
        self.failed.emit(message)
        self.updated.emit()


def aac_available() -> bool:
    try:
        from PySide6.QtMultimedia import QAudioBufferInput  # Qt >= 6.8
        fmt = QMediaFormat(QMediaFormat.Mpeg4Audio)
        fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        return hasattr(QMediaRecorder, "setAutoStop") and fmt.isSupported(QMediaFormat.Encode)
    except ImportError:
        return False


class VoiceEncoder(QObject):
    """Feed a WAV draft to Qt's encoder with bounded, backpressured buffers."""
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reader = None
        self._pending = None
        self._frames = 0
        self._ended = False
        self._done = False
        self._started = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._fail("Audio encoding timed out. You can save the WAV recording instead."))

    def start(self, source: Path, destination: Path) -> None:
        from PySide6.QtMultimedia import QAudioBufferInput
        self.path = destination
        try:
            self._reader = wave.open(str(source), "rb")
            self.format = QAudioFormat()
            self.format.setSampleRate(self._reader.getframerate())
            self.format.setChannelCount(self._reader.getnchannels())
            self.format.setSampleFormat(QAudioFormat.Int16)
            self.session = QMediaCaptureSession(self)
            self.recorder = QMediaRecorder(self)
            self.input = QAudioBufferInput(self.format, self)
            self.session.setAudioBufferInput(self.input)
            self.session.setRecorder(self.recorder)
            fmt = QMediaFormat(QMediaFormat.Mpeg4Audio)
            fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC)
            self.recorder.setMediaFormat(fmt)
            self.recorder.setAudioBitRate(64000)
            self.recorder.setAutoStop(True)
            self.recorder.setOutputLocation(QUrl.fromLocalFile(str(destination)))
            self.recorder.errorOccurred.connect(lambda _error, text: self._fail(text))
            self.recorder.recorderStateChanged.connect(self._state_changed)
            self.input.readyToSendAudioBuffer.connect(self._send)
            self._timeout.start(30000)
            self.recorder.record()
        except (OSError, RuntimeError, wave.Error) as exc:
            self._fail(str(exc))

    def _send(self) -> None:
        if self._done or self._ended:
            return
        self._timeout.start(30000)
        # Yield between chunks; the encoder requests more as capacity opens.
        if self._pending is None:
            try:
                data = self._reader.readframes(4096)
            except (OSError, wave.Error) as exc:
                self._fail(str(exc))
                return
            self._pending = (QAudioBuffer(QByteArray(data), self.format,
                             self._frames * 1000000 // self.format.sampleRate()) if data else QAudioBuffer())
            self._frames += len(data) // self.format.bytesPerFrame()
        is_end = not self._pending.isValid()
        if self.input.sendAudioBuffer(self._pending):
            self._pending = None
            self._ended = is_end

    def _state_changed(self, state) -> None:
        if state == QMediaRecorder.RecordingState:
            self._started = True
        elif state == QMediaRecorder.StoppedState and self._started and not self._done:
            if self._ended and self.path.exists() and self.path.stat().st_size > 0:
                self._done = True
                self._close()
                self.succeeded.emit(self.path)
            else:
                self._fail("The audio encoder stopped before the recording was complete. Save as WAV instead.")

    def _close(self) -> None:
        self._timeout.stop()
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _fail(self, message: str) -> None:
        if self._done:
            return
        self._done = True
        self._close()
        if hasattr(self, "recorder"):
            self.recorder.stop()
        self.failed.emit(f"Could not encode M4A: {message}")
