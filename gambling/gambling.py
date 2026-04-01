import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

from .blackjack import (
    BlackjackGame,
    BlackjackView,
    InsuranceView,
    build_blackjack_embed,
    build_insurance_embed,
)

log = logging.getLogger("red.wzyss-cogs.gambling")


def _credits_str(n: int) -> str:
    return f"{n:,} cr"


# ---------------------------------------------------------------------------
# Gambling cog
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
        embed.add_field(name="Balance",      value=_credits_str(data["credits"]),    inline=True)
        embed.add_field(name="Games Played", value=str(played),                      inline=True)
        embed.add_field(name="Win Rate",     value=f"{win_rate:.1f}%",               inline=True)
        embed.add_field(name="Wins",         value=str(won),                         inline=True)
        embed.add_field(name="Losses",       value=str(data["games_lost"]),          inline=True)
        embed.add_field(name="Pushes",       value=str(data["games_pushed"]),        inline=True)
        embed.add_field(name="Total Won",    value=_credits_str(data["total_won"]),  inline=True)
        embed.add_field(name="Total Lost",   value=_credits_str(data["total_lost"]), inline=True)
        embed.add_field(name="BJ Naturals",  value=str(data["bj_naturals"]),         inline=True)
        embed.add_field(name="Best Win Streak",   value=str(data["best_win_streak"]),  inline=True)
        embed.add_field(name="Worst Loss Streak", value=str(data["worst_loss_streak"]), inline=True)
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
        embed.add_field(name="Enabled",          value=str(await cfg.enabled()),                  inline=True)
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
