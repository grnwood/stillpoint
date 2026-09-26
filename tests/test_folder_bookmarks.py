from __future__ import annotations

from pathlib import Path

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
    launches: list[Path] = []
    main_window.folder_bookmarks = [str(project)]
    monkeypatch.setattr(
        main_window,
        "_launch_folder_navigator",
        lambda path: launches.append(path) or True,
    )

    main_window._refresh_bookmark_buttons()

    button = main_window.folder_bookmark_buttons[str(project)]
    assert button.text() == "working-copy"
    assert button.property("folderBookmark") == "true"
    assert "Folder Navigator:" in button.toolTip()

    button.click()
    assert launches == [project]


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
