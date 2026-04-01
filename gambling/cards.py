"""
Shared card primitives and emoji for the gambling cog.

Any game that uses a standard 52-card deck should import from here.
"""
from typing import Dict, List, Tuple

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
# Card emoji
# ---------------------------------------------------------------------------

_SUIT_NAME: Dict[str, str] = {
    "♠": "Spades", "♥": "Hearts", "♦": "Diamonds", "♣": "Clubs",
}

CARD_EMOJI: Dict[str, str] = {
    "cardClubsA":     "<:cardClubsA:1488932325013328034>",
    "cardClubs2":     "<:cardClubs2:1488932315253051432>",
    "cardClubs3":     "<:cardClubs3:1488932315999768757>",
    "cardClubs4":     "<:cardClubs4:1488932316901282013>",
    "cardClubs5":     "<:cardClubs5:1488932317924954184>",
    "cardClubs6":     "<:cardClubs6:1488932319069999297>",
    "cardClubs7":     "<:cardClubs7:1488932319833362562>",
    "cardClubs8":     "<:cardClubs8:1488932321053905029>",
    "cardClubs9":     "<:cardClubs9:1488932323318824981>",
    "cardClubs10":    "<:cardClubs10:1488932324283387934>",
    "cardClubsJ":     "<:cardClubsJ:1488932291882516702>",
    "cardClubsQ":     "<:cardClubsQ:1488932293904040158>",
    "cardClubsK":     "<:cardClubsK:1488932292968710317>",
    "cardDiamondsA":  "<:cardDiamondsA:1488932335436169226>",
    "cardDiamonds2":  "<:cardDiamonds2:1488932326116167771>",
    "cardDiamonds3":  "<:cardDiamonds3:1488932327328579686>",
    "cardDiamonds4":  "<:cardDiamonds4:1488932328159055922>",
    "cardDiamonds5":  "<:cardDiamonds5:1488932329589051444>",
    "cardDiamonds6":  "<:cardDiamonds6:1488932330528837722>",
    "cardDiamonds7":  "<:cardDiamonds7:1488932331409641764>",
    "cardDiamonds8":  "<:cardDiamonds8:1488932332294504508>",
    "cardDiamonds9":  "<:cardDiamonds9:1488932333296812163>",
    "cardDiamonds10": "<:cardDiamonds10:1488932334290866256>",
    "cardDiamondsJ":  "<:cardDiamondsJ:1488932295116066866>",
    "cardDiamondsQ":  "<:cardDiamondsQ:1488932297431580812>",
    "cardDiamondsK":  "<:cardDiamondsK:1488932296525615379>",
    "cardHeartsA":    "<:cardHeartsA:1488932337776591089>",
    "cardHearts2":    "<:cardHearts2:1488932336505720992>",
    "cardHearts3":    "<:cardHearts3:1488932307393056915>",
    "cardHearts4":    "<:cardHearts4:1488932308386971819>",
    "cardHearts5":    "<:cardHearts5:1488932309364248606>",
    "cardHearts6":    "<:cardHearts6:1488932310958211092>",
    "cardHearts7":    "<:cardHearts7:1488932311859859629>",
    "cardHearts8":    "<:cardHearts8:1488932312820219914>",
    "cardHearts9":    "<:cardHearts9:1488932313567072527>",
    "cardHearts10":   "<:cardHearts10:1488932314476970097>",
    "cardHeartsJ":    "<:cardHeartsJ:1488932298790277211>",
    "cardHeartsQ":    "<:cardHeartsQ:1488932300736561475>",
    "cardHeartsK":    "<:cardHeartsK:1488932299990110370>",
    "cardSpadesA":    "<:cardSpadesA:1488932349482897621>",
    "cardSpades2":    "<:cardSpades2:1488932339173036312>",
    "cardSpades3":    "<:cardSpades3:1488932340695830579>",
    "cardSpades4":    "<:cardSpades4:1488932341870231714>",
    "cardSpades5":    "<:cardSpades5:1488932342998237194>",
    "cardSpades6":    "<:cardSpades6:1488932344021909624>",
    "cardSpades7":    "<:cardSpades7:1488932345158307910>",
    "cardSpades8":    "<:cardSpades8:1488932346081186064>",
    "cardSpades9":    "<:cardSpades9:1488932346953601076>",
    "cardSpades10":   "<:cardSpades10:1488932348543107072>",
    "cardSpadesJ":    "<:cardSpadesJ:1488932303458537595>",
    "cardSpadesQ":    "<:cardSpadesQ:1488932306243813579>",
    "cardSpadesK":    "<:cardSpadesK:1488932305144647900>",
}

CARD_BACK = "<:cardBack_blue1:1488932272781398117>"


def card_emoji(rank: str, suit: str) -> str:
    """Return the Discord emoji string for a card, falling back to text."""
    return CARD_EMOJI.get(f"card{_SUIT_NAME[suit]}{rank}", f"[{rank}{suit}]")


def fmt_hand(cards: List[Card], hide_second: bool = False) -> str:
    """Format a hand as emoji; optionally conceal the second card (dealer hole card)."""
    parts = [CARD_BACK if i == 1 and hide_second else card_emoji(rank, suit)
             for i, (rank, suit) in enumerate(cards)]
    return " ".join(parts)
