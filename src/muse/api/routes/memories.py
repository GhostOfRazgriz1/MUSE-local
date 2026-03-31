"""REST endpoints for browsing and managing memories."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from muse.api.app import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["memories"])

# Human-friendly labels for internal namespaces.
_NS_LABELS = {
    "_profile": "About You",
    "_facts": "Things I've Learned",
    "_project": "Your Projects",
    "_conversation": "Conversation Highlights",
    "_patterns": "Your Routines",
    "_system": "System",
    "_scheduled": "Scheduled Tasks",
}


def _friendly_ns(ns: str) -> str:
    return _NS_LABELS.get(ns, ns.strip("_").replace("_", " ").title())


def _entry_to_item(entry: dict) -> dict:
    """Strip heavy fields (embedding) and add friendly namespace label."""
    return {
        "id": entry["id"],
        "namespace": entry["namespace"],
        "namespace_label": _friendly_ns(entry["namespace"]),
        "key": entry["key"],
        "value": entry["value"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "access_count": entry["access_count"],
    }


@router.get("/memories")
async def list_memories(
    namespace: str | None = Query(None, description="Filter by namespace"),
    limit: int = Query(200, ge=1, le=500),
):
    """Return all non-superseded memories, optionally filtered by namespace."""
    orchestrator = get_orchestrator()
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")

    repo = orchestrator._memory_repo
    entries = await repo.get_by_relevance(namespace=namespace, limit=limit, min_score=0.0)
    items = [_entry_to_item(e) for e in entries]

    # Group by namespace for the profile card view.
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["namespace"], []).append(item)

    return {"memories": items, "groups": groups}


@router.get("/memories/stats")
async def memory_stats():
    """Return aggregate memory statistics."""
    orchestrator = get_orchestrator()
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")

    repo = orchestrator._memory_repo
    total = await repo.count_entries()

    # Count per namespace.
    ns_counts: dict[str, int] = {}
    for ns in list(_NS_LABELS.keys()):
        keys = await repo.list_keys(ns)
        if keys:
            ns_counts[_friendly_ns(ns)] = len(keys)

    return {"total": total, "by_category": ns_counts}


@router.post("/memories")
async def add_memory(body: dict):
    """Manually add a memory entry.

    Body: ``{"value": "I like sushi", "namespace": "_profile"}``
    The namespace defaults to ``_profile`` if omitted.
    A key is auto-generated from the value text.
    """
    orchestrator = get_orchestrator()
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")

    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(400, "value is required")

    namespace = body.get("namespace", "_profile").strip()
    # Generate a stable key from the first ~60 chars of the value.
    key = value[:60].lower().replace(" ", "_").replace(".", "")

    repo = orchestrator._memory_repo
    entry = await repo.put(namespace, key, value)
    return _entry_to_item(entry)


@router.delete("/memories/{namespace}/{key:path}")
async def delete_memory(namespace: str, key: str):
    """Delete a single memory entry."""
    orchestrator = get_orchestrator()
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")

    repo = orchestrator._memory_repo
    existing = await repo.get(namespace, key)
    if not existing:
        raise HTTPException(404, "Memory not found")
    await repo.delete(namespace, key)
    return {"ok": True}
