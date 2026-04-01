import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ui import Button, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.gambling")

# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------

SUITS: List[str] = ["♠", "♥", "♦", "♣"]
RANKS: List[str] = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUES: Dict[str, int] = {
    "A": 11, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "7": 7, "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10,
}

Card = Tuple[str, str]  # (rank, suit)

# Hand outcome tokens
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


def fmt_hand(cards: List[Card], hide_second: bool = False) -> str:
    """Format a list of cards; optionally conceal the dealer's hole card."""
    parts = ["[??]" if i == 1 and hide_second else f"[{rank}{suit}]"
             for i, (rank, suit) in enumerate(cards)]
    return "  ".join(parts)


def _classify_hand(hand: List[Card], is_only_hand: bool, dealer_hand: List[Card]) -> str:
    """
    Classify a player hand's outcome against the dealer.
    Returns one of the _BJ / _WIN / _PUSH / _LOSE / _BUST / _DEALER_BJ constants.
    """
    pv = hand_value(hand)
    dv = hand_value(dealer_hand)
    dealer_bj = is_natural(dealer_hand)
    player_bj = is_natural(hand) and is_only_hand  # naturals don't count on split hands

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


def _credits_str(n: int) -> str:
    return f"{n:,} cr"


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
    RESHUFFLE_AT = 52  # reshuffle when fewer cards remain

    def __init__(self, guild_id: int, user_id: int, initial_bet: int):
        self.guild_id = guild_id
        self.user_id = user_id
        self.initial_bet = initial_bet

        # One list per hand; bets[i] matches player_hands[i]
        self.player_hands: List[List[Card]] = [[]]
        self.bets: List[int] = [initial_bet]
        self.doubled: List[bool] = [False]

        self.dealer_hand: List[Card] = []
        self.current_hand: int = 0

        # Extra bets committed this round (doubles, splits, insurance)
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
        """Resolve immediate natural blackjacks; otherwise start player turn."""
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
        """Player takes insurance (half of initial bet, rounded down)."""
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
        self.hit()  # exactly one card; bust still auto-stands
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

    # --------------------------------------------------------------- dealer

    def play_dealer(self) -> None:
        """Play out dealer per S17: stand on all 17s."""
        while hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self._deal_card())
        self.phase = "done"

    # ------------------------------------------------------------ outcomes

    def _outcomes(self) -> List[str]:
        """Classify each player hand's outcome."""
        is_only = len(self.player_hands) == 1
        return [_classify_hand(h, is_only, self.dealer_hand) for h in self.player_hands]

    def calculate_winnings(self) -> int:
        """
        Credits to return to the player.
        All bets were deducted before the game started, so this is the
        gross return (not the net). Net = calculate_winnings() - total_wagered().
        """
        total = 0
        for outcome, bet in zip(self._outcomes(), self.bets):
            if outcome == _BJ:
                total += bet + int(bet * 1.5)   # 3:2
            elif outcome == _WIN:
                total += bet * 2                # even money
            elif outcome == _PUSH:
                total += bet                    # return bet
            # _BUST / _LOSE / _DEALER_BJ: 0

        if self.insurance_bet and is_natural(self.dealer_hand):
            total += self.insurance_bet * 3     # insurance pays 2:1

        return total

    def hand_results(self) -> List[str]:
        """Human-readable outcome label per hand."""
        return [_OUTCOME_LABEL[o] for o in self._outcomes()]

    def total_wagered(self) -> int:
        """Total credits committed this round (initial + extras)."""
        return self.initial_bet + self.extra_committed


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def build_blackjack_embed(
    game: BlackjackGame,
    reveal_dealer: bool = False,
    final: bool = False,
    net_change: Optional[int] = None,
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

    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name=f"Dealer  [{dealer_label}]",
        value=fmt_hand(game.dealer_hand, hide_second=not reveal_dealer),
        inline=False,
    )

    for i, hand in enumerate(game.player_hands):
        is_active = (i == game.current_hand) and not final
        marker = " ◀" if is_active and len(game.player_hands) > 1 else ""
        hand_label = f"Hand {i + 1}" if len(game.player_hands) > 1 else "Your Hand"
        result_suffix = f"  —  {results[i]}" if final else ""
        embed.add_field(
            name=f"{hand_label}{marker}  [{value_label(hand)}]{result_suffix}",
            value=f"{fmt_hand(hand)}   Bet: {_credits_str(game.bets[i])}",
            inline=False,
        )

    if game.insurance_bet:
        embed.add_field(name="Insurance Bet", value=_credits_str(game.insurance_bet), inline=True)

    if final and net_change is not None:
        sign = "+" if net_change >= 0 else ""
        embed.add_field(name="Net Result", value=f"{sign}{_credits_str(net_change)}", inline=True)
        if game.outcome_message:
            embed.set_footer(text=game.outcome_message)
    elif game.phase == "insurance_offer":
        embed.set_footer(text="Dealer shows an Ace — take insurance? (½ your bet)")
    else:
        embed.set_footer(text="🎰 Virtual credits only — gamble responsibly.")

    return embed


