from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from sp.app import config


def test_folder_bookmarks_are_vault_scoped_and_deduplicated(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    first = tmp_path / "project-a"
    second = tmp_path / "project-b"
    first.mkdir()
    second.mkdir()

    config.set_active_vault(str(vault))
    try:
        config.save_folder_bookmarks([str(first), str(second), str(first)])

        assert config.load_folder_bookmarks() == [str(first.resolve()), str(second.resolve())]

        config.rebuild_index_from_disk(vault)
        assert config.load_folder_bookmarks() == [str(first.resolve()), str(second.resolve())]
    finally:
        config.set_active_vault(None)


def test_folder_bookmark_renders_in_shared_strip_and_opens_navigator(
    main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "working-copy"
    project.mkdir()
    launches: list[tuple[Path, bool]] = []
    main_window.folder_bookmarks = [str(project)]
    monkeypatch.setattr(
        main_window,
        "_launch_folder_navigator",
        lambda path, *, force_new=False: launches.append((path, force_new)) or True,
    )

    main_window._refresh_bookmark_buttons()

    button = main_window.folder_bookmark_buttons[str(project)]
    assert button.text() == "working-copy"
    assert button.property("folderBookmark") == "true"
    assert "Folder Navigator:" in button.toolTip()

    button.click()
    assert launches == [(project, False)]

    modifier = Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier
    QTest.mouseClick(button, Qt.LeftButton, modifier)
    assert launches == [(project, False), (project, True)]


def test_folder_bookmark_activates_existing_unless_forced(
    main_window, monkeypatch, tmp_path: Path,
) -> None:
    from sp.app.folder_navigator import instances, launch

    project = tmp_path / "working-copy"
    project.mkdir()
    activations: list[Path] = []
    launches: list[Path] = []
    monkeypatch.setattr(instances, "activate_existing", lambda path: activations.append(path) or True)
    monkeypatch.setattr(launch, "launch", lambda path: launches.append(path) or object())
    monkeypatch.setattr(main_window, "_monitor_folder_navigator", lambda _process: None)

    main_window._open_folder_bookmark(str(project))
    assert activations == [project]
    assert launches == []

    main_window._open_folder_bookmark(str(project), force_new=True)
    assert activations == [project]
    assert launches == [project]


def test_jump_to_bookmark_opens_folder_navigator_entry(
    main_window, monkeypatch, tmp_path: Path,
) -> None:
    from sp.app.ui import main_window as main_window_module

    folder = tmp_path / "working-copy"
    folder.mkdir()
    monkeypatch.setattr(config, "has_active_vault", lambda: True)
    monkeypatch.setattr(config, "load_folder_bookmarks", lambda: [str(folder)])
    main_window.bookmarks = []
    captured = {}

    class FakeJumpDialog:
        def __init__(self, _parent, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.Accepted

        def selected_path(self):
            return str(folder)

        def selected_is_folder_navigator(self):
            return True

    launches = []
    monkeypatch.setattr(main_window_module, "JumpToPageDialog", FakeJumpDialog)
    monkeypatch.setattr(main_window, "_open_folder_bookmark", lambda path: launches.append(path))

    main_window._jump_to_bookmark()

    assert captured["folder_paths"] == [str(folder)]
    assert launches == [str(folder)]


def test_bookmark_folder_flow_persists_and_opens(
    main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "agent-codebase"
    project.mkdir()
    saved: list[list[str]] = []
    launches: list[Path] = []
    monkeypatch.setattr(
        "sp.app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *_args, **_kwargs: str(project),
    )
    monkeypatch.setattr(config, "save_folder_bookmarks", lambda paths: saved.append(list(paths)))
    monkeypatch.setattr(
        main_window,
        "_launch_folder_navigator",
        lambda path: launches.append(path) or True,
    )

    main_window._bookmark_folder_navigator()

    assert main_window.folder_bookmarks == [str(project.resolve())]
    assert saved == [[str(project.resolve())]]
    assert launches == [project.resolve()]
