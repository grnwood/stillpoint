from __future__ import annotations

import pytest

from sp.app import config
from sp.server import file_ops


@pytest.fixture
def isolated_file_ops(monkeypatch):
    monkeypatch.setattr(config, "validate_move_tree_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        config,
        "move_tree_index",
        lambda src, dest, root, **kwargs: {
            "path_map": {f"{src}/{src.rsplit('/', 1)[-1]}.md": f"{dest}/{dest.rsplit('/', 1)[-1]}.md"},
            "orders": {},
        },
    )
    monkeypatch.setattr(config, "update_link_paths", lambda path_map: None)
    monkeypatch.setattr(config, "bump_tree_version", lambda: 1)


def _rename_page(tmp_path, old_name: str, new_name: str, content: str) -> str:
    page_dir = tmp_path / old_name
    page_dir.mkdir()
    (page_dir / f"{old_name}.md").write_bytes(content.encode("utf-8"))

    file_ops.rename_folder(tmp_path, f"/{old_name}", f"/{new_name}")

    return (tmp_path / new_name / f"{new_name}.md").read_bytes().decode("utf-8")


def test_rename_updates_exact_leading_h1_to_actual_destination_name(tmp_path, isolated_file_ops) -> None:
    content = "\r\n  # New Page  \r\n\r\nBody\r\n"

    renamed = _rename_page(tmp_path, "New Page", "new_page", content)

    assert renamed == "\r\n  # new_page  \r\n\r\nBody\r\n"


@pytest.mark.parametrize(
    "content",
    [
        "## New Page\n\nBody\n",
        "# new page\n\nBody\n",
        "# New Page Notes\n\nBody\n",
        "Intro first\n\n# New Page\n",
        "# **New Page**\n\nBody\n",
    ],
)
def test_rename_leaves_nonmatching_or_non_h1_content_unchanged(
    tmp_path,
    isolated_file_ops,
    content: str,
) -> None:
    assert _rename_page(tmp_path, "New Page", "new_page", content) == content


def test_heading_helper_preserves_utf8_bom_and_missing_final_newline(tmp_path) -> None:
    page = tmp_path / "New Page.md"
    page.write_text("\ufeff# New Page", encoding="utf-8")

    assert file_ops._rewrite_heading_if_matches(page, "New Page", "new_page") is True
    assert page.read_text(encoding="utf-8") == "\ufeff# new_page"