def build_insurance_embed(game: BlackjackGame) -> discord.Embed:
    half = game.initial_bet // 2
    return discord.Embed(
        title="🛡️ Insurance Offer",
        description=(
            f"The dealer is showing **[A]**.\n\n"
            f"Would you like to take insurance? It costs **{_credits_str(half)}** "
            f"(half your bet) and pays **2:1** if the dealer has Blackjack.\n\n"
            f"> *Insurance is generally a poor bet for the player.*"
        ),
        color=discord.Color.orange(),
    )


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

    @discord.ui.button(label="Yes — Take Insurance", style=discord.ButtonStyle.success, emoji="🛡️")
    async def take_insurance(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -(self.game.initial_bet // 2))
        self.game.accept_insurance()
        await self._transition(interaction)

    @discord.ui.button(label="No — Decline", style=discord.ButtonStyle.secondary, emoji="❌")
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
            await interaction.response.edit_message(embed=build_blackjack_embed(self.game), view=view)

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
        await interaction.response.edit_message(embed=build_blackjack_embed(self.game), view=self)

    async def _run_dealer(self, interaction: discord.Interaction):
        self.stop()
        self.game.play_dealer()
        await self.cog._finish_game(interaction, self.game)

    async def on_timeout(self):
        self.cog._active_games.pop((self.game.guild_id, self.game.user_id), None)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👆", row=0)
    async def hit(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self._after_action(interaction, self.game.hit())

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋", row=0)
    async def stand(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self._after_action(interaction, self.game.stand())

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, emoji="✌️", row=0)
    async def double_down(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -self.game.active_bet)
        self.player_credits -= self.game.active_bet
        await self._after_action(interaction, self.game.double_down())

    @discord.ui.button(label="Split", style=discord.ButtonStyle.danger, emoji="✂️", row=0)
    async def split(self, interaction: discord.Interaction, button: Button):
        if not await self._guard(interaction):
            return
        await self.cog._adjust_credits(interaction.guild, interaction.user, -self.game.initial_bet)
        self.player_credits -= self.game.initial_bet
        self.game.split()
        await self._after_action(interaction, "ok")


# ---------------------------------------------------------------------------
# GamblingCog
# ---------------------------------------------------------------------------

class Gambling(commands.Cog):
    """Virtual credits casino — Blackjack and leaderboards. No real money involved."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=7183920461, force_registration=True)

        default_guild = {
            "enabled": True,
            "starting_credits": 1_000,
            "daily_bonus": 500,
            "min_bet": 10,
            "max_bet": 50_000,
        }

        default_member = {
            "credits": 0,
            "initialized": False,
            "daily_last_claimed": None,
            "total_won": 0,
            "total_lost": 0,
            "games_played": 0,
            "games_won": 0,
            "games_lost": 0,
            "games_pushed": 0,
            "bj_naturals": 0,
            "win_streak": 0,
            "best_win_streak": 0,
            "loss_streak": 0,
            "worst_loss_streak": 0,
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        # (guild_id, user_id) → active BlackjackGame
        self._active_games: Dict[Tuple[int, int], BlackjackGame] = {}

    # ---------------------------------------------------------------- helpers

    async def _ensure_initialized(self, guild: discord.Guild, member: discord.Member) -> None:
        """Grant starting credits on first interaction."""
        if not await self.config.member_from_ids(guild.id, member.id).initialized():
            starting = await self.config.guild(guild).starting_credits()
            await self.config.member(member).credits.set(starting)
            await self.config.member(member).initialized.set(True)

    async def _get_credits(self, guild: discord.Guild, member: discord.Member) -> int:
        await self._ensure_initialized(guild, member)
        return await self.config.member(member).credits()

    async def _adjust_credits(
        self, guild: discord.Guild, member: discord.Member, delta: int
    ) -> int:
        """Add delta (can be negative) to credits. Returns new balance."""
        await self._ensure_initialized(guild, member)
        current = await self.config.member(member).credits()
        new_val = max(0, current + delta)
        await self.config.member(member).credits.set(new_val)
        return new_val

    async def _record_game_result(
        self,
        member: discord.Member,
        game: BlackjackGame,
        net: int,
    ) -> None:
        """Update lifetime stats after a game ends."""
        results = game.hand_results()
        had_natural = any("Blackjack!" in r for r in results)

        async with self.config.member(member).all() as data:
            data["games_played"] += 1
            if had_natural:
                data["bj_naturals"] += 1
            if net > 0:
                data["total_won"] += net
                data["games_won"] += 1
                data["win_streak"] += 1
                data["loss_streak"] = 0
                data["best_win_streak"] = max(data["win_streak"], data["best_win_streak"])
            elif net < 0:
                data["total_lost"] += abs(net)
                data["games_lost"] += 1
                data["loss_streak"] += 1
                data["win_streak"] = 0
                data["worst_loss_streak"] = max(data["loss_streak"], data["worst_loss_streak"])
            else:
                data["games_pushed"] += 1
                data["win_streak"] = 0
                data["loss_streak"] = 0

    async def _resolve_game(
        self, guild: discord.Guild, member: discord.Member, game: BlackjackGame
    ) -> Tuple[discord.Embed, int]:
        """Settle a finished game: update credits and stats. Returns (embed, net)."""
        self._active_games.pop((game.guild_id, game.user_id), None)
        winnings = game.calculate_winnings()
        await self._adjust_credits(guild, member, winnings)
        net = winnings - game.total_wagered()
        await self._record_game_result(member, game, net)
        embed = build_blackjack_embed(game, reveal_dealer=True, final=True, net_change=net)
        return embed, net

    async def _finish_game(
        self, interaction: discord.Interaction, game: BlackjackGame
    ) -> None:
        """Resolve a game triggered from a button interaction and edit the message."""
        embed, _ = await self._resolve_game(interaction.guild, interaction.user, game)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)

    # ---------------------------------------------------------------- commands

    @commands.group(name="gambling", aliases=["casino", "gam"])
    @commands.guild_only()
    async def _gambling(self, ctx: commands.Context):
        """Virtual casino — earn and spend credits. No real money involved."""
        pass

    @_gambling.command(name="balance", aliases=["bal", "credits"])
    async def _balance(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Check your credit balance (or another member's).

        Usage: `[p]gambling balance [@member]`
        """
        target = member or ctx.author
        credits = await self._get_credits(ctx.guild, target)
        embed = discord.Embed(title="💰 Credit Balance", color=await ctx.embed_color())
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.add_field(name="Balance", value=_credits_str(credits))
        await ctx.send(embed=embed)

    @_gambling.command(name="daily")
    async def _daily(self, ctx: commands.Context):
        """Claim your daily credit bonus (resets every 24 hours)."""
        await self._ensure_initialized(ctx.guild, ctx.author)
        cfg = self.config.member(ctx.author)
        last_str = await cfg.daily_last_claimed()
        bonus = await self.config.guild(ctx.guild).daily_bonus()
        now = datetime.now(timezone.utc)

        if last_str:
            last = datetime.fromisoformat(last_str)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            next_claim = last + timedelta(hours=24)
            if now < next_claim:
                remaining = next_claim - now
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                await ctx.send(
                    f"⏳ You already claimed your daily bonus. "
                    f"Come back in **{hours}h {rem // 60}m**."
                )
                return

        await cfg.daily_last_claimed.set(now.isoformat())
        new_bal = await self._adjust_credits(ctx.guild, ctx.author, bonus)
        await ctx.send(
            f"🎁 You claimed your daily bonus of **{_credits_str(bonus)}**!\n"
            f"New balance: **{_credits_str(new_bal)}**"
        )

    @_gambling.command(name="stats")
    async def _stats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """View game statistics.

        Usage: `[p]gambling stats [@member]`
        """
        target = member or ctx.author
        await self._ensure_initialized(ctx.guild, target)
        data = await self.config.member(target).all()
        played = data["games_played"]
        won = data["games_won"]
        win_rate = (won / played * 100) if played else 0.0

        embed = discord.Embed(title="📊 Gambling Statistics", color=await ctx.embed_color())
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.add_field(name="Balance",      value=_credits_str(data["credits"]),   inline=True)
        embed.add_field(name="Games Played", value=str(played),                     inline=True)
        embed.add_field(name="Win Rate",     value=f"{win_rate:.1f}%",              inline=True)
        embed.add_field(name="Wins",         value=str(won),                        inline=True)
        embed.add_field(name="Losses",       value=str(data["games_lost"]),         inline=True)
        embed.add_field(name="Pushes",       value=str(data["games_pushed"]),       inline=True)
        embed.add_field(name="Total Won",    value=_credits_str(data["total_won"]), inline=True)
        embed.add_field(name="Total Lost",   value=_credits_str(data["total_lost"]),inline=True)
        embed.add_field(name="BJ Naturals",  value=str(data["bj_naturals"]),        inline=True)
        embed.add_field(name="Best Win Streak",  value=str(data["best_win_streak"]), inline=True)
        embed.add_field(name="Worst Loss Streak",value=str(data["worst_loss_streak"]),inline=True)
        embed.set_footer(text="🎰 Virtual credits only — gamble responsibly.")
        await ctx.send(embed=embed)

    # ---- leaderboard

    _LEADERBOARD_TYPES: Dict[str, Tuple[str, object]] = {
        "credits":  ("💰 Richest Players",               lambda d: d["credits"]),
        "won":      ("🏆 Most Credits Won",               lambda d: d["total_won"]),
        "games":    ("🎲 Most Games Played",              lambda d: d["games_played"]),
        "winrate":  ("📈 Highest Win Rate (min 10 games)",
                     lambda d: d["games_won"] / d["games_played"] * 100 if d["games_played"] >= 10 else -1),
        "naturals": ("🃏 Most Blackjack Naturals",        lambda d: d["bj_naturals"]),
        "streak":   ("🔥 Best Win Streak",                lambda d: d["best_win_streak"]),
    }

    @_gambling.command(name="leaderboard", aliases=["lb", "top"])
    async def _leaderboard(self, ctx: commands.Context, board: str = "credits"):
        """Show a leaderboard.

        Available boards: `credits`, `won`, `games`, `winrate`, `naturals`, `streak`

        Usage: `[p]gambling leaderboard [board]`
        """
        board = board.lower()
        if board not in self._LEADERBOARD_TYPES:
            valid = ", ".join(f"`{k}`" for k in self._LEADERBOARD_TYPES)
            await ctx.send(f"❌ Unknown board. Valid options: {valid}")
            return

        title, key_fn = self._LEADERBOARD_TYPES[board]
        all_members = await self.config.all_members(ctx.guild)

        entries = []
        for uid, data in all_members.items():
            if not data.get("initialized"):
                continue
            score = key_fn(data)
            if score < 0:
                continue
            member = ctx.guild.get_member(uid)
            entries.append((score, member.display_name if member else f"User {uid}"))

        if not entries:
            await ctx.send("No data yet — play some games first!")
            return

        entries.sort(reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (score, name) in enumerate(entries[:10]):
            prefix = medals[i] if i < 3 else f"**{i + 1}.**"
            fmt = f"{score:.1f}%" if board == "winrate" else (
                _credits_str(int(score)) if board in ("credits", "won") else f"{score:,}"
            )
            lines.append(f"{prefix} {name} — {fmt}")

        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=await ctx.embed_color(),
        )
        embed.set_footer(text=f"Showing top {min(10, len(entries))} of {len(entries)} players.")
        await ctx.send(embed=embed)

    # ---- blackjack

    @_gambling.command(name="blackjack", aliases=["bj"])
    async def _blackjack(self, ctx: commands.Context, bet: int):
        """Play a hand of Blackjack.

        Standard casino rules (S17): dealer stands on all 17s, BJ pays 3:2,
        double down and split available, insurance offered on dealer Ace.

        Usage: `[p]gambling blackjack <bet>`
        """
        key = (ctx.guild.id, ctx.author.id)
        if key in self._active_games:
            await ctx.send("❌ You already have an active game. Finish it first.")
            return

        min_bet = await self.config.guild(ctx.guild).min_bet()
        max_bet = await self.config.guild(ctx.guild).max_bet()
        if bet < min_bet:
            await ctx.send(f"❌ Minimum bet is {_credits_str(min_bet)}.")
            return
        if bet > max_bet:
            await ctx.send(f"❌ Maximum bet is {_credits_str(max_bet)}.")
            return

        credits = await self._get_credits(ctx.guild, ctx.author)
        if credits < bet:
            await ctx.send(
                f"❌ Insufficient credits. You have {_credits_str(credits)}, "
                f"but the bet is {_credits_str(bet)}."
            )
            return

        await self._adjust_credits(ctx.guild, ctx.author, -bet)
        credits -= bet

        game = BlackjackGame(ctx.guild.id, ctx.author.id, bet)
        game.deal_initial()
        self._active_games[key] = game

        if game.phase == "insurance_offer":
            await ctx.send(
                embeds=[build_insurance_embed(game), build_blackjack_embed(game)],
                view=InsuranceView(self, game, credits),
            )
        elif game.phase == "done":
            # Natural blackjack with no insurance offered
            embed, _ = await self._resolve_game(ctx.guild, ctx.author, game)
            await ctx.send(embed=embed)
        else:
            await ctx.send(
                embed=build_blackjack_embed(game),
                view=BlackjackView(self, game, credits),
            )

    # ---- admin commands

    @_gambling.group(name="admin")
    @commands.admin_or_permissions(manage_guild=True)
    async def _admin(self, ctx: commands.Context):
        """Admin commands for the gambling system."""
        pass

    @_admin.command(name="give")
    async def _admin_give(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Give a member credits.

        Usage: `[p]gambling admin give @member <amount>`
        """
        if amount <= 0:
            await ctx.send("❌ Amount must be positive.")
            return
        new_bal = await self._adjust_credits(ctx.guild, member, amount)
        await ctx.send(f"✅ Gave {_credits_str(amount)} to {member.mention}. New balance: {_credits_str(new_bal)}.")

    @_admin.command(name="take")
    async def _admin_take(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Remove credits from a member.

        Usage: `[p]gambling admin take @member <amount>`
        """
        if amount <= 0:
            await ctx.send("❌ Amount must be positive.")
            return
        current = await self._get_credits(ctx.guild, member)
        removed = min(amount, current)
        new_bal = await self._adjust_credits(ctx.guild, member, -removed)
        await ctx.send(f"✅ Removed {_credits_str(removed)} from {member.mention}. New balance: {_credits_str(new_bal)}.")

    @_admin.command(name="set")
    async def _admin_set(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Set a member's credits to an exact amount.

        Usage: `[p]gambling admin set @member <amount>`
        """
        if amount < 0:
            await ctx.send("❌ Amount cannot be negative.")
            return
        await self._ensure_initialized(ctx.guild, member)
        await self.config.member(member).credits.set(amount)
        await ctx.send(f"✅ Set {member.mention}'s balance to {_credits_str(amount)}.")

    @_admin.command(name="reset")
    async def _admin_reset(self, ctx: commands.Context, member: discord.Member):
        """Reset a member's credits to the server starting amount.

        Usage: `[p]gambling admin reset @member`
        """
        starting = await self.config.guild(ctx.guild).starting_credits()
        await self.config.member(member).credits.set(starting)
        await self.config.member(member).initialized.set(True)
        await ctx.send(f"✅ Reset {member.mention}'s balance to {_credits_str(starting)}.")

    @_gambling.group(name="settings", aliases=["config"])
    @commands.admin_or_permissions(manage_guild=True)
    async def _settings(self, ctx: commands.Context):
        """Configure gambling system settings."""
        pass

    @_settings.command(name="show")
    async def _settings_show(self, ctx: commands.Context):
        """Show current gambling settings."""
        cfg = self.config.guild(ctx.guild)
        embed = discord.Embed(title="🎰 Gambling Settings", color=await ctx.embed_color())
        embed.add_field(name="Enabled",          value=str(await cfg.enabled()),           inline=True)
        embed.add_field(name="Starting Credits", value=_credits_str(await cfg.starting_credits()), inline=True)
        embed.add_field(name="Daily Bonus",      value=_credits_str(await cfg.daily_bonus()),      inline=True)
        embed.add_field(name="Min Bet",          value=_credits_str(await cfg.min_bet()),           inline=True)
        embed.add_field(name="Max Bet",          value=_credits_str(await cfg.max_bet()),           inline=True)
        await ctx.send(embed=embed)

    @_settings.command(name="startingcredits")
    async def _set_starting(self, ctx: commands.Context, amount: int):
        """Set the starting credit amount for new players.

        Usage: `[p]gambling settings startingcredits <amount>`
        """
        if amount < 0:
            await ctx.send("❌ Amount cannot be negative.")
            return
        await self.config.guild(ctx.guild).starting_credits.set(amount)
        await ctx.send(f"✅ Starting credits set to {_credits_str(amount)}.")

    @_settings.command(name="dailybonus")
    async def _set_daily(self, ctx: commands.Context, amount: int):
        """Set the daily bonus credit amount.

        Usage: `[p]gambling settings dailybonus <amount>`
        """
        if amount < 0:
            await ctx.send("❌ Amount cannot be negative.")
            return
        await self.config.guild(ctx.guild).daily_bonus.set(amount)
        await ctx.send(f"✅ Daily bonus set to {_credits_str(amount)}.")

    @_settings.command(name="minbet")
    async def _set_minbet(self, ctx: commands.Context, amount: int):
        """Set the minimum bet.

        Usage: `[p]gambling settings minbet <amount>`
        """
        if amount < 1:
            await ctx.send("❌ Minimum bet must be at least 1.")
            return
        max_bet = await self.config.guild(ctx.guild).max_bet()
        if amount > max_bet:
            await ctx.send(f"❌ Min bet cannot exceed max bet ({_credits_str(max_bet)}).")
            return
        await self.config.guild(ctx.guild).min_bet.set(amount)
        await ctx.send(f"✅ Minimum bet set to {_credits_str(amount)}.")

    @_settings.command(name="maxbet")
    async def _set_maxbet(self, ctx: commands.Context, amount: int):
        """Set the maximum bet.

        Usage: `[p]gambling settings maxbet <amount>`
        """
        min_bet = await self.config.guild(ctx.guild).min_bet()
        if amount < min_bet:
            await ctx.send(f"❌ Max bet cannot be less than min bet ({_credits_str(min_bet)}).")
            return
        await self.config.guild(ctx.guild).max_bet.set(amount)
        await ctx.send(f"✅ Maximum bet set to {_credits_str(amount)}.")


async def setup(bot: Red):
    """Load the Gambling cog."""
    cog = Gambling(bot)
    await bot.add_cog(cog)
    log.info("Gambling cog loaded")
