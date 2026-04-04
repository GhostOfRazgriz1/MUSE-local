"""Intent classification for the orchestrator.

Single LLM call decides which skill(s) handle the user's message.
Greetings and meta-questions are caught by a cheap regex fast-path
to avoid unnecessary LLM calls.
"""

from __future__ import annotations

import json
import logging
import re

from muse.debug import get_tracer
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# ── Thresholds ───────────────────────────────────────────────────────
# Tuned for multi-vector max-similarity (scores are higher and more
# spread than single-vector, so thresholds are higher too).
HIGH_CONFIDENCE = 0.55   # Above → delegate immediately (no LLM call)
class ExecutionMode(Enum):
    INLINE = "inline"
    DELEGATED = "delegated"
    MULTI_DELEGATED = "multi_delegated"
    GOAL = "goal"
    CLARIFY = "clarify"


@dataclass
class SubTask:
    """A single sub-task within a multi-task intent."""
    skill_id: str
    instruction: str
    action: str | None = None
    depends_on: list[int] = field(default_factory=list)


@dataclass
class ClassifiedIntent:
    mode: ExecutionMode
    skill_id: str | None = None
    action: str | None = None  # resolved action within the skill
    skill_ids: list[str] = field(default_factory=list)
    sub_tasks: list[SubTask] = field(default_factory=list)
    task_description: str = ""
    model_override: str | None = None
    confidence: float = 1.0
    clarify_question: str = ""  # Set when mode == CLARIFY



# Messages matching these are ALWAYS handled inline — no LLM call needed.
_INLINE_RE = re.compile(
    r"^(?:h(?:i|ello|ey|owdy|iya)|yo|good\s+(?:morning|afternoon|evening))"
    r"|^(?:thanks?(?:\s+you)?|thx|ty|cheers|great|perfect|ok(?:ay)?|nice|cool|got it)[\s!.]*$"
    r"|^(?:who|what)\s+(?:are\s+you|can\s+you\s+do)"
    r"|^(?:help|assist|\?+)$",
    re.IGNORECASE,
)


# ── Classifier ──────────────────────────────────────────────────────

