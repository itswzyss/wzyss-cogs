"""
Shared card primitives and emoji for the gambling cog.

Any game that uses a standard 52-card deck should import from here.

Emoji strings are loaded at import time from emoji_map.json (same directory).
To refresh IDs, run get-ids.py then reload the cog.
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger("red.wzyss-cogs.gambling")

# ---------------------------------------------------------------------------
# Deck constants
# ---------------------------------------------------------------------------

SUITS: List[str] = ["♠", "♥", "♦", "♣"]
RANKS: List[str] = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUES: Dict[str, int] = {
    "A": 11, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "7": 7, "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10,
}

Card = Tuple[str, str]  # (rank, suit)

# ---------------------------------------------------------------------------
# Card emoji — loaded from emoji_map.json at import time
# ---------------------------------------------------------------------------

_SUIT_NAME: Dict[str, str] = {
    "♠": "Spades", "♥": "Hearts", "♦": "Diamonds", "♣": "Clubs",
}

_EMOJI_MAP_PATH = Path(__file__).parent / "emoji_map.json"

try:
    CARD_EMOJI: Dict[str, str] = json.loads(_EMOJI_MAP_PATH.read_text(encoding="utf-8"))
    CARD_BACK: str = CARD_EMOJI.get("cardBack_blue1", "🂠")
    log.debug("Loaded %d emoji from %s", len(CARD_EMOJI), _EMOJI_MAP_PATH)
except FileNotFoundError:
    log.warning("emoji_map.json not found at %s — card emoji will show as text", _EMOJI_MAP_PATH)
    CARD_EMOJI = {}
    CARD_BACK = "🂠"


def card_emoji(rank: str, suit: str) -> str:
    """Return the Discord emoji string for a card, falling back to text."""
    return CARD_EMOJI.get(f"card{_SUIT_NAME[suit]}{rank}", f"[{rank}{suit}]")


def fmt_hand(cards: List[Card], hide_second: bool = False) -> str:
    """Format a hand as emoji; optionally conceal the second card (dealer hole card)."""
    parts = [CARD_BACK if i == 1 and hide_second else card_emoji(rank, suit)
             for i, (rank, suit) in enumerate(cards)]
    return " ".join(parts)
