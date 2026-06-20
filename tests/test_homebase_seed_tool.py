from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path

from sp.sync.crypto import decrypt_bytes, derive_key_from_passphrase


def _load_seed_module():
    module_path = Path(__file__).resolve().parent.parent / "tools" / "homebase-seed-vault.py"
    spec = importlib.util.spec_from_file_location("homebase_seed_vault", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_seed_creates_homebase_layout_from_plaintext_staging(tmp_path) -> None:
    module = _load_seed_module()
    vaults_root = tmp_path / "vaults"
    homebase_base = vaults_root / "homebase" / "vault-123"
    homebase_base.mkdir(parents=True, exist_ok=True)
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    page_path = page_dir / "Page.md"
    page_path.write_text("# Seeded\n\nhello from staging\n", encoding="utf-8")
    image_path = source_root / "Notes" / "paste_image_001.png"
    image_path.write_bytes(b"PNGDATA")

    result = module.seed_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        source_root=source_root,
        passphrase="secret",
        device_id="server-seed",
        overwrite_latest=True,
        vault_name="Seed Vault",
    )

    checkpoint_id = result["checkpoint_id"]
    latest = json.loads((homebase_base / "refs" / "latest.json").read_text(encoding="utf-8"))
    assert latest["checkpoint_id"] == checkpoint_id

    manifest_path = homebase_base / "manifests" / checkpoint_id[:2] / checkpoint_id
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["vault_id"] == "vault-123"
    assert manifest["device_id"] == "server-seed"
    assert sorted(manifest["entries"]) == ["Notes/Page/Page.md", "Notes/paste_image_001.png"]

    key = derive_key_from_passphrase("secret", "vault-123")
    entry = manifest["entries"]["Notes/Page/Page.md"]
    object_id = entry["object_id"]
    object_path = homebase_base / "objects" / object_id[:2] / object_id
    plaintext = decrypt_bytes(key, object_path.read_bytes())
    assert plaintext.decode("utf-8") == "# Seeded\n\nhello from staging\n"

    checkpoint_meta = json.loads((homebase_base / "checkpoints" / f"{checkpoint_id}.json").read_text(encoding="utf-8"))
    assert checkpoint_meta["manifest_id"] == checkpoint_id

    meta = json.loads((homebase_base / "meta.json").read_text(encoding="utf-8"))
    assert meta["vault_name"] == "Seed Vault"


def test_write_seed_requires_overwrite_flag_when_latest_exists(tmp_path) -> None:
    module = _load_seed_module()
    vaults_root = tmp_path / "vaults"
    homebase_base = vaults_root / "homebase" / "vault-123"
    (homebase_base / "refs").mkdir(parents=True, exist_ok=True)
    (homebase_base / "refs" / "latest.json").write_text(
        json.dumps({"checkpoint_id": "a" * 64}),
        encoding="utf-8",
    )
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    (page_dir / "Page.md").write_text("# Seeded\n", encoding="utf-8")

    try:
        module.seed_homebase_vault(
            vaults_root=vaults_root,
            vault_id="vault-123",
            source_root=source_root,
            passphrase="secret",
            device_id="server-seed",
            overwrite_latest=False,
        )
    except RuntimeError as exc:
        assert "overwrite-latest" in str(exc)
    else:
        raise AssertionError("Expected seeding to require --overwrite-latest when latest.json exists")


def test_dry_run_does_not_write_server_state(tmp_path) -> None:
    module = _load_seed_module()
    vaults_root = tmp_path / "vaults"
    homebase_base = vaults_root / "homebase" / "vault-123"
    homebase_base.mkdir(parents=True, exist_ok=True)
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    (page_dir / "Page.md").write_text("# Seeded\n", encoding="utf-8")

    result = module.seed_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        source_root=source_root,
        passphrase="secret",
        device_id="server-seed",
        overwrite_latest=True,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["files"] == 1
    assert not (homebase_base / "refs" / "latest.json").exists()
    assert not (homebase_base / "checkpoints").exists()


def test_create_homebase_vault_and_seed_companion_flow(tmp_path) -> None:
    module = _load_seed_module()
    vaults_root = tmp_path / "vaults"
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    (page_dir / "Page.md").write_text("# Seeded\n", encoding="utf-8")

    created = module.create_homebase_vault(
        vaults_root=vaults_root,
        username="alice",
        password="secret-password",
        vault_name="Seed Vault",
        vault_id="vault-abc",
    )
    seeded = module.seed_homebase_vault(
        vaults_root=vaults_root,
        vault_id=created["vault_id"],
        source_root=source_root,
        passphrase="shared-passphrase",
        device_id="server-seed",
        overwrite_latest=True,
        vault_name="Seed Vault",
    )

    assert created["vault_id"] == "vault-abc"
    auth_path = vaults_root / "homebase" / "vault-abc" / "auth" / "auth.json"
    assert auth_path.exists()
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert "alice" in auth["users"]
    assert seeded["files"] == 1


def test_progress_callback_reports_seed_phases(tmp_path) -> None:
    module = _load_seed_module()
    vaults_root = tmp_path / "vaults"
    homebase_base = vaults_root / "homebase" / "vault-123"
    homebase_base.mkdir(parents=True, exist_ok=True)
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    (page_dir / "Page.md").write_text("# Seeded\n", encoding="utf-8")

    messages: list[str] = []
    module.seed_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        source_root=source_root,
        passphrase="secret",
        device_id="server-seed",
        overwrite_latest=True,
        progress=messages.append,
    )

    assert any("Preparing Homebase seed" in message for message in messages)
    assert any("Encrypting Notes/Page/Page.md" in message for message in messages)
    assert any("Seed complete:" in message for message in messages)


def test_create_and_seed_script_emits_progress_to_stderr(tmp_path) -> None:
    module_path = Path(__file__).resolve().parent.parent / "tools" / "homebase-create-and-seed-vault.py"
    spec = importlib.util.spec_from_file_location("homebase_create_and_seed_vault", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    vaults_root = tmp_path / "vaults"
    source_root = tmp_path / "staging"
    page_dir = source_root / "Notes" / "Page"
    page_dir.mkdir(parents=True)
    (page_dir / "Page.md").write_text("# Seeded\n", encoding="utf-8")

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = module.main(
            [
                "--vaults-root",
                str(vaults_root),
                "--source",
                str(source_root),
                "--username",
                "alice",
                "--password",
                "secret-password",
                "--passphrase",
                "shared-passphrase",
                "--vault-name",
                "Seed Vault",
            ]
        )

    assert exit_code == 0
    progress_output = stderr.getvalue()
    assert "[HomebaseSeed] Creating Homebase vault" in progress_output
    assert "[HomebaseSeed] Seed complete:" in progress_output
