========================================
  StillPoint - macOS Install Notes
========================================

This build is unsigned and not notarized.
Gatekeeper may block launch until you clear quarantine.

PACKAGE CONTENTS
----------------

- StillPoint.app
- StillPoint Folder Navigator.app (folder-badged companion launcher)
- StillPoint Quick Capture.app (standalone quick-entry window)
- stillpoint-capture (helper binary)
- stillpoint.sh (fallback launcher for StillPoint.app)
- README.txt (this file)

INSTALL
-------

1. Unzip the download.
2. Move all three .app bundles to /Applications (recommended). Keep StillPoint.app
   and StillPoint Folder Navigator.app beside each other so the companion launcher
   can start Folder Navigator with its distinct Dock and app-switcher icon. Quick
   Capture is self-contained and can run independently.

FIRST RUN (GUI METHOD)
----------------------

1. In Finder, open /Applications.
2. Right-click StillPoint.app.
3. Click Open.
4. Confirm Open in the macOS prompt.
5. Repeat for StillPoint Folder Navigator.app and StillPoint Quick Capture.app
   the first time you use each one.

FIRST RUN (TERMINAL METHOD)
---------------------------

If Gatekeeper still blocks launch, clear quarantine:

  xattr -dr com.apple.quarantine /Applications/StillPoint.app
  xattr -dr com.apple.quarantine "/Applications/StillPoint Folder Navigator.app"
  xattr -dr com.apple.quarantine "/Applications/StillPoint Quick Capture.app"
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

SYSTEM-WIDE HOTKEY FOR stillpoint-capture
-----------------------------------------

The zip also includes a helper binary at:

  stillpoint-capture/stillpoint-capture

Recommended install location:

1. Move it to /usr/local/bin:

  sudo mkdir -p /usr/local/bin
  sudo cp stillpoint-capture/stillpoint-capture /usr/local/bin/stillpoint-capture
  sudo chmod +x /usr/local/bin/stillpoint-capture
  xattr -dr com.apple.quarantine /usr/local/bin/stillpoint-capture

2. Create a macOS Quick Action in Automator:
   - Open Automator, choose "Quick Action".
   - Set "Workflow receives" to "no input" in "any application".
   - Add "Run Shell Script".
   - Use this script:

  /usr/local/bin/stillpoint-capture >/tmp/stillpoint-capture.log 2>&1 &

   - Save as: StillPoint Capture

3. Bind a global keyboard shortcut:
   - Open System Settings -> Keyboard -> Keyboard Shortcuts.
   - Go to Services (or Quick Actions).
   - Find "StillPoint Capture" and assign your hotkey.

UNINSTALL
---------

- Remove /Applications/StillPoint.app
- Remove "/Applications/StillPoint Folder Navigator.app"
- Remove "/Applications/StillPoint Quick Capture.app"
- Remove /usr/local/bin/stillpoint-capture (if installed)

PROJECT
-------

https://github.com/grnwood/StillPoint

========================================
