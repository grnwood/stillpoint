# packaging/sp-macos.spec
import os
from PyInstaller.utils.hooks import collect_submodules

def _find_root():
    cand = os.getcwd()
    for _ in range(6):
        probe = os.path.join(cand, 'sp', 'app', 'main.py')
        if os.path.exists(probe):
            return cand
        cand = os.path.dirname(cand)
    return os.getcwd()

ROOT = _find_root()
MAIN = os.path.join(ROOT, 'sp', 'app', 'main.py')
QUICKCAPTURE = os.path.join(ROOT, 'sp', 'app', 'quickcapture.py')

STILLPOINT_VERSION = os.getenv('STILLPOINT_VERSION', '0.99')

hidden = (
    collect_submodules('sp')
    + collect_submodules('sp.app')
    + collect_submodules('sp.app.ui')
    + [
        # Explicit UI modules (keeps PyInstaller honest if dynamic imports exist)
        'sp.app.ui.main_window',
        'sp.app.ui.markdown_editor',
        'sp.app.ui.page_editor_window',
        'sp.app.ui.plantuml_editor_window',
        'sp.app.ui.preferences_dialog',
        'sp.app.ui.ai_chat_panel',
        'sp.app.ui.calendar_panel',
        'sp.app.ui.task_panel',
        'sp.app.ui.toc_widget',
        'sp.app.quickcapture',

        # Web/server stack (if used)
        'fastapi',
        'httpx',
        'pydantic',
        'uvicorn',
        'jinja2',
        'anyio',
        'starlette',

        # RAG / ML / tokenization bits (only if you truly ship these on mac)
        'chromadb.api.rust',
    ]
    + collect_submodules('chromadb')
    + collect_submodules('onnxruntime')
    + collect_submodules('tokenizers')
    + collect_submodules('docx')
)

_datas = [
    (os.path.join(ROOT, 'sp', 'templates'), 'sp/templates'),
    (os.path.join(ROOT, 'sp', 'server', 'templates'), 'sp/server/templates'),
    (os.path.join(ROOT, 'sp', 'app', 'puml_shortcuts.json'), 'sp/app'),
    (os.path.join(ROOT, 'sp', 'app', 'calendar-day-insight-prompt.txt'), 'sp/app'),
    (os.path.join(ROOT, 'sp', 'help-vault'), 'sp/help-vault'),
    (os.path.join(ROOT, 'LICENSE'), '.'),
    (os.path.join(ROOT, 'NOTICE'), '.'),
]

for subdir in ['assets', 'slipstream', 'rag', 'ai']:
    path = os.path.join(ROOT, 'sp', subdir)
    if os.path.exists(path):
        _datas.append((path, f'sp/{subdir}'))

datas = _datas

block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, BUNDLE

a = Analysis(
    [MAIN, QUICKCAPTURE],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[os.path.join(ROOT, "packaging", "pyi_runtime_hook.py")],
    excludes=['tkinter', 'pytest', 'tests', 'unittest'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_assets_dir = os.path.join(ROOT, 'sp', 'assets')
_icon_icns = os.path.join(_assets_dir, 'StillPoint.icns')

exe = EXE(
    pyz,
    [s for s in a.scripts if os.path.basename(s[0]).startswith('main')],
    [],
    exclude_binaries=True,
    name='StillPoint',
    debug=False,
    bootloader_ignore_signals=False,

    # Start safe on macOS; you can enable strip later
    strip=False,
    upx=False,

    console=False,
    icon=_icon_icns if os.path.exists(_icon_icns) else None,
)

quickcapture_exe = EXE(
    pyz,
    [s for s in a.scripts if os.path.basename(s[0]).startswith('quickcapture')],
    [],
    exclude_binaries=True,
    name='quickcapture',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=_icon_icns if os.path.exists(_icon_icns) else None,
)

app = BUNDLE(
    exe,
    quickcapture_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='StillPoint.app',
    icon=_icon_icns if os.path.exists(_icon_icns) else None,
    bundle_identifier='app.stillpoint.desktop',
    info_plist={
        'CFBundleName': 'StillPoint',
        'CFBundleDisplayName': 'StillPoint',
        'CFBundleVersion': STILLPOINT_VERSION,
        'CFBundleShortVersionString': STILLPOINT_VERSION,
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
        'LSMinimumSystemVersion': '10.13.0',
    },
)
