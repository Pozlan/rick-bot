"""
Dex gets annoyed by low-effort messages and rapid spamming.
Score decays naturally over time. Two stages: warning, then silence.
"""
import time
from datetime import datetime

from database.models import user_data
from database.storage import save_user_data
from config import (
    ANNOYANCE_THRESHOLD_WARN, ANNOYANCE_THRESHOLD_IGNORE,
    IGNORE_DURATION_SHORT, IGNORE_DURATION_LONG,
)

DISMISSAL_LINES = [
    "I'm done with you for now.",
    "Talk to me when you have something worth saying.",
    "You've used up your quota of my attention.",
    "Come back when your IQ catches up to your enthusiasm.",
    "I need a break from this level of input.",
]


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
