v1 spec

StillPoint Vault + Remote Models (v1)

1. Goals

1. StillPoint is **local-first**: all editing, rendering, search, graph, etc. operate on the **local vault**.
2. Support two remote concepts:

   • **Plain Remote** (legacy/compat): remote is a readable/plaintext vault; server can do “vault operations.”
   • **Homebase Remote** (new): remote is a **sync homebase** with **encrypted blobs at rest**; multiple clients sync independently; **eventual consistency**.
3. Homebase Remote must work well with:

   • intermittent connectivity
   • multi-device clients
   • conflict-safe behavior (never silently lose data)

2. Non-goals (v1)

• No server-side rendering/search/graph for Homebase Remote.
• No multi-user sharing/permissions model beyond “who has the token can sync.”
• No encrypted local vault (local-at-rest encryption is v2+).
• No fancy 3-way merges; conflicts become copy files.

3. Terminology

• **Vault (Local)**: the user’s working copy on disk (plaintext v1).
• **Plain Remote**: remote endpoint where the server stores a readable vault (plaintext).
• **Homebase Remote**: remote endpoint that stores encrypted blobs + sync metadata only.
• **Object**: a content-addressed blob representing a file version.
• **Manifest**: mapping of `path -> file metadata + object id`.
• **Checkpoint**: a named immutable snapshot of a manifest (commit-like).
• **Cursor**: server-issued pointer for incremental sync (changes feed).

4. Vault Types and Configuration

Preferences:
In Vault preferences there should be a setup for Homebase vault configs.
This shoud have a server URL, and a shared encryption key (this can be plugged into mulitple clients and later the mobile pwa).
If this is configured, the push / pull logic is enabled as well as the auto sync functionality.
logging for all homebase vaults will be wrapped it its own log category and added to the conditional logging that is in place.  SP_HOMEBASE_*
logging MUST be very human readable and clear when the client sync state machine operations are running.  similar to git push/pulls but less nerdy.  understanding when it's pushing, pulling, detecting the differences, etc should be clearly and susinctly logged if logging category is turned on.

Configs

Each local vault has:

• `vault_id` (UUID)
• `vault_root_path` (local filesystem)
• `remote_mode` enum:

  • `none`
  • `plain_remote`
  • `homebase_remote`
• `remote_url`
• `auth` (token)
• `device_id` (stable per client install)
• `sync_policy`:

  • auto sync: on/off, this should sync on load/save as well.
  • interval seconds (default 60)
  • push debounce seconds (default 2–5)
  • max parallel transfers (default 4–8)

5. Local Vault Storage (v1)

Local vault is plaintext on disk:

```
VaultRoot/
  Journal/
    2026-02-08.md
  Notes/
    idea.md
  Attachments/
    img.png
  .stillpoint/
    vault.json
    sync/
      local_state.json
      last_scan.json
      conflict_log.json
```

`.stillpoint/vault.json`

Contains vault identity, remote config, etc.

`.stillpoint/sync/local_state.json`

Stores:

• last successful sync cursor for homebase remote
• last pulled checkpoint id
• last pushed checkpoint id
• device_id

6. Plain Remote (legacy mode)

Summary

Plain Remote behaves like your current remote server:

• Remote stores plaintext files.
• Server may support “live” content APIs (render/search/graph).
• Sync is optional; can be “direct remote editing” depending on your current implementation.

Compatibility requirement (v1)

This mode remains supported unchanged.

7. Homebase Remote (new v1)

Summary

Homebase Remote is a **Git-like sync homebase**:

• Remote stores **encrypted** file blobs at rest.
• Clients operate on local plaintext vault.
• Clients sync changes to/from homebase.
• Server never needs to interpret plaintext vault content.

Security model (v1)

• Encryption boundary is **client → server storage**.
• Server stores only encrypted blobs and sync metadata.
• v1 does not attempt to hide filenames/paths from the server (manifest can be plaintext). (You can make manifests encrypted in v2 without changing the object store.)

8. Homebase Remote Data Model

