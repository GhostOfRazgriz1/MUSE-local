"""Notes skill — create, read, update, and search personal notes.

Each public async function maps to an action declared in manifest.json.
The orchestrator calls the specific function directly based on the
two-level classifier (skill → action).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


async def create(ctx) -> dict:
    """Save a new note."""
    instruction = ctx.brief.get("instruction", "")

    # ── Pipeline context: upstream results from earlier tasks ─────
    # When this skill runs as part of a chain (e.g., "search X then
    # save a note about the results"), incorporate upstream data.
    pipeline = ctx.brief.get("context", {}).get("pipeline_context", {})
    upstream_text = ""
    if pipeline:
        parts = []
        for key, val in sorted(pipeline.items()):
            if key.endswith("_result") and val:
                parts.append(str(val))
        if parts:
            upstream_text = "\n\n".join(parts)

    if upstream_text:
        prompt = (
            f"Extract a short title (max 5 words) and compose the note content "
            f"from this request and the provided context.\n"
            f"Respond with JSON: {{\"title\": \"...\", \"content\": \"...\"}}\n\n"
            f"Request: {instruction}\n\n"
            f"Context from previous steps:\n{upstream_text[:3000]}"
        )
    else:
        prompt = (
            f"Extract a short title (max 5 words) and the note content from this request.\n"
            f"Respond with JSON: {{\"title\": \"...\", \"content\": \"...\"}}\n\n"
            f"Request: {instruction}"
        )

    result = await ctx.llm.complete(
        prompt=prompt,
        system="You extract structured data from user requests. Respond only with valid JSON.",
    )

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        parsed = {"title": "untitled", "content": instruction}

    title = parsed.get("title", "untitled")
    content = parsed.get("content", instruction)
    now = datetime.now(timezone.utc).isoformat()

    key = f"note.{title.lower().replace(' ', '_')}"
    note_data = json.dumps({
        "title": title,
        "content": content,
        "created_at": now,
        "updated_at": now,
    })

    await ctx.memory.write(key, note_data, value_type="json")

    return {
        "payload": {"title": title, "key": key},
        "summary": f"Saved note: \"{title}\"",
        "success": True,
    }


async def list(ctx) -> dict:
    """List all notes."""
    keys = await ctx.memory.list_keys("note.")

    if not keys:
        return {
            "payload": {"notes": []},
            "summary": "You don't have any notes yet.",
            "success": True,
        }

    return {
        "payload": {"keys": keys},
        "summary": f"You have {len(keys)} notes:\n" + "\n".join(f"- {k}" for k in keys),
        "success": True,
    }


async def read(ctx) -> dict:
    """Read a specific note."""
    instruction = ctx.brief.get("instruction", "")

    keys = await ctx.memory.list_keys("note.")
    if not keys:
        return {"payload": None, "summary": "No notes found.", "success": True}

    results = await ctx.memory.search(instruction, limit=1)
    if results:
        try:
            note = json.loads(results[0].value)
            return {
                "payload": note,
                "summary": f"**{note.get('title', 'Note')}**\n\n{note.get('content', '')}",
                "success": True,
            }
        except (json.JSONDecodeError, AttributeError):
            return {
                "payload": {"content": results[0].value},
                "summary": results[0].value,
                "success": True,
            }

    return {"payload": None, "summary": "Note not found.", "success": True}


async def delete(ctx) -> dict:
    """Delete a note."""
    instruction = ctx.brief.get("instruction", "")

    results = await ctx.memory.search(instruction, limit=1)
    if results:
        await ctx.memory.delete(results[0].key)
        return {
            "payload": {"deleted": results[0].key},
            "summary": f"Deleted note: {results[0].key}",
            "success": True,
        }
    return {"payload": None, "summary": "Note not found.", "success": True}


async def search(ctx) -> dict:
    """Search notes by content."""
    instruction = ctx.brief.get("instruction", "")

    query = instruction
    for prefix in ["find", "search", "search for", "look for", "find notes about"]:
        if query.lower().startswith(prefix):
            query = query[len(prefix):].strip()

    results = await ctx.memory.search(query, limit=5)

    if not results:
        return {
            "payload": {"results": []},
            "summary": f"No notes found matching \"{query}\".",
            "success": True,
        }

    notes = []
    for entry in results:
        try:
            note = json.loads(entry.value)
            notes.append(note)
        except (json.JSONDecodeError, AttributeError):
            notes.append({"title": entry.key, "content": entry.value})

    summaries = [f"- {n.get('title', 'Untitled')}: {n.get('content', '')[:80]}" for n in notes]
    return {
        "payload": {"results": notes},
        "summary": f"Found {len(notes)} notes:\n" + "\n".join(summaries),
        "success": True,
    }


# Legacy entry point — only used if the classifier doesn't resolve an action
async def run(ctx) -> dict:
    """Fallback: use LLM to figure out what to do."""
    instruction = ctx.brief.get("instruction", "")
    response = await ctx.llm.complete(
        prompt=(
            f"The user said: \"{instruction}\"\n\n"
            f"This is a notes skill. Help the user with their note-related request."
        ),
        system="You are a helpful notes assistant.",
    )
    return {
        "payload": {"response": response},
        "summary": response,
        "success": True,
    }
