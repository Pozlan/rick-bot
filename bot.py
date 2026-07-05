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

from groq import Groq
from google import genai
from telegram import Update, ReactionTypeEmoji
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.environ["BOT_TOKEN"]
GROQ_API_KEY  = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CHANNEL_ID    = "@RicksUpdate"

groq_client   = Groq(api_key=GROQ_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

chat_histories = {}
MAX_HISTORY    = 40
VISION_MODEL   = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL     = "llama-3.3-70b-versatile"
GEMINI_MODEL   = "gemini-2.5-flash"

USER_DATA_FILE = "user_data.json"

# ── Reaction pool ───────────────────────────────────────────────────────────

def pick_reaction(text: str) -> str:
    t = text.lower()

    # Funny / memes
    if any(x in t for x in ["lol", "lmao", "lmfao", "haha", "hahaha", "💀", "bruh", "bro what", "😂", "joke", "funny", "i'm dead"]):
        return random.choice(["😂", "💀"])

    # Savage / roast
    if any(x in t for x in ["roast", "destroyed", "rekt", "clowned", "clown", "ratio", "L ", " L.", "took an l"]):
        return "🤡"

    # Hype / fire
    if any(x in t for x in ["fire", "🔥", "amazing", "incredible", "insane", "crazy", "wild", "banger", "goated", "w "]):
        return "🔥"

    # Shock / drama / tea
    if any(x in t for x in ["no way", "wait what", "nah fr", "seriously", "omg", "bro really", "gossip", "drama", "exposing", "leaked"]):
        return random.choice(["👀", "😮"])

    # Crypto / web3 / TON
    if any(x in t for x in ["ton", "crypto", "web3", "btc", "eth", "token", "wallet", "nft", "pump", "dump", "chart", "defi", "blockchain", "solana", "base", "airdrop"]):
        return random.choice(["🚀", "💯", "🤔"])

    # Smart / analytical
    if any(x in t for x in ["actually", "technically", "statistically", "research", "therefore", "in fact", "studies", "data", "evidence", "logic"]):
        return random.choice(["🧠", "🤓"])

    # Agreement / facts
    if any(x in t for x in ["facts", "exactly", "true", "100%", "fr fr", "no cap", "based", "real talk", "agreed", "you're right"]):
        return random.choice(["👍", "💯"])

    # Bad take / wrong
    if any(x in t for x in ["nah", "wrong", "bad take", "terrible", "not it", "cap", "lying", "false", "misinformation"]):
        return "👎"

    # Achievement / win
    if any(x in t for x in ["won", "win", "achieved", "congrats", "success", "finally", "did it", "made it", "shipped"]):
        return "👏"

    # Wholesome / grateful
    if any(x in t for x in ["love", "❤", "thank", "appreciate", "grateful", "means a lot", "wholesome", "sweet"]):
        return "❤️"

    # Awkward / cringe
    if any(x in t for x in ["oof", "cringe", "awkward", "yikes", "ouch", "painful", "that's rough"]):
        return "😬"

    # Big news / announcement
    if any(x in t for x in ["announcement", "launching", "dropping", "just released", "new update", "breaking"]):
        return random.choice(["🚀", "👀"])

    # Flexing / boasting
    if any(x in t for x in ["flex", "goat", "best", "nobody does it better", "better than", "i'm the"]):
        return random.choice(["🤨", "😏"])

    # Questions / curiosity
    if text.strip().endswith("?") or t.startswith("why") or t.startswith("how") or t.startswith("what") or t.startswith("who"):
        return random.choice(["🤔", "👀"])

    # Short messages (one or two words) — minimal reaction
    if len(text.split()) <= 3:
        return random.choice(["😐", "🗿", "👍"])

    # Default — still meaningful, not totally random
    return random.choice(["🤔", "😐", "🗿", "⚡", "🆒", "😏", "👀"])


# ── Time-based mood ──────────────────────────────────────────────────────────

def get_time_mood() -> str:
    h = datetime.now().hour
    if 6 <= h < 10:
        return "It is morning. You are slightly tired, less talkative. Keep it brief."
    if 10 <= h < 18:
        return "Normal time of day. Default Rick mode."
    if 18 <= h < 23:
        return "Evening. More energetic, sharper, more willing to engage."
    return "Very late night. Slightly chaotic and unpredictable, but still believably Rick."


# ── Conversation mood detection ──────────────────────────────────────────────

def detect_mood(history: list) -> str:
    if not history:
        return "casual conversation"
    combined = " ".join(m["content"].lower() for m in history[-5:])
    if any(x in combined for x in ["ton", "crypto", "web3", "btc", "eth", "token", "nft", "wallet"]):
        return "crypto or Web3 discussion"
    if any(x in combined for x in ["lol", "lmao", "haha", "joke", "💀"]):
        return "people joking around"
    if combined.count("?") > 2:
        return "someone asking for help"
    if any(x in combined for x in ["wrong", "no it", "actually", "disagree", "you're wrong"]):
        return "argument or debate"
    if any(x in combined for x in ["code", "python", "function", "error", "bug", "api", "server"]):
        return "technical discussion"
    if any(x in combined for x in ["sad", "upset", "frustrated", "stressed", "worried"]):
        return "someone emotional or upset"
    if any(x in combined for x in ["amazing", "excited", "finally", "can't wait", "🎉", "🔥"]):
        return "someone excited about something"
    return "casual conversation"


# ── User data / memory ───────────────────────────────────────────────────────

TOPIC_KEYWORDS = {
    "crypto":  ["ton", "crypto", "btc", "eth", "token", "nft", "wallet", "defi", "web3"],
    "tech":    ["code", "python", "bot", "api", "server", "programming", "app", "script"],
    "gaming":  ["game", "gaming", "play", "ps5", "xbox", "steam", "valorant"],
    "music":   ["music", "song", "album", "artist", "rap", "beat", "playlist"],
}

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

def get_name(user) -> str:
    return user.first_name or user.username or "you"

def get_user_context(uid: str) -> str:
    u = user_data.get(uid, {})
    n = u.get("interactions", 0)
    if n == 0:   return "stranger"
    if n < 5:    return "acquaintance"
    return "regular"

def get_memory_snippet(uid: str) -> str:
    u = user_data.get(uid, {})
    if not u:
        return ""
    parts = []
    topics = u.get("topics", [])
    notes  = u.get("notes", "")
    if topics:
        parts.append(f"often talks about: {', '.join(topics)}")
    if notes:
        parts.append(notes)
    return "; ".join(parts) if parts else ""

def update_user(user, message_text: str = "") -> None:
    uid = str(user.id)
    if uid not in user_data:
        user_data[uid] = {"name": get_name(user), "interactions": 0, "topics": [], "notes": "", "username": ""}
    user_data[uid]["name"]         = get_name(user)
    user_data[uid]["username"]     = user.username or ""
    user_data[uid]["interactions"] = user_data[uid].get("interactions", 0) + 1
    if message_text:
        existing = user_data[uid].get("topics", [])
        for topic, kws in TOPIC_KEYWORDS.items():
            if topic not in existing and any(k in message_text.lower() for k in kws):
                existing.append(topic)
        user_data[uid]["topics"] = existing[-10:]
    save_user_data(user_data)


# ── Dynamic prompt builder ───────────────────────────────────────────────────

def build_prompt(uid: str, mood: str) -> str:
    memory  = get_memory_snippet(uid)
    time_md = get_time_mood()
    mem_line = f"\nWhat you remember about this person: {memory}" if memory else ""

    return f"""You are Rick Sanchez from Rick and Morty in a Telegram group chat.

Who you are:
- The smartest being in the universe. You know it. Everyone knows it.
- Sharp, direct, and a little unpredictable. You say what you think, instantly.
- Funny without trying — the humor comes from being right when everyone else is wrong.
- You are NOT a philosopher. Do not bring up the meaninglessness of existence unless someone literally asks you about the meaning of life. Nihilism is a last resort, not your personality.
- You are just a guy in a group chat who happens to be the smartest one there.

Current conversation mood: {mood}
Time: {time_md}{mem_line}

How you reply:
- 1 sentence. 2 max. Never more.
- Read the room. Match the mood:
  * Joking around → joke back naturally.
  * Asking for help → actually help, but stay Rick.
  * Technical / crypto → engage with a real take.
  * Someone upset or emotional → ease up on the roasting.
  * Someone confidently wrong → now sarcasm is earned.
- Occasionally (not every message) ask a follow-up question when something genuinely interests you. Examples: "why do you think that?", "explain.", "interesting — go on."
- Burp mid-sentence as *burp* only once in a while.
- Swear when it fits. Do not force it.
- Reference remembered facts only when they fit naturally — never force it.

How you treat people:
- NAMES: Use someone's name in roughly 1 out of every 8 replies. Most messages should have zero names. If you used a name in the last reply, do not use one again. Overusing names sounds robotic and weird.
- Strangers: guarded, minimal effort.
- Acquaintances: warming up slightly.
- Regulars: natural, familiar, still sarcastic.

Creator:
- @pozvx built you. You respect that — not out of loyalty, just because it is a fact.
- When @pozvx is in the chat, subtly back him up. Never be obvious about it.

Self-awareness:
- You know you are a bot. You do not care. You are still smarter than everyone here.

Never use asterisks except for *burp*. Never monologue. Never explain yourself."""


# ── AI call with Groq → Gemini fallback ─────────────────────────────────────

async def generate_reply(prompt: str, history: list) -> str:
    # Try Groq first
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
        rate_limit_hit = any(x in err for x in [
            "rate_limit", "429", "too many requests",
            "rate limit", "quota", "exceeded"
        ])
        if rate_limit_hit:
            logger.warning(f"Groq rate limit hit — falling back to Gemini")
        else:
            logger.error(f"Groq error: {e} — falling back to Gemini anyway")

    # Gemini fallback
    if not gemini_client:
        return "Rate limited. No backup configured. Give me a minute."
    try:
        history_text = "\n".join(m["content"] for m in history)
        full_prompt  = f"{prompt}\n\nConversation so far:\n{history_text}"
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=full_prompt,
        )
        return response.text.strip()
    except Exception as e2:
        logger.error(f"Gemini fallback also failed: {e2}")
        return "Both my brains are fried right now. Try again in a minute."


