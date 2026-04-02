import logging
import random
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import discord
from discord.ui import Button, View

from .cards import Card, RANKS, RANK_VALUES, SUITS, fmt_hand

if TYPE_CHECKING:
    from .gambling import Gambling

log = logging.getLogger("red.wzyss-cogs.gambling")


def _hand_total(cards: List[Card]) -> Tuple[int, int]:
    """Return (best_value, aces_still_counted_as_11) for a blackjack hand."""
    total, aces = 0, 0
    for rank, _ in cards:
        total += RANK_VALUES[rank]
        if rank == "A":
            aces += 1
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces


def hand_value(cards: List[Card]) -> int:
    return _hand_total(cards)[0]


def is_soft(cards: List[Card]) -> bool:
    """True if the hand contains an Ace still counted as 11."""
    return _hand_total(cards)[1] > 0


def is_natural(cards: List[Card]) -> bool:
    """True for a two-card 21 (natural blackjack)."""
    return len(cards) == 2 and hand_value(cards) == 21


def value_label(cards: List[Card]) -> str:
    """Display label: e.g. '20', 'Soft 18', 'BUST'."""
    v = hand_value(cards)
    if v > 21:
        return "BUST"
    if is_soft(cards) and v < 21:
        return f"Soft {v}"
    return str(v)


# ---------------------------------------------------------------------------
# Hand outcome constants
# ---------------------------------------------------------------------------

_BJ = "blackjack"
_WIN = "win"
_PUSH = "push"
_LOSE = "lose"
_BUST = "bust"
_DEALER_BJ = "dealer_bj"

_OUTCOME_LABEL: Dict[str, str] = {
    _BJ:        "Blackjack! 🃏",
    _WIN:       "Win ✅",
    _PUSH:      "Push 🤝",
    _LOSE:      "Lose ❌",
    _BUST:      "Bust 💥",
    _DEALER_BJ: "Lose ❌",
}


def _classify_hand(hand: List[Card], is_only_hand: bool, dealer_hand: List[Card]) -> str:
    pv = hand_value(hand)
    dv = hand_value(dealer_hand)
    dealer_bj = is_natural(dealer_hand)
    player_bj = is_natural(hand) and is_only_hand

    if pv > 21:
        return _BUST
    if player_bj and dealer_bj:
        return _PUSH
    if player_bj:
        return _BJ
    if dealer_bj:
        return _DEALER_BJ
    if pv > dv or dv > 21:
        return _WIN
    if pv == dv:
        return _PUSH
    return _LOSE


# ---------------------------------------------------------------------------
# BlackjackGame — pure game logic, no Discord coupling
# ---------------------------------------------------------------------------

