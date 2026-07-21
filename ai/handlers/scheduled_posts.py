"""
CHANNEL AUTO-POSTS — scheduled jobs that post to CHANNEL_ID.
"""
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from database.models import user_data
from database.storage import load_posted_facts, save_posted_facts
from ai.client import generate_reply
from ai.personality import CORE_PERSONALITY
from config import CHANNEL_ID, MAX_STORED_FACTS

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
    """
    8:30 PM EAT: Post a mind-blowing fact to the channel.

    Without knowing what's already been posted, the model reaches for the
    same small set of "classic" facts (immortal jellyfish, Venus's day being
    longer than its year, etc.) over and over — there's nothing random about
    it, it's just the most likely completion every single time. Tracking a
    rolling history and explicitly excluding it fixes that the same way
    extract_facts_for_user already excludes a user's known facts.
    """
    try:
        posted = load_posted_facts()
        exclude_block = ""
        if posted:
            recent = posted[-MAX_STORED_FACTS:]
            exclude_block = (
                "You've already posted these — do NOT repeat any of them or "
                "anything close to them, pick something genuinely different:\n"
                + "\n".join(f"- {f}" for f in recent) + "\n\n"
            )

        base_prompt = (
            f"{CORE_PERSONALITY}\n\n"
            f"{exclude_block}"
            "Drop one accurate, mind-blowing fact to your Telegram channel. 2 short sentences max. "
            "Do NOT say 'I know what you're thinking' or 'blowing your mind'. "
            "Just the fact and one sharp reaction. No hashtags. No emojis. "
            "Like: 'A day on Venus is longer than a year on Venus. Let that ruin your morning.'"
        )

        response = await generate_reply(base_prompt, [])

        # One retry if it collided anyway — cheap insurance, not a guarantee.
        if posted and any(response.strip().lower()[:40] == p.strip().lower()[:40] for p in posted):
            response = await generate_reply(base_prompt, [])

        posted.append(response)
        save_posted_facts(posted[-MAX_STORED_FACTS:])

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"{response}\n\n✦ @dexsupdate"
        )
        logger.info("Daily fact posted")
    except Exception as e:
        logger.error(f"Fact post error: {e}")
