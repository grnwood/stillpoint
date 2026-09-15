API] 200 /files/attach
[MainWindow] Opening Mermaid editor for: /Users/jogreenw/Vaults/WorkNotes2026/Journal/2026/09/11/anotherone.mmd
[Mermaid] Loaded shortcuts from /Users/jogreenw/code/stillpoint/sp/app/mmd_shortcuts.json (28 items)
[Mermaid] Shortcuts loaded: 28 categories from /Users/jogreenw/code/stillpoint/sp/app/mmd_shortcuts.json
[1]    80294 segmentation fault  python -m sp.app.main
(venv) ➜  stillpoint git:(develop) [81301:2463321:0911/170449.906912:ERROR:./../../../qtwebengine/src/3rdparty/chromium/base/apple/mach_port_rendezvous_mac.cc:255] bootstrap_look_up org.chromium.Chromium.MachPortRendezvousServer.1: Permission denied (1100)
[81301:2463321:0911/170449.908269:ERROR:./../../../qtwebengine/src/3rdparty/chromium/base/memory/shared_memory_switch.cc:266] No rendezvous client, terminating process (parent died?)
(venv)



---




Fatal crash detected (faulthandler log)

eenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Thread 0x000000016f98b000 (most recent call first):
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 363 in wait
  File "/Users/jogreenw/code/stillpoint/sp/sync/engine.py", line 810 in _run_loop
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 994 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1043 in _bootstrap_inner
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Thread 0x00000001789f7000 (most recent call first):
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/concurrent/futures/thread.py", line 90 in _worker
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 994 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1043 in _bootstrap_inner
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Thread 0x00000001779eb000 (most recent call first):
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/concurrent/futures/thread.py", line 90 in _worker
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 994 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1043 in _bootstrap_inner
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Thread 0x00000001749c7000 (most recent call first):
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/concurrent/futures/thread.py", line 90 in _worker
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 994 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1043 in _bootstrap_inner
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Thread 0x000000016cd6b000 (most recent call first):
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/selectors.py", line 548 in select
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/asyncio/base_events.py", line 2012 in _run_once
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/asyncio/base_events.py", line 683 in run_forever
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/asyncio/base_events.py", line 712 in run_until_complete
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/asyncio/runners.py", line 118 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/asyncio/runners.py", line 195 in run
  File "/Users/jogreenw/code/stillpoint/venv/lib/python3.13/site-packages/uvicorn/server.py", line 77 in run
  File "/Users/jogreenw/code/stillpoint/sp/app/main.py", line 716 in run_server
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 994 in run
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1043 in _bootstrap_inner
  File "/Users/jogreenw/.pyenv/versions/3.13.7/lib/python3.13/threading.py", line 1014 in _bootstrap

Current thread 0x00000001f52c2180 (most recent call first):
  File "/Users/jogreenw/code/stillpoint/sp/app/main.py", line 1026 in main
  File "/Users/jogreenw/code/stillpoint/sp/app/main.py", line 1049 in <module></module>
  File "<frozen runpy></frozen>", line 88 in _run_code
  File "<frozen runpy></frozen>", line 198 in _run_module_as_main

Extension modules: shiboken6.Shiboken, PySide6.QtCore, PySide6.QtGui, PySide6.QtWidgets, _cffi_backend, PIL._imaging, numpy._core._multiarray_umath, numpy.linalg._umath_linalg, PySide6.QtSvg, lxml._elementpath, lxml.etree, charset_normalizer.md, charset_normalizer.cd, markupsafe._speedups, pybase64._pybase64, yaml._yaml, grpc._cython.cygrpc, google._upb._message, PySide6.QtNetwork, PySide6.QtPrintSupport, PySide6.QtWebChannel, PySide6.QtWebEngineCore, PySide6.QtWebEngineWidgets (total: 23)
