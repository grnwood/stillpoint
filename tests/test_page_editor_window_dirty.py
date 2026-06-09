from PySide6.QtCore import Qt

from sp.app.ui.page_editor_window import PageEditorWindow


def test_application_deactivated_saves_dirty_popup_editor() -> None:
    class _Dummy:
        def __init__(self) -> None:
            self.saved_calls = []

        def _is_dirty(self) -> bool:
            return True

        def _save_current_file(self, *args, **kwargs) -> None:
            self.saved_calls.append((args, kwargs))

    dummy = _Dummy()
    PageEditorWindow._on_application_state_changed(dummy, Qt.ApplicationState.ApplicationInactive)

    assert len(dummy.saved_calls) == 1
    _args, kwargs = dummy.saved_calls[0]
    assert kwargs.get("auto") is True
    assert kwargs.get("reason") == "application deactivated"


def test_application_deactivated_skips_clean_popup_editor() -> None:
    class _Dummy:
        def __init__(self) -> None:
            self.saved_calls = []

        def _is_dirty(self) -> bool:
            return False

        def _save_current_file(self, *args, **kwargs) -> None:
            self.saved_calls.append((args, kwargs))

    dummy = _Dummy()
    PageEditorWindow._on_application_state_changed(dummy, Qt.ApplicationState.ApplicationInactive)

    assert dummy.saved_calls == []
