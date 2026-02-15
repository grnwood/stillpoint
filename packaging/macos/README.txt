========================================
  StillPoint - macOS Install Notes
========================================

This build is unsigned and not notarized.
Gatekeeper may block launch until you clear quarantine.

PACKAGE CONTENTS
----------------

- StillPoint.app
- stillpoint-capture (helper binary)
- README.txt (this file)

INSTALL
-------

1. Unzip the download.
2. Move StillPoint.app to /Applications (recommended).

FIRST RUN (GUI METHOD)
----------------------

1. In Finder, open /Applications.
2. Right-click StillPoint.app.
3. Click Open.
4. Confirm Open in the macOS prompt.

FIRST RUN (TERMINAL METHOD)
---------------------------

If Gatekeeper still blocks launch, clear quarantine:

  xattr -dr com.apple.quarantine /Applications/StillPoint.app
  open /Applications/StillPoint.app

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
- Remove /usr/local/bin/stillpoint-capture (if installed)

PROJECT
-------

https://github.com/grnwood/StillPoint

========================================