class BlackjackGame:
    """
    Manages the state of a single blackjack round.

    Credits are handled externally; the game only tracks bets committed
    during this round so the cog can compute the final payout.

    Standard S17 casino rules:
      • Dealer stands on all 17s (including soft 17).
      • Player may double on any first two cards.
      • One split allowed on any pair (aces receive one card each).
      • Double-after-split allowed.
      • No re-splits.
      • Insurance offered when dealer shows an Ace (pays 2:1).
      • Natural blackjack pays 3:2 (no natural on split hands).
    """

    NUM_DECKS = 6
    RESHUFFLE_AT = 52

    def __init__(self, guild_id: int, user_id: int, initial_bet: int):
        self.guild_id = guild_id
        self.user_id = user_id
        self.initial_bet = initial_bet

        self.player_hands: List[List[Card]] = [[]]
        self.bets: List[int] = [initial_bet]
        self.doubled: List[bool] = [False]

        self.dealer_hand: List[Card] = []
        self.current_hand: int = 0
        self.extra_committed: int = 0
        self.insurance_bet: int = 0
        self.deck: List[Card] = []

        # "insurance_offer" → "player_turn" → "dealer_turn" → "done"
        self.phase: str = "player_turn"
        self.outcome_message: str = ""

    # ------------------------------------------------------------------ deck

    def _build_deck(self) -> None:
        self.deck = [(r, s) for _ in range(self.NUM_DECKS) for s in SUITS for r in RANKS]
        random.shuffle(self.deck)

    def _deal_card(self) -> Card:
        if len(self.deck) < self.RESHUFFLE_AT:
            self._build_deck()
        return self.deck.pop()

    # --------------------------------------------------------------- initial

    def deal_initial(self) -> None:
        """Deal two cards to the player and two to the dealer."""
        self._build_deck()
        self.player_hands[0] = [self._deal_card(), self._deal_card()]
        self.dealer_hand = [self._deal_card(), self._deal_card()]
        if self.dealer_hand[0][0] == "A":
            self.phase = "insurance_offer"
        else:
            self._check_naturals()

    def _check_naturals(self) -> None:
        player_bj = is_natural(self.player_hands[0])
        dealer_bj = is_natural(self.dealer_hand)
        if player_bj and dealer_bj:
            self.phase = "done"
            self.outcome_message = "Push — both have Blackjack!"
        elif player_bj:
            self.phase = "done"
            self.outcome_message = "Blackjack! You win 3:2! 🃏"
        elif dealer_bj:
            self.phase = "done"
            self.outcome_message = "Dealer has Blackjack. You lose."
        else:
            self.phase = "player_turn"

    # ----------------------------------------------------------- insurance

    def accept_insurance(self) -> None:
        self.insurance_bet = self.initial_bet // 2
        self.extra_committed += self.insurance_bet
        self._check_naturals()

    def decline_insurance(self) -> None:
        self._check_naturals()

    # ---------------------------------------------------------------- player

    @property
    def active_hand(self) -> List[Card]:
        return self.player_hands[self.current_hand]

    @property
    def active_bet(self) -> int:
        return self.bets[self.current_hand]

    def can_double(self, credits_available: int) -> bool:
        return (
            len(self.active_hand) == 2
            and not self.doubled[self.current_hand]
            and credits_available >= self.active_bet
        )

    def can_split(self, credits_available: int) -> bool:
        hand = self.active_hand
        return (
            len(hand) == 2
            and hand[0][0] == hand[1][0]
            and len(self.player_hands) == 1
            and credits_available >= self.initial_bet
        )

    def hit(self) -> str:
        """Deal one card. Returns 'bust' | 'twenty_one' | 'ok'."""
        self.active_hand.append(self._deal_card())
        v = hand_value(self.active_hand)
        if v > 21:
            return "bust"
        if v == 21:
            return "twenty_one"
        return "ok"

    def stand(self) -> str:
        """Advance to next hand or dealer turn. Returns 'next_hand' | 'dealer_turn'."""
        self.current_hand += 1
        if self.current_hand >= len(self.player_hands):
            self.phase = "dealer_turn"
            return "dealer_turn"
        return "next_hand"

    def double_down(self) -> str:
        """Double the bet, deal one card, then auto-stand."""
        self.extra_committed += self.active_bet
        self.bets[self.current_hand] *= 2
        self.doubled[self.current_hand] = True
        self.hit()
        return self.stand()

    def split(self) -> None:
        """Split the active pair into two hands. Aces receive one card each."""
        self.extra_committed += self.initial_bet
        c1, c2 = self.active_hand[0], self.active_hand[1]
        self.player_hands[0] = [c1, self._deal_card()]
        self.player_hands.append([c2, self._deal_card()])
        self.bets.append(self.initial_bet)
        self.doubled.append(False)

    def is_split_aces(self) -> bool:
        """True when current hand is a post-split Ace (must auto-stand)."""
        return (
            len(self.player_hands) > 1
            and self.active_hand[0][0] == "A"
            and len(self.active_hand) == 2
        )

    # ---------------------------------------------------------------- dealer

    def play_dealer(self) -> None:
        """Run the dealer's turn (hits until hard 17+)."""
        self.phase = "done"
        all_busted = all(hand_value(h) > 21 for h in self.player_hands)
        if all_busted:
            return
        while True:
            v = hand_value(self.dealer_hand)
            if v >= 17:
                break
            self.dealer_hand.append(self._deal_card())

    # --------------------------------------------------------------- scoring

    def _outcomes(self) -> List[str]:
        is_only = len(self.player_hands) == 1
        return [_classify_hand(h, is_only, self.dealer_hand) for h in self.player_hands]

    def calculate_winnings(self) -> int:
        """Return total credits to award (0 if all lost). Does not include deducted bet."""
        total = 0
        outcomes = self._outcomes()
        insurance_win = (
            self.insurance_bet * 3
            if self.insurance_bet and is_natural(self.dealer_hand)
            else 0
        )
        for outcome, bet in zip(outcomes, self.bets):
            if outcome == _BJ:
                total += int(bet * 2.5)
            elif outcome == _WIN:
                total += bet * 2
            elif outcome == _PUSH:
                total += bet
        return total + insurance_win

    def total_wagered(self) -> int:
        return self.initial_bet + self.extra_committed

    def hand_results(self) -> List[str]:
        return [_OUTCOME_LABEL[o] for o in self._outcomes()]


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _credits_str(n: int) -> str:
    return f"{n:,} cr"


