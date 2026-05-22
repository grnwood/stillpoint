from __future__ import annotations

import hashlib
from argon2.low_level import Type, hash_secret_raw
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)


_ENVELOPE_MAGIC = b"SPHB1"
_KEY_BYTES = 32
_NONCE_PERSONALIZATION = b"sphb-nonce-v1"


def derive_key_from_passphrase(passphrase: str, vault_id: str) -> bytes:
    if not passphrase:
        raise ValueError("Homebase passphrase is required")
    salt = hashlib.sha256(f"stillpoint-homebase:{vault_id}".encode("utf-8")).digest()[:16]
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=1,
        hash_len=_KEY_BYTES,
        type=Type.ID,
    )


def encrypt_bytes(key: bytes, plaintext: bytes) -> bytes:
    # Use a keyed deterministic nonce so repeated uploads of unchanged files
    # produce the same ciphertext envelope and therefore the same object id.
    # This intentionally reveals plaintext equality within a vault, which the
    # object store already exposes by deduping on object id.
    nonce = hashlib.blake2b(
        plaintext,
        digest_size=crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
        key=key,
        person=_NONCE_PERSONALIZATION,
    ).digest()
    ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, None, nonce, key)
    return _ENVELOPE_MAGIC + nonce + ciphertext


def decrypt_bytes(key: bytes, envelope: bytes) -> bytes:
    if not envelope.startswith(_ENVELOPE_MAGIC):
        raise ValueError("Invalid Homebase ciphertext envelope header")
    if len(envelope) < len(_ENVELOPE_MAGIC) + crypto_aead_xchacha20poly1305_ietf_NPUBBYTES:
        raise ValueError("Invalid Homebase ciphertext envelope length")
    nonce_start = len(_ENVELOPE_MAGIC)
    nonce_end = nonce_start + crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
    nonce = envelope[nonce_start:nonce_end]
    ciphertext = envelope[nonce_end:]
    return crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, None, nonce, key)


def object_id_from_ciphertext(ciphertext_envelope: bytes) -> str:
    return hashlib.sha256(ciphertext_envelope).hexdigest()
