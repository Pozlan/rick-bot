import os
import logging
import random
import threading
import json
import base64
import asyncio
import time
from datetime import datetime
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
BOT_TOKEN      = os.environ["BOT_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
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
# CONVERSATION MOOD DETECTION (Enhanced)
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
# On restart, Dex loads these and remembers what happened before.
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
# Uses Gemini to extract specific, memorable facts about a user from their messages.
# Runs every FACT_EXTRACT_EVERY interactions (not every message — keeps API cost low).
# Deduplicates against existing facts. Stores max 10 facts per user.
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
# When history hits MAX_HISTORY, summarize old messages with Gemini.
# Persist the summary so it survives restarts.
# Keep only the last KEEP_AFTER_SUM messages in active history.
# ──────────────────────────────────────────────────────────────────────────────

async def maybe_summarize(chat_id) -> None:
    history = chat_histories.get(chat_id, [])
    if len(history) < MAX_HISTORY:
        return

    to_summarize = history[:-KEEP_AFTER_SUM]
    keep         = history[-KEEP_AFTER_SUM:]

    if not gemini_client:
        # No Gemini — just trim to avoid unbounded growth
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
        chat_histories[chat_id] = keep  # trim anyway


# ──────────────────────────────────────────────────────────────────────────────
# DYNAMIC PROMPT BUILDER
# Builds Dex's system prompt fresh for every request.
# Combines: core personality + time mood + user memory + conversation summary + mood.
# This is modular so each section can be updated independently.
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

    # Section 2: Current date + time mood
    current_date = datetime.now(pytz.timezone("Africa/Addis_Ababa")).strftime("%A, %B %d, %Y")
    sections.append(f"Today is {current_date}. {get_time_mood()}")

    # Section 3: Persistent user memory (facts or topics)
    memory = get_memory_snippet(uid)
    if memory:
        sections.append(f"What you remember about this person: {memory}")

    # Section 4: Persistent conversation summary (survives restarts)
    summary_data = chat_summaries.get(str(chat_id), {})
    if summary_data.get("summary"):
        sections.append(f"Previous conversation context: {summary_data['summary']}")

    # Section 5: Current conversation mood
    sections.append(f"Current conversation mood: {mood}")

    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────────────────────
# AI CALL: Groq primary, Gemini fallback
# Catches rate limits and other errors gracefully.
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
# Score decays naturally over time. Two stages: warning, then silence.
# ──────────────────────────────────────────────────────────────────────────────

ANNOYANCE_THRESHOLD_WARN   = 12
ANNOYANCE_THRESHOLD_IGNORE = 20
IGNORE_DURATION_SHORT      = 1800   # 30 mins
IGNORE_DURATION_LONG       = 7200   # 2 hours

def evaluate_annoyance(uid: str, message_text: str) -> None:
    """Track annoyance score and daily dumbness score for a user."""
    u         = user_data.get(uid, {})
    score     = u.get("annoyance_score", 0.0)
    last_time = u.get("last_trigger_time", 0)
    now       = time.time()

    # Natural decay: -1 point per 10 minutes idle
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

    # Daily dumbness tracking (for stupids-of-day channel post)
    today = datetime.now().strftime("%Y-%m-%d")
    if user_data[uid].get("daily_date") != today:
        user_data[uid]["daily_date"]  = today
        user_data[uid]["daily_score"] = 0.0
    user_data[uid]["daily_score"] = user_data[uid].get("daily_score", 0.0) + added

    save_user_data(user_data)


def get_ignore_state(uid: str) -> str:
    """Returns 'clear', 'warn', or 'ignored'."""
    u             = user_data.get(uid, {})
    ignored_until = u.get("ignored_until", 0)
    now           = time.time()
    today         = datetime.now().strftime("%Y-%m-%d")

    # Full forgiveness at the start of each new day
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
# Dex uses DuckDuckGo to look up recent info when someone asks about
# something that might be past the model's knowledge cutoff.
# No API key needed — completely free.
# ──────────────────────────────────────────────────────────────────────────────

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

# ──────────────────────────────────────────────────────────────────────────────
# FOOTBALL API
# Detects football-related questions in messages and fetches live data.
# Dex responds with actual match info in his own voice, not raw stats.
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

    # Decide whether Dex should respond
    triggered    = is_triggered(update, context.bot.username, context.bot.id)
    random_roll  = random.random()
    random_chime = not triggered and msg.chat.type != "private" and random_roll < 0.10
    react_only   = not triggered and msg.chat.type != "private" and 0.10 <= random_roll < 0.18

    if react_only:
        await react_to_message(context, chat_id, msg.message_id, msg.text)
        return

    if not triggered and not random_chime:
        return

    should_react = random.random() < 0.35
    update_user(user, msg.text)

    # Annoyance check (only for direct triggers, not random chimes)
    if triggered:
        evaluate_annoyance(uid, msg.text)
        ignore_state = get_ignore_state(uid)
        if ignore_state == "ignored":
            return
        if ignore_state == "warn":
            await msg.reply_text(random.choice(DISMISSAL_LINES))
            return

    # Fire-and-forget fact extraction every N interactions
    interactions = user_data.get(uid, {}).get("interactions", 0)
    if interactions % FACT_EXTRACT_EVERY == 0:
        recent_msgs = [m["content"] for m in chat_histories.get(chat_id, [])[-20:]]
        asyncio.create_task(extract_facts_for_user(uid, display_name, recent_msgs))

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    mood   = detect_mood(chat_histories[chat_id])
    prompt = build_prompt(uid, mood, chat_id)

    try:
        # Web search for recent/current info
        if needs_web_search(msg.text):
            search_reply = await get_search_reply(msg.text, prompt)
            if search_reply:
                reply = search_reply
            # Fall through to football or normal if search returns nothing
            else:
                if is_football_question(msg.text):
                    football_reply = await get_football_reply(msg.text, prompt)
                    reply = football_reply if football_reply else await generate_reply(prompt, chat_histories[chat_id])
                else:
                    reply = await generate_reply(prompt, chat_histories[chat_id])
        # Football question without needing web search
        elif is_football_question(msg.text):
            football_reply = await get_football_reply(msg.text, prompt)
            reply = football_reply if football_reply else await generate_reply(prompt, chat_histories[chat_id])
        else:
            reply = await generate_reply(prompt, chat_histories[chat_id])
        chat_histories[chat_id].append({"role": "assistant", "content": reply})

        # Human-like typing delay — proportional to reply length
        words = len(reply.split())
        delay = min(random.uniform(1.5, 2.5) + (words * 0.05), 5.0)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(delay)

        if should_react:
            await react_to_message(context, chat_id, msg.message_id, msg.text)
        await msg.reply_text(reply)

    except Exception as e:
        logger.error(f"Reply error: {e}")
        await msg.reply_text("Something broke. Not my fault. Probably yours.")


# ──────────────────────────────────────────────────────────────────────────────
# PHOTO HANDLER — Vision via Groq llama-4-scout
# ──────────────────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.photo:
        return

    chat_id      = msg.chat_id
    user         = msg.from_user
    display_name = get_name(user)
    uid          = str(user.id)
    user_ctx     = get_user_context(uid)
    caption      = msg.caption or ""

    triggered = msg.chat.type == "private"
    if caption and "dex" in caption.lower():
        triggered = True
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            triggered = True
    if msg.caption_entities:
        for entity in msg.caption_entities:
            if entity.type == "mention":
                mention = caption[entity.offset:entity.offset + entity.length].lower()
                if context.bot.username and mention == f"@{context.bot.username.lower()}":
                    triggered = True

    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    note = f"[{display_name} ({user_ctx})]: sent an image"
    if caption:
        note += f' with caption: "{caption}"'
    chat_histories[chat_id].append({"role": "user", "content": note})
    await maybe_summarize(chat_id)

    random_roll  = random.random()
    random_chime = not triggered and msg.chat.type != "private" and random_roll < 0.15
    react_only   = not triggered and msg.chat.type != "private" and 0.15 <= random_roll < 0.25

    if react_only:
        await react_to_message(context, chat_id, msg.message_id, caption)
        return
    if not triggered and not random_chime:
        return

    update_user(user, caption)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        photo      = msg.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        file_bytes = await photo_file.download_as_bytearray()
        b64_image  = base64.b64encode(bytes(file_bytes)).decode("utf-8")

        mood        = detect_mood(chat_histories[chat_id])
        prompt      = build_prompt(uid, mood, chat_id)
        vision_text = "React to this image exactly like Dex would in a group chat. Stay short and in character."
        if caption:
            vision_text += f' They captioned it: "{caption}"'

        response = groq_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text",      "text": vision_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                ]},
            ],
            max_tokens=150,
            temperature=0.9,
        )
        reply = response.choices[0].message.content.strip()
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        await asyncio.sleep(random.uniform(1.5, 3.5))
        if random.random() < 0.35:
            await react_to_message(context, chat_id, msg.message_id, caption)
        await msg.reply_text(reply)

    except Exception as e:
        logger.error(f"Vision error: {e}")
        await msg.reply_text("Can't see that right now. Not that I was dying to.")


