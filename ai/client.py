"""
LLM client setup and the single call path every reply goes through:
Groq first, Gemini as fallback on rate limit or error.

This module has no dependency on any other project module — it's the base
layer everything else (database, services, handlers) builds on.
"""
import logging

from groq import Groq
from google import genai

from config import GROQ_API_KEY, GEMINI_API_KEY, GROQ_MODEL, GEMINI_MODEL

logger = logging.getLogger(__name__)

groq_client   = Groq(api_key=GROQ_API_KEY)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


async def generate_reply(prompt: str, history: list) -> str:
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": prompt}, *history],
            max_tokens=150,
            temperature=0.9,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["rate_limit","429","too many requests","quota","exceeded"]):
            logger.warning("Groq rate limit — falling back to Gemini")
        else:
            logger.error(f"Groq error: {e} — falling back to Gemini")

    if not gemini_client:
        return "Rate limited. No backup configured. Give me a minute."
    try:
        history_text = "\n".join(m["content"] for m in history)
        response     = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"{prompt}\n\nConversation:\n{history_text}",
        )
        return response.text.strip()
    except Exception as e2:
        logger.error(f"Gemini fallback failed: {e2}")
        return "Both my brains are fried. Try again in a minute."
