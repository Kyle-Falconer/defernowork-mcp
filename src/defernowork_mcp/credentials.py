"""Persistent credential storage for defernowork-mcp.

Credentials are saved to ``~/.config/defernowork/credentials.json`` with
mode 0o600 so only the owning user can read them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CRED_DIR = Path.home() / ".config" / "defernowork"
_CRED_PATH = _CRED_DIR / "credentials.json"


def load_credentials() -> dict[str, Any] | None:
    """Return saved credentials dict, or None if absent or unreadable."""
    try:
        with _CRED_PATH.open() as f:
            data = json.load(f)
        if not isinstance(data, dict) or "token" not in data:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_credentials(token: str, username: str, base_url: str) -> None:
    """Write credentials to disk, creating the directory if needed."""
    _CRED_DIR.mkdir(parents=True, exist_ok=True)
    data = {"token": token, "username": username, "base_url": base_url}
    with _CRED_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    os.chmod(_CRED_PATH, 0o600)


def clear_credentials() -> None:
    """Delete saved credentials (best-effort)."""
    try:
        _CRED_PATH.unlink()
    except FileNotFoundError:
        pass
