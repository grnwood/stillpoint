from __future__ import annotations

from pathlib import Path

import pytest

from sp.server.adapters.files import FileAccessError, list_dir, read_file
from sp.sync.local_fs import iter_files
from sp.sync.engine import HomebaseSyncConfig, HomebaseSyncEngine
from sp.vault_boundary import (
    NestedVaultError,
    find_nested_vault_roots,
    path_crosses_nested_vault,
    validate_vault_root,
)


def _mark_vault(root: Path) -> None:
    metadata = root / ".stillpoint"
    metadata.mkdir(parents=True)
    (metadata / "settings.db").touch()


def test_validate_rejects_parent_containing_multiple_vaults(tmp_path: Path) -> None:
    first = tmp_path / "First Vault"
    second = tmp_path / "Second Vault"
    _mark_vault(first)
    _mark_vault(second)

    assert find_nested_vault_roots(tmp_path) == [first, second]
    with pytest.raises(NestedVaultError) as caught:
        validate_vault_root(tmp_path)

    assert first in caught.value.nested_roots
    assert second in caught.value.nested_roots
    assert "Choose one exact vault folder" in str(caught.value)


def test_validate_allows_the_exact_vault_root(tmp_path: Path) -> None:
    _mark_vault(tmp_path)

    assert validate_vault_root(tmp_path) == tmp_path.resolve()
    assert find_nested_vault_roots(tmp_path) == []


def test_parent_with_its_own_metadata_still_rejects_child_vault(tmp_path: Path) -> None:
    _mark_vault(tmp_path)
    child = tmp_path / "ActualVault"
    _mark_vault(child)

    with pytest.raises(NestedVaultError):
        validate_vault_root(tmp_path)


def test_homebase_scan_prunes_nested_vault_contents(tmp_path: Path) -> None:
    (tmp_path / "ordinary.txt").write_text("ordinary", encoding="utf-8")
    nested = tmp_path / "NestedVault"
    _mark_vault(nested)
    nested_page = nested / "Page" / "Page.md"
    nested_page.parent.mkdir(parents=True)
    nested_page.write_text("# Must not upload", encoding="utf-8")

    scanned = {rel for rel, _path in iter_files(tmp_path)}

    assert scanned == {"ordinary.txt"}


def test_homebase_engine_refuses_container_of_other_vaults(tmp_path: Path) -> None:
    nested = tmp_path / "NestedVault"
    _mark_vault(nested)
    cfg = HomebaseSyncConfig(
        vault_root=tmp_path,
        vault_id="vault-id",
        device_id="device-id",
        remote_url="https://homebase.invalid",
        verify_ssl=True,
        auth_token="",
        passphrase="passphrase",
    )

    with pytest.raises(NestedVaultError):
        HomebaseSyncEngine(cfg)


def test_file_adapter_hides_and_blocks_nested_vault(tmp_path: Path) -> None:
    ordinary = tmp_path / "Ordinary"
    ordinary.mkdir()
    (ordinary / "Ordinary.md").write_text("# Ordinary", encoding="utf-8")
    nested = tmp_path / "NestedVault"
    _mark_vault(nested)
    nested_page = nested / "Private" / "Private.md"
    nested_page.parent.mkdir(parents=True)
    nested_page.write_text("# Private", encoding="utf-8")

    tree = list_dir(tmp_path)
    child_names = {child["name"] for child in tree[0]["children"]}

    assert child_names == {"Ordinary"}
    assert path_crosses_nested_vault(tmp_path, nested_page)
    with pytest.raises(FileAccessError, match="separate nested vault"):
        read_file(tmp_path, "/NestedVault/Private/Private.md")


def test_main_window_rejects_container_before_stopping_current_vault(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    first = tmp_path / "FirstVault"
    second = tmp_path / "SecondVault"
    _mark_vault(first)
    _mark_vault(second)
    sync_shutdowns = []
    warnings = []

    monkeypatch.setattr(
        main_window,
        "_shutdown_homebase_sync",
        lambda: sync_shutdowns.append(True),
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.QMessageBox.critical",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    assert main_window._set_vault(str(tmp_path)) is False
    assert sync_shutdowns == []
    assert warnings and warnings[0][0] == "Choose an Exact Vault Folder"
    assert "No files, index, or Homebase sync state were changed" in warnings[0][1]
