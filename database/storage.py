"""
Persistent JSON storage for user profiles and chat summaries.

v2.1+ roadmap item: migrate this to SQLite. Because every caller goes through
load_user_data() / save_user_data() / load_summaries() / save_summaries()
instead of touching files directly, that migration will only touch this file.
"""
import json
import logging
import os
import shutil

from config import USER_DATA_FILE, SUMMARIES_FILE, DATA_DIR, BASE_DIR

logger = logging.getLogger(__name__)

os.makedirs(DATA_DIR, exist_ok=True)


def _migrate_legacy_file(new_path: str, legacy_filename: str) -> None:
    """
    Pre-2.0.0 deployments stored user_data.json / chat_summaries.json in the
    project root. If this looks like an upgrade of an existing deployment
    (root-level file exists, data/ file doesn't yet), copy it over once so
    no user data or chat memory is lost on upgrade.
    """
    if os.path.exists(new_path):
        return
    legacy_path = os.path.join(BASE_DIR, legacy_filename)
    if os.path.exists(legacy_path):
        shutil.copy(legacy_path, new_path)
        logger.info(f"Migrated legacy {legacy_filename} into data/")


_migrate_legacy_file(USER_DATA_FILE, "user_data.json")
_migrate_legacy_file(SUMMARIES_FILE, "chat_summaries.json")


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


def load_summaries() -> dict:
    try:
        with open(SUMMARIES_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_summaries(data: dict) -> None:
    try:
        with open(SUMMARIES_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save summaries: {e}")
