"""Test database cleanup when deleting pages and folders."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sp.app import config


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    config.set_active_vault(str(tmp_path))
    yield
    config.set_active_vault(None)


def test_delete_single_page_cleans_database(test_db):
    """Deleting a single page should remove all its tasks and tags."""
    path = "/TestPage/TestPage.md"
    
    # Index a page with tasks and tags
    config.update_page_index(
        path=path,
        title="Test Page",
        tags=["@work", "@urgent"],
        links=["/OtherPage.md"],
        tasks=[
            {
                "id": f"{path}:1",
                "line": 1,
                "text": "Task 1",
                "status": "todo",
                "priority": 1,
                "due": "2025-12-01",
                "start": None,
                "tags": ["@bug"],
            },
            {
                "id": f"{path}:2",
                "line": 2,
                "text": "Task 2",
                "status": "todo",
                "priority": 0,
                "due": None,
                "start": None,
                "tags": [],
            },
        ],
    )
    
    # Verify data was inserted
    pages = config.search_pages("Test")
    assert len(pages) == 1
    
    tasks = config.fetch_tasks()
    assert len(tasks) == 2
    
    tags = config.fetch_tag_summary()
    assert len(tags) == 2  # @work, @urgent
    
    task_tags = config.fetch_task_tags()
    assert len(task_tags) == 1  # @bug
    
    # Delete the page
    config.delete_page_index(path)
    
    # Verify everything was cleaned up
    pages = config.search_pages("Test")
    assert len(pages) == 0
    
    tasks = config.fetch_tasks()
    assert len(tasks) == 0
    
    tags = config.fetch_tag_summary()
    assert len(tags) == 0
    
    task_tags = config.fetch_task_tags()
    assert len(task_tags) == 0


def test_delete_folder_cleans_all_subpages(test_db):
    """Deleting a folder should remove all pages, tasks, and tags within it."""
    # Create a folder structure:
    # /Folder/
    #   Folder.md
    #   SubPage1/SubPage1.md
    #   SubPage2/SubPage2.md
    #   Deep/Nested/Nested.md
    
    pages = [
        "/Folder/Folder.md",
        "/Folder/SubPage1/SubPage1.md",
        "/Folder/SubPage2/SubPage2.md",
        "/Folder/Deep/Nested/Nested.md",
    ]
    
    for i, path in enumerate(pages):
        config.update_page_index(
            path=path,
            title=f"Page {i}",
            tags=[f"@tag{i}"],
            links=[],
            tasks=[
                {
                    "id": f"{path}:{i}",
                    "line": i,
                    "text": f"Task {i}",
                    "status": "todo",
                    "priority": i,
                    "due": None,
                    "start": None,
                    "tags": [f"@task{i}"],
                }
            ],
        )
    
    # Also add a page outside the folder that shouldn't be deleted
    config.update_page_index(
        path="/Other/Other.md",
        title="Other Page",
        tags=["@other"],
        links=[],
        tasks=[
            {
                "id": "/Other/Other.md:99",
                "line": 99,
                "text": "Other Task",
                "status": "todo",
                "priority": 0,
                "due": None,
                "start": None,
                "tags": ["@keep"],
            }
        ],
    )
    
    # Verify all data was inserted
    all_pages = config.search_pages("")
    assert len(all_pages) == 5  # 4 in folder + 1 outside
    
    all_tasks = config.fetch_tasks()
    assert len(all_tasks) == 5
    
    # Delete the entire folder
    config.delete_folder_index("/Folder")
    
    # Verify only the folder contents were deleted
    remaining_pages = config.search_pages("")
    assert len(remaining_pages) == 1
    assert remaining_pages[0]["path"] == "/Other/Other.md"
    
    remaining_tasks = config.fetch_tasks()
    assert len(remaining_tasks) == 1
    assert remaining_tasks[0]["id"] == "/Other/Other.md:99"
    
    # Verify the tags from folder pages are gone
    remaining_tags = config.fetch_tag_summary()
    assert len(remaining_tags) == 1
    assert remaining_tags[0][0] == "@other"
    
    remaining_task_tags = config.fetch_task_tags()
    assert len(remaining_task_tags) == 1
    assert remaining_task_tags[0][0] == "@keep"


def test_delete_folder_with_no_leading_slash(test_db):
    """delete_folder_index should handle paths without leading slash."""
    path = "/Test/Test.md"
    config.update_page_index(
        path=path,
        title="Test",
        tags=["@test"],
        links=[],
        tasks=[],
    )
    
    # Delete using path without leading slash
    config.delete_folder_index("Test")
    
    pages = config.search_pages("Test")
    assert len(pages) == 0


def test_delete_folder_with_trailing_slash(test_db):
    """delete_folder_index should handle paths with trailing slash."""
    path = "/Test/Test.md"
    config.update_page_index(
        path=path,
        title="Test",
        tags=["@test"],
        links=[],
        tasks=[],
    )
    
    # Delete using path with trailing slash
    config.delete_folder_index("/Test/")
    
    pages = config.search_pages("Test")
    assert len(pages) == 0


def test_delete_preserves_links_to_deleted_pages(test_db):
    """Links TO deleted pages should be removed, but links FROM other pages should remain."""
    # Create two pages with cross-links
    config.update_page_index(
        path="/PageA/PageA.md",
        title="Page A",
        tags=[],
        links=["/PageB/PageB.md"],
        tasks=[],
    )
    
    config.update_page_index(
        path="/PageB/PageB.md",
        title="Page B",
        tags=[],
        links=["/PageA/PageA.md"],
        tasks=[],
    )
    
    # Delete PageB
    config.delete_page_index("/PageB/PageB.md")
    
    # PageA should still exist
    pages = config.search_pages("Page A")
    assert len(pages) == 1
    
    # But the link TO PageB should be removed
    # (This would require checking the links table directly, which we don't expose)
    # For now, just verify PageB is gone
    pages_b = config.search_pages("Page B")
    assert len(pages_b) == 0
