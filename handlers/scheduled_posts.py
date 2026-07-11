"""
CHANNEL AUTO-POSTS — scheduled jobs that post to CHANNEL_ID.
"""
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from database.models import user_data
from ai.client import generate_reply
from ai.personality import CORE_PERSONALITY
from config import CHANNEL_ID

logger = logging.getLogger(__name__)


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
