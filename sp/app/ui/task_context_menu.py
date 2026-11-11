"""Task menu labels and commands shared by task lists and editor windows."""

from PySide6.QtWidgets import QMenu

from .theme import apply_menu_theme


def build_task_context_menu(parent, targets, dispatch, *, undo_available=False, theme_anchor=None):
    menu = QMenu(parent)
    apply_menu_theme(menu, theme_anchor if theme_anchor is not None else parent)

    def add(label, command):
        menu.addAction(label).triggered.connect(lambda checked=False: dispatch(command))

    if any((target.get("task") or {}).get("status") != "done" for target in targets):
        add("Mark Complete", "complete")
    if any((target.get("task") or {}).get("status") == "done" for target in targets):
        add("Reopen Task", "reopen")
    menu.addSeparator()
    for label, command in (
        ("Edit Task… (E)", "edit"), ("Set Due Date… (D)", "due"),
        ("Set Start Date… (S)", "start"), ("Edit Tags… (T)", "tags"),
        ("Move… (M)", "move"), ("Remove Task Indicators (R)", "remove_indicators"),
        ("Delete Task…", "delete"),
    ):
        add(label, command)
    if undo_available:
        menu.addSeparator()
        add("Undo Last Change (Ctrl+Z)", "undo")
    return menu
