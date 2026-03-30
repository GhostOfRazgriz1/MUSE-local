"""Webpage Reader skill — fetch and extract readable text from URLs.

Uses stdlib html.parser for HTML extraction (no external dependencies).
Routes through the gateway which enforces SSRF protection.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser


# ── HTML text extraction ────────────────────────────────────────

# Tags whose content should be ignored entirely
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "math", "head",
    "nav", "footer", "header", "aside", "form", "button",
    "iframe", "object", "embed",
})

# Block-level tags that should insert line breaks
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "tr", "dt", "dd", "blockquote", "pre", "section", "article",
    "figcaption", "summary", "details",
})


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, stripping scripts/styles/nav."""

    def __init__(self):
        super().__init__()
        self._text: list[str] = []
        self._skip_depth = 0
        self._title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            self._skip_depth += 1
        if tag_lower == "title":
            self._in_title = True
        if tag_lower in _BLOCK_TAGS:
            self._text.append("\n")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag_lower == "title":
            self._in_title = False
        if tag_lower in _BLOCK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str):
        if self._in_title and not self._title:
            self._title = data.strip()
        if self._skip_depth == 0:
            self._text.append(data)

    def get_text(self) -> str:
        raw = "".join(self._text)
        # Normalize whitespace: collapse runs of spaces/tabs within lines
        lines = []
        for line in raw.split("\n"):
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)

    def get_title(self) -> str:
        return self._title


def extract_text(html: str) -> tuple[str, str]:
    """Extract (title, body_text) from HTML."""
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_title(), parser.get_text()


# ── URL extraction ──────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"'`]+")


def _extract_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;:)]}") if m else None


# ── Helpers ─────────────────────────────────────────────────────

MAX_CONTENT_CHARS = 8000


def _err(msg: str) -> dict:
    return {"payload": None, "summary": msg, "success": False}


# ── Entry points ────────────────────────────────────────────────


async def read(ctx) -> dict:
    """Fetch a URL and return the extracted text."""
    instruction = ctx.brief.get("instruction", "")
    url = _extract_url(instruction)

    if not url:
        # Ask the LLM to find the URL in the instruction
        extracted = await ctx.llm.complete(
            prompt=f"Extract the URL from this request. Reply with ONLY the URL.\n\n{instruction}",
            system="Output only a URL. Nothing else.",
            max_tokens=100,
        )
        url = _extract_url(extracted.strip())

    if not url:
        return _err("No URL found. Please provide a webpage URL to read.")

    await ctx.task.report_status(f"Fetching {url}")

    try:
        resp = await ctx.http.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AgentOS/1.0)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
    except Exception as e:
        return _err(f"Failed to fetch URL: {e}")

    if resp.status_code != 200:
        return _err(f"HTTP {resp.status_code} fetching {url}")

    html = resp.text()
    title, text = extract_text(html)

    if not text.strip():
        return _err(f"No readable text extracted from {url}")

    # Truncate for LLM context limits
    truncated = text[:MAX_CONTENT_CHARS]
    was_truncated = len(text) > MAX_CONTENT_CHARS
    word_count = len(text.split())

    # Cache the result in memory for follow-up questions
    cache_key = f"page.{url[:80]}"
    await ctx.memory.write(cache_key, truncated[:4000], value_type="text")

    summary_text = truncated[:2000]
    if title:
        summary_text = f"**{title}**\n\n{summary_text}"
    if was_truncated:
        summary_text += f"\n\n*[Content truncated — {word_count} words total]*"

    return {
        "payload": {
            "url": url,
            "title": title,
            "content": truncated,
            "word_count": word_count,
            "truncated": was_truncated,
        },
        "summary": summary_text,
        "success": True,
    }


async def summarize(ctx) -> dict:
    """Fetch a URL and return an LLM-generated summary."""
    result = await read(ctx)
    if not result["success"]:
        return result

    content = result["payload"]["content"]
    title = result["payload"]["title"]
    url = result["payload"]["url"]

    await ctx.task.report_status("Summarizing...")

    summary = await ctx.llm.complete(
        prompt=(
            f"Summarize the following webpage content. Be concise but comprehensive.\n\n"
            f"Title: {title}\nURL: {url}\n\n"
            f"Content:\n{content[:6000]}"
        ),
        system=(
            "Provide a clear, well-structured summary of the webpage content. "
            "Use markdown formatting. Highlight key points."
        ),
        max_tokens=1000,
    )

    return {
        "payload": {
            "url": url,
            "title": title,
            "summary": summary,
            "word_count": result["payload"]["word_count"],
        },
        "summary": summary,
        "success": True,
    }


async def run(ctx) -> dict:
    """Default entry point — routes based on instruction."""
    instruction = ctx.brief.get("instruction", "").lower()
    if any(w in instruction for w in ["summarize", "summary", "tldr", "brief"]):
        return await summarize(ctx)
    return await read(ctx)
