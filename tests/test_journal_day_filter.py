"""Tests for filtering Journal day pages out of navigation search."""

import pytest

from sp.app import config


@pytest.fixture
def test_db(tmp_path):
    config.set_active_vault(str(tmp_path))
    yield
    config.set_active_vault(None)


def _index_stub_page(path: str, title: str = "") -> None:
    config.update_page_index(
        path=path,
        title=title or path,
        tags=[],
        links=[],
        tasks=[],
    )


def test_search_pages_hides_bare_journal_days_without_subpages(test_db):
    # Bare day with no subpages: should be hidden
    day_no_subpages = "/Journal/2026/01/14/14.md"
    _index_stub_page(day_no_subpages, title="2026-01-14")

    # Day with subpages: day page should remain visible, and subpage should remain visible
    day_with_subpages = "/Journal/2026/01/15/15.md"
    day_subpage = "/Journal/2026/01/15/SubPage/SubPage.md"
    _index_stub_page(day_with_subpages, title="2026-01-15")
    _index_stub_page(day_subpage, title="SubPage")

    # Non-journal page should never be filtered out
    normal_page = "/Projects/Alpha/Alpha.md"
    _index_stub_page(normal_page, title="Alpha")

    journal_results = config.search_pages("Journal", limit=100)
    paths = {row["path"] for row in journal_results}

    assert day_no_subpages not in paths
    assert day_with_subpages in paths
    assert day_subpage in paths

    non_journal_results = config.search_pages("Alpha", limit=100)
    non_journal_paths = {row["path"] for row in non_journal_results}
    assert normal_page in non_journal_paths
