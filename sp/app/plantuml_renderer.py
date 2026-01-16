"""PlantUML rendering and cache management module.

Handles:
- JAR and Java discovery
- SVG rendering via plantuml.jar
- SVG caching with content hashing
- Error handling and reporting
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PLANTUML_ALIASES = {"puml", "uml", "plantuml"}

# Common installation paths for plantuml.jar
COMMON_JAR_PATHS = [
    Path.home() / ".plantuml" / "plantuml.jar",
    Path.home() / "plantuml.jar",
    Path("/usr/share/plantuml/plantuml.jar"),
    Path("/opt/plantuml/plantuml.jar"),
    Path("/usr/local/opt/plantuml/plantuml.jar"),  # macOS Homebrew
    Path("C:\\Program Files\\PlantUML\\plantuml.jar"),  # Windows
    Path("C:\\Users") / os.environ.get("USERNAME", "") / "plantuml.jar",  # Windows user home
]


@dataclass
class RenderResult:
    """Result of a PlantUML render attempt."""
    success: bool
    svg_content: Optional[str] = None
    error_message: Optional[str] = None
    stderr: Optional[str] = None
    duration_ms: float = 0.0


class PlantUMLRenderer:
    """Manages PlantUML rendering with caching and async support."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize the renderer.

        Args:
            cache_dir: Directory for caching rendered SVGs. If None, uses system temp.
        """
        self.cache_dir = cache_dir or (Path.home() / ".stillpoint_cache" / "plantuml")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._jar_path: Optional[Path] = None
        self._java_path: Optional[Path] = None
        self._java_available: bool = False
        self._render_lock = threading.Lock()
        self._config_initialized = False

    def initialize_from_config(self) -> None:
        """Load configured paths from settings if available."""
        if self._config_initialized:
            return

        try:
            from sp.app import config

            # Load configured paths
            jar_path_str = config.load_plantuml_jar_path()
            if jar_path_str:
                self.set_jar_path(jar_path_str)

            java_path_str = config.load_plantuml_java_path()
            if java_path_str:
                self.set_java_path(java_path_str)
        except ImportError:
            pass

        self._config_initialized = True

    def discover_java(self) -> bool:
        """Detect if 'java' is available in PATH."""
        java_path = shutil.which("java")
        if java_path:
            self._java_path = Path(java_path)
            self._java_available = True
            return True
        self._java_available = False
        return False

    def discover_jar(self) -> Optional[Path]:
        """Attempt to locate plantuml executable or jar in common locations."""
        # Check if plantuml is on PATH (could be wrapper executable or jar)
        jar_in_path = shutil.which("plantuml")
        if jar_in_path:
            jar_path = Path(jar_in_path)
            if jar_path.exists():
                self._jar_path = jar_path
                return jar_path

        # Check common locations for a jar
        for candidate in COMMON_JAR_PATHS:
            if candidate.exists() and candidate.is_file():
                self._jar_path = candidate
                return candidate

        return None

    def set_jar_path(self, jar_path: str) -> bool:
        """Explicitly set the JAR path."""
        path = Path(jar_path)
        if path.exists() and path.is_file():
            self._jar_path = path
            return True
        return False

    def set_java_path(self, java_path: str) -> bool:
        """Explicitly set the Java executable path."""
        path = Path(java_path)
        if path.exists() and path.is_file():
            self._java_path = path
            self._java_available = True
            return True
        return False

    def get_jar_path(self) -> Optional[Path]:
        """Get the currently configured JAR path."""
        return self._jar_path

    def get_java_path(self) -> Optional[Path]:
        """Get the currently configured Java path."""
        return self._java_path

    def is_configured(self) -> bool:
        """Check if PlantUML is properly configured."""
        self.initialize_from_config()

        if not self._java_available:
            self.discover_java()
        if self._jar_path is None:
            self.discover_jar()
        return self._java_available and self._jar_path is not None

    def render_svg(self, puml_text: str) -> RenderResult:
        """Render PlantUML diagram to SVG.

        Args:
            puml_text: PlantUML source code

        Returns:
            RenderResult with SVG content or error details
        """
        import time
        t0 = time.perf_counter()

        self.initialize_from_config()

        # Check cache first
        cache_key = self._compute_cache_key(puml_text)
        cached_svg = self._read_from_cache(cache_key)
        if cached_svg:
            return RenderResult(
                success=True,
                svg_content=cached_svg,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        # Verify configuration
        if not self._java_available:
            self.discover_java()
        if not self._java_available:
            return RenderResult(
                success=False,
                error_message="Java not found. Install Java or set JAVA_HOME.",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        if self._jar_path is None:
            self.discover_jar()
        if self._jar_path is None:
            return RenderResult(
                success=False,
                error_message="PlantUML JAR not found. Set plantuml.jar_path in settings.",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        # Render using subprocess
        with self._render_lock:
            result = self._invoke_plantuml(puml_text)

        if result.success and result.svg_content:
            # Cache the result
            self._write_to_cache(cache_key, result.svg_content)

        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    def test_setup(self) -> RenderResult:
        """Run a tiny diagram render to validate configuration."""
        sample = """@startuml
