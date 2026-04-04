"""Memory consolidation ("dreaming") — runs when the agent is idle.

When no user messages arrive for a configurable period, the agent
reviews the current session's conversation and extracts durable
knowledge into persistent memory.  This is analogous to how sleep
consolidates episodic memories into long-term storage.

Extracted memories are written to the demotion pipeline (cache → disk)
using the existing namespace conventions:
  - _profile   : user preferences, role, habits
  - _project   : project-specific facts, decisions, constraints
  - _facts     : general knowledge, key findings
  - _conversation : session summaries for cross-session continuity

On startup, ``prewarm_cache`` already loads _profile, _conversation,
and top-frequency entries — so consolidated memories are automatically
available in the next session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from muse.debug import get_tracer

logger = logging.getLogger(__name__)

# How long the agent must be idle before dreaming starts (seconds).
DEFAULT_IDLE_THRESHOLD = 120  # 2 minutes

# Minimum conversation turns to bother consolidating.
MIN_TURNS_FOR_CONSOLIDATION = 4


class DreamingManager:
    """Monitors idle time and triggers memory consolidation."""

    def __init__(
        self,
        orchestrator,
        idle_threshold: int = DEFAULT_IDLE_THRESHOLD,
    ):
        self._orch = orchestrator
        self._idle_threshold = idle_threshold
        self._last_activity: float = 0.0
        self._running = False
        self._consolidated_sessions: set[str] = set()
        self._task: asyncio.Task | None = None

    def touch(self) -> None:
        """Record user activity — resets the idle timer."""
        self._last_activity = asyncio.get_event_loop().time()

    def start(self) -> None:
        """Start the background idle-watcher."""
        self._running = True
        self._last_activity = asyncio.get_event_loop().time()
        self._task = asyncio.create_task(self._idle_watcher())
        logger.info("Dreaming manager started (idle threshold: %ds)", self._idle_threshold)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _idle_watcher(self) -> None:
        """Periodically check if idle long enough to dream."""
        while self._running:
            await asyncio.sleep(30)  # check every 30s
            if not self._running:
                break

            elapsed = asyncio.get_event_loop().time() - self._last_activity
            if elapsed < self._idle_threshold:
                continue

            session_id = self._orch._session_id
            if not session_id:
                continue
            if session_id in self._consolidated_sessions:
                continue

            history = self._orch._conversation_history
            if len(history) < MIN_TURNS_FOR_CONSOLIDATION:
                continue

            logger.info("Agent idle for %.0fs — starting memory consolidation", elapsed)
            get_tracer().event("dreaming", "start",
                               session_id=session_id,
                               turns=len(history),
                               idle_seconds=round(elapsed))

            await self._orch.set_mood("dreaming", force=True)
            try:
                # Flush usage patterns to disk before consolidation
                await self._orch._patterns.flush()

                await self._consolidate(session_id, history)
                await self._analyze_patterns()
                self._consolidated_sessions.add(session_id)
            except Exception as e:
                logger.error("Memory consolidation failed: %s", e, exc_info=True)
                get_tracer().error("dreaming", f"Consolidation failed: {e}")
            finally:
                await self._orch.set_mood("resting", force=True)

    async def _consolidate(
        self, session_id: str, history: list[dict],
    ) -> None:
        """Extract durable knowledge from the conversation and persist it."""
        import asyncio as _aio
        import json
        import re

        # Build the conversation text
        conv_text = "\n".join(
            f"{t['role']}: {t['content']}" for t in history
        )

        model = await self._orch._model_router.resolve_model()

        # ── Run memory extraction + session summary in parallel ──
        # Both read conv_text independently; no dependency between them.
        extract_task = self._orch._provider.complete(
            model=model,
            messages=[
                {"role": "user", "content": (
                    f"Conversation:\n{conv_text}\n\n"
                    "Extract facts worth remembering. JSON array:\n"
                    '[{"namespace":"_profile","key":"user-likes-coffee","value":"User prefers coffee over tea"}]\n\n'
                    "Namespaces:\n"
                    "- _profile: user preferences, habits, people in their life\n"
                    "- _project: project decisions, tech stack, deadlines\n"
                    "- _facts: key findings, important data\n"
                    "- _emotions: how user felt about events\n\n"
                    "Skip greetings and task status. Reply with ONLY a JSON array."
                )},
            ],
            system="Extract memorable facts from the conversation. Reply with ONLY a valid JSON array.",
            max_tokens=1000,
        )

        summary_task = self._orch._provider.complete(
            model=model,
            messages=[
                {"role": "user", "content": (
                    f"Conversation:\n{conv_text}\n\n"
                    "Write a 2-3 sentence summary. What was accomplished? Any decisions made?"
                )},
            ],
            system="Summarize the conversation in 2-3 sentences.",
            max_tokens=150,
        )

        result, summary_result = await _aio.gather(extract_task, summary_task)

        # ── Parse extracted memories ───────────────────────────
        raw = result.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()

        try:
            memories = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Consolidation LLM returned invalid JSON: %s", raw[:200])
            get_tracer().error("dreaming", "Invalid JSON from consolidation LLM",
                               response=raw[:300])
            memories = []

        if not isinstance(memories, list):
            memories = memories.get("memories", []) if isinstance(memories, dict) else []

        # ── Store memories + summary in parallel ───────────────
        valid_namespaces = {"_profile", "_project", "_facts", "_emotions"}
        facts = []
        for mem in memories:
            ns = mem.get("namespace", "")
            key = mem.get("key", "")
            value = mem.get("value", "")
            if ns not in valid_namespaces or not key or not value:
                continue
            facts.append({
                "key": key,
                "value": value,
                "namespace": ns,
            })

        session_summary = summary_result.text.strip()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

        # Run demotion + summary storage concurrently
        async def _store_memories():
            if facts:
                inserted = await self._orch._demotion.demote_to_cache(facts)
                await self._orch._demotion.flush_cache_to_disk()
                return inserted
            return []

        async def _store_summary():
            await self._orch._memory_repo.put(
                namespace="_conversation",
                key=f"session_{timestamp}",
                value=session_summary,
                value_type="text",
            )

        inserted_result, _ = await _aio.gather(_store_memories(), _store_summary())
        inserted = inserted_result or []

        if not facts:
            get_tracer().event("dreaming", "no_memories", session_id=session_id)

        # Save compaction checkpoint
        try:
            await self._orch._compaction._save_checkpoint_async()
        except Exception as exc:
            logger.debug("Dreaming: compaction checkpoint skipped: %s", exc)

        get_tracer().event("dreaming", "complete",
                           session_id=session_id,
                           memories_extracted=len(facts),
                           memories_inserted=len(inserted),
                           session_summary=session_summary[:200])

        logger.info(
            "Memory consolidation complete: %d memories extracted, "
            "%d novel (inserted), session summary saved",
            len(facts), len(inserted),
        )

    async def _analyze_patterns(self) -> None:
        """Review usage patterns and generate proactive suggestions.

        Suggestions are stored in _patterns namespace under the
        "suggestions" key. The greeting system reads them to offer
        proactive actions when the user connects.
        """
        import json
        import re

        pattern_summary = self._orch._patterns.summarize_recent()
        if "No recent activity" in pattern_summary:
            return

        # Also get historical patterns if available
        try:
            history = await self._orch._patterns.get_history(days=7)
        except Exception:
            history = []

        history_summary = ""
        if history:
            from collections import Counter
            skills = Counter(e.get("skill_id", "") for e in history if e.get("skill_id"))
            hours = Counter(e.get("hour", 0) for e in history)
            weekdays = Counter(e.get("weekday", "") for e in history)
            history_summary = (
                f"\n7-day history ({len(history)} events):\n"
                f"Top skills: {dict(skills.most_common(5))}\n"
                f"Active hours: {dict(hours.most_common(5))}\n"
                f"Active days: {dict(weekdays.most_common(3))}"
            )

        model = await self._orch._model_router.resolve_model()

        try:
            result = await self._orch._provider.complete(
                model=model,
                messages=[
                    {"role": "user", "content": (
                        f"Usage data:\n{pattern_summary}\n"
                        f"{history_summary}\n\n"
                        "Suggest 0-3 actions based on the data. JSON array:\n"
                        '[{"type":"remind","message":"...","skill_id":"...","confidence":0.8}]\n\n'
                        "Types: automate, remind, optimize, inform.\n"
                        "Only suggest with clear evidence. Reply with ONLY a JSON array."
                    )},
                ],
                system="Generate suggestions from usage patterns. Reply with ONLY a valid JSON array.",
                max_tokens=300,
            )

            raw = result.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw).strip()

            suggestions = json.loads(raw)
            if not isinstance(suggestions, list):
                suggestions = []

            # Filter low-confidence suggestions
            suggestions = [s for s in suggestions if s.get("confidence", 0) >= 0.5]

            if suggestions:
                await self._orch._memory_repo.put(
                    namespace="_patterns",
                    key="suggestions",
                    value=json.dumps(suggestions),
                    value_type="json",
                )
                get_tracer().event("dreaming", "suggestions_generated",
                                   count=len(suggestions),
                                   suggestions=[s.get("message", "")[:60] for s in suggestions])
                logger.info("Generated %d proactive suggestions", len(suggestions))

        except Exception as e:
            logger.warning("Pattern analysis failed: %s", e)
            get_tracer().error("dreaming", f"Pattern analysis failed: {e}")
