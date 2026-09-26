from sp.app.crash_reporting import enable_faulthandler_log

from .window import main

if __name__ == "__main__":
    enable_faulthandler_log()
    raise SystemExit(main())
