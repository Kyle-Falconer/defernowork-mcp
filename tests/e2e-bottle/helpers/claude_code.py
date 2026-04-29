"""Subprocess wrapper around `claude -p`, with the wrapper-string contract.

The Phase 2 spike picks ONE of two extractor strategies:
  - Outcome A: assistant final message in envelope.result. Default.
  - Outcome B: stream-json events. Set EXTRACTOR = _extract_from_streaming_events
    and OUTPUT_FORMAT = "stream-json".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# From Phase 2 spike — adjust if Claude Code's credential file lives elsewhere
CREDENTIAL_FILE = Path(os.environ.get("HOME", "/root")) / ".claude" / "mcp_credentials.json"

WRAPPER_RE = re.compile(
    r"<<<DEFERNO_E2E_BEGIN>>>\s*(.*?)\s*<<<DEFERNO_E2E_END>>>",
    re.DOTALL,
)


class ModelFormatDeviation(Exception):
    """Claude returned output that did not match the wrapper-string contract."""


@dataclass
class ToolCallResult:
    raw_envelope: Any
    parsed: dict[str, Any]


def parse_wrapper(text: str) -> dict[str, Any]:
    """Find exactly one DEFERNO_E2E wrapper pair in `text`, parse its inner JSON."""
    matches = WRAPPER_RE.findall(text)
    if len(matches) != 1:
        raise ModelFormatDeviation(
            f"Expected exactly one wrapper pair, got {len(matches)}. Raw: {text[:500]!r}"
        )
    try:
        return json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ModelFormatDeviation(
            f"Wrapper contents did not parse as JSON: {exc}. Contents: {matches[0][:500]!r}"
        ) from exc


def _extract_from_result_field(envelope_text: str) -> tuple[Any, str]:
    """Spike outcome A: final message lives in envelope['result']."""
    envelope = json.loads(envelope_text)
    return envelope, envelope.get("result", "")


def _extract_from_streaming_events(envelope_text: str) -> tuple[Any, str]:
    """Spike outcome B: stream-json output; concatenate text events."""
    events: list[dict] = []
    for line in envelope_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    chunks: list[str] = []
    for ev in events:
        if (ev.get("type") == "content_block_delta"
                and ev.get("delta", {}).get("type") == "text_delta"):
            chunks.append(ev["delta"]["text"])
        elif ev.get("type") == "tool_result" and isinstance(ev.get("content"), str):
            chunks.append(ev["content"])
    return events, "".join(chunks)


# Set per Phase 2 spike outcome
EXTRACTOR: Callable[[str], tuple[Any, str]] = _extract_from_result_field
OUTPUT_FORMAT = "json"


def add_mcp_server(name: str, url: str) -> None:
    subprocess.run(
        ["claude", "mcp", "add", "--transport", "http", name, url],
        check=True,
    )


def run_prompt(prompt: str, timeout: int = 120) -> ToolCallResult:
    """Run `claude -p`, parse the wrapper out of the envelope. Raises on non-zero exit."""
    completed = subprocess.run(
        ["claude", "-p", prompt, "--output-format", OUTPUT_FORMAT],
        capture_output=True, text=True, timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {completed.returncode}: {completed.stderr[:1000]}"
        )

    envelope, assistant_text = EXTRACTOR(completed.stdout)
    return ToolCallResult(raw_envelope=envelope, parsed=parse_wrapper(assistant_text))


def clear_credential_file() -> None:
    """Per-test fixture calls this to force a fresh OAuth dance."""
    if CREDENTIAL_FILE.exists():
        CREDENTIAL_FILE.unlink()


def credential_file_has_bearer(server_name: str = "deferno") -> bool:
    """Probe used by test_self.py to verify the Phase 2 spike's credential path is valid."""
    if not CREDENTIAL_FILE.exists():
        return False
    try:
        data = json.loads(CREDENTIAL_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("mcpServers", {}).get(server_name, {}).get("bearer"))


WRAPPER_PROMPT_TEMPLATE = """\
{user_prompt}

Output the tool's raw JSON response wrapped EXACTLY between the markers
shown, on their own lines, with no commentary before, between, or after:

<<<DEFERNO_E2E_BEGIN>>>
<the tool's raw JSON, no surrounding prose>
<<<DEFERNO_E2E_END>>>
"""


def whoami_prompt(server_name: str = "deferno") -> str:
    return WRAPPER_PROMPT_TEMPLATE.format(
        user_prompt=f"Call the {server_name} whoami tool."
    )


def logout_prompt(server_name: str = "deferno") -> str:
    return WRAPPER_PROMPT_TEMPLATE.format(
        user_prompt=f"Call the {server_name} logout tool."
    )
