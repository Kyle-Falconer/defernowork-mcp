"""Persistent credential storage for defernowork-mcp.

Credentials are saved to ``~/.config/defernowork/credentials.json`` with
mode 0o600 so only the owning user can read them.

When multiple Deferno accounts are used on the same machine, each gets
its own file: ``credentials-<username>.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CRED_DIR = Path.home() / ".config" / "defernowork"
# Legacy path — checked for backward compat, never written to.
_LEGACY_CRED_PATH = _CRED_DIR / "credentials.json"


def _cred_path(username: str | None = None) -> Path:
    """Return the credential file path for a given username."""
    if username:
        safe = username.replace("/", "_").replace("\\", "_")
        return _CRED_DIR / f"credentials-{safe}.json"
    return _LEGACY_CRED_PATH


def load_credentials() -> dict[str, Any] | None:
    """Return saved credentials dict, or None if absent or unreadable.

    Checks per-user files first (glob), then falls back to the legacy
    single-file path for backward compatibility.
    """
    try:
        # Look for any per-user credential file.
        _CRED_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(_CRED_DIR.glob("credentials-*.json")):
            try:
                with path.open() as f:
                    data = json.load(f)
                if isinstance(data, dict) and "token" in data:
                    return data
            except (json.JSONDecodeError, OSError):
                continue

        # Legacy fallback.
        if _LEGACY_CRED_PATH.exists():
            with _LEGACY_CRED_PATH.open() as f:
                data = json.load(f)
            if isinstance(data, dict) and "token" in data:
                return data
    except (FileNotFoundError, OSError):
        pass
    return None


def save_credentials(token: str, username: str, base_url: str) -> None:
    """Write credentials to a per-user file, creating the directory if needed."""
    _CRED_DIR.mkdir(parents=True, exist_ok=True)
    path = _cred_path(username)
    data = {"token": token, "username": username, "base_url": base_url}
    with path.open("w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)

    # Remove legacy file if it exists to avoid confusion.
    if _LEGACY_CRED_PATH.exists() and path != _LEGACY_CRED_PATH:
        try:
            _LEGACY_CRED_PATH.unlink()
        except OSError:
            pass


def clear_credentials() -> None:
    """Delete all saved credential files (best-effort)."""
    try:
        for path in _CRED_DIR.glob("credentials*.json"):
            path.unlink()
    except (FileNotFoundError, OSError):
        pass