# ──────────────────────────────────────────────────────────────────────────────
# CHANNEL AUTO-POSTS
# ──────────────────────────────────────────────────────────────────────────────

async def post_stupids_of_day(context: ContextTypes.DEFAULT_TYPE):
    """8 PM EAT: Post the top 3 most annoying users from today's group chats."""
    try:
        today      = datetime.now().strftime("%Y-%m-%d")
        candidates = []
        for uid, u in user_data.items():
            if u.get("daily_date") == today and u.get("daily_score", 0) > 0:
                handle = f"@{u['username']}" if u.get("username") else u.get("name", "someone")
                candidates.append((handle, u["daily_score"]))

        candidates.sort(key=lambda x: x[1], reverse=True)
        top = candidates[:3]

        if not top:
            text = "Nobody was particularly stupid today. Statistically, tomorrow will fix that.\n\n✦ @dexsupdate"
        else:
            names_list = "\n".join([f"{i+1}. {h}" for i, (h, _) in enumerate(top)])
            prompt = (
                f"{CORE_PERSONALITY}\n\n"
                "You are posting to your Telegram channel.\n"
                f"Write the 'Stupids of the Day' post. Candidates in order:\n{names_list}\n\n"
                "Rules: Short intro line, then each person gets a unique one-line roast. "
                "Under 6 lines total. No hashtags. No emojis. "
                "Sharp and funny, not mean-spirited."
            )
            response = await generate_reply(prompt, [])
            text     = f"{response}\n\n✦ @dexsupdate"

        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
        logger.info("Stupids of the day posted")
    except Exception as e:
        logger.error(f"Stupids post error: {e}")


