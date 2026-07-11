"""
Dex uses DuckDuckGo to look up recent info when someone asks about
something that might be past the model's knowledge cutoff.
No API key needed — completely free.
"""
import asyncio
import logging

from duckduckgo_search import DDGS

from ai.client import generate_reply

logger = logging.getLogger(__name__)

RECENT_KEYWORDS = [
    "new album", "latest", "just dropped", "new song", "new movie",
    "who won", "world cup 2026", "2026", "right now", "currently",
    "this year", "this week", "today", "recent", "update", "news",
    "still", "anymore", "is he", "is she", "did they", "what happened",
    "transfer", "signed", "released", "dropped", "announced"
]


def needs_web_search(text: str) -> bool:
    """Detect if the message is asking about something recent or current."""
    t = text.lower()
    return any(kw in t for kw in RECENT_KEYWORDS)


def web_search(query: str, max_results: int = 4) -> str:
    """
    Search DuckDuckGo and return a short summary of results.
    Runs synchronously — called inside async context via executor if needed.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return ""
        lines = []
        for r in results:
            title = r.get("title", "")
            body  = r.get("body", "")
            if body:
                lines.append(f"{title}: {body[:150]}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return ""


async def get_search_reply(message_text: str, prompt: str) -> str:
    """
    Search for relevant info and have Dex respond naturally using what he found.
    """
    try:
        loop    = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: web_search(message_text))
        if not results:
            return ""

        search_prompt = (
            f"{prompt}\n\n"
            f"Someone asked: \"{message_text}\"\n\n"
            f"Here's what a quick search found:\n{results}\n\n"
            "Use this info to answer as Dex — naturally, like someone who actually knows. "
            "Keep it short. Don't sound like you're reading search results."
        )
        return await generate_reply(search_prompt, [])
    except Exception as e:
        logger.error(f"Search reply error: {e}")
        return ""