8.1 Object (encrypted blob)

• `object_id = sha256(ciphertext_bytes)` (content-addressed)
• Stored as:

  • `objects/<object_id>` = ciphertext bytes

Object represents a single file content version (file bytes).

8.2 Manifest

A manifest is a JSON (or msgpack) document:

```json
{
  "vault_id": "…",
  "created_at": "…",
  "device_id": "…",
  "entries": {
    "Journal/2026-02-08.md": {
      "object_id": "sha256…",
      "size": 12345,
      "mtime": 1739030000,
      "mode": "file"
    },
    "Attachments/img.png": {
      "object_id": "sha256…",
      "size": 54321,
      "mtime": 1739030100,
      "mode": "file"
    }
  }
}
```

Manifest can be:

• plaintext (v1)
• encrypted (v2+)

8.3 Checkpoint

A checkpoint is an immutable pointer to a manifest:

• `checkpoint_id = sha256(manifest_bytes)` (or random UUID)
• Stored as:

  • `manifests/<checkpoint_id>` = manifest bytes (plaintext v1)
  • `checkpoints/<checkpoint_id>.json` = metadata:

    • parent checkpoint id (optional, can be omitted v1)
    • created_at
    • device_id

8.4 Changes Feed

Server provides changes since cursor:

• object uploads
• checkpoint updates
• deletion markers (optional v1)

9. Homebase Remote API (FastAPI)

Minimal endpoints:

Auth

• Bearer token in `Authorization: Bearer <token>`

Fetch changes

• `GET /v1/homebase/{vault_id}/changes?since=<cursor>`
  Returns:

```json
{
  "cursor": "new-cursor",
  "latest_checkpoint_id": "…",
  "required_objects": ["obj1", "obj2", "..."],
  "tombstones": ["path1", "..."]  // optional v1
}
```

Download object

• `GET /v1/homebase/{vault_id}/objects/{object_id}`
  Returns ciphertext bytes.

Upload object (idempotent)

• `PUT /v1/homebase/{vault_id}/objects/{object_id}`
  Body: ciphertext bytes
  Server may validate hash matches object_id.

Download manifest

• `GET /v1/homebase/{vault_id}/manifests/{checkpoint_id}`

Upload manifest

• `PUT /v1/homebase/{vault_id}/manifests/{checkpoint_id}`

Publish “latest checkpoint”

• `PUT /v1/homebase/{vault_id}/latest`
  Body:

```json
{ "checkpoint_id": "…" }
```

Server stores “latest” pointer for the vault.

10. Homebase Encryption (v1)

Inputs

• Vault encryption key is configured on client (per vault).
• v1: key can be derived from a passphrase or stored in OS keyring.
• this should be under 'vault options' 

Behavior

• Before upload: client encrypts file bytes → ciphertext
• After download: client decrypts ciphertext → file bytes

Constraints:

• Encryption must be AEAD (authenticated). (Implementation detail: AES-GCM or XChaCha20-Poly1305.)
• Nonce must be unique per object encryption (store nonce alongside ciphertext or in a header).

11. Sync Algorithm (Homebase Remote)

11.1 High-level: local is authoritative during editing

• UI reads/writes local files directly.
• Sync runs in background:

  • pull remote changes
  • merge into local
  • push local changes

11.2 Local scan (produce local manifest)

For each file under `VaultRoot/` excluding `.stillpoint/`:

• compute `plaintext_hash = sha256(file_bytes)` (or use mtime+size as a fast path + optional hash)
• track `path, size, mtime, plaintext_hash`

11.3 Push

If file differs from last pushed state:

1. encrypt bytes → ciphertext
2. compute `object_id = sha256(ciphertext)`
3. `PUT object` if missing
4. update manifest entry → object_id
   After all changed files:
5. create new manifest
6. compute checkpoint_id
7. `PUT manifest`
8. `PUT latest` = checkpoint_id

11.4 Pull

