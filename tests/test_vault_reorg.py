from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from sp.app import config
from sp.server import vault_reorg


@pytest.fixture
def reorg_vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    config.set_active_vault(str(root))
    try:
        yield root
    finally:
        config.set_active_vault(None)


def _write_page(root: Path, folder: str, content: str, *, title: str | None = None, links=None) -> str:
    folder_path = root / folder.strip("/")
    folder_path.mkdir(parents=True, exist_ok=True)
    leaf = folder_path.name
    page_path = f"/{folder.strip('/')}/{leaf}.md"
    (folder_path / f"{leaf}.md").write_text(content, encoding="utf-8")
    config.update_page_index(
        path=page_path,
        title=title or leaf,
        tags=[],
        links=list(links or []),
        tasks=[],
    )
    return page_path


def test_candidate_search_uses_title_and_path_without_content_index(reorg_vault) -> None:
    _write_page(reorg_vault, "/Projects/Pickles", "# Pickles\n", title="Hamburger Pickles")
    _write_page(reorg_vault, "/Journal/2026/08/01/Other", "# Other\n")

    result = vault_reorg.search_candidates("hamburger", include_content=False)

    assert [item["folder_path"] for item in result["results"]] == ["/Projects/Pickles"]
    assert result["results"][0]["match_type"] == "title_path"


def test_journal_day_candidate_is_marked_as_reference_only(reorg_vault) -> None:
    _write_page(
        reorg_vault,
        "/Journal/2026/08/01",
        "# Saturday 01 August 2026\n\n## MSC\n",
        title="Saturday 01 August 2026 MSC",
    )

    result = vault_reorg.search_candidates("MSC", include_content=False)

    assert result["results"][0]["folder_path"] == "/Journal/2026/08/01"
    assert result["results"][0]["operation_type"] == "add_reference"


def test_journal_day_cannot_be_staged_as_a_move(reorg_vault) -> None:
    _write_page(reorg_vault, "/Journal/2026/08/01", "# Day\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")

    result = vault_reorg.preflight_plan(
        reorg_vault,
        [
            {
                "from": "/Journal/2026/08/01",
                "destination_parent": "/Topics",
                "new_name": "Day",
                "operation_type": "move",
            }
        ],
        config.get_tree_version(),
    )

    assert result["ok"] is False
    assert "historical records" in result["errors"][0]["message"]


