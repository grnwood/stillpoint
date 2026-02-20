from argon2 import PasswordHasher
import hashlib
import json
from datetime import datetime, UTC

vault_password = "your password"
server_admin_password = "your server password"
  
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

combined = f"{vault_password}:{server_admin_password}"

payload = {
    "username": "grnwood",
    "password_hash": ph.hash(combined),
    "vault_password_hash": ph.hash(vault_password),
    "server_password_hash": hashlib.sha256(server_admin_password.encode()).hexdigest(),
    "configured_at": datetime.now(UTC).isoformat(),
}

print(json.dumps(payload, indent=2))
