========================================
  StillPoint - macOS Install Notes
========================================

This build is unsigned and not notarized.
Gatekeeper may block launch until you clear quarantine.

PACKAGE CONTENTS
----------------

- StillPoint.app
- StillPoint Folder Navigator.app (folder-badged companion launcher)
- stillpoint.sh (fallback launcher for StillPoint.app)
- README.txt (this file)

INSTALL
-------

1. Unzip the download.
2. Move both .app bundles to /Applications (recommended). Keep StillPoint.app
   and StillPoint Folder Navigator.app beside each other so the companion launcher
   can start Folder Navigator with its distinct Dock and app-switcher icon.

FIRST RUN (GUI METHOD)
----------------------

1. In Finder, open /Applications.
2. Right-click StillPoint.app.
3. Click Open.
4. Confirm Open in the macOS prompt.
5. Repeat for StillPoint Folder Navigator.app the first time you use it.

FIRST RUN (TERMINAL METHOD)
---------------------------

If Gatekeeper still blocks launch, clear quarantine:

  xattr -dr com.apple.quarantine /Applications/StillPoint.app
  xattr -dr com.apple.quarantine "/Applications/StillPoint Folder Navigator.app"
  open /Applications/StillPoint.app

FALLBACK LAUNCHER
-----------------

If "open /Applications/StillPoint.app" returns without showing a window, use
the included launcher from the extracted download or mounted disk image:

  ./stillpoint.sh

The launcher starts /Applications/StillPoint.app directly with a stable
per-user vaults root at ~/.stillpoint/vaults. If StillPoint is already running,
it asks macOS to bring that instance forward. It does not use AppleScript and
therefore does not require Automation access to System Events.

If the launcher is not executable after copying it, run:

  chmod +x stillpoint.sh

Startup output is appended to:

  ~/Library/Logs/StillPoint-launch.log

QUICK CAPTURE HOTKEY
--------------------

While StillPoint is running, press Control+Option+Space from any application.
Change or clear the binding in StillPoint Preferences.

UNINSTALL
---------

- Remove /Applications/StillPoint.app
- Remove "/Applications/StillPoint Folder Navigator.app"

PROJECT
-------

https://github.com/grnwood/StillPoint

========================================
