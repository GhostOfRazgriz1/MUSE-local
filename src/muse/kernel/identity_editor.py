"""Inline identity editing — lets the user change agent identity mid-session."""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from muse.config import Config

logger = logging.getLogger(__name__)

SKILL_ID = "change_identity"
SKILL_NAME = "Change Identity"
SKILL_DESCRIPTION = (
    "Change the agent's identity: rename the agent, change what it calls the user, "
    "adjust personality or communication style, update the session greeting, "
    "or modify any aspect of how the agent behaves and communicates."
)

_IDENTITY_START = "<<<IDENTITY>>>"
_IDENTITY_END = "<<<END_IDENTITY>>>"

_SYSTEM = f"""\
You edit the agent's identity file. The user wants to change something.

Current identity:
---
{{current_identity}}
---

Steps:
1. Figure out what the user wants changed.
2. Confirm the change briefly.
3. Output the COMPLETE updated file between these delimiters:

{_IDENTITY_START}
(full updated identity.md here)
{_IDENTITY_END}

Rules:
- Only change what was asked. Keep everything else the same.
- Never remove or weaken Principles or Boundaries sections.
- If you need info, ask ONE short question. Otherwise make the change immediately.
- Output one cohesive message with the identity block included."""


async def handle_identity_edit(
    user_message: str,
    current_identity: str,
    provider,
    model: str,
    config: Config,
) -> AsyncIterator[dict]:
    """Handle an identity change request inline."""
    system = _SYSTEM.replace("{current_identity}", current_identity)

    result = await provider.complete(
        model=model,
        messages=[{"role": "user", "content": user_message}],
        system=system,
        max_tokens=1500,
    )
    reply = result.text.strip()

    # Extract and write the updated identity
    new_identity = _extract_identity(reply)
    if new_identity:
        from muse.kernel.context_assembly import validate_identity
        new_identity = validate_identity(new_identity)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.identity_path.write_text(new_identity, encoding="utf-8")
        logger.info("Identity updated at %s", config.identity_path)

    # Strip the raw block from the displayed message
    display = _strip_identity_block(reply)

    yield {
        "type": "response",
        "content": display,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model_used,
    }


def _extract_identity(text: str) -> str | None:
    pattern = re.escape(_IDENTITY_START) + r"\s*\n(.*?)\n\s*" + re.escape(_IDENTITY_END)
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"
    return None


def _strip_identity_block(text: str) -> str:
    pattern = re.escape(_IDENTITY_START) + r".*?" + re.escape(_IDENTITY_END)
    cleaned = re.sub(pattern, "", text, flags=re.DOTALL).strip()
    return cleaned