def test_journal_day_reference_preserves_day_and_updates_topic_page(reorg_vault) -> None:
    day_page = _write_page(
        reorg_vault,
        "/Journal/2026/08/01",
        "# Saturday 01 August 2026\n\n## MSC\nDaily work\n",
    )
    topic_page = _write_page(reorg_vault, "/Topics/MSC", "# MSC\n")
    operation = {
        "from": "/Journal/2026/08/01",
        "destination_parent": "/Topics/MSC",
        "new_name": "MSC",
        "operation_type": "add_reference",
    }

    preflight = vault_reorg.preflight_plan(
        reorg_vault,
        [operation],
        config.get_tree_version(),
    )

    assert preflight["ok"] is True
    assert preflight["operations"][0]["journal_reference_action"] == "add_reference"
    result = vault_reorg.commit_plan(
        reorg_vault,
        [operation],
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    assert (reorg_vault / day_page.lstrip("/")).exists()
    topic_content = (reorg_vault / topic_page.lstrip("/")).read_text(encoding="utf-8")
    assert "# Journal References" in topic_content
    assert "[Journal:2026:08:01|Saturday 01 August 2026] — MSC" in topic_content
    assert result["page_map"] == {}
    assert result["touched_paths"] == [topic_page]


def test_preflight_refreshes_a_stale_tree_version_without_blocking(reorg_vault) -> None:
    _write_page(reorg_vault, "/Source", "# Source\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    current_version = config.get_tree_version()

    result = vault_reorg.preflight_plan(
        reorg_vault,
        [{"from": "/Source", "destination_parent": "/Topics", "new_name": "Source"}],
        current_version - 1,
    )

    assert result["ok"] is True
    assert result["tree_version_changed"] is True
    assert result["tree_version"] == current_version
    assert result["plan_token"]


def test_commit_still_rejects_a_token_when_tree_changes_after_preflight(reorg_vault) -> None:
    _write_page(reorg_vault, "/Source", "# Source\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operation = {"from": "/Source", "destination_parent": "/Topics", "new_name": "Source"}
    preflight = vault_reorg.preflight_plan(
        reorg_vault,
        [operation],
        config.get_tree_version(),
    )
    config.bump_tree_version()

    with pytest.raises(vault_reorg.ReorganizationError, match="staged plan changed"):
        vault_reorg.commit_plan(
            reorg_vault,
            [operation],
            tree_version=preflight["tree_version"],
            plan_token=preflight["plan_token"],
        )

    assert (reorg_vault / "Source" / "Source.md").exists()


def test_reorganization_moves_renames_and_adds_journal_reference(reorg_vault) -> None:
    day_page = _write_page(
        reorg_vault,
        "/Journal/2026/08/01",
        "# Saturday 01 August 2026\n\nDaily notes\n",
    )
    source_page = _write_page(
        reorg_vault,
        "/Journal/2026/08/01/TopicA",
        "# TopicA\n\nProgress notes\n",
    )
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operation = {
        "from": "/Journal/2026/08/01/TopicA",
        "destination_parent": "/Topics",
        "new_name": "Topic A",
    }

    preflight = vault_reorg.preflight_plan(
        reorg_vault,
        [operation],
        config.get_tree_version(),
    )

    assert preflight["ok"] is True
    assert preflight["operations"][0]["journal_reference_action"] == "append"

    result = vault_reorg.commit_plan(
        reorg_vault,
        [operation],
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    destination = reorg_vault / "Topics" / "Topic A" / "Topic A.md"
    assert destination.read_text(encoding="utf-8").startswith("# Topic A\n")
    assert not (reorg_vault / "Journal" / "2026" / "08" / "01" / "TopicA").exists()
    day_content = (reorg_vault / day_page.lstrip("/")).read_text(encoding="utf-8")
    assert "# Moved Pages" in day_content
    assert "- [Topics:Topic_A|Topic A]" in day_content
    assert result["page_map"][source_page] == "/Topics/Topic A/Topic A.md"
    assert result["journal_paths"] == [day_page]


def test_candidate_index_uses_new_path_immediately_after_move(reorg_vault) -> None:
    _write_page(reorg_vault, "/Source", "# Source\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operation = {"from": "/Source", "destination_parent": "/Topics", "new_name": "Source"}
    preflight = vault_reorg.preflight_plan(
        reorg_vault,
        [operation],
        config.get_tree_version(),
    )
    vault_reorg.commit_plan(
        reorg_vault,
        [operation],
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    result = vault_reorg.search_candidates("Source")

    assert [item["folder_path"] for item in result["results"]] == ["/Topics/Source"]


def test_existing_journal_link_is_not_duplicated(reorg_vault) -> None:
    source_page = "/Journal/2026/08/01/TopicA/TopicA.md"
    day_page = _write_page(
        reorg_vault,
        "/Journal/2026/08/01",
        "# Day\n\n[Journal:2026:08:01:TopicA|Topic A]\n",
        links=[source_page],
    )
    _write_page(reorg_vault, "/Journal/2026/08/01/TopicA", "# TopicA\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operation = {
        "from": "/Journal/2026/08/01/TopicA",
        "destination_parent": "/Topics",
        "new_name": "TopicA",
    }

    preflight = vault_reorg.preflight_plan(reorg_vault, [operation], config.get_tree_version())
    assert preflight["operations"][0]["journal_reference_action"] == "rewrite_existing"

    result = vault_reorg.commit_plan(
        reorg_vault,
        [operation],
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    content = (reorg_vault / day_page.lstrip("/")).read_text(encoding="utf-8")
    assert "# Moved Pages" not in content
    assert result["journal_paths"] == []


def test_batch_orders_move_into_page_before_rehoming_that_page(reorg_vault) -> None:
    page_a = _write_page(reorg_vault, "/A", "# A\n")
    page_b = _write_page(reorg_vault, "/B", "# B\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operations = [
        {"from": "/B", "destination_parent": "/A", "new_name": "B"},
        {"from": "/A", "destination_parent": "/Topics", "new_name": "A"},
    ]

    preflight = vault_reorg.preflight_plan(reorg_vault, operations, config.get_tree_version())

    assert preflight["ok"] is True
    assert preflight["execution_order"] == [0, 1]
    assert preflight["operations"][0]["destination_path"] == "/Topics/A/B"

    result = vault_reorg.commit_plan(
        reorg_vault,
        operations,
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    assert (reorg_vault / "Topics" / "A" / "A.md").exists()
    assert (reorg_vault / "Topics" / "A" / "B" / "B.md").exists()
    assert result["page_map"][page_a] == "/Topics/A/A.md"
    assert result["page_map"][page_b] == "/Topics/A/B/B.md"


def test_batch_can_use_a_destination_vacated_by_an_earlier_move(reorg_vault) -> None:
    page_a = _write_page(reorg_vault, "/A", "# A\n")
    page_b = _write_page(reorg_vault, "/B", "# B\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operations = [
        {"from": "/A", "destination_parent": "/", "new_name": "B"},
        {"from": "/B", "destination_parent": "/Topics", "new_name": "B"},
    ]

    preflight = vault_reorg.preflight_plan(reorg_vault, operations, config.get_tree_version())

    assert preflight["ok"] is True
    assert preflight["execution_order"] == [1, 0]

    result = vault_reorg.commit_plan(
        reorg_vault,
        operations,
        tree_version=preflight["tree_version"],
        plan_token=preflight["plan_token"],
    )

    assert (reorg_vault / "B" / "B.md").exists()
    assert (reorg_vault / "Topics" / "B" / "B.md").exists()
    assert result["page_map"][page_a] == "/B/B.md"
    assert result["page_map"][page_b] == "/Topics/B/B.md"


def test_incomplete_manifest_blocks_plans_and_can_restore_journal(reorg_vault) -> None:
    day_page = _write_page(reorg_vault, "/Journal/2026/08/01", "# Original Day\n")
    day_file = reorg_vault / day_page.lstrip("/")
    original = day_file.read_bytes()
    day_file.write_text("# Damaged Day\n", encoding="utf-8")
    vault_reorg._write_manifest(
        reorg_vault,
        {
            "status": "recovery_required",
            "completed": [],
            "journal_backups": {day_page: base64.b64encode(original).decode("ascii")},
        },
    )

    blocked = vault_reorg.preflight_plan(reorg_vault, [], config.get_tree_version())
    assert blocked["ok"] is False
    assert "requires recovery" in blocked["errors"][0]["message"]

    result = vault_reorg.recover_incomplete(reorg_vault)

    assert result["recovered"] is True
    assert day_file.read_bytes() == original
    assert vault_reorg.recovery_status(reorg_vault) == {"recovery_required": False}


def test_failed_batch_rolls_back_completed_moves(reorg_vault, monkeypatch) -> None:
    _write_page(reorg_vault, "/A", "# A\n")
    _write_page(reorg_vault, "/B", "# B\n")
    _write_page(reorg_vault, "/Topics", "# Topics\n")
    operations = [
        {"from": "/A", "destination_parent": "/Topics", "new_name": "A"},
        {"from": "/B", "destination_parent": "/Topics", "new_name": "B"},
    ]
    preflight = vault_reorg.preflight_plan(reorg_vault, operations, config.get_tree_version())
    original_move = vault_reorg.file_ops.move_folder
    calls = 0

    def fail_second_move(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated move failure")
        return original_move(*args, **kwargs)

    monkeypatch.setattr(vault_reorg.file_ops, "move_folder", fail_second_move)

    with pytest.raises(vault_reorg.ReorganizationError, match="simulated move failure"):
        vault_reorg.commit_plan(
            reorg_vault,
            operations,
            tree_version=preflight["tree_version"],
            plan_token=preflight["plan_token"],
        )

    assert (reorg_vault / "A" / "A.md").exists()
    assert (reorg_vault / "B" / "B.md").exists()
    assert not (reorg_vault / "Topics" / "A").exists()
    assert vault_reorg.recovery_status(reorg_vault) == {"recovery_required": False}


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = httpx.Request("GET", "http://localhost/test")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_workspace_stages_hidden_journal_page_and_optional_rename(qtbot) -> None:
    from PySide6.QtGui import QPalette

    from sp.app.ui.vault_reorg_window import VaultReorgWindow, _PATH_ROLE

    class Http:
        def __init__(self):
            self.candidate_calls = 0

        def get(self, path, params=None):
            if path == "/api/vault/reorganize/candidates":
                self.candidate_calls += 1
                return _Response({"results": [], "content_index_available": True})
            assert path == "/api/vault/tree"
            return _Response(
                {
                    "version": 7,
                    "tree": [
                        {
                            "path": "/",
                            "children": [
                                {
                                    "name": "Journal",
                                    "path": "/Journal",
                                    "children": [
                                        {
                                            "name": "TopicA",
                                            "path": "/Journal/2026/08/01/TopicA",
                                            "children": [],
                                        }
                                    ],
                                },
                                {"name": "Topics", "path": "/Topics", "children": []},
                            ],
                        }
                    ],
                }
            )

    http = Http()
    window = VaultReorgWindow(
        http_client=http,
        vault_name="Test",
        read_only=False,
    )
    qtbot.addWidget(window)

    assert window.candidate_list.alternatingRowColors() is True
    assert "QListWidget::item:alternate" in window.candidate_list.styleSheet()
    assert window._candidate_alternate_color != window.candidate_list.palette().color(
        QPalette.ColorRole.Base
    ).name()
    journal_item = window._find_tree_item("/Journal")
    topics_item = window._find_tree_item("/Topics")
    assert journal_item is not None
    assert topics_item is not None
    journal_item.setExpanded(True)
    window.destination_tree.setCurrentItem(topics_item)
    window._stage_paths(["/Journal/2026/08/01/TopicA"], "/Topics")
    window.plan_table.item(0, 3).setText("Topic A")

    assert window._plan == [
        {
            "operation_type": "move",
            "source_path": "/Journal/2026/08/01/TopicA",
            "destination_parent": "/Topics",
            "new_name": "Topic A",
            "journal_reference_action": "none",
            "status": "Not validated",
        }
    ]
    assert window._find_tree_item("/Journal") is not None
    assert window._find_tree_item("/Journal").isExpanded() is True
    assert window.destination_tree.currentItem().data(0, _PATH_ROLE) == "/Topics"

    window.destination_search_edit.setText("Topics")
    assert window._find_tree_item("/Topics") is not None
    assert window._find_tree_item("/Journal") is None
    window.destination_search_edit.clear()

    window.destination_staged_only.setChecked(True)
    assert window._find_tree_item("/Journal/2026/08/01/TopicA") is not None
    assert window._find_tree_item("/Topics") is not None
    window._clear_plan()
    window.destination_staged_only.setChecked(False)
    window.search_edit.setText("MSC")
    window._stage_paths(["/Journal/2026/08/01"], "/Topics")

    assert window._plan[0]["operation_type"] == "add_reference"
    assert window._plan[0]["new_name"] == "MSC"
    assert window.plan_table.item(0, 0).text() == "Add reference"
    window._clear_plan()
    assert http.candidate_calls >= 1


def test_main_menu_exposes_reorganization_in_command_palette(main_window) -> None:
    labels = [label for label, _action in main_window._collect_menu_actions()]

    assert "Vault / Reorganize Vault…" in labels
