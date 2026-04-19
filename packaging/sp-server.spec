# PyInstaller spec file for StillPoint headless server
# Usage:
#   pyinstaller -y packaging/sp-server.spec

import os
import shutil
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT


def _find_root():
    """Resolve project root regardless of where the spec file is invoked."""
    cand = os.getcwd()
    for _ in range(4):
        probe = os.path.join(cand, "sp", "server", "api.py")
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    return os.getcwd()


ROOT = _find_root()
MAIN = os.path.join(ROOT, "sp", "server", "api.py")

# Server-only import graph:
# - sp.server.* (API, adapters, file ops, auth, state)
# - sp.rag.* (vector retrieval/chroma)
# - sp.app.config + sp.app.indexer + sp.app.ui.path_utils (non-GUI utilities used by API)
hidden = (
    collect_submodules("sp.server")
    + collect_submodules("sp.server.adapters")
    + collect_submodules("sp.rag")
    + collect_submodules("chromadb")
    + collect_submodules("onnxruntime")
    + collect_submodules("tokenizers")
    + [
        "sp.server.api",
        "sp.app.config",
        "sp.app.indexer",
        "sp.app.ui.path_utils",
        "sp.logging_flags",
        "fastapi",
        "httpx",
        "pydantic",
        "uvicorn",
        "jinja2",
        "anyio",
        "starlette",
        "argon2",
        "jose",
        "markdown",
        "multipart",
        "chromadb.api.rust",
    ]
)

# Runtime data needed by headless API server
_datas = [
    (os.path.join(ROOT, "sp", "server", "templates"), "sp/server/templates"),
    (os.path.join(ROOT, "packaging", "server", "run-server.sh"), "."),
    (os.path.join(ROOT, "packaging", "server", "_launch.sh"), "."),
    (os.path.join(ROOT, "packaging", "server", "stillpoint-server.service"), "."),
    (os.path.join(ROOT, "packaging", "server", "run-homebase-gc.sh"), "."),
    (os.path.join(ROOT, "packaging", "server", "homebase-gc.service"), "."),
    (os.path.join(ROOT, "packaging", "server", "homebase-gc.timer"), "."),
    (os.path.join(ROOT, ".env.example"), "."),
    (os.path.join(ROOT, "LICENSE"), "."),
    (os.path.join(ROOT, "NOTICE"), "."),
]

datas = _datas
block_cipher = None

a = Analysis(
    [MAIN],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[os.path.join(ROOT, "packaging", "pyi_runtime_hook.py")],
    excludes=["PySide6", "tkinter", "pytest", "tests", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="stillpoint-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
    icon=None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="stillpoint-server",
)

# Move convenience files to dist root (PyInstaller puts datas under _internal).
dist_root = os.path.join("dist", "stillpoint-server")
internal_dir = os.path.join(dist_root, "_internal")
for filename in (
    "run-server.sh",
    "_launch.sh",
    "stillpoint-server.service",
    "run-homebase-gc.sh",
    "homebase-gc.service",
    "homebase-gc.timer",
    ".env.example",
    "stillpoint-server.sh",
    "LICENSE",
    "NOTICE",
):
    src = os.path.join(internal_dir, filename)
    dst = os.path.join(dist_root, filename)
    if os.path.exists(src):
        shutil.copy2(src, dst)