1. `GET changes?since=cursor`
2. if `latest_checkpoint_id` is newer than local known:

   • download manifest
   • for each entry, if object missing locally, download object
   • decrypt object → file bytes
   • merge into local filesystem using rules below
3. update local cursor

11.5 Merge rules (filesystem)

For each path in remote manifest:

• If local file does not exist → write remote content
• If local file exists and is identical → do nothing
• If local file exists and differs:

**Conflict policy (v1): create conflict copy**

• Keep local version as-is (since user is actively working locally)
• Write remote version as:

  • `<filename>.sync-conflict-<YYYYMMDD>-<HHMMSS>-<device_id>.<ext>`
    This naming pattern is consistent with how Syncthing keeps conflicts adjacent and non-destructive. ([Syncthing Community Forum][1])

(If you prefer, you can invert “keep remote and conflict-copy local,” but pick one deterministic rule.)

11.6 Deletes (optional v1)

v1 can treat deletes as “out of scope” (no delete propagation), OR:

• represent deletes as tombstones in changes feed
• when pulling, apply tombstone if local file not modified since last sync

Recommended v1: **do not propagate deletes automatically**. Provide “cleanup” UX later.

12. Background Sync Scheduling

• Default: auto sync every 60 seconds
• Push debounce: 2–5 seconds after last local write
• Manual “Sync Now” action
• Sync should never block typing/UI thread
• Show status:

  • last sync time
  • pending uploads/downloads
  • conflicts count

13. Failure and Recovery

• If network fails mid-sync:

  • leave local untouched
  • persist partial transfer state in `.stillpoint/sync/`
  • retry later

• If corruption detected (decrypt/auth fails):

  • quarantine object
  • surface error with object_id and path
  • do not overwrite local file

14. UX Rules (v1)

• Vault is always usable offline (local-first).
• Remote indicator is informational:

  • “Up to date”
  • “Syncing…”
  • “Offline (changes pending)”
  • “Conflicts: N”
• Conflicts are visible in a dedicated panel; conflict files are also written to disk (transparent and user-controllable).

15. Feature Matrix (v1)

| Feature                           |        Local |          Plain Remote |      Homebase Remote |
| --------------------------------- | -----------: | --------------------: | -------------------: |
| Full render/search/graph          |            ✅ |        ✅ (server can) |       ✅ (local only) |
| Remote stores plaintext           |          n/a |                     ✅ |                    ❌ |
| Remote encrypted at rest          |     optional | ❌ (unless disk-level) |        ✅ (by design) |
| Multi-device eventual consistency | ✅ (via sync) |               depends |                    ✅ |
| Conflict-safe                     |            ✅ |               depends | ✅ (copy-on-conflict) |

---

Recommended v1 Implementation Order

1. Keep Plain Remote unchanged.
2. Implement Homebase Remote server endpoints (objects, manifests, changes, latest).
3. Implement client sync engine:

   • local scan
   • push (encrypt+upload+checkpoint)
   • pull (download+decrypt+merge+conflicts)
4. Add status UI + conflict panel.

---
# on disk layout

---

# v1 JSON Schemas

## 1) `vault.json` (local vault config)

