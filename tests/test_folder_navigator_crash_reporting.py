from __future__ import annotations


def test_folder_navigator_entry_enables_shared_faulthandler():
    source = __import__("pathlib").Path(
        "sp/app/folder_navigator/__main__.py"
    ).read_text(encoding="utf-8")
    assert "enable_faulthandler_log()" in source


def test_folder_navigator_crash_returncodes_are_detected():
    from sp.app.ui.main_window import MainWindow

    assert MainWindow._is_crash_returncode(-11)
    assert MainWindow._is_crash_returncode(0xC0000005)
    assert MainWindow._is_crash_returncode(-1073741819)
    assert not MainWindow._is_crash_returncode(0)
    assert not MainWindow._is_crash_returncode(1)
