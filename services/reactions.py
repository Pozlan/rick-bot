"""
Context-aware emoji reactions. Only uses confirmed valid Telegram reaction emojis.
"""
import logging
import random

from telegram import ReactionTypeEmoji

logger = logging.getLogger(__name__)


def pick_reaction(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ["lol","lmao","lmfao","haha","hahaha","joke","funny","i'm dead","💀","bruh"]):
        return random.choice(["🤣", "😁"])
    if any(x in t for x in ["roast","destroyed","rekt","ratio","took an l","clowned"]):
        return "🤡"
    if any(x in t for x in ["fire","amazing","incredible","insane","wild","banger","goated"]):
        return "🔥"
    if any(x in t for x in ["no way","wait what","seriously","omg","gossip","drama","leaked","exposing"]):
        return random.choice(["😱", "👀"])
    if any(x in t for x in ["ton","crypto","web3","btc","eth","token","wallet","nft","pump","dump","defi","blockchain","airdrop"]):
        return random.choice(["💯", "🤔", "⚡"])
    if any(x in t for x in ["actually","technically","research","therefore","in fact","data","evidence","logic"]):
        return random.choice(["🤓", "🤔"])
    if any(x in t for x in ["facts","exactly","true","100%","fr fr","no cap","based","agreed"]):
        return random.choice(["👍", "💯"])
    if any(x in t for x in ["wrong","bad take","terrible","cap","lying","false","misinformation"]):
        return "👎"
    if any(x in t for x in ["won","win","achieved","congrats","success","finally","did it","shipped"]):
        return "👏"
    if any(x in t for x in ["love","thank","appreciate","grateful","wholesome","sweet"]):
        return "❤️"
    if any(x in t for x in ["cringe","awkward","yikes","ouch","oof"]):
        return random.choice(["🥴", "😱"])
    if any(x in t for x in ["announcement","launching","dropping","breaking","new update"]):
        return random.choice(["🤯", "👀"])
    if any(x in t for x in ["goat","best","better than","i'm the","flex"]):
        return "🤨"
    if text.strip().endswith("?") or t.startswith(("why","how","what","who","when")):
        return random.choice(["🤔", "👀"])
    if len(text.split()) <= 3:
        return random.choice(["😐", "🗿", "👍"])
    return random.choice(["🤔","😐","🗿","⚡","🆒","🤨","👀","🤯"])


async def react_to_message(context, chat_id, message_id, text: str = "") -> None:
    try:
        emoji = pick_reaction(text)
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        logger.error(f"Reaction error: {e}")