Path: `VaultRoot/.stillpoint/vault.json`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",
  "vault_name": "My Vault",
  "created_at": "2026-02-08T21:00:00Z",

  "remote": {
    "mode": "none | plain_remote | homebase_remote",
    "url": "https://example.com",
    "auth": {
      "type": "bearer",
      "token_ref": "keyring:stillpoint:vault:<vault_id>:remote_token"
    }
  },

  "device_id": "stable-device-id-string",

  "sync_policy": {
    "auto_sync": true,
    "interval_seconds": 60,
    "push_debounce_seconds": 3,
    "max_parallel_transfers": 6
  },

  "crypto": {
    "homebase_enabled": false,
    "cipher": "xchacha20-poly1305",
    "kdf": "argon2id",
    "key_ref": "keyring:stillpoint:vault:<vault_id>:homebase_key"
  }
}
```

Notes:

* `token_ref` and `key_ref` are **opaque strings** that your app resolves from OS keyring (or a fallback file) — v1 can implement keyring later, but spec supports it.
* For v1, `crypto.homebase_enabled` can be implied by `remote.mode == homebase_remote`.

---

## 2) `local_state.json` (sync state)

Path: `VaultRoot/.stillpoint/sync/local_state.json`

```json
{
  "schema_version": 1,

  "vault_id": "uuid-string",
  "device_id": "stable-device-id-string",

  "remote_mode": "none | plain_remote | homebase_remote",

  "homebase": {
    "last_seen_latest_checkpoint_id": null,
    "last_pulled_checkpoint_id": null,
    "last_pushed_checkpoint_id": null,

    "last_sync_at": null,

    "last_error": null,
    "error_count": 0,
    "backoff_until": null
  },

  "local": {
    "last_scan_at": null,
    "last_scan_root_mtime": null,
    "dirty_paths": []
  }
}
```

Notes:

* `dirty_paths` is optional; you can keep dirty tracking purely in memory. But it’s handy for crash recovery.

---

## 3) `latest.json` (server ref pointer)

Server path: `vaults/<vault_id>/refs/latest.json`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",

  "checkpoint_id": "hex-string-or-uuid",
  "updated_at": "2026-02-08T21:14:12Z",
  "updated_by_device_id": "device-id"
}
```

---

## 4) `checkpoint.json` (server checkpoint metadata)

Server path: `vaults/<vault_id>/checkpoints/<checkpoint_id>.json`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",
  "checkpoint_id": "hex-string-or-uuid",
  "manifest_id": "hex-string-or-uuid",

  "created_at": "2026-02-08T21:14:00Z",
  "device_id": "device-id",

  "parent_checkpoint_id": null
}
```

Notes:

* `manifest_id` can equal `checkpoint_id` if you choose a 1:1 mapping.

---

## 5) `manifest.json` (server manifest bytes; plaintext v1)

Server path: `vaults/<vault_id>/manifests/<prefix2>/<manifest_id>`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",
  "created_at": "2026-02-08T21:14:00Z",
  "device_id": "device-id",

  "entries": {
    "Notes/idea.md": {
      "object_id": "hex-sha256-of-ciphertext",
      "size": 1234,
      "mtime": 1739058840,
      "kind": "file"
    },
    "Attachments/img.png": {
      "object_id": "hex-sha256-of-ciphertext",
      "size": 54321,
      "mtime": 1739058900,
      "kind": "file"
    }
  }
}
```

Constraints:

* Paths are relative, forward-slash normalized.
* `.stillpoint/` is excluded.

---

## 6) `conflict_log.json` (local conflict tracking)

Path: `VaultRoot/.stillpoint/sync/conflict_log.json`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",
  "conflicts": [
    {
      "ts": "2026-02-08T21:14:12Z",
      "path": "Notes/idea.md",
      "conflict_copy_path": "Notes/idea.sync-conflict-20260208-211412-deviceB.md",
      "remote_checkpoint_id": "…",
      "remote_device_id": "deviceB"
    }
  ]
}
```

---

# Sync Module Layout (v1)

Suggested package layout:

```
sp/
  sync/
    __init__.py
    engine.py               # orchestrator, state machine, scheduling
    model.py                # dataclasses for schemas
    local_fs.py             # scanning, hashing, file read/write, conflict copy
    homebase_client.py      # HTTP client for objects/manifests/latest
    crypto.py               # encrypt/decrypt primitives, key loading
    util.py                 # path normalization, time, small helpers
```

---

# Pseudo Code: Core Concepts

## `model.py` (dataclasses)

```python
# sp/sync/model.py

from dataclasses import dataclass
from typing import Optional, Dict, List

@dataclass
class VaultRemote:
    mode: str  # none|plain_remote|homebase_remote
    url: str
    token_ref: str

@dataclass
class VaultConfig:
    schema_version: int
    vault_id: str
    vault_root_path: str
    device_id: str
    remote: VaultRemote
    sync_interval_seconds: int
    push_debounce_seconds: int
    max_parallel_transfers: int
    homebase_key_ref: Optional[str] = None

