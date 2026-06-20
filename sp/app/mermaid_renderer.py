"""Mermaid rendering and cache management module.

Handles:
- mmdc discovery
- SVG rendering via mermaid-cli (mmdc)
- SVG caching with content hashing
- Error handling and reporting
"""

from __future__ import annotations

import hashlib
import logging
import os
import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from sp.logging_flags import log_enabled

logger = logging.getLogger(__name__)


@dataclass
class RenderResult:
    """Result of a Mermaid render attempt."""
    success: bool
    svg_content: Optional[str] = None
    png_bytes: Optional[bytes] = None
    error_message: Optional[str] = None
    stderr: Optional[str] = None
    duration_ms: float = 0.0


class MermaidRenderer:
    """Manages Mermaid rendering with caching and async support."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or (Path.home() / ".stillpoint_cache" / "mermaid")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mmdc_path: Optional[Path] = None
        self._render_lock = threading.Lock()

    def discover_mmdc(self) -> Optional[Path]:
        """Attempt to locate mmdc on PATH."""
        mmdc_path = shutil.which("mmdc")
        if mmdc_path:
            path = Path(mmdc_path)
            if path.exists():
                self._mmdc_path = path
                return path
        return None

    def set_mmdc_path(self, mmdc_path: str) -> bool:
        """Explicitly set the mmdc path."""
        path = Path(mmdc_path)
        if path.exists() and path.is_file():
            self._mmdc_path = path
            return True
        return False

    def get_mmdc_path(self) -> Optional[Path]:
        """Get the currently configured mmdc path."""
        return self._mmdc_path

    def is_configured(self) -> bool:
        """Check if Mermaid CLI is available."""
        if self._mmdc_path is None:
            self.discover_mmdc()
        return self._mmdc_path is not None

    def render_svg(
        self,
        mermaid_text: str,
        *,
        theme: str = "neutral",
        background_color: Optional[str] = None,
    ) -> RenderResult:
        """Render Mermaid diagram to SVG."""
        t0 = time.perf_counter()

        cache_key = self._compute_cache_key(mermaid_text, theme=theme, background_color=background_color)
        cached_svg = self._read_from_cache(cache_key)
        if cached_svg:
            return RenderResult(
                success=True,
                svg_content=cached_svg,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        if not self.is_configured():
            return RenderResult(
                success=False,
                error_message="Mermaid CLI (mmdc) not found. Install with npm install -g @mermaid-js/mermaid-cli",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        with self._render_lock:
            result = self._invoke_mmdc_svg(
                mermaid_text,
                theme=theme,
                background_color=background_color,
            )

        if result.success and result.svg_content:
            self._write_to_cache(cache_key, result.svg_content)

        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    def render_png(
        self,
        mermaid_text: str,
        *,
        theme: str = "neutral",
        background_color: Optional[str] = None,
    ) -> RenderResult:
        """Render Mermaid diagram to PNG."""
        t0 = time.perf_counter()

        cache_key = self._compute_cache_key(mermaid_text, theme=theme, background_color=background_color)
        cached_png = self._read_png_from_cache(cache_key)
        if cached_png:
            return RenderResult(
                success=True,
                png_bytes=cached_png,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        if not self.is_configured():
            return RenderResult(
                success=False,
                error_message="Mermaid CLI (mmdc) not found. Install with npm install -g @mermaid-js/mermaid-cli",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        with self._render_lock:
            result = self._invoke_mmdc(
                mermaid_text,
                theme=theme,
                background_color=background_color,
            )

        if result.success and result.png_bytes:
            self._write_png_to_cache(cache_key, result.png_bytes)

        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    def test_setup(self) -> RenderResult:
        """Run a tiny diagram render to validate configuration."""
        sample = "flowchart TD\n  A[Start] --> B[End]\n"
        return self.render_png(sample)

    def _invoke_mmdc(
        self,
        mermaid_text: str,
        *,
        theme: str = "neutral",
        background_color: Optional[str] = None,
    ) -> RenderResult:
        try:
            mmdc_cmd = str(self._mmdc_path) if self._mmdc_path else "mmdc"
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = Path(tmpdir) / "diagram.mmd"
                output_path = Path(tmpdir) / "diagram.png"
                config_path = Path(tmpdir) / "mermaid-config.json"
                puppeteer_config_path = Path(tmpdir) / "puppeteer-config.json"
                input_path.write_text(mermaid_text, encoding="utf-8")
                effective_theme = self._normalize_theme(theme, background_color)
                config_path.write_text(
                    json.dumps({"theme": effective_theme}, ensure_ascii=True),
                    encoding="utf-8",
                )
                self._write_puppeteer_config(puppeteer_config_path)

                cmd = [
                    mmdc_cmd,
                    "-i", str(input_path),
                    "-o", str(output_path),
                    "-t", effective_theme,
                    "-c", str(config_path),
                    "-b", background_color or "white",
                ]
                if puppeteer_config_path.exists():
                    cmd.extend(["-p", str(puppeteer_config_path)])

                if log_enabled("diagrams"):
                    print(f"[Mermaid] Command: {' '.join(cmd)}", file=__import__("sys").stdout, flush=True)

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                )

                stderr_text = result.stderr.decode("utf-8", errors="replace")
                if output_path.exists():
                    png_bytes = output_path.read_bytes()
                    if png_bytes:
                        return RenderResult(success=True, png_bytes=png_bytes)

                if result.returncode != 0:
                    return RenderResult(
                        success=False,
                        error_message=f"Mermaid render error (exit {result.returncode})",
                        stderr=stderr_text,
                    )

                return RenderResult(
                    success=False,
                    error_message="Invalid PNG output from Mermaid",
                    stderr=stderr_text,
                )
        except subprocess.TimeoutExpired:
            return RenderResult(
                success=False,
                error_message="Mermaid render timed out (>15s)",
            )
        except FileNotFoundError:
            return RenderResult(
                success=False,
                error_message="Mermaid CLI (mmdc) not found",
            )
        except Exception as exc:
            return RenderResult(
                success=False,
                error_message=f"Render error: {str(exc)}",
            )

    def _invoke_mmdc_svg(
        self,
        mermaid_text: str,
        *,
        theme: str = "neutral",
        background_color: Optional[str] = None,
    ) -> RenderResult:
        try:
            mmdc_cmd = str(self._mmdc_path) if self._mmdc_path else "mmdc"
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = Path(tmpdir) / "diagram.mmd"
                output_path = Path(tmpdir) / "diagram.svg"
                config_path = Path(tmpdir) / "mermaid-config.json"
                puppeteer_config_path = Path(tmpdir) / "puppeteer-config.json"
                input_path.write_text(mermaid_text, encoding="utf-8")
                effective_theme = self._normalize_theme(theme, background_color)
                config_path.write_text(
                    json.dumps({"theme": effective_theme}, ensure_ascii=True),
                    encoding="utf-8",
                )
                self._write_puppeteer_config(puppeteer_config_path)

                cmd = [
                    mmdc_cmd,
                    "-i", str(input_path),
                    "-o", str(output_path),
                    "-t", effective_theme,
                    "-c", str(config_path),
                    "-b", background_color or "white",
                ]
                if puppeteer_config_path.exists():
                    cmd.extend(["-p", str(puppeteer_config_path)])

                if log_enabled("diagrams"):
                    print(f"[Mermaid] Command: {' '.join(cmd)}", file=__import__("sys").stdout, flush=True)

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                )

                stderr_text = result.stderr.decode("utf-8", errors="replace")
                if output_path.exists():
                    svg_content = output_path.read_text(encoding="utf-8", errors="replace")
                    if "<svg" in svg_content:
                        return RenderResult(success=True, svg_content=svg_content)

                if result.returncode != 0:
                    return RenderResult(
                        success=False,
                        error_message=f"Mermaid render error (exit {result.returncode})",
                        stderr=stderr_text,
                    )

                return RenderResult(
                    success=False,
                    error_message="Invalid SVG output from Mermaid",
                    stderr=stderr_text,
                )
        except subprocess.TimeoutExpired:
            return RenderResult(
                success=False,
                error_message="Mermaid render timed out (>15s)",
            )
        except FileNotFoundError:
            return RenderResult(
                success=False,
                error_message="Mermaid CLI (mmdc) not found",
            )
        except Exception as exc:
            return RenderResult(
                success=False,
                error_message=f"Render error: {str(exc)}",
            )

    def _normalize_theme(self, theme: str, background_color: Optional[str]) -> str:
        value = (theme or "neutral").strip().lower()
        if value == "base":
            if background_color and self._is_dark_color(background_color):
                return "dark"
            return "default"
        if value in {"default", "forest", "dark", "neutral"}:
            return value
        return "neutral"

    def _compute_cache_key(
        self,
        mermaid_text: str,
        *,
        theme: str = "neutral",
        background_color: Optional[str] = None,
    ) -> str:
        combined = f"{mermaid_text}|{self._mmdc_path}|{theme}|{background_color or ''}"
        return hashlib.sha256(combined.encode()).hexdigest()

    @staticmethod
    def _is_dark_color(value: str) -> bool:
        text = (value or "").strip()
        if not text.startswith("#"):
            return False
        hex_value = text[1:]
        if len(hex_value) == 3:
            hex_value = "".join(ch * 2 for ch in hex_value)
        if len(hex_value) != 6:
            return False
        try:
            r = int(hex_value[0:2], 16)
            g = int(hex_value[2:4], 16)
            b = int(hex_value[4:6], 16)
        except ValueError:
            return False
        luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
        return luminance < 140

    @staticmethod
    def _write_puppeteer_config(path: Path) -> None:
        if os.name != "posix":
            return
        payload = {
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        }
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    def _read_from_cache(self, cache_key: str) -> Optional[str]:
        cache_file = self.cache_dir / f"{cache_key}.svg"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to read cache %s: %s", cache_key, exc)
        return None

    def _write_to_cache(self, cache_key: str, svg_content: str) -> None:
        cache_file = self.cache_dir / f"{cache_key}.svg"
        try:
            cache_file.write_text(svg_content, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write cache %s: %s", cache_key, exc)

    def _read_png_from_cache(self, cache_key: str) -> Optional[bytes]:
        cache_file = self.cache_dir / f"{cache_key}.png"
        if cache_file.exists():
            try:
                return cache_file.read_bytes()
            except Exception as exc:
                logger.warning("Failed to read PNG cache %s: %s", cache_key, exc)
        return None

    def _write_png_to_cache(self, cache_key: str, png_bytes: bytes) -> None:
        cache_file = self.cache_dir / f"{cache_key}.png"
        try:
            cache_file.write_bytes(png_bytes)
        except Exception as exc:
            logger.warning("Failed to write PNG cache %s: %s", cache_key, exc)
