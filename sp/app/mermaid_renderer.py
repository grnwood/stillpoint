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
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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

    _DEFAULT_FONT_STACK = "Arial,Helvetica,'DejaVu Sans','Noto Sans',sans-serif"
    _LINUX_FONT_FAMILY_REPLACEMENT = "'DejaVu Sans','Noto Sans',Arial,Helvetica,sans-serif"
    _RENDER_PIPELINE_VERSION = "3"
    _QTSVG_TEXT_OVERRIDE = (
        "<style id=\"stillpoint-qtsvg-fixes\">"
        "#my-svg text,#my-svg tspan,text,tspan{"
        "fill:#24292F !important;"
        "stroke:none !important;"
        "font-family:'DejaVu Sans','Noto Sans',Arial,Helvetica,sans-serif !important;"
        "}"
        "</style>"
    )

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
                prepared_text = self._prepare_mermaid_text(mermaid_text)
                input_path.write_text(prepared_text, encoding="utf-8")
                effective_theme = self._normalize_theme(theme, background_color)
                config_payload = {
                    "theme": effective_theme,
                    "themeVariables": {
                        "fontFamily": self._DEFAULT_FONT_STACK,
                    },
                    "flowchart": {
                        # Avoid foreignObject-based labels that QtSvg may not render.
                        "htmlLabels": False,
                    },
                }
                config_path.write_text(
                    json.dumps(config_payload, ensure_ascii=True),
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
                prepared_text = self._prepare_mermaid_text(mermaid_text)
                input_path.write_text(prepared_text, encoding="utf-8")
                effective_theme = self._normalize_theme(theme, background_color)
                config_payload = {
                    "theme": effective_theme,
                    "themeVariables": {
                        "fontFamily": self._DEFAULT_FONT_STACK,
                    },
                    "flowchart": {
                        # Avoid foreignObject-based labels that QtSvg may not render.
                        "htmlLabels": False,
                    },
                }
                config_path.write_text(
                    json.dumps(config_payload, ensure_ascii=True),
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

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                )

                stderr_text = result.stderr.decode("utf-8", errors="replace")
                if output_path.exists():
                    svg_content = output_path.read_text(encoding="utf-8", errors="replace")
                    svg_content = self._normalize_svg_for_qtsvg(svg_content)
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
        combined = (
            f"{self._RENDER_PIPELINE_VERSION}|{mermaid_text}|{self._mmdc_path}|"
            f"{theme}|{background_color or ''}"
        )
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

    def _prepare_mermaid_text(self, mermaid_text: str) -> str:
        """Normalize Mermaid source to improve Linux font fallback in headless Chromium."""
        if not os.name == "posix":
            return mermaid_text
        if not mermaid_text:
            return mermaid_text
        return mermaid_text.replace(
            "ui-sans-serif,system-ui",
            self._LINUX_FONT_FAMILY_REPLACEMENT,
        )

    def _normalize_svg_for_qtsvg(self, svg_content: str) -> str:
        """Normalize Mermaid SVG for QtSvg compatibility in fallback preview mode."""
        if not svg_content:
            return svg_content

        normalized = self._inline_class_styles_for_qtsvg(svg_content)

        # QtSvg can render black placeholder boxes for foreignObject-heavy labels.
        normalized = re.sub(
            r"<foreignObject\\b[\\s\\S]*?</foreignObject>",
            "",
            normalized,
            flags=re.IGNORECASE,
        )

        # Strip CSS features that QtSvg may not parse reliably.
        normalized = normalized.replace("position:absolute;", "")
        normalized = re.sub(r"box-shadow:[^;]+;", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"filter:drop-shadow\\([^)]*\\);", "", normalized, flags=re.IGNORECASE)

        # Enforce a known-good text style for labels.
        if "stillpoint-qtsvg-fixes" not in normalized:
            if "</svg>" in normalized:
                normalized = normalized.replace("</svg>", f"{self._QTSVG_TEXT_OVERRIDE}</svg>", 1)
            else:
                normalized = normalized + self._QTSVG_TEXT_OVERRIDE

        return normalized

    def _inline_class_styles_for_qtsvg(self, svg_content: str) -> str:
        """Inline selected Mermaid CSS rules so QtSvg applies diagram styling reliably."""
        try:
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
            root = ET.fromstring(svg_content)
        except Exception:
            return svg_content

        style_blocks: list[str] = []
        for elem in list(root.iter()):
            if self._local_name(elem.tag) == "style" and elem.text:
                style_blocks.append(elem.text)

        if not style_blocks:
            return svg_content

        rules = self._parse_css_rules("\n".join(style_blocks))
        if not rules:
            return svg_content

        for elem in root.iter():
            for rule in rules:
                if rule["child_tag"] is None and self._matches_rule(elem, rule):
                    self._apply_decl_map(elem, rule["decl"])

        for parent in root.iter():
            for rule in rules:
                child_tag = rule["child_tag"]
                if child_tag is None:
                    continue
                if not self._matches_rule(parent, rule):
                    continue
                for child in list(parent):
                    if self._local_name(child.tag) == child_tag:
                        self._apply_decl_map(child, rule["decl"])

        try:
            return ET.tostring(root, encoding="unicode")
        except Exception:
            return svg_content

    @staticmethod
    def _local_name(tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def _parse_css_rules(self, css_text: str) -> list[dict[str, object]]:
        allowed = {
            "fill",
            "stroke",
            "stroke-width",
            "stroke-dasharray",
            "font-size",
            "font-weight",
            "font-family",
            "text-anchor",
        }
        rules: list[dict[str, object]] = []
        for match in re.finditer(r"([^{}]+)\{([^{}]+)\}", css_text):
            raw_selector = match.group(1).strip()
            if raw_selector.startswith("@"):
                continue
            raw_decl = match.group(2)
            decl_map: dict[str, str] = {}
            for item in raw_decl.split(";"):
                if ":" not in item:
                    continue
                prop, value = item.split(":", 1)
                prop = prop.strip().lower()
                if prop not in allowed:
                    continue
                clean_value = value.replace("!important", "").strip()
                if clean_value:
                    decl_map[prop] = clean_value
            if not decl_map:
                continue
            for selector in [s.strip() for s in raw_selector.split(",") if s.strip()]:
                parsed = self._parse_selector(selector)
                if parsed is None:
                    continue
                parsed["decl"] = decl_map
                rules.append(parsed)
        return rules

    @staticmethod
    def _parse_selector(selector: str) -> Optional[dict[str, object]]:
        s = selector.replace("#my-svg", "").strip()
        if not s or "(" in s:
            return None
        s = s.split(":", 1)[0].strip()

        child_tag: Optional[str] = None
        if ">" in s:
            left, right = s.split(">", 1)
            child_tag = right.strip().split(".", 1)[0].strip()
            s = left.strip()

        parent_tag: Optional[str] = None
        parent_class: Optional[str] = None
        if s.startswith("."):
            parent_class = s[1:].split(".", 1)[0].strip()
        elif "." in s:
            parent_tag, klass = s.split(".", 1)
            parent_tag = parent_tag.strip() or None
            parent_class = klass.split(".", 1)[0].strip() or None
        else:
            parent_tag = s.strip() or None

        if not parent_tag and not parent_class:
            return None

        return {
            "parent_tag": parent_tag,
            "parent_class": parent_class,
            "child_tag": child_tag,
        }

    def _matches_rule(self, elem: ET.Element, rule: dict[str, object]) -> bool:
        parent_tag = rule.get("parent_tag")
        parent_class = rule.get("parent_class")
        if parent_tag and self._local_name(elem.tag) != parent_tag:
            return False
        if parent_class:
            classes = set((elem.attrib.get("class") or "").split())
            if parent_class not in classes:
                return False
        return True

    @staticmethod
    def _apply_decl_map(elem: ET.Element, decl_map: dict[str, str]) -> None:
        style_map: dict[str, str] = {}
        style_attr = elem.attrib.get("style")
        if style_attr:
            for item in style_attr.split(";"):
                if ":" in item:
                    k, v = item.split(":", 1)
                    style_map[k.strip().lower()] = v.strip()
        for prop, value in decl_map.items():
            style_map[prop] = value
            elem.set(prop, value)
        if style_map:
            elem.set("style", "; ".join(f"{k}: {v}" for k, v in style_map.items()))

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
