from __future__ import annotations

from sp.app.ui.task_panel import _active_tag_token, _should_suspend_nav_for_tag


def test_is_typing_tag_detects_active_tag_token() -> None:
    assert _active_tag_token("@todo", 5) == "@todo"
    assert _active_tag_token("fix @todo", 9) == "@todo"
    assert _active_tag_token("fix @todo later", 9) == "@todo"
    assert _active_tag_token("fix @todo later", 14) is None
    assert _active_tag_token("no tag", 2) is None
    assert _active_tag_token("", 0) is None


def test_should_suspend_nav_only_for_unknown_tag() -> None:
    available = {"todo", "wt"}
    assert _should_suspend_nav_for_tag("fix @to", 7, available) is True  # partial not known
    assert _should_suspend_nav_for_tag("fix @todo", 9, available) is False  # known tag; allow nav
    assert _should_suspend_nav_for_tag("fix @todo later", 14, available) is False  # cursor past tag
    assert _should_suspend_nav_for_tag("plain", 3, available) is False
