"""
Conversation-context extraction.

Pipeline position:
    Message → Conversation Context (this file) → Memory Retrieval (database/models)
    → World State (not built yet — future roadmap item) → Prompt Builder
    (ai/personality.build_prompt) → LLM → Memory Update (chat_histories append,
    already existing)

This module turns raw chat history + the current message into a small
structured ConversationContext object, instead of handing the LLM a flat
mood string and hoping it infers who's talking and what about.

Thread-continuity tracking (_thread_sequence / _thread_last_seen) lives here
too, in-memory only — same as chat_histories, not yet persisted like
chat_summaries. See build_conversation_context docstring.

Topic detection is a small hand-curated keyword table, not NER or an API
call — deliberately cheap since it runs on every reply. Real limitation:
it only knows what's in the table. Expand TOPIC_ENTITIES as gaps show up.
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ── Named-entity topic detection ──────────────────────────────────────────────
# Narrower than database/models.py's topic_kws (which tags broad categories
# like "crypto" or "tech" onto a user's profile). This table is for specific
# things Dex would recognize in the moment — "Champions League", not just
# "sports".
TOPIC_ENTITIES = {
    "Champions League": ["champions league", "ucl"],
    "World Cup":        ["world cup"],
    "Premier League":   ["premier league", "epl"],
    "La Liga":          ["la liga"],
    "Serie A":          ["serie a"],
    "Bundesliga":       ["bundesliga"],
    "Real Madrid":      ["real madrid", "los blancos"],
    "Barcelona":        ["barcelona", "barca"],
    "Man City":         ["man city", "manchester city"],
    "Man Utd":          ["man utd", "manchester united"],
    "Liverpool":        ["liverpool"],
    "Chelsea":          ["chelsea"],
    "Arsenal":          ["arsenal"],
    "Bayern":           ["bayern"],
    "PSG":              ["psg", "paris saint"],
    "Juventus":         ["juventus"],
    "TON / Web3":       ["ton", "web3", "airdrop", "defi"],
    "Crypto markets":   ["btc", "eth", "crypto", "token", "wallet", "nft"],
    "Drake":            ["drake"],
    "The Weeknd":       ["the weeknd", "abel"],
    "Python":           ["python"],
    "JavaScript":       ["javascript", "js framework", "typescript"],
}


def extract_topic(recent_messages: List[str]) -> str:
    """Scan recent messages newest-first, return the first recognized entity."""
    for text in reversed(recent_messages):
        t = text.lower()
        for label, keywords in TOPIC_ENTITIES.items():
            if any(kw in t for kw in keywords):
                return label
    return ""


# ── Mood detection ────────────────────────────────────────────────────────────
# Moved here from ai/personality.py — this is a conversation-context signal
# (what's happening right now), not a personality trait, so it belongs with
# the rest of the context-extraction logic. Logic itself is unchanged.

def detect_mood(history: list) -> str:
    if not history:
        return "casual conversation"

    recent   = [m["content"].lower() for m in history[-6:]]
    combined = " ".join(recent)

    disagree = ["wrong","no it","actually no","disagree","that's not","nah","you're wrong","not true"]
    if sum(1 for msg in recent if any(w in msg for w in disagree)) >= 2:
        return "active debate — people are disagreeing"

    jokes = ["lol","lmao","haha","hahaha","💀","😂","joke","funny"]
    if sum(1 for msg in recent if any(x in msg for x in jokes)) >= 2:
        return "people joking around — keep it light"

    if len(recent) >= 2 and len(recent[-2].split()) > 20 and len(recent[-1].split()) <= 3:
        return "someone may feel ignored — be aware"

    help_signals = ["how do","help me","can you","explain","what is","how to","?"]
    if sum(1 for msg in recent if any(x in msg for x in help_signals)) >= 2:
        return "someone asking for help or explanation"

    if sum(1 for msg in recent if any(x in msg for x in ["ton","crypto","btc","eth","nft","wallet","defi"])) >= 2:
        return "crypto or Web3 discussion"

    if any(x in combined for x in ["code","python","function","error","bug","api","server","script","deploy"]):
        return "technical discussion"

    if any(x in combined for x in ["sad","upset","frustrated","stressed","worried","tired","crying"]):
        return "someone is upset or emotional — go easy"

    if any(x in combined for x in ["amazing","excited","finally","can't wait","🎉","🔥","big news"]):
        return "someone excited about something"

    return "casual conversation"


# ── Active users ───────────────────────────────────────────────────────────────
# chat_histories entries look like {"role": "user", "content": "[Name (ctx)]: text"}
# — pull the display name back out of that bracketed prefix.
_NAME_PATTERN = re.compile(r"^\[([^(\]]+)\s*\(")


def _extract_active_users(history: list, window: int = 8) -> List[str]:
    """Distinct display names from recent '[Name (ctx)]: ...' entries, in encounter order."""
    names = []
    for m in history[-window:]:
        if m.get("role") != "user":
            continue
        match = _NAME_PATTERN.match(m["content"])
        if match:
            name = match.group(1).strip()
            if name not in names:
                names.append(name)
    return names


# ── Thread continuity ─────────────────────────────────────────────────────────
# thread_id groups messages by (chat + topic), so Dex can recognize "we
# talked about this before" even after the conversation moved on and came
# back — not just within the last few messages in the window.
#
# Deliberately topic+chat only, NOT topic+active_users: active_users is a
# windowed snapshot (last 8 messages), so if unrelated people chat in between
# two mentions of the same topic, the active_users set drifts and a
# topic+users hash would silently fail to reconnect — exactly the case this
# feature exists for.
#
# `seq` counts context-builds per chat, i.e. how many times Dex has actually
# engaged in that chat — not raw message count. That's deliberate: 30 raw
# messages Dex never responded to shouldn't count as a long gap the same way
# 30 messages Dex was actively part of would.
_thread_sequence  = {}   # {chat_id: int}
_thread_last_seen = {}   # {thread_id: seq at last sighting}
RECONNECT_GAP = 8        # seq-ticks before a repeat topic counts as "reconnecting"


def _thread_id(chat_id, topic: str) -> str:
    if not topic:
        return ""
    key = f"{chat_id}:{topic}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


# ── The context object ──────────────────────────────────────────────────────

@dataclass
class ConversationContext:
    current_topic: str = ""
    active_users: List[str] = field(default_factory=list)
    replying_to: Optional[str] = None
    trigger: str = "random"          # "private" | "mention" | "reply" | "random"
    mood: str = "casual conversation"
    follow_up: bool = False
    thread_id: str = ""
    reconnected: bool = False        # same thread seen before, after a real gap

    @property
    def priority(self) -> str:
        """
        Single source of truth for "does this deserve real effort" — derived
        from trigger + follow_up rather than stored separately, so it can't
        drift out of sync with them.

        high: directly addressed (private/mention/reply), or continuing
              a thread Dex was just part of.
        low:  an unprompted chime-in with no direct connection to Dex.

        Not wired into any decision yet — this is the seam for later
        autonomous behavior (e.g. skip replying to low-priority messages,
        or spend less effort on them). That's future work, not this pass.
        """
        if self.trigger in ("private", "mention", "reply"):
            return "high"
        if self.follow_up:
            return "high"
        return "low"

    def to_prompt_sections(self) -> List[str]:
        """Render as standalone prompt sections — each becomes its own paragraph in build_prompt."""
        sections = []

        if self.current_topic:
            sections.append(f"Current topic:\n{self.current_topic}")

        if self.replying_to:
            if self.trigger in ("private", "mention", "reply"):
                sections.append(f"{self.replying_to} is asking you directly.")
            else:
                sections.append(f"{self.replying_to} is the one talking right now.")

        if self.follow_up:
            sections.append("This is a follow-up to your previous reply.")

        if self.reconnected:
            sections.append("You've touched on this topic with them before, earlier in this chat.")

        others = [u for u in self.active_users if u != self.replying_to]
        if others:
            sections.append(f"Also active in the chat right now: {', '.join(others)}.")

        sections.append(f"Current conversation mood: {self.mood}")
        return sections


def build_conversation_context(
    chat_id,
    history: list,
    display_name: str,
    trigger: str,
    replying_to_dex: bool = False,
) -> ConversationContext:
    """
    Build a ConversationContext from chat history.

    `history` is the chat's message list from database.models.chat_histories,
    and must already include the current incoming message.

    `trigger` is the reason Dex is responding at all — one of "private",
    "mention", "reply", "random". Caller determines this (it already knows
    whether this is a DM, an explicit mention, a reply to Dex, or a random
    chime-in) since that's Telegram-specific logic this module shouldn't own.

    `replying_to_dex` is True when the current message is a native Telegram
    reply to one of Dex's own messages — kept as a separate signal from
    `trigger == "reply"` for clarity, even though right now they're the
    same condition.
    """
    recent_texts = [m["content"] for m in history[-8:] if m.get("role") == "user"]
    topic        = extract_topic(recent_texts)
    active_users = _extract_active_users(history)

    # Someone kept talking right after Dex's last message, without an
    # explicit Telegram reply — still a follow-up in practice.
    dex_spoke_last = len(history) >= 2 and history[-2].get("role") == "assistant"

    # Thread continuity
    seq = _thread_sequence.get(chat_id, 0) + 1
    _thread_sequence[chat_id] = seq

    tid         = _thread_id(chat_id, topic)
    reconnected = False
    if tid:
        last_seen = _thread_last_seen.get(tid)
        if last_seen is not None and (seq - last_seen) >= RECONNECT_GAP:
            reconnected = True
        _thread_last_seen[tid] = seq

    return ConversationContext(
        current_topic=topic,
        active_users=active_users,
        replying_to=display_name,
        trigger=trigger,
        mood=detect_mood(history),
        follow_up=replying_to_dex or dex_spoke_last,
        thread_id=tid,
        reconnected=reconnected,
    )