@dataclass
class LocalStateHomebase:
    last_seen_latest_checkpoint_id: Optional[str]
    last_pulled_checkpoint_id: Optional[str]
    last_pushed_checkpoint_id: Optional[str]
    last_sync_at: Optional[str]
    last_error: Optional[str]
    error_count: int
    backoff_until: Optional[str]

@dataclass
class LocalState:
    schema_version: int
    vault_id: str
    device_id: str
    remote_mode: str
    homebase: LocalStateHomebase
    dirty_paths: List[str]
```

---

# `local_fs.py` (scan + merge + conflicts)

```python
# sp/sync/local_fs.py

import os, time, hashlib
from typing import Dict, Tuple, Optional

EXCLUDE_DIRS = {".stillpoint"}

def iter_files(vault_root: str):
    for root, dirs, files in os.walk(vault_root):
        # exclude .stillpoint and other ignored dirs
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, vault_root).replace("\\", "/")
            if rel.startswith(".stillpoint/"):
                continue
            yield rel, full

def stat_file(full_path: str) -> Tuple[int, int]:
    st = os.stat(full_path)
    size = int(st.st_size)
    mtime = int(st.st_mtime)
    return size, mtime

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def read_bytes(full_path: str) -> bytes:
    with open(full_path, "rb") as f:
        return f.read()

def write_bytes_atomic(full_path: str, data: bytes):
    tmp = full_path + ".tmp"
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, full_path)

def conflict_copy_path(rel_path: str, device_id: str, ts: Optional[int] = None) -> str:
    ts = ts or int(time.time())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ts))
    base, ext = os.path.splitext(rel_path)
    return f"{base}.sync-conflict-{stamp}-{device_id}{ext}"

def bytes_equal(a: bytes, b: bytes) -> bool:
    # fast path can be hash compare; v1 simple
    return a == b
```

---

# `crypto.py` (v1 encryption wrapper)

This is pseudo code; Codex can implement using `cryptography`.

```python
# sp/sync/crypto.py

from dataclasses import dataclass
from typing import Tuple

@dataclass
class CiphertextBlob:
    # Simple envelope so nonce/tag are carried with ciphertext
    # For v1 you can use: header JSON + raw bytes, or a binary format.
    nonce: bytes
    ciphertext: bytes

def load_homebase_key(key_ref: str) -> bytes:
    """
    Resolve key_ref from OS keyring (preferred) or local secure storage.
    v1 implementation can stub with an env var or config file for dev.
    """
    raise NotImplementedError

def encrypt_bytes(key: bytes, plaintext: bytes) -> bytes:
    """
    Return an encoded envelope containing nonce + ciphertext.
    Must be AEAD authenticated encryption.
    Output bytes must be deterministic only w.r.t plaintext? No—nonce random => output changes.
    """
    raise NotImplementedError

def decrypt_bytes(key: bytes, envelope: bytes) -> bytes:
    raise NotImplementedError

def object_id_from_ciphertext(ciphertext_envelope: bytes) -> str:
    import hashlib
    return hashlib.sha256(ciphertext_envelope).hexdigest()
```

---

# `homebase_client.py` (HTTP API)

```python
# sp/sync/homebase_client.py

import requests
from typing import Optional, Dict

class HomebaseClient:
    def __init__(self, base_url: str, token: str, vault_id: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.vault_id = vault_id
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.timeout = timeout

    def get_latest(self) -> Dict:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/latest"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def put_latest(self, checkpoint_id: str) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/latest"
        r = self.session.put(url, json={"checkpoint_id": checkpoint_id}, timeout=self.timeout)
        r.raise_for_status()

    def get_manifest(self, manifest_id: str) -> bytes:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def put_manifest(self, manifest_id: str, data: bytes) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}"
        r = self.session.put(url, data=data, timeout=self.timeout)
        r.raise_for_status()

    def has_object(self, object_id: str) -> bool:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        r = self.session.head(url, timeout=self.timeout)
        return r.status_code == 200

    def get_object(self, object_id: str) -> bytes:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def put_object(self, object_id: str, data: bytes) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        r = self.session.put(url, data=data, timeout=self.timeout)
        r.raise_for_status()
