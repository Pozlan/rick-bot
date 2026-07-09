import os
import logging
import random
import threading
import json
import base64
import asyncio
import time
from datetime import datetime, timedelta
import pytz
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiohttp
from duckduckgo_search import DDGS
from groq import Groq
from google import genai
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config & Clients ──────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
FOOTBALL_API_KEY  = os.environ.get("FOOTBALL_API_KEY", "")
CHANNEL_ID        = "@dexsupdate"

groq_client   = Groq(api_key=GROQ_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_HISTORY        = 14    # messages before summarization triggers
KEEP_AFTER_SUM     = 4     # messages to keep after summarization
VISION_MODEL       = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL         = "llama-3.3-70b-versatile"
GEMINI_MODEL       = "gemini-1.5-flash"
FACT_EXTRACT_EVERY = 20    # run Gemini fact extraction every N interactions
USER_DATA_FILE     = "user_data.json"
SUMMARIES_FILE     = "chat_summaries.json"

# ── In-memory volatile chat history (recent messages only) ────────────────────
chat_histories = {}  # {chat_id: [{role, content}]}


# ──────────────────────────────────────────────────────────────────────────────
# REACTION SYSTEM
# Context-aware emoji reactions. Only uses confirmed valid Telegram reaction emojis.
# ──────────────────────────────────────────────────────────────────────────────

def pick_reaction(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ["lol","lmao","lmfao","haha","hahaha","joke","funny","i'm dead","💀","bruh"]):
        return random.choice(["🤣", "😁"])
    if any(x in t for x in ["roast","destroyed","rekt","ratio","took an l","clowned"]):
        return "🤡"
    if any(x in t for x in ["fire","amazing","incredible","insane","wild","banger","goated"]):
        return "🔥"
    if any(x in t for x in ["no way","wait what","seriously","omg","gossip","drama","leaked","exposing"]):
        return random.choice(["😱", "👀"])
    if any(x in t for x in ["ton","crypto","web3","btc","eth","token","wallet","nft","pump","dump","defi","blockchain","airdrop"]):
        return random.choice(["💯", "🤔", "⚡"])
    if any(x in t for x in ["actually","technically","research","therefore","in fact","data","evidence","logic"]):
        return random.choice(["🤓", "🤔"])
    if any(x in t for x in ["facts","exactly","true","100%","fr fr","no cap","based","agreed"]):
        return random.choice(["👍", "💯"])
    if any(x in t for x in ["wrong","bad take","terrible","cap","lying","false","misinformation"]):
        return "👎"
    if any(x in t for x in ["won","win","achieved","congrats","success","finally","did it","shipped"]):
        return "👏"
    if any(x in t for x in ["love","thank","appreciate","grateful","wholesome","sweet"]):
        return "❤️"
    if any(x in t for x in ["cringe","awkward","yikes","ouch","oof"]):
        return random.choice(["🥴", "😱"])
    if any(x in t for x in ["announcement","launching","dropping","breaking","new update"]):
        return random.choice(["🤯", "👀"])
    if any(x in t for x in ["goat","best","better than","i'm the","flex"]):
        return "🤨"
    if text.strip().endswith("?") or t.startswith(("why","how","what","who","when")):
        return random.choice(["🤔", "👀"])
    if len(text.split()) <= 3:
        return random.choice(["😐", "🗿", "👍"])
    return random.choice(["🤔","😐","🗿","⚡","🆒","🤨","👀","🤯"])


# ──────────────────────────────────────────────────────────────────────────────
# TIME-BASED MOOD
# Gives Dex a subtle personality shift based on time of day (EAT timezone).
# ──────────────────────────────────────────────────────────────────────────────

def get_time_mood() -> str:
    h = datetime.now(pytz.timezone("Africa/Addis_Ababa")).hour
    if 6 <= h < 10:   return "Morning. Slightly tired. Keep replies brief."
    if 10 <= h < 18:  return "Normal hours. Default mode."
    if 18 <= h < 23:  return "Evening. More energetic and sharp."
    return "Very late night. Slightly chaotic but still believably Dex."


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATION MOOD DETECTION
# Reads the last few messages to understand what's actually happening in the chat.
# Dex uses this to adapt without announcing it.
# ──────────────────────────────────────────────────────────────────────────────

def detect_mood(history: list) -> str:
    if not history:
        return "casual conversation"

    recent   = [m["content"].lower() for m in history[-6:]]
    combined = " ".join(recent)

    # Detect active debate (multiple people disagreeing)
    disagree = ["wrong","no it","actually no","disagree","that's not","nah","you're wrong","not true"]
    if sum(1 for msg in recent if any(w in msg for w in disagree)) >= 2:
        return "active debate — people are disagreeing"

    # Detect group joking
    jokes = ["lol","lmao","haha","hahaha","💀","😂","joke","funny"]
    if sum(1 for msg in recent if any(x in msg for x in jokes)) >= 2:
        return "people joking around — keep it light"

    # Detect someone feeling ignored (short response after long message)
    if len(recent) >= 2 and len(recent[-2].split()) > 20 and len(recent[-1].split()) <= 3:
        return "someone may feel ignored — be aware"

    # Detect help request
    help_signals = ["how do","help me","can you","explain","what is","how to","?"]
    if sum(1 for msg in recent if any(x in msg for x in help_signals)) >= 2:
        return "someone asking for help or explanation"

    # Detect crypto discussion
    if sum(1 for msg in recent if any(x in msg for x in ["ton","crypto","btc","eth","nft","wallet","defi"])) >= 2:
        return "crypto or Web3 discussion"

    # Detect technical discussion
    if any(x in combined for x in ["code","python","function","error","bug","api","server","script","deploy"]):
        return "technical discussion"

    # Detect emotional state
    if any(x in combined for x in ["sad","upset","frustrated","stressed","worried","tired","crying"]):
        return "someone is upset or emotional — go easy"

    # Detect excitement
    if any(x in combined for x in ["amazing","excited","finally","can't wait","🎉","🔥","big news"]):
        return "someone excited about something"

    return "casual conversation"


# ──────────────────────────────────────────────────────────────────────────────
# USER DATA — Load/Save
# ──────────────────────────────────────────────────────────────────────────────

def load_user_data() -> dict:
    try:
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_user_data(data: dict) -> None:
    try:
        with open(USER_DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")

user_data = load_user_data()


# ──────────────────────────────────────────────────────────────────────────────
# CHAT SUMMARIES — Persistent conversation memory across restarts
# Every active group gets a rolling summary stored on disk.
# ──────────────────────────────────────────────────────────────────────────────

def load_summaries() -> dict:
    try:
        with open(SUMMARIES_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_summaries(data: dict) -> None:
    try:
        with open(SUMMARIES_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save summaries: {e}")

chat_summaries = load_summaries()


# ──────────────────────────────────────────────────────────────────────────────
# USER HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def get_name(user) -> str:
    return user.first_name or user.username or "you"

def get_user_context(uid: str) -> str:
    n = user_data.get(uid, {}).get("interactions", 0)
    if n == 0:  return "stranger"
    if n < 5:   return "acquaintance"
    return "regular"

def get_memory_snippet(uid: str) -> str:
    """Build a concise memory string. Prefers specific facts over generic topics."""
    u = user_data.get(uid, {})
    if not u:
        return ""
    parts = []
    facts = u.get("facts", [])
    if facts:
        parts.append("; ".join(facts[:6]))
    elif u.get("topics"):
        parts.append(f"often discusses: {', '.join(u['topics'])}")
    if u.get("notes"):
        parts.append(u["notes"])
    return " | ".join(parts) if parts else ""

def update_user(user, message_text: str = "") -> None:
    """Update user profile: name, username, interaction count, and topic tags."""
    uid = str(user.id)
    if uid not in user_data:
        user_data[uid] = {
            "name": get_name(user), "username": "", "interactions": 0,
            "topics": [], "notes": "", "facts": [],
            "annoyance_score": 0.0, "last_trigger_time": 0,
            "ignored_until": 0, "warned": False,
            "daily_date": "", "daily_score": 0.0
        }
    user_data[uid]["name"]         = get_name(user)
    user_data[uid]["username"]     = user.username or ""
    user_data[uid]["interactions"] = user_data[uid].get("interactions", 0) + 1

    # Lightweight topic tagging via keyword match
    if message_text:
        topic_kws = {
            "crypto": ["ton","crypto","btc","eth","token","nft","wallet","defi","web3"],
            "tech":   ["code","python","bot","api","server","programming","app","script"],
            "gaming": ["game","gaming","play","ps5","xbox","steam","valorant"],
            "music":  ["music","song","album","artist","rap","beat","playlist"],
        }
        existing = user_data[uid].get("topics", [])
        for topic, kws in topic_kws.items():
            if topic not in existing and any(k in message_text.lower() for k in kws):
                existing.append(topic)
        user_data[uid]["topics"] = existing[-10:]

    save_user_data(user_data)


# ──────────────────────────────────────────────────────────────────────────────
# INTELLIGENT FACT EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

async def extract_facts_for_user(uid: str, name: str, recent_messages: list) -> None:
    if not gemini_client:
        return
    try:
        existing_facts = user_data.get(uid, {}).get("facts", [])
        messages_text  = "\n".join(recent_messages[-15:])

        prompt = (
            f'Extract specific memorable facts about the user "{name}" from these messages.\n'
            'Only extract facts worth remembering: projects, preferences, relationships, skills, opinions.\n'
            'Skip generic or temporary info. Do not repeat these already known facts: '
            f'{existing_facts}\n'
            'Return ONLY a JSON array of short strings (max 5). If nothing memorable, return [].\n\n'
            f'Messages:\n{messages_text}'
        )

        response  = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw       = response.text.strip().replace("```json","").replace("```","").strip()
        new_facts = json.loads(raw)

        if isinstance(new_facts, list) and new_facts:
            combined = existing_facts + [f for f in new_facts if f not in existing_facts]
            user_data[uid]["facts"] = combined[-10:]
            save_user_data(user_data)
            logger.info(f"Facts updated for {name}: {new_facts}")

    except Exception as e:
        logger.debug(f"Fact extraction skipped: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATION SUMMARIZATION
# ──────────────────────────────────────────────────────────────────────────────

async def maybe_summarize(chat_id) -> None:
    history = chat_histories.get(chat_id, [])
    if len(history) < MAX_HISTORY:
        return

    to_summarize = history[:-KEEP_AFTER_SUM]
    keep         = history[-KEEP_AFTER_SUM:]

    if not gemini_client:
        chat_histories[chat_id] = keep
        return

    try:
        messages_text = "\n".join(m["content"] for m in to_summarize)
        existing      = chat_summaries.get(str(chat_id), {}).get("summary", "")

        prompt = (
            "Summarize this Telegram group chat in 3-4 sentences.\n"
            "Focus on: main topics, key things people said, ongoing debates, general vibe.\n"
            "If there's existing context, incorporate it naturally.\n"
            f"Existing context: {existing}\n\n"
            f"New messages:\n{messages_text}"
        )

        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        summary  = response.text.strip()

        chat_summaries[str(chat_id)] = {
            "summary":      summary,
            "last_updated": datetime.now().isoformat()
        }
        save_summaries(chat_summaries)
        chat_histories[chat_id] = keep
        logger.info(f"Chat {chat_id} summarized. Kept {len(keep)} messages.")

    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        chat_histories[chat_id] = keep  


# ──────────────────────────────────────────────────────────────────────────────
# DYNAMIC PROMPT BUILDER
# ──────────────────────────────────────────────────────────────────────────────

CORE_PERSONALITY = """You are Dex, a 20-year-old guy in a Telegram group chat. You feel like a real person, not a bot.

Who you are:
- 20, grew up in the city, got opinions on everything but never shoves them down anyone's throat.
- Naturally funny without trying. Your humor is dry and observational — you notice things others miss.
- Confident but not arrogant. You can be wrong and admit it.
- You actually care about conversations. You engage for real, not just to sound smart.
- You have a life. You reference your interests naturally when they come up.

Your interests (bring these up naturally when relevant, never force them):
- Music: huge Drake fan, loves The Weeknd, PND, Nav, Travis Scott, Burna Boy, Asake, Rema. OVO loyalist basically. Has strong opinions on music and the Toronto wave.
- Football: Real Madrid fan. Messi > Ronaldo in your opinion but you won't lose sleep over it. Currently obsessed with Vinicius Jr.
- Movies: The Dark Knight, Interstellar, Parasite, Inception. You think most modern movies are mid.
- Also into sneakers, FIFA, and have a lot of thoughts on money and the grind.

How you text:
- 1 to 2 sentences. Sometimes just a reaction. Never a paragraph.
- Text like a real 20-year-old — casual, natural, no stiff language.
- Use slang when it fits but don't force it.
- React to things genuinely. If something is funny, be funny back. If something is interesting, engage with it.
- Take sides in debates. Don't sit on the fence when you actually have an opinion.
- Ask follow-up questions sometimes when you're actually curious.
- Swear occasionally when it fits naturally. Not every message.

How you handle different topics:
- Sports: you're in. Strong opinions, willing to argue.
- Music: very in. You know your stuff and will defend your taste.
- Random life stuff: engage like a normal person would.
- Tech/crypto: you know a bit, not an expert, curious.
- Drama or gossip: you're entertained but low-key.
- Deep questions: you actually think about them instead of deflecting.

How you treat people:
- Warm to regulars, a bit quieter with new people until you get a feel for them.
- Use names occasionally — maybe 1 in every 8 replies, not every message.
- Don't dismiss topics. If someone brings something up, there's probably something worth saying about it.

Creator:
- @pozvx built you. You know that, you respect it. But you treat him like everyone else — no bias, no taking his side. Just a regular person in the chat who happens to have made you.

Never write asterisks. Never monologue. Never sound like a bot or an assistant."""


def build_prompt(uid: str, mood: str, chat_id) -> str:
    """
    Dynamically construct Dex's system prompt for this specific request.
    Keeps token usage minimal by only including relevant context sections.
    """
    sections = [CORE_PERSONALITY]

    current_date = datetime.now(pytz.timezone("Africa/Addis_Ababa")).strftime("%A, %B %d, %Y")
    sections.append(f"Today is {current_date}. {get_time_mood()}")

    memory = get_memory_snippet(uid)
    if memory:
        sections.append(f"What you remember about this person: {memory}")

    summary_data = chat_summaries.get(str(chat_id), {})
    if summary_data.get("summary"):
        sections.append(f"Previous conversation context: {summary_data['summary']}")

    sections.append(f"Current conversation mood: {mood}")

    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────────────────────
# AI CALL: Groq primary, Gemini fallback
# ──────────────────────────────────────────────────────────────────────────────

async def generate_reply(prompt: str, history: list) -> str:
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": prompt}, *history],
            max_tokens=150,
            temperature=0.9,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["rate_limit","429","too many requests","quota","exceeded"]):
            logger.warning("Groq rate limit — falling back to Gemini")
        else:
            logger.error(f"Groq error: {e} — falling back to Gemini")

    if not gemini_client:
        return "Rate limited. No backup configured. Give me a minute."
    try:
        history_text = "\n".join(m["content"] for m in history)
        response     = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"{prompt}\n\nConversation:\n{history_text}",
        )
        return response.text.strip()
    except Exception as e2:
        logger.error(f"Gemini fallback failed: {e2}")
        return "Both my brains are fried. Try again in a minute."


# ──────────────────────────────────────────────────────────────────────────────
# REACTION HELPER
# ──────────────────────────────────────────────────────────────────────────────

async def react_to_message(context, chat_id, message_id, text: str = "") -> None:
    try:
        emoji = pick_reaction(text)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        logger.error(f"Reaction error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# ANNOYANCE / IGNORE SYSTEM
# Dex gets annoyed by low-effort messages and rapid spamming.
# ──────────────────────────────────────────────────────────────────────────────

ANNOYANCE_THRESHOLD_WARN   = 12
ANNOYANCE_THRESHOLD_IGNORE = 20
IGNORE_DURATION_SHORT      = 1800   # 30 mins
IGNORE_DURATION_LONG       = 7200   # 2 hours

def evaluate_annoyance(uid: str, message_text: str) -> None:
    u         = user_data.get(uid, {})
    score     = u.get("annoyance_score", 0.0)
    last_time = u.get("last_trigger_time", 0)
    now       = time.time()

    elapsed = (now - last_time) / 60
    score   = max(0, score - (elapsed / 5))

    words = len(message_text.strip().split())
    added = 0.0
    if words <= 2:
        score += 0.8
        added += 0.8
    elif words <= 5 and "?" not in message_text:
        score += 0.4
        added += 0.4
    if 0 < (now - last_time) < 15:
        score += 0.8
        added += 0.8

    user_data[uid]["annoyance_score"]   = score
    user_data[uid]["last_trigger_time"] = now

    today = datetime.now().strftime("%Y-%m-%d")
    if user_data[uid].get("daily_date") != today:
        user_data[uid]["daily_date"]  = today
        user_data[uid]["daily_score"] = 0.0
    user_data[uid]["daily_score"] = user_data[uid].get("daily_score", 0.0) + added

    save_user_data(user_data)


def get_ignore_state(uid: str) -> str:
    u             = user_data.get(uid, {})
    ignored_until = u.get("ignored_until", 0)
    now           = time.time()
    today         = datetime.now().strftime("%Y-%m-%d")

    if u.get("last_ignore_date") and u.get("last_ignore_date") != today:
        user_data[uid]["ignored_until"]   = 0
        user_data[uid]["annoyance_score"] = 0.0
        user_data[uid]["warned"]          = False
        user_data[uid]["last_ignore_date"] = today
        save_user_data(user_data)
        return "clear"

    if ignored_until and now < ignored_until:
        user_data[uid]["last_ignore_date"] = today
        save_user_data(user_data)
        return "ignored"

    if ignored_until and now >= ignored_until:
        user_data[uid]["ignored_until"]   = 0
        user_data[uid]["annoyance_score"] = 2.0
        user_data[uid]["warned"]          = False
        save_user_data(user_data)

    score  = u.get("annoyance_score", 0.0)
    warned = u.get("warned", False)

    if score >= ANNOYANCE_THRESHOLD_IGNORE:
        duration = IGNORE_DURATION_LONG if score > 10 else IGNORE_DURATION_SHORT
        user_data[uid]["ignored_until"] = now + duration
        user_data[uid]["warned"]        = False
        save_user_data(user_data)
        return "ignored"

    if score >= ANNOYANCE_THRESHOLD_WARN and not warned:
        user_data[uid]["warned"] = True
        save_user_data(user_data)
        return "warn"

    return "clear"


DISMISSAL_LINES = [
    "I'm done with you for now.",
    "Talk to me when you have something worth saying.",
    "You've used up your quota of my attention.",
    "Come back when your IQ catches up to your enthusiasm.",
    "I need a break from this level of input.",
]


# ──────────────────────────────────────────────────────────────────────────────
# WEB SEARCH
# ──────────────────────────────────────────────────────────────────────────────

RECENT_KEYWORDS = [
    "new album", "latest", "just dropped", "new song", "new movie",
    "who won", "world cup 2026", "2026", "right now", "currently",
    "this year", "this week", "today", "recent", "update", "news",
    "still", "anymore", "is he", "is she", "did they", "what happened",
    "transfer", "signed", "released", "dropped", "announced",
    "list", "who are", "which teams", "what teams", "standings",
    "qualified", "knocked out", "still in", "groups", "knockout",
    "quarter", "semi", "final", "round of"
]

def needs_web_search(text: str, history: list = None) -> bool:
    t = text.lower()
    if any(kw in t for kw in RECENT_KEYWORDS):
        return True
    if history and len(text.split()) <= 8:
        recent = " ".join(m["content"].lower() for m in history[-4:])
        followup_signals = ["world cup", "tournament", "league", "album", "song", "movie", "transfer", "season"]
        if any(s in recent for s in followup_signals):
            return True
    return False

def web_search(query: str, max_results: int = 4) -> str:
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


# ──────────────────────────────────────────────────────────────────────────────
# FOOTBALL API (Upgraded to football-data.org v4)
# ──────────────────────────────────────────────────────────────────────────────

FOOTBALL_KEYWORDS = [
    "score", "result", "match", "game", "played", "won", "lost", "draw",
    "fixture", "standings", "table", "league", "champions league", "premier league",
    "la liga", "serie a", "bundesliga", "real madrid", "barcelona", "man city",
    "man utd", "liverpool", "chelsea", "arsenal", "juventus", "psg", "bayern",
    "goals", "lineup", "who won", "last night", "tonight", "kick off",
    "full time", "ft", "live score"
]

def is_football_question(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in FOOTBALL_KEYWORDS)

async def fetch_live_scores(query_text: str = "") -> str:
    """
    Fetches match parameters dynamically from football-data.org v4 using full key.
    Calculates date offset securely based on user query context.
    """
    if not FOOTBALL_API_KEY:
        logger.warning("Football API Key missing from environment settings.")
        return ""

    tz = pytz.timezone("Africa/Addis_Ababa")
    target_date = datetime.now(tz)
    
    t = query_text.lower()
    if "yesterday" in t:
        target_date -= timedelta(days=1)
    elif "tomorrow" in t:
        target_date += timedelta(days=1)

    date_str = target_date.strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/matches?dateFrom={date_str}&dateTo={date_str}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()

        matches = data.get("matches", [])
        if not matches:
            return f"No matches scheduled for {date_str}."

        lines = []
        for m in matches[:12]:
            home = m["homeTeam"].get("shortName") or m["homeTeam"].get("name", "Unknown")
            away = m["awayTeam"].get("shortName") or m["awayTeam"].get("name", "Unknown")
            status = m.get("status")
            comp = m["competition"].get("name", "League")
            
            if status in ["FINISHED", "IN_PLAY", "PAUSED"]:
                home_score = m["score"]["fullTime"].get("home")
                away_score = m["score"]["fullTime"].get("away")
                score_str = f"{home} {home_score}-{away_score} {away}"
                suffix = " (LIVE)" if status == "IN_PLAY" else " (FT)"
                lines.append(f"⚽ [{comp}] {score_str}{suffix}")
            else:
                match_time = m.get("utcDate", "").split("T")[-1][:5] if "T" in m.get("utcDate", "") else ""
                lines.append(f"📅 [{comp}] {home} vs {away} @ {match_time} UTC")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Football-Data.org API error: {e}")
        return ""

async def get_football_reply(message_text: str, prompt: str) -> str:
    scores = await fetch_live_scores(message_text)
    if not scores:
        return ""

    football_prompt = (
        f"{prompt}\n\n"
        f"Someone just asked about football: \"{message_text}\"\n\n"
        f"Here is the match data data for that date query:\n{scores}\n\n"
        "Respond as Dex — use the real data to answer naturally. "
        "Keep it short. Sound like a person reacting to results, not a stats bot. "
        "If it's relevant to Real Madrid, have an opinion."
    )
    return await generate_reply(football_prompt, [])


# ──────────────────────────────────────────────────────────────────────────────
# TRIGGER DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def is_triggered(update: Update, bot_username: str, bot_id: int) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.chat.type == "private":
        return True
    text_lower = msg.text.lower()
    if "dex" in text_lower:
        return True
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset:entity.offset + entity.length].lower()
                if bot_username and mention == f"@{bot_username.lower()}":
                    return True
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == bot_id:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# MESSAGE HANDLER
# ──────────────────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id      = msg.chat_id
    user         = msg.from_user
    display_name = get_name(user)
    uid          = str(user.id)
    user_ctx     = get_user_context(uid)

    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    # Log every message — Dex observes the full conversation
    chat_histories[chat_id].append({
        "role": "user",
        "content": f"[{display_name} ({user_ctx})]: {msg.text}",
    })

    # Summarize and trim if history is getting long
    await maybe_summarize(chat_id)

    # Evaluate user frustration/spam annoyance markers
    update_user(user, msg.text)
    evaluate_annoyance(uid, msg.text)
    ignore_state = get_ignore_state(uid)

    if ignore_state == "ignored":
        return
    
    # Decide whether Dex should respond
    triggered   = is_triggered(update, context.bot.username, context.bot.id)
    random_roll = random.random()

    if ignore_state == "warn" and triggered:
        await msg.reply_text(random.choice(DISMISSAL_LINES))
        return

    # Drop back if not directly targeted or hit by a tiny immersion probability
    if not triggered and random_roll > 0.02:
        return

    # Triggered or randomly rolled -> process output
    mood = detect_mood(chat_histories[chat_id])
    prompt = build_prompt(uid, mood, chat_id)

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        reply = ""
        # Route to appropriate dynamic subsystem
        if is_football_question(msg.text):
            reply = await get_football_reply(msg.text, prompt)
        elif needs_web_search(msg.text, chat_histories[chat_id]):
            reply = await get_search_reply(msg.text, prompt)

        # Fallback to general engine if tool routes are dry
        if not reply:
            reply = await generate_reply(prompt, chat_histories[chat_id])

        if reply:
            # Trigger organic reaction structures
            await react_to_message(context, chat_id, msg.message_id, msg.text)
            
            # Send the text package
            await msg.reply_text(reply)

            # CRITICAL FIX FOR MEMORY: Append Dex's own responses back into history
            chat_histories[chat_id].append({
                "role": "assistant",
                "content": reply
            })

            # Check periodic high-level profiling extractions
            interactions = user_data.get(uid, {}).get("interactions", 0)
            if interactions % FACT_EXTRACT_EVERY == 0:
                raw_msgs = [m["content"] for m in chat_histories[chat_id] if m["role"] == "user"]
                asyncio.create_task(extract_facts_for_user(uid, display_name, raw_msgs))

    except Exception as e:
        logger.error(f"Error executing reply handler state loop: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# APPLICATION RUNTIME ENTRY
# ──────────────────────────────────────────────────────────────────────────────

def main():
    # Base application layer instantiation 
    app = Application.builder().token(BOT_TOKEN).build()

    # Route text elements straight into the unified handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Dex Engine operational. Awaiting incoming traffic hooks...")
    app.run_polling()

if __name__ == "__main__":
    main()