async def post_daily_fact(context: ContextTypes.DEFAULT_TYPE):
    """8:30 PM EAT: Post a mind-blowing fact to the channel."""
    try:
        response = await generate_reply(
            f"{CORE_PERSONALITY}\n\n"
            "Drop one accurate, mind-blowing fact to your Telegram channel. 2 short sentences max. "
            "Do NOT say 'I know what you're thinking' or 'blowing your mind'. "
            "Just the fact and one sharp reaction. No hashtags. No emojis. "
            "Like: 'A day on Venus is longer than a year on Venus. Let that ruin your morning.'",
            []
        )
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"{response}\n\n✦ @dexsupdate"
        )
        logger.info("Daily fact posted")
    except Exception as e:
        logger.error(f"Fact post error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# START COMMAND
# ──────────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("yo what's good")


# ──────────────────────────────────────────────────────────────────────────────
# HEALTH SERVER — keeps Render free tier alive via UptimeRobot pings
# ──────────────────────────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"alive")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Schedule daily channel posts
    eat = pytz.timezone("Africa/Addis_Ababa")
    import datetime as dt
    if app.job_queue:
        app.job_queue.run_daily(post_stupids_of_day, time=dt.time(20, 0,  tzinfo=eat))
        app.job_queue.run_daily(post_daily_fact,     time=dt.time(20, 30, tzinfo=eat))
        logger.info("Daily posts scheduled: 8:00 PM and 8:30 PM EAT")
    else:
        logger.warning("JobQueue unavailable — install python-telegram-bot[job-queue]")

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Dex is online.")
    app.run_polling(drop_pending_updates=True, timeout=30)


if __name__ == "__main__":
    main()
