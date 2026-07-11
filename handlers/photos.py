"""
PHOTO HANDLER — Vision via Groq llama-4-scout
"""
import asyncio
import base64
import logging
import random

from telegram import Update
from telegram.ext import ContextTypes

from database.models import chat_histories, update_user, get_name, get_user_context, maybe_summarize
from ai.personality import build_prompt
from ai.context import build_conversation_context
from ai.client import groq_client
from services.reactions import react_to_message
from config import VISION_MODEL

logger = logging.getLogger(__name__)


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

    trigger_type = "private" if msg.chat.type == "private" else None
    replying_to_dex = False
    if caption and "dex" in caption.lower():
        trigger_type = trigger_type or "mention"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            trigger_type = trigger_type or "reply"
            replying_to_dex = True
    if msg.caption_entities:
        for entity in msg.caption_entities:
            if entity.type == "mention":
                mention = caption[entity.offset:entity.offset + entity.length].lower()
                if context.bot.username and mention == f"@{context.bot.username.lower()}":
                    trigger_type = trigger_type or "mention"
    triggered = trigger_type is not None

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
    if random_chime:
        trigger_type = "random"

    update_user(user, caption)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        photo      = msg.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        file_bytes = await photo_file.download_as_bytearray()
        b64_image  = base64.b64encode(bytes(file_bytes)).decode("utf-8")

        conv_ctx    = build_conversation_context(
            chat_id, chat_histories[chat_id], display_name, trigger_type, replying_to_dex
        )
        prompt      = build_prompt(uid, conv_ctx, chat_id)
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
