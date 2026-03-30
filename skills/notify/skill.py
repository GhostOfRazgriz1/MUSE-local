"""Notify skill — send notifications to the user.

Uses ctx.user.notify() for in-chat notifications.  The frontend
detects notification events and fires browser Notifications API
for desktop alerts when the tab is not visible.
"""

from __future__ import annotations

import json


async def send(ctx) -> dict:
    """Send a notification to the user."""
    instruction = ctx.brief.get("instruction", "")

    # Use LLM to extract a clean notification title and body
    result = await ctx.llm.complete(
        prompt=(
            f"Extract a notification title (max 10 words) and body from this request.\n"
            f"Respond with JSON: {{\"title\": \"...\", \"body\": \"...\"}}\n\n"
            f"Request: {instruction}"
        ),
        system="Extract structured notification data. Respond only with valid JSON.",
    )

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        parsed = {"title": "Notification", "body": instruction}

    title = parsed.get("title", "Notification")
    body = parsed.get("body", instruction)

    # Send in-chat notification (always works)
    await ctx.user.notify(f"{title}: {body}")

    return {
        "payload": {"title": title, "body": body},
        "summary": f"Notification sent: {title}",
        "success": True,
    }


async def run(ctx) -> dict:
    """Default entry point."""
    return await send(ctx)