```

---

# `engine.py` (sync orchestrator pseudo code)

```python
# sp/sync/engine.py

import json, os, threading, time
from typing import Dict, Optional, Set

from sp.sync.local_fs import iter_files, stat_file, read_bytes, write_bytes_atomic, conflict_copy_path
from sp.sync.crypto import load_homebase_key, encrypt_bytes, decrypt_bytes, object_id_from_ciphertext
from sp.sync.homebase_client import HomebaseClient

SYNC_LOCKS = {}  # vault_id -> threading.Lock

def _lock_for(vault_id: str) -> threading.Lock:
    SYNC_LOCKS.setdefault(vault_id, threading.Lock())
    return SYNC_LOCKS[vault_id]

def load_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def build_local_manifest_plain(vault_root: str, vault_id: str, device_id: str) -> Dict:
    entries = {}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for rel, full in iter_files(vault_root):
        size, mtime = stat_file(full)
        # object_id is not known until we encrypt; store local meta only.
        entries[rel] = {
            "size": size,
            "mtime": mtime,
            "kind": "file"
        }
    return {
        "schema_version": 1,
        "vault_id": vault_id,
        "created_at": now_iso,
        "device_id": device_id,
        "entries": entries
    }

def encrypt_and_stage_objects(vault_root: str, key: bytes, local_manifest: Dict) -> Dict:
    """
    Produce a remote-ready manifest where each entry has object_id.
    Returns:
      - remote_manifest (schema_version 1)
      - objects_to_upload: dict object_id -> ciphertext_envelope bytes
    """
    remote_entries = {}
    objects_to_upload = {}

    for rel, meta in local_manifest["entries"].items():
        full = os.path.join(vault_root, rel)
        plaintext = read_bytes(full)
        envelope = encrypt_bytes(key, plaintext)  # bytes
        object_id = object_id_from_ciphertext(envelope)
        objects_to_upload[object_id] = envelope

        remote_entries[rel] = {
            "object_id": object_id,
            "size": meta["size"],
            "mtime": meta["mtime"],
            "kind": "file"
        }

    remote_manifest = dict(local_manifest)
    remote_manifest["entries"] = remote_entries
    return remote_manifest, objects_to_upload

def manifest_id_from_bytes(manifest_bytes: bytes) -> str:
    import hashlib
    return hashlib.sha256(manifest_bytes).hexdigest()

