========================================
  StillPoint - macOS Installation
========================================

Thank you for downloading StillPoint!

INSTALLATION
------------

To install StillPoint on your Mac:

1. Unzip the download
2. Drag "StillPoint.app" to your Applications folder
3. Done!

FIRST LAUNCH
------------

When you first open StillPoint:

1. Go to Applications folder
2. Right-click "StillPoint.app"
3. Select "Open"
4. Click "Open" in the security dialog

(This is only needed the first time due to macOS Gatekeeper)

ALTERNATIVE: Using Terminal
---------------------------

If you prefer, you can remove the quarantine flag:

  xattr -cr /Applications/StillPoint.app
  open /Applications/StillPoint.app

UNINSTALL
---------

To uninstall:
  - Drag StillPoint.app from Applications to Trash

TROUBLESHOOTING
---------------

If the icon doesn't appear in the Dock:
  - Make sure you ran the "create-icns.sh" script before building
  - Rebuild with: pyinstaller -y packaging/sp-macos.spec

For more help, visit:
https://github.com/grnwood/StillPoint

========================================
