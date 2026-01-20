========================================
  StillPoint - Windows Installation
========================================

Thank you for downloading StillPoint!

QUICK START
-----------

To install StillPoint with Start Menu and Desktop shortcuts:

1. Right-click on "install-win32.ps1" in this folder
2. Select "Run with PowerShell"
3. Follow the prompts

The installer will:
  ✓ Copy StillPoint to your user AppData folder
  ✓ Create a Start Menu entry
  ✓ Create a Desktop shortcut
  ✓ No admin rights required

MANUAL START (Without Installing)
----------------------------------

You can run StillPoint directly without installing:
  - Double-click "stillpoint.exe" in this folder

QUICK CAPTURE HOTKEY (Windows)
------------------------------

Use the provided screenshot to set up a Quick Capture shortcut:

1. Copy the existing StillPoint shortcut (Start Menu or Desktop).
2. Paste it and rename the copy (e.g., "StillPoint Quick Capture").
3. Edit the shortcut target to:
   "C:\Path\To\StillPoint\stillpoint.exe" --quick-capture
4. Assign a Shortcut key (hotkey).
5. Apply and close — you're good to go.

See: packaging/win32/windows-quicklaunch-shortcut.png

Note: The taskbar icon may not display correctly unless you 
      use the install.ps1 script.

UNINSTALL
---------

To uninstall:
1. Delete: %LOCALAPPDATA%\Programs\StillPoint
2. Remove shortcuts from Start Menu and Desktop

TROUBLESHOOTING
---------------

If install-win32.ps1 won't run:
  - Open PowerShell
  - Unblock-File .\install-win32.ps1
  - Re run the file (terminal)

If you see security warnings:
  - This is normal for PowerShell scripts
  - The script only copies files to your user folder

For more help, visit:
https://github.com/grnwood/StillPoint

========================================