def sync_homebase_once(vault_root: str, vault_cfg: Dict) -> None:
    vault_id = vault_cfg["vault_id"]
    device_id = vault_cfg["device_id"]
    remote = vault_cfg["remote"]
    if remote["mode"] != "homebase_remote":
        return

    lock = _lock_for(vault_id)
    if not lock.acquire(blocking=False):
        return
    try:
        # Load state
        state_path = os.path.join(vault_root, ".stillpoint/sync/local_state.json")
        state = load_json(state_path) or {
            "schema_version": 1,
            "vault_id": vault_id,
            "device_id": device_id,
            "remote_mode": "homebase_remote",
            "homebase": {
                "last_seen_latest_checkpoint_id": None,
                "last_pulled_checkpoint_id": None,
                "last_pushed_checkpoint_id": None,
                "last_sync_at": None,
                "last_error": None,
                "error_count": 0,
                "backoff_until": None
            },
            "local": {"dirty_paths": []}
        }

        token = resolve_token(remote["auth"]["token_ref"])
        client = HomebaseClient(remote["url"], token=token, vault_id=vault_id)
        key = resolve_homebase_key(vault_cfg)

        # ---- PULL PHASE ----
        latest = client.get_latest()  # {checkpoint_id,...}
        remote_head = latest.get("checkpoint_id")
        local_seen = state["homebase"]["last_seen_latest_checkpoint_id"]

        if remote_head and remote_head != local_seen:
            apply_remote_checkpoint(
                client=client,
                key=key,
                vault_root=vault_root,
                remote_checkpoint_id=remote_head,
                local_device_id=device_id
            )
            state["homebase"]["last_seen_latest_checkpoint_id"] = remote_head
            state["homebase"]["last_pulled_checkpoint_id"] = remote_head

        # ---- PUSH PHASE ----
        # v1: always scan; optimize later with dirty tracking
        local_manifest = build_local_manifest_plain(vault_root, vault_id, device_id)
        remote_manifest, objects_to_upload = encrypt_and_stage_objects(vault_root, key, local_manifest)

        # Serialize manifest bytes
        manifest_bytes = json.dumps(remote_manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
        manifest_id = manifest_id_from_bytes(manifest_bytes)
        checkpoint_id = manifest_id  # v1: 1:1 mapping

        # Upload objects (idempotent)
        for object_id, blob in objects_to_upload.items():
            if not client.has_object(object_id):
                client.put_object(object_id, blob)

        # Upload manifest + publish latest
        client.put_manifest(manifest_id, manifest_bytes)
        client.put_latest(checkpoint_id)

        state["homebase"]["last_pushed_checkpoint_id"] = checkpoint_id
        state["homebase"]["last_seen_latest_checkpoint_id"] = checkpoint_id
        state["homebase"]["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state["homebase"]["last_error"] = None
        state["homebase"]["error_count"] = 0
        state["homebase"]["backoff_until"] = None

        save_json(state_path, state)

    except Exception as exc:
        # Update backoff in local state
        _record_sync_error(vault_root, vault_id, device_id, exc)
        raise
    finally:
        lock.release()

def apply_remote_checkpoint(client: HomebaseClient, key: bytes, vault_root: str,
                           remote_checkpoint_id: str, local_device_id: str) -> None:
    # v1: checkpoint_id == manifest_id
    manifest_bytes = client.get_manifest(remote_checkpoint_id)
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    entries = manifest.get("entries", {})

    for rel, meta in entries.items():
        obj_id = meta["object_id"]
        ciphertext = client.get_object(obj_id)
        plaintext = decrypt_bytes(key, ciphertext)

        full = os.path.join(vault_root, rel)

        if not os.path.exists(full):
            write_bytes_atomic(full, plaintext)
            continue

        local_bytes = read_bytes(full)
        if local_bytes == plaintext:
            continue

        # conflict: keep local, write remote to conflict copy
        conflict_rel = conflict_copy_path(rel, device_id=manifest.get("device_id", "remote"))
        conflict_full = os.path.join(vault_root, conflict_rel)
        write_bytes_atomic(conflict_full, plaintext)
        record_conflict(vault_root, rel, conflict_rel, remote_checkpoint_id, manifest.get("device_id"))

def resolve_token(token_ref: str) -> str:
    """
    v1 stub: read from env or local config.
    later: OS keyring.
    """
    # example: token_ref = "env:STILLPOINT_REMOTE_TOKEN"
    raise NotImplementedError

def resolve_homebase_key(vault_cfg: Dict) -> bytes:
    key_ref = vault_cfg.get("crypto", {}).get("key_ref")
    return load_homebase_key(key_ref)

def record_conflict(vault_root: str, path: str, conflict_copy: str,
                    remote_checkpoint_id: str, remote_device_id: Optional[str]) -> None:
    log_path = os.path.join(vault_root, ".stillpoint/sync/conflict_log.json")
    log = load_json(log_path) or {"schema_version": 1, "vault_id": "", "conflicts": []}
    log["conflicts"].append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "path": path,
        "conflict_copy_path": conflict_copy,
        "remote_checkpoint_id": remote_checkpoint_id,
        "remote_device_id": remote_device_id
    })
    save_json(log_path, log)