Alice -> Bob: test
@enduml"""
        return self.render_svg(sample)

    def _invoke_plantuml(self, puml_text: str) -> RenderResult:
        """Invoke plantuml via java -jar or direct executable."""
        try:
            jar_cmd = str(self._jar_path)
            is_executable = jar_cmd and not jar_cmd.lower().endswith(".jar")
            if is_executable:
                # plantuml wrapper script/binary already knows how to call Java
                cmd = [jar_cmd, "-tsvg", "-pipe"]
            else:
                java_cmd = str(self._java_path) if self._java_path else "java"
                cmd = [java_cmd, "-jar", jar_cmd, "-tsvg", "-pipe"]

            # Debug output: show command
            if os.getenv("ZIMX_DEBUG_PLANTUML", "0") not in ("0", "false", "False", ""):
                print(f"[PlantUML] Command: {' '.join(cmd)}", file=sys.stdout, flush=True)
                print(f"[PlantUML] Input length: {len(puml_text)} bytes", file=sys.stdout, flush=True)

            result = subprocess.run(
                cmd,
                input=puml_text.encode("utf-8"),
                capture_output=True,
                timeout=10,
            )

            # Debug output: show result
            if os.getenv("ZIMX_DEBUG_PLANTUML", "0") not in ("0", "false", "False", ""):
                print(f"[PlantUML] Return code: {result.returncode}", file=sys.stdout, flush=True)
                print(f"[PlantUML] Stdout length: {len(result.stdout)} bytes", file=sys.stdout, flush=True)
                print(f"[PlantUML] Stderr length: {len(result.stderr)} bytes", file=sys.stdout, flush=True)

            # Try to extract SVG regardless of exit code (PlantUML sometimes returns non-zero with valid SVG)
            svg_content = result.stdout.decode("utf-8", errors="replace")
            stderr_text = result.stderr.decode("utf-8", errors="replace")
            
            # Check if we got valid SVG
            if '<svg' in svg_content:
                if os.getenv("ZIMX_DEBUG_PLANTUML", "0") not in ("0", "false", "False", ""):
                    print(f"[PlantUML] ✓ Render successful (SVG produced)", file=sys.stdout, flush=True)
                    if result.returncode != 0 and stderr_text:
                        print(f"[PlantUML] Warning - non-zero exit but SVG produced:\n{stderr_text}", file=sys.stdout, flush=True)
                return RenderResult(success=True, svg_content=svg_content)
            
            # No valid SVG - report error
            if result.returncode != 0:
                if os.getenv("ZIMX_DEBUG_PLANTUML", "0") not in ("0", "false", "False", ""):
                    print(f"[PlantUML] Stderr output:\n{stderr_text}", file=sys.stdout, flush=True)
                return RenderResult(
                    success=False,
                    error_message=f"PlantUML render error (exit {result.returncode})",
                    stderr=stderr_text,
                )
            
            # Zero exit code but no SVG - also an error
            if os.getenv("ZIMX_DEBUG_PLANTUML", "0") not in ("0", "false", "False", ""):
                print(f"[PlantUML] Invalid SVG - stderr:\n{stderr_text}", file=sys.stdout, flush=True)
            return RenderResult(
                success=False,
                error_message="Invalid SVG output from PlantUML",
                stderr=stderr_text,
            )

        except subprocess.TimeoutExpired:
            return RenderResult(
                success=False,
                error_message="PlantUML render timed out (>10s)",
            )
        except FileNotFoundError:
            return RenderResult(
                success=False,
                error_message="Java executable not found",
            )
        except Exception as e:
            return RenderResult(
                success=False,
                error_message=f"Render error: {str(e)}",
            )

    def _compute_cache_key(self, puml_text: str) -> str:
        """Compute cache key from content and configuration."""
        combined = f"{puml_text}|{self._jar_path}|{self._java_path}"
        return hashlib.sha256(combined.encode()).hexdigest()

    def _read_from_cache(self, cache_key: str) -> Optional[str]:
        """Read SVG from cache."""
        cache_file = self.cache_dir / f"{cache_key}.svg"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read cache {cache_key}: {e}")
        return None

    def _write_to_cache(self, cache_key: str, svg_content: str) -> None:
        """Write SVG to cache."""
        cache_file = self.cache_dir / f"{cache_key}.svg"
        try:
            cache_file.write_text(svg_content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write cache {cache_key}: {e}")

    def clear_cache(self) -> None:
        """Clear all cached SVG files."""
        try:
            for svg_file in self.cache_dir.glob("*.svg"):
                svg_file.unlink()
            logger.info("Cleared PlantUML cache")
        except Exception as e:
            logger.warning(f"Failed to clear cache: {e}")


def extract_plantuml_blocks(markdown_text: str) -> list[tuple[int, int, str]]:
    """Extract PlantUML code blocks from markdown.

    Returns:
        List of (start_line, end_line, content) tuples (1-indexed lines)
    """
    blocks = []
    lines = markdown_text.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]
        # Check for fenced code block start
        if line.strip().startswith("```"):
            fence_info = line.strip()[3:].strip()
            lang = fence_info.split()[0].lower() if fence_info else ""

            # Check if it's a PlantUML block
            if lang in PLANTUML_ALIASES:
                start_line = i + 1  # 1-indexed
                i += 1
                code_lines = []

                # Collect code until closing fence
                while i < len(lines):
                    if lines[i].strip().startswith("```"):
                        end_line = i  # 1-indexed (points to closing fence)
                        content = "\n".join(code_lines)
                        blocks.append((start_line, end_line, content))
                        break
                    code_lines.append(lines[i])
                    i += 1

        i += 1

    return blocks
