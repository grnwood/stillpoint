# PyInstaller spec file for StillPoint Quick Capture (lite, onedir)
# Usage: pyinstaller -y packaging/stillpoint-capture.spec

import os
from PyInstaller.utils.hooks import collect_submodules


def _find_root():
    cand = os.getcwd()
    for _ in range(4):
        probe = os.path.join(cand, "sp", "app", "quickcapture_lite.py")
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    return os.getcwd()


ROOT = _find_root()
MAIN = os.path.join(ROOT, "sp", "app", "quickcapture_lite.py")

hidden = [
    "sp.app.quickcapture_lite",
    "sp.app.ui.quick_capture_overlay",
    "sp.server.adapters",
    "sp.server.adapters.files",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

datas = [
    (os.path.join(ROOT, "sp", "assets"), "sp/assets"),
]

block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

a = Analysis(
    [MAIN],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "pytest",
        "tests",
        "unittest",
        "chromadb",
        "onnxruntime",
        "tokenizers",
        "docx",
        "sp.ai",
        "sp.rag",
        "sp.web",
        "httpx",
        "sp.app.quickcapture",
        "sp.app.ui.mermaid_editor_window",
        "sp.app.ui.plantuml_editor_window",
        "sp.app.ui.ai_chat_panel",
        "sp.app.ui.task_panel",
        "sp.app.ui.calendar_panel",
        "sp.app.ui.preferences_dialog",
        "sp.app.ui.main_window",
        "sp.app.ui.markdown_editor",
        "sp.app.ui.page_editor_window",
    ],
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
    name="stillpoint-capture",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="stillpoint-capture",
)