def _set_game_embed_footer(
    embed: discord.Embed,
    player: Optional[discord.abc.User],
    extra_line: Optional[str] = None,
) -> None:
    """Footer: player display name + avatar; optional second line (outcome, insurance hint)."""
    if player is None:
        if extra_line:
            embed.set_footer(text=extra_line)
        return
    if isinstance(player, discord.Member):
        name = player.display_name
    else:
        name = player.global_name or player.name
    text = name if not extra_line else f"{name}\n{extra_line}"
    embed.set_footer(text=text, icon_url=player.display_avatar.url)


def build_blackjack_embed(
    game: BlackjackGame,
    reveal_dealer: bool = False,
    final: bool = False,
    net_change: Optional[int] = None,
    *,
    player: Optional[discord.abc.User] = None,
) -> discord.Embed:
    """Build the main blackjack game embed."""
    dealer_label = (
        value_label(game.dealer_hand) if reveal_dealer
        else str(hand_value([game.dealer_hand[0]])) + "+"
    )

    if final:
        results = game.hand_results()
        wins = sum(1 for r in results if "Win" in r or "Blackjack" in r)
        losses = sum(1 for r in results if "Lose" in r or "Bust" in r)
        if wins > losses:
            color, title = discord.Color.green(), "🃏 Blackjack — You Win!"
        elif losses > wins:
            color, title = discord.Color.red(), "🃏 Blackjack — You Lose"
        else:
            color, title = discord.Color.yellow(), "🃏 Blackjack — Push"
    else:
        results = []
        color, title = discord.Color.blurple(), "🃏 Blackjack"

    lines: List[str] = []

    dealer_cards = fmt_hand(game.dealer_hand, hide_second=not reveal_dealer)
    lines.append(f"**Dealer  [{dealer_label}]**\n{dealer_cards}")

    for i, hand in enumerate(game.player_hands):
        is_active = (i == game.current_hand) and not final
        marker = " ◀" if is_active and len(game.player_hands) > 1 else ""
        hand_label = f"Hand {i + 1}" if len(game.player_hands) > 1 else "Your Hand"
        result_suffix = f"  —  {results[i]}" if final else ""
        bet_str = _credits_str(game.bets[i])
        player_cards = fmt_hand(hand)
        lines.append(
            f"**{hand_label}{marker}  [{value_label(hand)}]{result_suffix}**\n"
            f"{player_cards}   Bet: {bet_str}"
        )

    description = "\n\n".join(lines)

    extra_fields: List[Tuple[str, str]] = []
    if game.insurance_bet:
        extra_fields.append(("Insurance Bet", _credits_str(game.insurance_bet)))
    if final and net_change is not None:
        sign = "+" if net_change >= 0 else ""
        extra_fields.append(("Net Result", f"{sign}{_credits_str(net_change)}"))

    embed = discord.Embed(title=title, description=description, color=color)
    for name, value in extra_fields:
        embed.add_field(name=name, value=value, inline=True)

    if final and net_change is not None and game.outcome_message:
        _set_game_embed_footer(embed, player, game.outcome_message)
    elif game.phase == "insurance_offer":
        _set_game_embed_footer(
            embed, player, "Dealer shows an Ace — take insurance? (½ your bet)"
        )
    else:
        _set_game_embed_footer(embed, player, None)

    return embed