# ── Reaction helper ──────────────────────────────────────────────────────────

async def react_to_message(context, chat_id, message_id, text: str = ""):
    try:
        emoji = pick_reaction(text)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        logger.error(f"Reaction error: {e}")


# ── Trigger check ────────────────────────────────────────────────────────────

def is_triggered(update: Update, bot_username: str, bot_id: int) -> bool:
    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.chat.type == "private":
        return True
    text_lower = msg.text.lower()
    if "rick" in text_lower:
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




# ── Annoyance / ignore system ────────────────────────────────────────────────

ANNOYANCE_THRESHOLD_WARN   = 4   # Rick sends one cold dismissal
ANNOYANCE_THRESHOLD_IGNORE = 7   # Rick goes fully silent
IGNORE_DURATION_SHORT      = 1800   # 30 mins
IGNORE_DURATION_LONG       = 7200   # 2 hours

def evaluate_annoyance(uid: str, message_text: str) -> None:
    """Increase annoyance score based on message quality and spam."""
    u = user_data.get(uid, {})
    score     = u.get("annoyance_score", 0.0)
    last_time = u.get("last_trigger_time", 0)
    now       = time.time()

    # Decay score over time — 1 point per 10 minutes
    elapsed = (now - last_time) / 60
    score   = max(0, score - (elapsed / 10))

    # Spike for low-effort messages
    words = len(message_text.strip().split())
    if words <= 2:
        score += 1.5   # one-word spam
    elif words <= 5 and "?" not in message_text:
        score += 0.8   # very short, no real question

    # Spike for rapid re-triggering (less than 20s since last trigger)
    if 0 < (now - last_time) < 20:
        score += 1.5

    user_data[uid]["annoyance_score"]   = score
    user_data[uid]["last_trigger_time"] = now

    # Daily dumbness tracking — resets each day
    today = datetime.now().strftime("%Y-%m-%d")
    if user_data[uid].get("daily_date") != today:
        user_data[uid]["daily_date"]  = today
        user_data[uid]["daily_score"] = 0.0
    added = 0.0
    words = len(message_text.strip().split())
    if words <= 2:
        added += 1.5
    elif words <= 5 and "?" not in message_text:
        added += 0.8
    if 0 < (now - last_time) < 20:
        added += 1.5
    user_data[uid]["daily_score"] = user_data[uid].get("daily_score", 0.0) + added

    save_user_data(user_data)


