"""Tests for tree API performance optimisations.

Covers:
- Fix #1: Batch tree path loading via /api/vault/tree/expand-path
- Fix #2: Cached fetch_display_order_map (version-gated)
- Fix #3: SQL-based count_folders
- Fix #4: JSON-based tree cache (no deepcopy)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault_db(tmp_path: Path, pages: list[dict]) -> Path:
    """Create a minimal vault database and return the db file path."""
    sp_dir = tmp_path / ".stillpoint"
    sp_dir.mkdir(exist_ok=True)
    db_path = sp_dir / "settings.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            path TEXT PRIMARY KEY,
            title TEXT,
            updated REAL,
            created_at REAL,
            parent_path TEXT,
            display_order INTEGER,
            path_ci TEXT,
            title_ci TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES ('tree_version', '1')")
    for page in pages:
        conn.execute(
            "INSERT INTO pages(path, parent_path, display_order) VALUES (?, ?, ?)",
            (page["path"], page.get("parent_path"), page.get("display_order")),
        )
    conn.commit()
    conn.close()
    return db_path


# ===================================================================
# Fix #3 – SQL-based count_folders
# ===================================================================

class TestCountFolders:
    """Verify count_folders returns correct counts using the SQL path."""

    def test_simple_folder_pages(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/PageA/PageA.md", "parent_path": "/"},
            {"path": "/PageB/PageB.md", "parent_path": "/"},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            assert config.count_folders() == 2

    def test_flat_pages_not_counted(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/PageA/PageA.md", "parent_path": "/"},
            {"path": "/PageA/SubPage.md", "parent_path": "/PageA"},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            # Only /PageA/PageA.md is a folder page; /PageA/SubPage.md is not
            assert config.count_folders() == 1

    def test_nested_folders(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/A/A.md", "parent_path": "/"},
            {"path": "/A/B/B.md", "parent_path": "/A"},
            {"path": "/A/B/C/C.md", "parent_path": "/A/B"},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            assert config.count_folders() == 3

    def test_mixed_folder_and_flat(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/Projects/Projects.md", "parent_path": "/"},
            {"path": "/Projects/Todo/Todo.md", "parent_path": "/Projects"},
            {"path": "/Projects/Notes.md", "parent_path": "/Projects"},
            {"path": "/Journal/Journal.md", "parent_path": "/"},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            # Projects, Todo, Journal are folders; Notes is flat
            assert config.count_folders() == 3

    def test_empty_db(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            assert config.count_folders() == 0

    def test_txt_pages_counted(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/Legacy/Legacy.txt", "parent_path": "/"},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            assert config.count_folders() == 1

    def test_no_parent_path_skipped(self, tmp_path):
        """Pages with NULL parent_path should be excluded."""
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/A/A.md", "parent_path": None},
        ])
        with patch.object(config, "_connect_to_vault_db",
                          return_value=sqlite3.connect(str(db_path))):
            assert config.count_folders() == 0


# ===================================================================
# Fix #2 – Cached fetch_display_order_map
# ===================================================================

class TestDisplayOrderCache:
    """Verify that fetch_display_order_map caches per tree version."""

    def test_cache_hit_same_version(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/A/A.md", "parent_path": "/", "display_order": 1},
            {"path": "/B/B.md", "parent_path": "/", "display_order": 2},
        ])
        config._DISPLAY_ORDER_CACHE.clear()
        with patch.object(config, "_connect_to_vault_db",
                          side_effect=lambda: sqlite3.connect(str(db_path))), \
             patch.object(config, "get_tree_version", return_value=1):
            result1 = config.fetch_display_order_map()
            assert result1 == {"/A/A.md": 1, "/B/B.md": 2}

            # Second call should return cached result
            result2 = config.fetch_display_order_map()
            assert result2 is result1  # exact same dict object

    def test_cache_invalidated_on_version_bump(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/A/A.md", "parent_path": "/", "display_order": 1},
        ])
        config._DISPLAY_ORDER_CACHE.clear()
        with patch.object(config, "_connect_to_vault_db",
                          side_effect=lambda: sqlite3.connect(str(db_path))):
            with patch.object(config, "get_tree_version", return_value=1):
                result1 = config.fetch_display_order_map()
                assert result1 == {"/A/A.md": 1}

            # Change the DB and bump version
            conn = sqlite3.connect(str(db_path))
            conn.execute("UPDATE pages SET display_order = 99 WHERE path = '/A/A.md'")
            conn.commit()
            conn.close()

            with patch.object(config, "get_tree_version", return_value=2):
                result2 = config.fetch_display_order_map()
                assert result2 == {"/A/A.md": 99}
                assert result2 is not result1

    def test_invalidate_display_order_cache(self, tmp_path):
        from sp.app import config
        db_path = _make_vault_db(tmp_path, [
            {"path": "/A/A.md", "parent_path": "/", "display_order": 5},
        ])
        config._DISPLAY_ORDER_CACHE.clear()
        with patch.object(config, "_connect_to_vault_db",
                          side_effect=lambda: sqlite3.connect(str(db_path))), \
             patch.object(config, "get_tree_version", return_value=1):
            config.fetch_display_order_map()
            assert config._DISPLAY_ORDER_CACHE.get("map") is not None

        config.invalidate_display_order_cache()
        assert config._DISPLAY_ORDER_CACHE == {}


# ===================================================================
# Fix #4 – JSON-based tree cache (vs deepcopy)
# ===================================================================

class TestTreeCacheJsonBased:
    """Verify tree cache uses JSON serialisation and produces isolated copies."""

    def test_set_and_get_round_trip(self):
        from sp.server.api import _set_cached_tree, _get_cached_tree, _clear_tree_cache
        _clear_tree_cache()
        tree = [{"name": "A", "path": "/A", "children": [{"name": "B", "path": "/A/B", "children": []}]}]
        root = Path("/vault")
        _set_cached_tree(root, "/", True, False, 1, tree)

        result = _get_cached_tree(root, "/", True, False, 1)
        assert result is not None
        assert result == tree
        # Ensure it's a distinct copy
        assert result is not tree
        _clear_tree_cache()

    def test_mutation_isolation(self):
        from sp.server.api import _set_cached_tree, _get_cached_tree, _clear_tree_cache
        _clear_tree_cache()
        tree = [{"name": "A", "children": []}]
        root = Path("/vault")
        _set_cached_tree(root, "/", True, False, 1, tree)

        result = _get_cached_tree(root, "/", True, False, 1)
        result[0]["name"] = "MUTATED"

        fresh = _get_cached_tree(root, "/", True, False, 1)
        assert fresh[0]["name"] == "A"  # original not affected
        _clear_tree_cache()

    def test_version_mismatch_returns_none(self):
        from sp.server.api import _set_cached_tree, _get_cached_tree, _clear_tree_cache
        _clear_tree_cache()
        tree = [{"name": "X", "children": []}]
        root = Path("/vault")
        _set_cached_tree(root, "/", True, False, 1, tree)

        result = _get_cached_tree(root, "/", True, False, 2)
        assert result is None
        _clear_tree_cache()

    def test_cache_stores_json_string(self):
        from sp.server.api import _set_cached_tree, _TREE_CACHE, _clear_tree_cache
        _clear_tree_cache()
        root = Path("/vault")
        tree = [{"name": "Test"}]
        _set_cached_tree(root, "/", True, False, 1, tree)
        key = (str(root), "/", True, False)
        cached_entry = _TREE_CACHE[key]
        # The stored tree should be a JSON string, not a list
        assert isinstance(cached_entry["tree"], str)
        assert json.loads(cached_entry["tree"]) == tree
        _clear_tree_cache()


# ===================================================================
# Fix #1 – Batch /api/vault/tree/expand-path endpoint
# ===================================================================

class TestExpandPathEndpoint:
    """Verify the expand-path API returns segments for ancestor paths."""

    def test_endpoint_returns_segments(self):
        from sp.server.api import vault_tree_expand_path, _clear_tree_cache
        from sp.server.state import vault_state
        from sp.server.adapters import files as files_mod
        from sp.app import config

        _clear_tree_cache()

        def fake_list_dir(root, subpath="/", recursive=True):
            nodes = {
                "/": [{"name": "root", "path": "/", "children": [
                    {"name": "A", "path": "/A", "children": []},
                    {"name": "B", "path": "/B", "children": []},
                ]}],
                "/A": [{"name": "A", "path": "/A", "children": [
                    {"name": "C", "path": "/A/C", "children": []},
                ]}],
                "/A/C": [{"name": "C", "path": "/A/C", "children": []}],
            }
            return nodes.get(subpath, [])

        with patch.object(vault_state, "get_root", return_value=Path("/test")), \
             patch.object(config, "get_tree_version", return_value=1), \
             patch.object(config, "fetch_display_order_map", return_value={}), \
             patch.object(files_mod, "list_dir", side_effect=fake_list_dir):
            result = vault_tree_expand_path(target="/A/C", include_journal=False)

        assert "segments" in result
        assert "version" in result
        segments = result["segments"]
        # Should have entries for /, /A, and /A/C
        assert "/" in segments
        assert "/A" in segments
        assert "/A/C" in segments
        _clear_tree_cache()

    def test_endpoint_uses_cache(self):
        """Second call for same segments should use cache."""
        from sp.server.api import vault_tree_expand_path, _clear_tree_cache, _set_cached_tree
        from sp.server.state import vault_state
        from sp.app import config

        _clear_tree_cache()
        root = Path("/test")
        # Pre-populate cache for /
        cached_tree = [{"name": "root", "path": "/", "children": [
            {"name": "X", "path": "/X", "children": []},
        ]}]
        _set_cached_tree(root, "/", False, False, 1, cached_tree)

        call_count = 0
        original_list_dir = None

        def counting_list_dir(root_arg, subpath="/", recursive=True):
            nonlocal call_count
            call_count += 1
            return [{"name": subpath.strip("/") or "root", "path": subpath, "children": []}]

        from sp.server.adapters import files as files_mod
        with patch.object(vault_state, "get_root", return_value=root), \
             patch.object(config, "get_tree_version", return_value=1), \
             patch.object(config, "fetch_display_order_map", return_value={}), \
             patch.object(files_mod, "list_dir", side_effect=counting_list_dir):
            vault_tree_expand_path(target="/X", include_journal=False)

        # Cache should handle /, so list_dir should only be called for /X
        assert call_count == 1
        _clear_tree_cache()
