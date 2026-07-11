"""
Central configuration. Nothing in here does work — it only defines
constants that every other module reads from. If a value needs to change
(a model name, a threshold, a file path), it changes here and nowhere else.
"""
import os

# ── Telegram / API keys ──────────────────────────────────────────────────────
BOT_TOKEN         = os.environ["BOT_TOKEN"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
FOOTBALL_API_KEY  = os.environ.get("FOOTBALL_API_KEY", "")
CHANNEL_ID        = "@dexsupdate"

# ── Models ────────────────────────────────────────────────────────────────────
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MODEL   = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-1.5-flash"

# ── Conversation memory tuning ────────────────────────────────────────────────
MAX_HISTORY         = 14   # messages before summarization triggers
KEEP_AFTER_SUM       = 4    # messages to keep after summarization
FACT_EXTRACT_EVERY  = 20   # run Gemini fact extraction every N interactions

# ── Annoyance / ignore system ─────────────────────────────────────────────────
ANNOYANCE_THRESHOLD_WARN   = 12
ANNOYANCE_THRESHOLD_IGNORE = 20
IGNORE_DURATION_SHORT      = 1800   # 30 mins
IGNORE_DURATION_LONG       = 7200   # 2 hours

# ── Storage paths ──────────────────────────────────────────────────────────────
# Everything persistent lives under data/. Previously these JSON files sat in
# the project root — see database/storage.py for the one-time migration that
# moves an existing deployment's files into data/ automatically.
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "data")
USER_DATA_FILE = os.path.join(DATA_DIR, "user_data.json")
SUMMARIES_FILE = os.path.join(DATA_DIR, "chat_summaries.json")