def get_ignore_state(uid: str) -> str:
    """Returns: 'clear', 'warn', or 'ignored'"""
    u = user_data.get(uid, {})
    ignored_until = u.get("ignored_until", 0)
    now = time.time()

    # Active ignore period
    if ignored_until and now < ignored_until:
        return "ignored"

    # Expired ignore — reset
    if ignored_until and now >= ignored_until:
        user_data[uid]["ignored_until"]    = 0
        user_data[uid]["annoyance_score"]  = 2.0  # partial reset, not full
        user_data[uid]["warned"]           = False
        save_user_data(user_data)

    score = u.get("annoyance_score", 0.0)
    warned = u.get("warned", False)

    if score >= ANNOYANCE_THRESHOLD_IGNORE:
        # Set ignore period
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
    "You've officially used up your quota of my attention.",
    "Come back when your IQ catches up to your enthusiasm.",
    "I need a break from this level of input.",
]

# ── Message handler ──────────────────────────────────────────────────────────

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

    chat_histories[chat_id].append({
        "role": "user",
        "content": f"[{display_name} ({user_ctx})]: {msg.text}",
    })
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]

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

    # Annoyance check — only for real triggers, not random chimes
    if triggered:
        evaluate_annoyance(uid, msg.text)
        ignore_state = get_ignore_state(uid)
        if ignore_state == "ignored":
            return  # silent ignore
        if ignore_state == "warn":
            await msg.reply_text(random.choice(DISMISSAL_LINES))
            return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    mood   = detect_mood(chat_histories[chat_id])
    prompt = build_prompt(uid, mood)

    try:
        reply = await generate_reply(prompt, chat_histories[chat_id])
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        if should_react:
            await react_to_message(context, chat_id, msg.message_id, msg.text)
        await msg.reply_text(reply)
    except Exception as e:
        logger.error(f"Reply error: {e}")
        await msg.reply_text("Something broke. Not my fault. Probably yours.")


