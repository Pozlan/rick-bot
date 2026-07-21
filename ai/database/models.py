"""
Shared in-memory state plus the logic that updates it.

Three data structures live here:
- user_data:       persisted to disk (profiles, facts, annoyance scores)
- chat_summaries:  persisted to disk (rolling per-chat summaries)
- chat_histories:  volatile, in-memory only (recent raw messages per chat)

Other modules import `user_data` / `chat_summaries` / `chat_histories` directly
and mutate them in place (e.g. user_data[uid]["facts"] = [...]) — since dicts
are mutated rather than reassigned, every module sees the same live object.
"""
import json
import logging
from datetime import datetime

from database.storage import (
    load_user_data, save_user_data,
    load_summaries, save_summaries,
)
from ai.client import gemini_client
from config import GEMINI_MODEL, MAX_HISTORY, KEEP_AFTER_SUM

logger = logging.getLogger(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────
user_data      = load_user_data()
chat_summaries = load_summaries()
chat_histories = {}   # {chat_id: [{role, content}]} — volatile, not persisted


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
            "Important: messages from 'assistant' are Dex's own past replies, not verified "
            "facts. If Dex stated a specific score, stat, or claim that nobody else in the "
            "chat confirmed, summarize it as 'Dex said/claimed X' — don't restate it as "
            "settled fact. Established facts (things people in the group actually reported) "
            "are fine to state plainly.\n"
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
