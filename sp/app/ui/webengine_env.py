"""Qt WebEngine startup environment helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def configure_linux_webengine_env(*, disable_env_var: str | None = None) -> None:
    """Apply conservative Chromium flags for Linux Qt WebEngine stability."""
    if not sys.platform.startswith("linux"):
        return
    if disable_env_var and env_truthy(disable_env_var):
        return

    profile = os.getenv("SP_WEBENGINE_PROFILE", "safe").strip().lower() or "safe"
    if profile in {"off", "disabled", "none"}:
        return

    if env_truthy("SP_WEBENGINE_FORCE_XCB") or profile == "xcb":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    if profile in {"safe", "software", "xcb", "single-process", "swiftshader"}:
        os.environ.setdefault("QT_OPENGL", "software")

    fontconfig = Path("/etc/fonts/fonts.conf")
    if fontconfig.exists():
        os.environ.setdefault("FONTCONFIG_FILE", str(fontconfig))

    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    required_flags = [
        "--no-sandbox",
        "--no-zygote",
        "--disable-dev-shm-usage",
        "--disable-vulkan",
        "--disable-accelerated-2d-canvas",
        "--disable-accelerated-video-decode",
        "--disable-features=VizDisplayCompositor",
    ]
    if profile == "swiftshader":
        required_flags.extend(
            [
                "--use-gl=swiftshader",
                "--ignore-gpu-blocklist",
            ]
        )
    else:
        required_flags.extend(
            [
                "--disable-gpu",
                "--disable-gpu-compositing",
            ]
        )
    if profile == "single-process":
        required_flags.append("--single-process")
    extra = (os.getenv("SP_WEBENGINE_EXTRA_FLAGS") or "").strip()
    if extra:
        required_flags.extend(flag for flag in extra.split() if flag)
    existing = (os.getenv("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    for flag in required_flags:
        if flag not in existing:
            existing = f"{existing} {flag}".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = existing
