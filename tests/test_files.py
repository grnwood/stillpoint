import pytest

from sp.server.adapters import files


def test_create_directory_creates_page_bundle(tmp_path):
    files.create_directory(tmp_path, "/Projects")
    page_dir = tmp_path / "Projects"
    assert page_dir.is_dir()
    assert (page_dir / "Projects.md").exists()


def test_list_dir_includes_root_and_children(tmp_path):
    files.create_directory(tmp_path, "/Projects")
    files.create_directory(tmp_path, "/Projects/Ideas")
    tree = files.list_dir(tmp_path)
    assert len(tree) == 1
    root = tree[0]
    assert root["name"] == tmp_path.name
    assert root["open_path"].endswith(f"/{tmp_path.name}.md")
    assert root["path"] == "/"
    child = root["children"][0]
    assert child["name"] == "Projects"
    assert child["open_path"].endswith("/Projects/Projects.md")
    assert child["children"][0]["name"] == "Ideas"


def test_list_dir_skips_hidden_dirs(tmp_path):
    hidden = tmp_path / ".stillpoint"
    hidden.mkdir()
    (hidden / "data").write_text("x", encoding="utf-8")
    tree = files.list_dir(tmp_path)
    assert tree[0]["children"] == []


def test_read_file_bootstraps_missing_page(tmp_path):
    page_dir = tmp_path / "Area"
    page_dir.mkdir()
    rel_path = "/Area/Area.md"
    content = files.read_file(tmp_path, rel_path)
    assert "# Area" in content
    assert (page_dir / "Area.md").exists()


def test_read_file_prefers_md_over_txt(tmp_path):
    page_dir = tmp_path / "Legacy"
    page_dir.mkdir()
    legacy = page_dir / "Legacy.txt"
    modern = page_dir / "Legacy.md"
    legacy.write_text("legacy", encoding="utf-8")
    modern.write_text("modern", encoding="utf-8")
    content = files.read_file(tmp_path, "/Legacy/Legacy.txt")
    assert content == "modern"


def test_write_file_with_txt_path_writes_md(tmp_path):
    files.write_file(tmp_path, "/Notes/Notes.txt", "hello")
    assert (tmp_path / "Notes" / "Notes.md").exists()
    assert not (tmp_path / "Notes" / "Notes.txt").exists()


def test_write_file_rejects_mismatched_names(tmp_path):
    with pytest.raises(files.FileAccessError):
        files.write_file(tmp_path, "/note.md", "hello")


def test_delete_path_removes_page_folder(tmp_path):
    files.create_directory(tmp_path, "/Archive")
    files.delete_path(tmp_path, "/Archive")
    assert not (tmp_path / "Archive").exists()


def test_delete_by_page_file_removes_folder(tmp_path):
    files.create_directory(tmp_path, "/Docs")
    files.delete_path(tmp_path, "/Docs/Docs.md")
    assert not (tmp_path / "Docs").exists()
