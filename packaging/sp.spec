# PyInstaller spec file for StillPoint
# Usage:
#   pyinstaller -y packaging/sp.spec
#   (Set STILLPOINT_VERSION env var for version stamping if desired.)

import os
from PyInstaller.utils.hooks import collect_submodules

# Resolve project root regardless of where the spec file lives

def _find_root():
    cand = os.getcwd()
    for _ in range(4):
        probe = os.path.join(cand, 'sp', 'app', 'main.py')
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    # Fallback to current working dir
    return os.getcwd()

ROOT = _find_root()

# Entry script (absolute)
MAIN = os.path.join(ROOT, 'sp', 'app', 'main.py')

# Hidden imports sometimes needed for PySide6 / FastAPI
hidden = (
    collect_submodules('PySide6')
    + [
        'fastapi',
        'httpx',
        'pydantic',
        'uvicorn',
        'jinja2',
        'anyio',
        'starlette',
        'chromadb.api.rust'
    ]
    + collect_submodules('chromadb')
    + collect_submodules('onnxruntime')
    + collect_submodules('tokenizers')
    + collect_submodules('docx')
)

STILLPOINT_VERSION = os.getenv('STILLPOINT_VERSION','0.99')

# Data files: templates + bundled assets
_datas = [
    (os.path.join(ROOT, 'sp', 'templates'), 'sp/templates'),
    (os.path.join(ROOT, 'sp', 'server', 'templates'), 'sp/server/templates'),
    (os.path.join(ROOT, 'sp', 'app', 'puml_shortcuts.json'), 'sp/app'),
    (os.path.join(ROOT, 'sp', 'app', 'calendar-day-insight-prompt.txt'), 'sp/app'),
    (os.path.join(ROOT, 'sp', 'help-vault'), 'sp/help-vault'),
    (os.path.join(ROOT, 'LICENSE'), '.'),
    (os.path.join(ROOT, 'NOTICE'), '.'),
]

# Add optional subdirectories if they exist
for subdir in ['assets', 'slipstream', 'rag', 'ai']:
    path = os.path.join(ROOT, 'sp', subdir)
    if os.path.exists(path):
        _datas.append((path, f'sp/{subdir}'))

_assets_dir = os.path.join(ROOT, 'sp', 'assets')

datas = _datas

block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

a = Analysis(
    [MAIN],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[os.path.join(ROOT, "packaging", "pyi_runtime_hook.py")],
    excludes=['tkinter','pytest','tests','unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Onedir bundle: exe + COLLECT for folder distribution (faster startup)
_icon_ico = os.path.join(_assets_dir, 'icon.ico')
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StillPoint',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_icon_ico if os.path.exists(_icon_ico) else None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='StillPoint'
)