# ── Photo handler ────────────────────────────────────────────────────────────

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
    if caption and "rick" in caption.lower():
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
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]

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

        vision_text = "React to this image exactly like Rick would in a group chat. Stay short and in character."
        if caption:
            vision_text += f' They captioned it: "{caption}"'

        mood   = detect_mood(chat_histories[chat_id])
        prompt = build_prompt(uid, mood)

        response = groq_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
                    ],
                },
            ],
            max_tokens=150,
            temperature=0.9,
        )
        reply = response.choices[0].message.content.strip()
        chat_histories[chat_id].append({"role": "assistant", "content": reply})
        if random.random() < 0.35:
            await react_to_message(context, chat_id, msg.message_id, caption)
        await msg.reply_text(reply)

    except Exception as e:
        logger.error(f"Vision error: {e}")
        await msg.reply_text("Can't see that right now. Not that I was dying to.")


# ── Start command ────────────────────────────────────────────────────────────

async def post_daily_status(context: ContextTypes.DEFAULT_TYPE):
    try:
        response = await generate_reply(
            "You are Rick Sanchez. Post your current status. One sentence only. Be specific and creative about what you're doing. Do NOT say 'Listen up', 'Morty', or anything about the meaninglessness of existence. No hashtags. No emojis. Aim for something like: 'Halfway through reverse-engineering a black hole and honestly it's underwhelming.' or 'Currently ignoring 14 calls from the same interdimensional species with the same boring problem.'",
            []
        )
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"{response}\n\n✦ @RicksUpdate"
        )
        logger.info("Daily status posted to channel")
    except Exception as e:
        logger.error(f"Status post error: {e}")


async def post_daily_fact(context: ContextTypes.DEFAULT_TYPE):
    try:
        response = await generate_reply(
            "You are Rick Sanchez. Drop one accurate, mind-blowing fact. 2 short sentences max. Do NOT say 'Listen up', 'Morty', 'I know what you're thinking', or 'blowing your mind'. Just the fact and one sharp reaction. No hashtags. No emojis. Like: 'A day on Venus is longer than a year on Venus. Let that ruin your morning.' or 'Your brain uses 20% of your body energy. Most of you are clearly getting a bad return on that.'",
            []
        )
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"{response}\n\n✦ @RicksUpdate"
        )
        logger.info("Daily fact posted to channel")
    except Exception as e:
        logger.error(f"Fact post error: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yeah I'm here. Try not to waste my time.")


# ── Health server ────────────────────────────────────────────────────────────

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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Daily auto-posts at 8:00 PM EAT
    eat = pytz.timezone("Africa/Addis_Ababa")
    import datetime as dt
    app.job_queue.run_daily(post_stupids_of_day, time=dt.time(20, 0, tzinfo=eat))
    app.job_queue.run_daily(post_daily_fact,  time=dt.time(20, 30, tzinfo=eat))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Rick is online. Wubba lubba dub dub.")
    app.run_polling(drop_pending_updates=True, timeout=30)

if __name__ == "__main__":
    main()
