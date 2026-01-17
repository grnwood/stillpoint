import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    internal = base / "_internal"
    p = str(internal)
    if internal.exists() and p not in sys.path:
        sys.path.insert(0, p)
