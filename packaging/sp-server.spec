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
HOMEBASE_SEED_TOOL = os.path.join(ROOT, "tools", "homebase-seed-vault.py")
HOMEBASE_CREATE_AND_SEED_TOOL = os.path.join(ROOT, "tools", "homebase-create-and-seed-vault.py")

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
    + collect_submodules("argon2")
    + [
        "sp.server.api",
        "sp.app.config",
        "sp.app.indexer",
        "sp.app.ui.ai_api",
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
        "_argon2_cffi_bindings",
        "tools.homebase_seed_lib",
        "jose",
        "markdown",
        "multipart",
        "chromadb.api.rust",
    ]
)

# Runtime data needed by headless API server
_datas = [
    (os.path.join(ROOT, "sp", "server", "templates"), "sp/server/templates"),
    (os.path.join(ROOT, "sp", "server", "static"), "sp/server/static"),
    (os.path.join(ROOT, "sp", "assets"), "sp/assets"),
    (os.path.join(ROOT, "sp", "app", "excal-prompt.txt"), "sp/app"),
    (os.path.join(ROOT, "sp", "app", "excal-deconstruct.txt"), "sp/app"),
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

homebase_tool_hidden = (
    collect_submodules("argon2")
    + [
        "argon2",
        "_argon2_cffi_bindings",
        "tools.homebase_seed_lib",
        "sp.sync.crypto",
        "sp.sync.local_fs",
    ]
)

seed_a = Analysis(
    [HOMEBASE_SEED_TOOL],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=homebase_tool_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "tkinter", "pytest", "tests", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

seed_pyz = PYZ(seed_a.pure, seed_a.zipped_data, cipher=block_cipher)

seed_exe = EXE(
    seed_pyz,
    seed_a.scripts,
    seed_a.binaries,
    seed_a.zipfiles,
    seed_a.datas,
    [],
    name="homebase-seed-vault",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
    icon=None,
    version=None,
)

create_seed_a = Analysis(
    [HOMEBASE_CREATE_AND_SEED_TOOL],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=homebase_tool_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "tkinter", "pytest", "tests", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

create_seed_pyz = PYZ(create_seed_a.pure, create_seed_a.zipped_data, cipher=block_cipher)

create_seed_exe = EXE(
    create_seed_pyz,
    create_seed_a.scripts,
    create_seed_a.binaries,
    create_seed_a.zipfiles,
    create_seed_a.datas,
    [],
    name="homebase-create-and-seed-vault",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=True,
    icon=None,
    version=None,
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

tools_root = os.path.join(dist_root, "tools")
os.makedirs(tools_root, exist_ok=True)
for filename in (
    "homebase-seed-vault",
    "homebase-create-and-seed-vault",
):
    src = os.path.join("dist", filename)
    dst = os.path.join(tools_root, filename)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        try:
            os.chmod(dst, 0o755)
        except OSError:
            pass
