from PySide6.QtGui import QAction


def test_about_action_uses_native_macos_menu_role(main_window):
    action = main_window._action_about

    assert action.text() == "About StillPoint"
    assert action.menuRole() == QAction.MenuRole.AboutRole
    assert action in main_window.findChildren(QAction)