class SemanticIntentClassifier:
    """LLM-based intent classifier.

    One LLM call decides: which skill (if any) handles the message,
    and whether it needs multiple skills (multi-task).
    """

    def __init__(self, embedding_service=None, provider=None):
        # embedding_service accepted for backward compat but unused —
        # classification is fully LLM-based now.
        self._provider = provider
        self._default_model: str = ""
        # skill_id -> {description, name}
        self._skills: dict[str, dict] = {}
        # Cached lookup structures — rebuilt only when skills change
        self._cached_skill_lines: str = ""
        self._cached_id_map: dict[str, str] = {}
        # Cached model list grouped by provider prefix, for "use X" resolution.
        # Populated lazily on first "use X" message, cleared on provider change.
        self._model_cache: dict[str, list] = {}

    def set_provider(self, provider, default_model: str) -> None:
        self._provider = provider
        self._default_model = default_model
        self._model_cache.clear()

    def _rebuild_cache(self) -> None:
        """Rebuild the cached skill_lines and id_map after skill registration changes."""
        self._cached_skill_lines = "\n".join(
            f"  - {sid}: {info['description']}"
            for sid, info in self._skills.items()
        )
        id_map: dict[str, str] = {}
        for sid in self._skills:
            id_map[sid.lower()] = sid
            id_map[sid.lower().replace(" ", "_")] = sid
            id_map[sid.lower().replace(" ", "")] = sid
            name = self._skills[sid]["name"]
            id_map[name.lower()] = sid
            id_map[name.lower().replace(" ", "_")] = sid
        self._cached_id_map = id_map

    def register_skill(
        self, skill_id: str, name: str, description: str,
        actions: list[dict] | None = None,
    ) -> None:
        self._skills[skill_id] = {
            "description": description,
            "name": name,
            "actions": actions or [],
        }
        self._rebuild_cache()
        logger.debug("Registered skill %s (%d actions)", skill_id, len(actions or []))

    def unregister_skill(self, skill_id: str) -> None:
        self._skills.pop(skill_id, None)
        self._rebuild_cache()

    async def classify(
        self, user_message: str,
        conversation_context: str = "",
    ) -> ClassifiedIntent:
        """Classify intent via a single LLM call."""
        msg_lower = user_message.lower().strip()

        # ── Fast inline exit: greetings, thanks, meta-questions ──
        if _INLINE_RE.search(msg_lower):
            logger.debug("Inline fast-path (greeting/meta): %r", msg_lower[:60])
            return ClassifiedIntent(
                mode=ExecutionMode.INLINE,
                task_description=user_message,
            )

        if not self._skills or not self._provider or not self._default_model:
            return ClassifiedIntent(
                mode=ExecutionMode.INLINE,
                task_description=user_message,
            )

        # ── Single LLM call for routing ─────────────────────────
        context_block = ""
        if conversation_context:
            context_block = (
                f"Context: {conversation_context}\n\n"
            )

        prompt = (
            f"{context_block}"
            f"User: \"{user_message}\"\n\n"
            f"Skills:\n{self._cached_skill_lines}\n\n"
            f"Pick ONE:\n"
            f'{{"action":"none"}} — chat, no skill\n'
            f'{{"action":"single","skill":"<id>"}} — use one skill\n'
            f'{{"action":"multi","sub_tasks":[{{"skill_id":"...","instruction":"...","depends_on":[]}}]}} — 2-3 skills\n'
            f'{{"action":"goal"}} — complex multi-step plan\n'
            f'{{"action":"clarify","question":"..."}} — ask user to clarify\n\n'
            f"JSON only:"
        )

        try:
            result = await self._provider.complete(
                model=self._default_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                system=(
                    "You route user messages to skills. Reply with ONLY valid JSON.\n\n"
                    "Rules:\n"
                    "- Chat/conversation → {{\"action\":\"none\"}}\n"
                    "- Creating files/documents/code → use Files skill\n"
                    "- Running code/math → use Code Runner skill\n"
                    "- Unsure → {{\"action\":\"none\"}}\n"
                    "- No markdown, no explanation, ONLY JSON."
                ),
            )

            raw = result.text.strip()
            get_tracer().event("llm", "response",
                               purpose="intent_classification",
                               model=self._default_model,
                               response=raw[:500])

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw).strip()

            data = json.loads(raw)
            action = data.get("action", "none")

            id_map = self._cached_id_map

            if action == "single":
                raw_skill = data.get("skill", "").strip().lower()
                resolved = (
                    id_map.get(raw_skill)
                    or id_map.get(raw_skill.replace("_", " "))
                    or id_map.get(raw_skill.replace(" ", "_"))
                )
                if resolved:
                    # Level 2: resolve action within the skill
                    resolved_action = await self._resolve_action(
                        resolved, user_message,
                    )
                    logger.debug("LLM routed → %s.%s", resolved, resolved_action or "run")
                    return ClassifiedIntent(
                        mode=ExecutionMode.DELEGATED,
                        skill_id=resolved,
                        action=resolved_action,
                        task_description=user_message,
                                confidence=1.0,
                    )
                else:
                    logger.warning("LLM returned unknown skill: %r", raw_skill)

            elif action == "multi":
                raw_tasks = data.get("sub_tasks", [])
                if len(raw_tasks) >= 2:
                    sub_tasks: list[SubTask] = []
                    skill_ids: list[str] = []
                    for rt in raw_tasks:
                        raw_id = rt.get("skill_id", "").strip().lower()
                        resolved = (
                            id_map.get(raw_id)
                            or id_map.get(raw_id.replace("_", " "))
                            or id_map.get(raw_id.replace(" ", "_"))
                        )
                        if not resolved:
                            continue
                        deps = rt.get("depends_on", [])
                        deps = [d for d in deps if isinstance(d, int) and 0 <= d < len(raw_tasks)]
                        sub_instruction = rt.get("instruction", "")
                        resolved_action = await self._resolve_action(
                            resolved, sub_instruction,
                        )
                        sub_tasks.append(SubTask(
                            skill_id=resolved,
                            instruction=sub_instruction,
                            action=resolved_action,
                            depends_on=deps,
                        ))
                        if resolved not in skill_ids:
                            skill_ids.append(resolved)

                    if len(sub_tasks) >= 2:
                        logger.info("LLM routed → multi-task: %s",
                                    [(st.skill_id, st.depends_on) for st in sub_tasks])
                        return ClassifiedIntent(
                            mode=ExecutionMode.MULTI_DELEGATED,
                            skill_ids=skill_ids,
                            sub_tasks=sub_tasks,
                            task_description=user_message,
                                        confidence=1.0,
                        )

            elif action == "goal":
                logger.debug("LLM routed → goal decomposition")
                return ClassifiedIntent(
                    mode=ExecutionMode.GOAL,
                    task_description=user_message,
                        confidence=1.0,
                )

            elif action == "clarify":
                question = data.get("question", "Could you clarify what you'd like me to do?")
                logger.debug("LLM routed → clarify: %s", question[:60])
                return ClassifiedIntent(
                    mode=ExecutionMode.CLARIFY,
                    task_description=user_message,
                        clarify_question=question,
                )

            # action == "none" or fallthrough
            logger.debug("LLM routed → inline")
            return ClassifiedIntent(
                mode=ExecutionMode.INLINE,
                task_description=user_message,
                confidence=1.0,
            )

        except Exception as e:
            logger.warning("LLM classification failed: %s", e, exc_info=True)
            get_tracer().error("classify", f"LLM classification failed: {e}")
            return ClassifiedIntent(
                mode=ExecutionMode.INLINE,
                task_description=user_message,
            )

    async def _resolve_action(
        self, skill_id: str, user_message: str,
    ) -> str | None:
        """Level 2: pick the action within a skill.

        If the skill has no actions defined, returns None (use run()).
        If it has actions, makes one short LLM call with just the
        action list (typically 3-6 options).
        """
        skill_info = self._skills.get(skill_id, {})
        actions = skill_info.get("actions", [])
        if not actions:
            return None

        # Single action — no need for a second LLM call
        if len(actions) == 1:
            return actions[0]["id"]

        action_lines = "\n".join(
            f"  - {a['id']}: {a['description']}" for a in actions
        )

        try:
            result = await self._provider.complete(
                model=self._default_model,
                messages=[{"role": "user", "content": (
                    f"User: \"{user_message}\"\n"
                    f"Actions:\n{action_lines}\n"
                    f"Reply with ONLY the action id."
                )}],
                max_tokens=20,
                system="Pick the best action. Reply with ONLY the action id. No explanation.",
            )

            picked = result.text.strip().strip('"\'.')
            get_tracer().event("classify", "action_resolved",
                               skill_id=skill_id, action=picked)

            # Validate against declared actions
            valid_ids = {a["id"] for a in actions}
            if picked in valid_ids:
                return picked

            # Try case-insensitive match
            id_lower = {a["id"].lower(): a["id"] for a in actions}
            resolved = id_lower.get(picked.lower())
            if resolved:
                return resolved

            logger.warning("Action %r not found in %s, falling back to run()", picked, skill_id)
            return None

        except Exception as e:
            logger.warning("Action resolution failed for %s: %s", skill_id, e)
            return None

