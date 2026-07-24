"""
Football data via football-data.org (free tier): top 5 leagues + Champions
League + World Cup + European Championship, plus a top-scorers endpoint.

Requires FOOTBALL_API_KEY (config.py) — confirmed working.
Free tier is rate-limited to 10 requests/minute, so every fetch is cached
for CACHE_TTL_SECONDS.
"""
import logging
import time

import aiohttp

from ai.client import generate_reply
from config import FOOTBALL_API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": FOOTBALL_API_KEY}
CACHE_TTL_SECONDS = 300   # 5 min — keeps us well under the 10 req/min free-tier limit

HISTORICAL_KEYWORDS = ["last year", "last season", "previous season", "in 2024", "in 2025"]

def is_historical_question(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HISTORICAL_KEYWORDS)
    
COMPETITION_CODES = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "SA":  "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL":  "Champions League",
    "WC":  "World Cup",
    "EC":  "European Championship",
}

LEAGUE_KEYWORDS = {
    "PL":  ["premier league", "epl", "man city", "manchester city", "man utd",
            "manchester united", "liverpool", "chelsea", "arsenal", "tottenham"],
    "PD":  ["la liga", "real madrid", "barcelona", "barca", "atletico madrid"],
    "SA":  ["serie a", "juventus", "inter milan", "ac milan", "napoli", "roma"],
    "BL1": ["bundesliga", "bayern", "dortmund", "borussia"],
    "FL1": ["ligue 1", "psg", "paris saint"],
    "CL":  ["champions league", "ucl"],
    "WC":  ["world cup"],
    "EC":  ["euros", "european championship", "euro 2024", "euro 2028"],
}

STAT_KEYWORDS = [
    "top scorer", "golden boot", "most goals", "leading scorer",
    "goals this season", "player stats", "assists", "who's scored", "whos scored",
]

FOOTBALL_KEYWORDS = [
    "football", "soccer", "score", "result", "match", "game", "played", "won", "lost", "draw",
    "fixture", "standings", "table", "league", "goals", "lineup", "who won",
    "last night", "tonight", "kick off", "full time", "ft", "live score",
] + [kw for kws in LEAGUE_KEYWORDS.values() for kw in kws] + STAT_KEYWORDS


def is_football_question(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in FOOTBALL_KEYWORDS)


def resolve_competition(text: str, topic: str = "") -> str:
    combined = f"{text} {topic}".lower()
    for code, keywords in LEAGUE_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return code
    return ""


def wants_player_stats(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in STAT_KEYWORDS)


_cache = {}   # {path: (fetched_at, data)}


async def _get(path: str) -> dict:
    now = time.time()
    cached = _cache.get(path)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    url = f"{BASE_URL}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                logger.warning(f"football-data.org returned {resp.status} for {path}")
                return {}
            data = await resp.json()

    _cache[path] = (now, data)
    return data


async def fetch_standings(code: str, top_n: int = 6) -> str:
    data = await _get(f"/competitions/{code}/standings")
    tables = data.get("standings", [])
    if not tables:
        return ""

    lines = []
    for table in tables:
        group_label = table.get("group") or ""
        rows = table.get("table", [])[:top_n]
        if group_label:
            lines.append(f"{group_label}:")
        for row in rows:
            team = row.get("team", {}).get("name", "")
            lines.append(
                f"{row.get('position')}. {team} — {row.get('points')}pts "
                f"(P{row.get('playedGames')} W{row.get('won')} D{row.get('draw')} L{row.get('lost')})"
            )
    return "\n".join(lines)


async def fetch_recent_matches(code: str, limit: int = 5) -> str:
    data = await _get(f"/competitions/{code}/matches?status=FINISHED")
    matches = data.get("matches", [])
    if not matches:
        return ""
    matches = sorted(matches, key=lambda m: m.get("utcDate", ""), reverse=True)[:limit]

    lines = []
    for m in matches:
        home = m.get("homeTeam", {}).get("name", "")
        away = m.get("awayTeam", {}).get("name", "")
        score = m.get("score", {}).get("fullTime", {})
        date = m.get("utcDate", "")[:10]
        lines.append(f"{home} {score.get('home')}-{score.get('away')} {away} ({date})")
    return "\n".join(lines)


async def fetch_scorers(code: str, limit: int = 5) -> str:
    data = await _get(f"/competitions/{code}/scorers?limit={limit}")
    scorers = data.get("scorers", [])
    if not scorers:
        return ""

    lines = []
    for s in scorers:
        name  = s.get("player", {}).get("name", "")
        team  = s.get("team", {}).get("name", "")
        goals = s.get("goals", "?")
        assists = s.get("assists")
        line = f"{name} ({team}) — {goals} goals"
        if assists is not None:
            line += f", {assists} assists"
        lines.append(line)
    return "\n".join(lines)


async def fetch_todays_matches() -> str:
    data = await _get("/matches")
    matches = data.get("matches", [])
    if not matches:
        return ""

    lines = []
    for m in matches[:10]:
        comp  = m.get("competition", {}).get("name", "")
        home  = m.get("homeTeam", {}).get("name", "")
        away  = m.get("awayTeam", {}).get("name", "")
        score = m.get("score", {}).get("fullTime", {})
        status = m.get("status", "")
        if status == "FINISHED":
            lines.append(f"{home} {score.get('home')}-{score.get('away')} {away} ({comp})")
        else:
            lines.append(f"{home} vs {away} — {status} ({comp})")
    return "\n".join(lines)


async def get_football_reply(message_text: str, prompt: str, topic: str = "") -> str:
    code = resolve_competition(message_text, topic)
    data_parts = []

    try:
        if code:
            comp_name = COMPETITION_CODES.get(code, code)
            if wants_player_stats(message_text):
                scorers = await fetch_scorers(code)
                if scorers:
                    data_parts.append(f"{comp_name} top scorers:\n{scorers}")
            else:
                standings = await fetch_standings(code)
                recent    = await fetch_recent_matches(code)
                if standings:
                    data_parts.append(f"{comp_name} table:\n{standings}")
                if recent:
                    data_parts.append(f"{comp_name} recent results:\n{recent}")
        else:
            todays = await fetch_todays_matches()
            if todays:
                data_parts.append(f"Today's matches:\n{todays}")

        if not data_parts:
            return ""

        data_summary = "\n\n".join(data_parts)
        logger.info(f"Football data sent to model: {data_summary}")
        football_prompt = (
            f"{prompt}\n\n"
            f"Someone just asked about football: \"{message_text}\"\n\n"
            f"Here's real, current data:\n{data_summary}\n\n"
            "Respond as Dex — use this real data to answer naturally. Keep it short. "
            "Sound like a person reacting to results, not a stats bot reading a table."
        )
        return await generate_reply(football_prompt, [])

    except Exception as e:
        logger.error(f"Football API error: {e}")
        return ""