def _record_sync_error(vault_root: str, vault_id: str, device_id: str, exc: Exception) -> None:
    state_path = os.path.join(vault_root, ".stillpoint/sync/local_state.json")
    state = load_json(state_path) or {}
    hb = state.setdefault("homebase", {})
    hb["last_error"] = repr(exc)
    hb["error_count"] = int(hb.get("error_count", 0)) + 1
    # exponential backoff capped
    delay = min(300, 2 ** min(8, hb["error_count"]))  # max 5 minutes
    hb["backoff_until"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay))
    save_json(state_path, state)
```

### Notes on the pseudo code

* It’s intentionally straight-line “Codex implementable.”
* v1 simplifies:

  * always scans + re-uploads (idempotent) — acceptable for small vaults; optimize later using dirty tracking + per-path hash caches.
  * checkpoint_id == manifest_id == sha256(manifest_bytes)
* You’ll likely add:

  * “skip push if no local changes since last push”
  * “don’t download objects already cached locally”
  * “parallel upload/download”

---

# Optional: Client-side object cache (recommended even in v1)

If you want to avoid re-downloading objects:

* store ciphertext blobs locally in:

  * `VaultRoot/.stillpoint/sync/cache/objects/<prefix2>/<object_id>`
* then `apply_remote_checkpoint` checks cache first.

This makes remote feel “instant” after first sync.

---

# Plain Remote Sync Hook (v1)

In `engine.py`, you keep your existing behavior:

```python
def sync_plain_remote_once(...):
    # existing implementation
    pass
```

And route based on `remote.mode`.

---

## Additions: Local per-path fingerprint cache (v1)

### Purpose

Add a **local fingerprint cache** so the sync engine can quickly detect unchanged files and **skip re-encrypting / re-uploading** them.

Fingerprint = `(size, mtime, plaintext_sha256)` per path.

---

## On-disk layout additions

Add one file under the vault:

```
VaultRoot/
  .stillpoint/
    sync/
      fingerprints.json
```

---

## `fingerprints.json` schema (v1)

Path: `VaultRoot/.stillpoint/sync/fingerprints.json`

```json
{
  "schema_version": 1,
  "vault_id": "uuid-string",
  "device_id": "device-id",
  "updated_at": "2026-02-08T21:14:12Z",
  "entries": {
    "Notes/idea.md": {
      "size": 1234,
      "mtime": 1739058840,
      "plaintext_sha256": "hex-sha256-of-plaintext"
    },
    "Attachments/img.png": {
      "size": 54321,
      "mtime": 1739058900,
      "plaintext_sha256": "hex-sha256-of-plaintext"
    }
  }
}
```

Rules:

* Paths are relative, forward-slash normalized.
* Exclude `.stillpoint/**`.

---

## Sync engine behavioral additions

### During scan (push path)

For each file:

1. Get `(size, mtime)` from filesystem.
2. Look up existing cache entry.
3. If cache entry exists and `(size, mtime)` match:

   * Treat as **unchanged**
   * Reuse previous known `plaintext_sha256` (do not re-hash file bytes)
4. Else:

   * Read file bytes
   * Compute `plaintext_sha256`
   * Update cache entry

### Use fingerprints to skip encryption/uploads

Maintain a second mapping (in memory or persisted) from `plaintext_sha256 -> object_id` from the last successful push (v1 can do in-memory first; persistence optional).

If `(path plaintext_sha256)` is unchanged since last push, then:

* **do not** re-encrypt
* **do not** upload object
* Keep manifest entry’s `object_id` as previously pushed for that path

---

## Minimal code hooks (pseudo additions)

### Load/save helpers

* Load `fingerprints.json` at sync start
* Save it after scan/push completes

### Scan function signature change

* `build_local_manifest_plain(...)` should return:

  * local manifest entries
  * updated fingerprints cache
  * list of changed paths

### Encrypt stage change

* `encrypt_and_stage_objects(...)` should accept:

  * `changed_paths` only
  * `prior_remote_manifest` (or prior mapping path->object_id) to reuse `object_id` for unchanged paths