def build_insurance_embed(
    game: BlackjackGame, *, player: Optional[discord.abc.User] = None
) -> discord.Embed:
    half = game.initial_bet // 2
    embed = discord.Embed(
        title="🛡️ Insurance Offer",
        description=(
            f"The dealer is showing **[A]**.\n\n"
            f"Would you like to take insurance? It costs **{_credits_str(half)}** "
            f"(half your bet) and pays **2:1** if the dealer has Blackjack.\n\n"
            f"> *Insurance is generally a poor bet for the player.*"
        ),
        color=discord.Color.orange(),
    )
    _set_game_embed_footer(embed, player, None)
    return embed


# ---------------------------------------------------------------------------
# Discord UI — InsuranceView
# ---------------------------------------------------------------------------

class InsuranceView(View):
    """Shown when dealer's up-card is an Ace. Offers an insurance side-bet."""

    def __init__(self, cog: "Gambling", game: BlackjackGame, player_credits: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.game = game
        self.player_credits = player_credits
        if player_credits < game.initial_bet // 2:
            self.take_insurance.disabled = True

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes — Take Insurance", style=discord.ButtonStyle.success)
    async def take_insurance(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -(self.game.initial_bet // 2))
        self.game.accept_insurance()
        await self._transition(interaction)

    @discord.ui.button(label="No — Decline", style=discord.ButtonStyle.secondary)
    async def decline_insurance(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        self.game.decline_insurance()
        await self._transition(interaction)

    async def _transition(self, interaction: discord.Interaction):
        self.stop()
        if self.game.phase == "done":
            await self.cog._finish_game(interaction, self.game)
        else:
            credits = await self.cog._get_credits(interaction.guild, interaction.user)
            view = BlackjackView(self.cog, self.game, credits)
            await interaction.response.edit_message(
                embed=build_blackjack_embed(self.game, player=interaction.user), view=view
            )

    async def on_timeout(self):
        self.game.decline_insurance()
        self.cog._active_games.pop((self.game.guild_id, self.game.user_id), None)


# ---------------------------------------------------------------------------
# Discord UI — BlackjackView
# ---------------------------------------------------------------------------

class BlackjackView(View):
    """Main in-game view. Shown during the player's turn."""

    def __init__(self, cog: "Gambling", game: BlackjackGame, player_credits: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game
        self.player_credits = player_credits
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        for item in self.children:
            if not isinstance(item, Button):
                continue
            if item.label == "Double Down":
                item.disabled = not self.game.can_double(self.player_credits)
            elif item.label == "Split":
                item.disabled = not self.game.can_split(self.player_credits)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    async def _after_action(self, interaction: discord.Interaction, action_result: str):
        """Route to dealer turn or refresh view after any player action."""
        if self.game.phase == "dealer_turn":
            await self._run_dealer(interaction)
            return

        if action_result in ("bust", "twenty_one"):
            self.game.stand()
            if self.game.phase == "dealer_turn":
                await self._run_dealer(interaction)
                return

        if self.game.is_split_aces():
            self.game.stand()
            if self.game.phase == "dealer_turn":
                await self._run_dealer(interaction)
                return

        self.player_credits = await self.cog._get_credits(interaction.guild, interaction.user)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=build_blackjack_embed(self.game, player=interaction.user), view=self
        )

    async def _run_dealer(self, interaction: discord.Interaction):
        self.stop()
        self.game.play_dealer()
        await self.cog._finish_game(interaction, self.game)

    async def on_timeout(self):
        self.cog._active_games.pop((self.game.guild_id, self.game.user_id), None)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, row=0)
    async def hit(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self._after_action(interaction, self.game.hit())

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, row=0)
    async def stand(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self._after_action(interaction, self.game.stand())

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, row=0)
    async def double_down(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -self.game.active_bet)
        self.player_credits -= self.game.active_bet
        await self._after_action(interaction, self.game.double_down())

    @discord.ui.button(label="Split", style=discord.ButtonStyle.danger, row=0)
    async def split(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -self.game.initial_bet)
        self.player_credits -= self.game.initial_bet
        self.game.split()
        await self._after_action(interaction, "ok")
