# WindowsStackOverflow
Created Friday 19 December 2025
---
NOTE: ==This was solved.==
This needed some guards on some methods.

---
This is what i'm getting on windows.

One instance seems to work fine, loading multiple and flipping in/out of audience more i get:

### First Time

```
Windows fatal exception: stack overflow
Current thread 0x00007b30 (most recent call first):
  File "C:\Users\jogreenw\code-local\github-personal\stillpoint\sp\app\ui\markdown_editor.py", line 5137 in _apply_hanging_indent
  File "C:\Users\jogreenw\code-local\github-personal\stillpoint\sp\app\ui\markdown_editor.py", line 5064 in _enforce_display_symbols
  File "C:\Users\jogreenw\code-local\github-
```

## Second Time
```
Windows fatal exception: stack overflow

Thread 0x00001710 (most recent call first):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 359 in wait
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\queue.py", line 199 in get
  File "C:\Users\jogreenw\code-local\github-personal\stillpoint\venv\Lib\site-packages\anyio\_backends\_asyncio.py", line 975 in run
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 1043 in _bootstrap_inner
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 1014 in _bootstrap

Thread 0x00008b5c (most recent call first):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\windows_events.py", line 775 in _poll
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\windows_events.py", line 446 in select
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\base_events.py", line 2012 in _run_once
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\base_events.py", line 683 in run_forever
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\base_events.py", line 712 in run_until_complete
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\runners.py", line 118 in run
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\asyncio\runners.py", line 195 in run
  File "C:\Users\jogreenw\code-local\github-personal\stillpoint\venv\Lib\site-packages\uvicorn\server.py", line 67 in run
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 994 in run
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 1043 in _bootstrap_inner
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.2544.0_x64__qbz5n2kfra8p0\Lib\threading.py", line 1014 in _bootstrap

Current thread 0x0000b24c (most recent call first):
  File "C:\Users\jogreenw\code-local\github-personal\stillpoint\sp\app\ui\markdown_editor.py", line 5141 in _apply_hanging_indent
```