"""
Message ↓ trigger check ↓ annoyance/ignore check ↓ mood + prompt build
↓ football / web search routing (if needed) ↓ LLM ↓ store + reply

This is the core pipeline described in the roadmap. Right now intent
detection is just needs_web_search()/is_football_question() keyword checks;
that's the seam where a proper intent-detection module slots in later.
"""
import asyncio
import logging
import random

from telegram import Update
from telegram.ext import ContextTypes

from database.models import (
    chat_histories, user_data, update_user, maybe_summarize,
    extract_facts_for_user, get_name, get_user_context,
)
from ai.personality import build_prompt
from ai.context import build_conversation_context
from ai.client import generate_reply
from services.moderation import evaluate_annoyance, get_ignore_state, DISMISSAL_LINES
from services.reactions import react_to_message
from services.web_search import needs_web_search, get_search_reply
from services.football import is_football_question, get_football_reply
from config import FACT_EXTRACT_EVERY

logger = logging.getLogger(__name__)


def classify_trigger(update: Update, bot_username: str, bot_id: int) -> "str | None":
    msg = update.message
    if not msg or not msg.text:
        return None
    if msg.chat.type == "private":
        return "private"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == bot_id:
            return "reply"
    text_lower = msg.text.lower()
    if "dex" in text_lower:
        return "mention"
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset:entity.offset + entity.length].lower()
                if bot_username and mention == f"@{bot_username.lower()}":
                    return "mention"
    return None


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

    await maybe_summarize(chat_id)

    trigger_type = classify_trigger(update, context.bot.username, context.bot.id)
    triggered    = trigger_type is not None
    random_roll  = random.random()
    random_chime = not triggered and msg.chat.type != "private" and random_roll < 0.10
    react_only   = not triggered and msg.chat.type != "private" and 0.10 <= random_roll < 0.18

    if react_only:
        await react_to_message(context, chat_id, msg.message_id, msg.text)
        return

    if not triggered and not random_chime:
        return

    if random_chime:
        trigger_type = "random"

    should_react = random.random() < 0.35
    update_user(user, msg.text)

    if triggered:
        evaluate_annoyance(uid, msg.text)
        ignore_state = get_ignore_state(uid)
        if ignore_state == "ignored":
            return
        if ignore_state == "warn":
            await msg.reply_text(random.choice(DISMISSAL_LINES))
            return

    interactions = user_data.get(uid, {}).get("interactions", 0)
    if interactions % FACT_EXTRACT_EVERY == 0:
        recent_msgs = [m["content"] for m in chat_histories.get(chat_id, [])[-20:]]
        asyncio.create_task(extract_facts_for_user(uid, display_name, recent_msgs))

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    replying_to_dex = bool(
        msg.reply_to_message and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    conv_ctx = build_conversation_context(
        chat_id, chat_histories[chat_id], display_name, trigger_type, replying_to_dex
    )
    prompt = build_prompt(uid, conv_ctx, chat_id)

    try:
        needed_current_info = needs_web_search(msg.text) or is_football_question(msg.text)
        reply = None

        # Football has a real, authoritative data source — check it FIRST.
        if is_football_question(msg.text):
            reply = await get_football_reply(msg.text, prompt, conv_ctx.current_topic)
            if not reply and needs_web_search(msg.text):
                reply = await get_search_reply(msg.text, prompt, conv_ctx.current_topic)
        elif needs_web_search(msg.text):
            reply = await get_search_reply(msg.text, prompt, conv_ctx.current_topic)

        if not reply:
            # Nothing real came back. Tell the model explicitly not to
            # invent a score/date/stat instead of guessing confidently.
            fallback_prompt = prompt
           if needed_current_info:
    fallback_prompt += (
        "\n\nYou tried to look this up but didn't get real data back. "
        "Do NOT invent or guess a name, score, date, or stat — not even as a "
        "question ('was it X?'). That's still passing off a guess as real info. "
        "Just say straight up you don't have that info, or ask them to fill you in."
    )
            reply = await generate_reply(fallback_prompt, chat_histories[chat_id])

        chat_histories[chat_id].append({"role": "assistant", "content": reply})

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
