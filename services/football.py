"""
Detects football-related questions in messages and fetches live data.
Dex responds with actual match info in his own voice, not raw stats.
"""
import logging

import aiohttp

from ai.client import generate_reply

logger = logging.getLogger(__name__)

FOOTBALL_KEYWORDS = [
    "score", "result", "match", "game", "played", "won", "lost", "draw",
    "fixture", "standings", "table", "league", "champions league", "premier league",
    "la liga", "serie a", "bundesliga", "real madrid", "barcelona", "man city",
    "man utd", "liverpool", "chelsea", "arsenal", "juventus", "psg", "bayern",
    "goals", "lineup", "who won", "last night", "tonight", "kick off",
    "full time", "ft", "live score"
]


def is_football_question(text: str) -> bool:
    """Check if a message is asking about football."""
    t = text.lower()
    return any(kw in t for kw in FOOTBALL_KEYWORDS)


async def fetch_live_scores() -> str:
    """
    Fetch recent football results and upcoming fixtures from TheSportsDB.
    Free key is 123 — no signup needed.
    Covers major leagues including La Liga, Premier League, Champions League.
    """
    try:
        # Get recent Real Madrid matches as default (most relevant for Dex)
        # TheSportsDB team ID for Real Madrid is 133604
        url = "https://www.thesportsdb.com/api/v1/json/123/eventslast.php?id=133604"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json()

        events = data.get("results", [])
        if not events:
            return ""

        lines = []
        for e in events[:5]:
            home    = e.get("strHomeTeam", "")
            away    = e.get("strAwayTeam", "")
            h_score = e.get("intHomeScore", "")
            a_score = e.get("intAwayScore", "")
            date    = e.get("dateEvent", "")
            league  = e.get("strLeague", "")
            if h_score is not None and a_score is not None:
                lines.append(f"{home} {h_score}-{a_score} {away} ({league}, {date})")

        return "\n".join(lines) if lines else ""

    except Exception as e:
        logger.error(f"Football API error: {e}")
        return ""


async def get_football_reply(message_text: str, prompt: str) -> str:
    """
    Fetch live scores and have Dex comment on them naturally.
    Dex gets the raw data and responds in his own voice.
    """
    scores = await fetch_live_scores()
    if not scores:
        return ""

    football_prompt = (
        f"{prompt}\n\n"
        f"Someone just asked about football: \"{message_text}\"\n\n"
        f"Here is today's actual match data:\n{scores}\n\n"
        "Respond as Dex — use the real data to answer naturally. "
        "Keep it short. Sound like a person reacting to results, not a stats bot. "
        "If it's relevant to Real Madrid, have an opinion."
    )
    return await generate_reply(football_prompt, [])
