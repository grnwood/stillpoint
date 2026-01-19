========================================
  StillPoint - Linux Installation
========================================

Thank you for downloading StillPoint!

INSTALLATION OPTIONS
--------------------

Option 1: System-wide Install (Recommended)
  Installs to /opt/stillpoint with desktop launcher

  sudo ./install.sh

  This will:
  ✓ Install StillPoint to /opt/stillpoint
  ✓ Create a launcher in your applications menu
  ✓ Add 'stillpoint' command to your PATH
  ✓ Install the desktop icon

Option 2: Run Without Installing
  You can run StillPoint directly from this folder:

  ./StillPoint

  Note: No desktop launcher or menu entry will be created

AFTER INSTALLATION
------------------

Launch from:
  - Applications menu → Accessories → StillPoint
  - Terminal: stillpoint
  - Or search for "StillPoint" in your app launcher

UNINSTALL
---------

To uninstall:
  sudo rm -rf /opt/stillpoint
  sudo rm /usr/local/bin/stillpoint
  sudo rm /usr/share/applications/stillpoint.desktop
  sudo rm /usr/share/icons/stillpoint.png

TROUBLESHOOTING
---------------

If the icon doesn't appear:
  - Make sure sp-icon.png exists in _internal/sp/assets/
  - Run: sudo update-desktop-database

If you see "Permission denied":
  - Make sure to run install.sh with sudo
  - Or run directly: chmod +x ./StillPoint && ./StillPoint

For more help, visit:
https://github.com/grnwood/StillPoint

========================================
