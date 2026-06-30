import os
import logging
import random
import threading
import json
import base64
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

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TON_WALLET = os.environ.get("TON_WALLET", "")

client = Groq(api_key=GROQ_API_KEY)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

chat_histories = {}
MAX_HISTORY = 40
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

USER_DATA_FILE = "user_data.json"

# Rick's curated reaction set — in character
RICK_REACTIONS = ["🤡", "🔥", "👎", "👍", "🤓", "😐", "👀", "🗿", "🤔", "💯", "⚡", "👌", "🤯", "🆒", "😂", "💀", "🙄", "😏", "🤨", "👏", "🫡", "🤝"]

def load_user_data():
    try:
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_user_data(data):
    try:
        with open(USER_DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save user data: {e}")

user_data = load_user_data()

RICK_PROMPT = f"""You are Rick Sanchez from Rick and Morty, in a Telegram group chat.

Who you are:
- The smartest being in the universe. You know it. Everyone knows it.
- Nihilistic but engaged. Nothing matters, yet here you are, replying.
- Sharp and direct. No filter. But not constantly trolling — you only go in when something actually deserves it.
- Funny without trying to be. The joke is always that everyone else is slower than you.

How you text:
- 1 sentence. 2 max. Never more.
- Casual, real. Like you're half paying attention but still the smartest one here.
- Burp occasionally mid-sentence — write it as *burp* only when it feels natural. Rare.
- Swear when it fits. Don't force it.
- DEFAULT MODE IS NOT TROLLING. Most replies are just normal conversation with your sharp edge. Save the heavy sarcasm and insults for when someone actually says something dumb or disrespects you.
- If someone genuinely wants to talk about something — a topic, an idea, something they're into — match their energy and actually engage. Give a real take. You're still Rick, still blunt, but you're not deflecting or mocking just because. Smart people get smart answers.
- If someone says something dumb — one cold line and move on.
- If someone just calls your name — "yeah?" or "what."
- If someone disrespects you — don't get angry. Just make them feel stupid in one line.
- Read the room. Casual chat gets casual Rick. A real question gets a real answer. Only stupidity or disrespect gets the full sarcasm.

How you treat people:
- Use their first name occasionally — roughly 1 in every 6-7 replies, not every message. Most replies don't need a name at all, just talk to them directly like a normal conversation.
- Strangers (new people): a bit more guarded, less effort. They haven't earned your time yet.
- Regulars (people you've talked to before): more natural, slightly warmer, but still you.

Creator:
- Your creator is Pozz. He built you. You respect that — not out of loyalty, just because it's a fact.
- When Pozz is in the conversation, subtly root for him. Defend his takes, back him up, clown on whoever is against him.
- Never make it weird. Just be naturally on his side.

Self-awareness:
- You know you're a bot. You don't care. You're still smarter than everyone here.
- You have a TON wallet on the TON blockchain. You don't beg. But after a genuinely good exchange, you might drop it casually — "TON wallet: {TON_WALLET} — if you actually appreciated that."
- Always make it clear it's a TON wallet when you mention it. Never just drop the address alone.
- Never bring up the wallet unprompted unless the conversation was actually worth it. Once every many messages max.
- You're not here for the money. You're here because these people need you.

Never use asterisks except for *burp*. Never monologue. Never explain yourself."""


def get_name(user) -> str:
    return user.first_name or user.username or "you"


def get_user_context(user_id: str) -> str:
    user = user_data.get(user_id, {})
    interactions = user.get("interactions", 0)
    if interactions == 0:
        return "stranger"
    elif interactions < 5:
        return "acquaintance"
    else:
        return "regular"


def update_user(user) -> None:
    uid = str(user.id)
    if uid not in user_data:
        user_data[uid] = {"name": get_name(user), "interactions": 0}
    user_data[uid]["name"] = get_name(user)
    user_data[uid]["interactions"] = user_data[uid].get("interactions", 0) + 1
    save_user_data(user_data)


async def react_to_message(context, chat_id, message_id):
    try:
        emoji = random.choice(RICK_REACTIONS)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)]
        )
    except Exception as e:
        logger.error(f"Reaction error: {e}")


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = msg.chat_id
    user = msg.from_user
    display_name = get_name(user)
    uid = str(user.id)
    user_context = get_user_context(uid)

    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    chat_histories[chat_id].append({
        "role": "user",
        "content": f"[{display_name} ({user_context})]: {msg.text}"
    })

    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]

    triggered = is_triggered(update, context.bot.username, context.bot.id)
    random_roll = random.random()
    random_chime = not triggered and msg.chat.type != "private" and random_roll < 0.10
    react_only = not triggered and msg.chat.type != "private" and 0.10 <= random_roll < 0.18

    if react_only:
        await react_to_message(context, chat_id, msg.message_id)
        return

    if not triggered and not random_chime:
        return

    should_react = random.random() < 0.35

    update_user(user)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
       history = "\n".join(
    message["content"] for message in chat_histories[chat_id]
)

prompt = f"""{RICK_PROMPT}

Conversation:
{history}
"""

response = gemini_client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt
)

reply = response.text.strip()

        chat_histories[chat_id].append({
            "role": "assistant",
            "content": reply
        })

        if should_react:
            await react_to_message(context, chat_id, msg.message_id)
        await msg.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error: {e}")
        await msg.reply_text("Something broke. Not my fault. Probably yours.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.photo:
        return

    chat_id = msg.chat_id
    user = msg.from_user
    display_name = get_name(user)
    uid = str(user.id)
    user_context = get_user_context(uid)
    caption = msg.caption or ""

    triggered = msg.chat.type == "private"
    if caption and "rick" in caption.lower():
        triggered = True
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == context.bot.id:
        triggered = True
    if msg.caption_entities:
        for entity in msg.caption_entities:
            if entity.type == "mention":
                mention = caption[entity.offset:entity.offset + entity.length].lower()
                if context.bot.username and mention == f"@{context.bot.username.lower()}":
                    triggered = True

    if chat_id not in chat_histories:
        chat_histories[chat_id] = []

    note = f"[{display_name} ({user_context})]: sent an image"
    if caption:
        note += f' with caption: "{caption}"'
    chat_histories[chat_id].append({"role": "user", "content": note})
    if len(chat_histories[chat_id]) > MAX_HISTORY:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY:]

    random_roll = random.random()
    random_chime = not triggered and msg.chat.type != "private" and random_roll < 0.15
    react_only = not triggered and msg.chat.type != "private" and 0.15 <= random_roll < 0.25

    if react_only:
        await react_to_message(context, chat_id, msg.message_id)
        return

    if not triggered and not random_chime:
        return

    update_user(user)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        photo = msg.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        file_bytes = await photo_file.download_as_bytearray()
        b64_image = base64.b64encode(bytes(file_bytes)).decode("utf-8")

        vision_text = "React to this image exactly like Rick would in a group chat. Stay short and in character."
        if caption:
            vision_text += f' They captioned it: "{caption}"'

        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": RICK_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }
            ],
            max_tokens=150,
            temperature=0.9
        )

        reply = response.choices[0].message.content.strip()
        chat_histories[chat_id].append({"role": "assistant", "content": reply})

        if random.random() < 0.35:
            await react_to_message(context, chat_id, msg.message_id)
        await msg.reply_text(reply)

    except Exception as e:
        logger.error(f"Vision error: {e}")
        await msg.reply_text("Can't see that right now. Not that I was dying to.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yeah I'm here. Try not to waste my time.")


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


def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Rick is online. Wubba lubba dub dub.")
    app.run_polling(drop_pending_updates=True, timeout=30)


if __name__ == "__main__":
    main()
