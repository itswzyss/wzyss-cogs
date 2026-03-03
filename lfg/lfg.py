"""
LFG cog: Looking for Group – guild-scoped games, availability, notify via DM, game requests.
"""
import logging
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

log = logging.getLogger("red.wzyss-cogs.lfg")

# Config identifier (unique)
LFG_CONFIG_IDENTIFIER = 987654324

# Cooldowns: notify 1/90s, request 1/120s, available/unavailable 3/15s (per user per guild)
NOTIFY_COOLDOWN_RATE = 1
NOTIFY_COOLDOWN_PER = 90
REQUEST_COOLDOWN_RATE = 1
REQUEST_COOLDOWN_PER = 120
AVAILABILITY_COOLDOWN_RATE = 3
AVAILABILITY_COOLDOWN_PER = 15

# Max game name length for requests
GAME_NAME_MAX_LENGTH = 100


def _normalize_game_name(name: str) -> str:
    """Normalize for storage and matching: lowercase, strip."""
    return name.strip().lower() if name else ""


def _resolve_game(games: List[str], input_name: str) -> Tuple[Optional[str], Optional[List[str]]]:
    """
    Resolve user input to a single canonical game name from the masterlist.
    Returns (canonical_name, None) if exactly one match, (None, suggestions) if zero or multiple.
    """
    normalized_input = _normalize_game_name(input_name)
    if not normalized_input:
        return (None, list(games)[:10] if games else None)

    exact = []
    starts = []
    contains = []
    for g in games:
        n = _normalize_game_name(g)
        if n == normalized_input:
            exact.append(g)
        elif n.startswith(normalized_input) or normalized_input.startswith(n):
            starts.append(g)
        elif normalized_input in n or n in normalized_input:
            contains.append(g)

    if exact:
        return (exact[0], None)
    candidates = (starts or contains) or []
    if len(candidates) == 1:
        return (candidates[0], None)
    if candidates:
        return (None, candidates[:10])
    return (None, list(games)[:10] if games else None)


class LFG(commands.Cog):
    """Looking for Group: register availability per game, see who's available, notify via DM, request games."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=LFG_CONFIG_IDENTIFIER, force_registration=True
        )
        default_guild = {
            "games": [],
            "game_requests": [],
            "notify_dm_opt_in": [],
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(availability={})
        log.info("LFG cog initialized")

    async def _send_reply(
        self,
        ctx: commands.Context,
        content: str,
        *,
        ephemeral: bool = False,
        embed: Optional[discord.Embed] = None,
    ):
        if getattr(ctx, "interaction", None) and ephemeral:
            await ctx.send(content, embed=embed, ephemeral=True)
        else:
            await ctx.send(content, embed=embed)

    def _resolve_game_from_ctx(self, games: List[str], input_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Returns (canonical_name, error_or_suggestions_message)."""
        canonical, suggestions = _resolve_game(games, input_name)
        if canonical:
            return (canonical, None)
        if suggestions:
            return (None, f"Did you mean one of: **{', '.join(suggestions)}**?")
        return (None, "No matching game in the list. Use `[p]lfg list` to see games, or request one with `[p]lfg request <name>`.")

    @commands.hybrid_group(name="lfg", invoke_without_command=True)
    @commands.guild_only()
    @app_commands.describe()
    async def lfg_group(self, ctx: commands.Context):
        """
        Looking for Group: set availability per game, see who's available, notify, request games.
        Use `[p]lfg list` to see games.
        """
        await self.lfg_list(ctx)

    @lfg_group.command(name="list")
    @commands.guild_only()
    @app_commands.describe()
    async def lfg_list(self, ctx: commands.Context):
        """List all games in the server's LFG masterlist."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        if not games:
            await self._send_reply(
                ctx,
                "No games in the list yet. An admin can add some with `[p]lfg add <name>`, or you can request one with `[p]lfg request <name>`.",
                ephemeral=True,
            )
            return
        color = await self.bot.get_embed_color(ctx.channel)
        embed = discord.Embed(
            title="LFG Games",
            description="Use `[p]lfg who <game>` to see who is available.",
            color=color,
        )
        chunk = ", ".join(f"**{g}**" for g in games)
        for page in pagify(chunk, page_length=1000):
            embed.add_field(name="Games", value=page or "\u200b", inline=False)
        await self._send_reply(ctx, None, embed=embed)

    @lfg_group.command(name="who")
    @commands.guild_only()
    @app_commands.describe(game="Game name (from the list)")
    async def lfg_who(self, ctx: commands.Context, *, game: str):
        """Show who is available (and optionally unavailable) for a game."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        all_members_data = await self.config.all_members(guild)
        available_ids = []
        unavailable_ids = []
        for mid, data in all_members_data.items():
            av = data.get("availability") or {}
            status = av.get(key)
            if status == "available":
                available_ids.append(mid)
            elif status == "unavailable":
                unavailable_ids.append(mid)
        color = await self.bot.get_embed_color(ctx.channel)
        embed = discord.Embed(title=f"Who is available for **{canonical}**?", color=color)
        if available_ids:
            names = []
            for uid in available_ids:
                m = guild.get_member(uid)
                names.append(m.display_name if m else str(uid))
            embed.add_field(name="Available", value=", ".join(names) or "\u200b", inline=False)
        else:
            embed.add_field(name="Available", value="No one right now.", inline=False)
        if unavailable_ids:
            names = []
            for uid in unavailable_ids:
                m = guild.get_member(uid)
                names.append(m.display_name if m else str(uid))
            embed.add_field(name="Unavailable", value=", ".join(names) or "\u200b", inline=False)
        await self._send_reply(ctx, None, embed=embed)

    @lfg_group.command(name="available")
    @commands.guild_only()
    @commands.cooldown(AVAILABILITY_COOLDOWN_RATE, AVAILABILITY_COOLDOWN_PER, commands.BucketType.member)
    @app_commands.describe(game="Game name (from the list)")
    async def lfg_available(self, ctx: commands.Context, *, game: str):
        """Set yourself as available for a game."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        async with self.config.member(ctx.author).availability() as av:
            av[key] = "available"
        await self._send_reply(ctx, f"You are now **available** for **{canonical}**.", ephemeral=True)

    @lfg_group.command(name="unavailable")
    @commands.guild_only()
    @commands.cooldown(AVAILABILITY_COOLDOWN_RATE, AVAILABILITY_COOLDOWN_PER, commands.BucketType.member)
    @app_commands.describe(game="Game name (from the list)")
    async def lfg_unavailable(self, ctx: commands.Context, *, game: str):
        """Set yourself as unavailable for a game."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        async with self.config.member(ctx.author).availability() as av:
            av[key] = "unavailable"
        await self._send_reply(ctx, f"You are now **unavailable** for **{canonical}**.", ephemeral=True)

    @lfg_group.command(name="clear")
    @commands.guild_only()
    @app_commands.describe(game="Game name to clear; omit to clear all your availability in this server")
    async def lfg_clear(self, ctx: commands.Context, *, game: Optional[str] = None):
        """Clear your availability for a game, or for all games in this server."""
        if game is None or not game.strip():
            async with self.config.member(ctx.author).availability() as av:
                av.clear()
            await self._send_reply(ctx, "Cleared your availability for all games in this server.", ephemeral=True)
            return
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        async with self.config.member(ctx.author).availability() as av:
            av.pop(key, None)
        await self._send_reply(ctx, f"Cleared your availability for **{canonical}**.", ephemeral=True)

    @lfg_group.group(name="notify", invoke_without_command=True)
    @commands.guild_only()
    @commands.cooldown(NOTIFY_COOLDOWN_RATE, NOTIFY_COOLDOWN_PER, commands.BucketType.member)
    @app_commands.describe(game="Game name to notify available (opted-in) users for")
    async def lfg_notify_group(self, ctx: commands.Context, *, game: Optional[str] = None):
        """Notify everyone who is available and opted in for a game (sends a DM). Use `[p]lfg notify optin` / `optout` to manage opt-in."""
        if game is None or not game.strip():
            await ctx.send_help()
            return
        await self._lfg_notify_game(ctx, game)

    @lfg_notify_group.command(name="optin")
    @commands.guild_only()
    @app_commands.describe()
    async def lfg_notify_optin(self, ctx: commands.Context):
        """Opt in to receive LFG notify DMs when you're available for the game someone notifies for."""
        guild = ctx.guild
        async with self.config.guild(guild).notify_dm_opt_in() as opt_in:
            uid = ctx.author.id
            if uid not in opt_in:
                opt_in.append(uid)
        await self._send_reply(ctx, "You are now opted in to LFG notify DMs for this server.", ephemeral=True)

    @lfg_notify_group.command(name="optout")
    @commands.guild_only()
    @app_commands.describe()
    async def lfg_notify_optout(self, ctx: commands.Context):
        """Opt out of LFG notify DMs."""
        guild = ctx.guild
        async with self.config.guild(guild).notify_dm_opt_in() as opt_in:
            uid = ctx.author.id
            while uid in opt_in:
                opt_in.remove(uid)
        await self._send_reply(ctx, "You are now opted out of LFG notify DMs.", ephemeral=True)

    async def _lfg_notify_game(self, ctx: commands.Context, game: str):
        """Send DMs to all users who are available for the game and opted in."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        opt_in_ids = await self.config.guild(guild).notify_dm_opt_in()
        opt_in_set = set(opt_in_ids or [])

        all_members_data = await self.config.all_members(guild)
        available_and_opted_in = [
            uid for uid, data in all_members_data.items()
            if (data.get("availability") or {}).get(key) == "available" and uid in opt_in_set
        ]
        if not available_and_opted_in:
            await self._send_reply(
                ctx,
                f"No one is both available for **{canonical}** and opted in to notify DMs.",
                ephemeral=True,
            )
            return
        channel_link = ctx.channel.jump_url if hasattr(ctx.channel, "jump_url") else None
        guild_name = guild.name
        notified = 0
        failed = 0
        for uid in available_and_opted_in:
            if uid == ctx.author.id:
                continue
            member = guild.get_member(uid)
            if not member:
                continue
            try:
                msg = f"You were notified for **{canonical}** in **{guild_name}**."
                if channel_link:
                    msg += f" [Jump to channel]({channel_link})"
                await member.send(msg)
                notified += 1
            except discord.Forbidden:
                failed += 1
            except Exception as e:
                log.warning("LFG notify DM failed for %s: %s", uid, e)
                failed += 1
        out = f"Notified **{notified}** user(s) for **{canonical}**."
        if failed:
            out += f" Could not DM {failed} user(s) (DMs may be disabled)."
        await self._send_reply(ctx, out)

    @lfg_group.command(name="request")
    @commands.guild_only()
    @commands.cooldown(REQUEST_COOLDOWN_RATE, REQUEST_COOLDOWN_PER, commands.BucketType.member)
    @app_commands.describe(game_name="Name of the game to request (can be multiple words)")
    async def lfg_request(self, ctx: commands.Context, *, game_name: str):
        """Request that a game be added to the masterlist. An admin will review it."""
        name = game_name.strip()
        if not name:
            await self._send_reply(ctx, "Please provide a game name.", ephemeral=True)
            return
        if len(name) > GAME_NAME_MAX_LENGTH:
            await self._send_reply(ctx, f"Game name is too long (max {GAME_NAME_MAX_LENGTH} characters).", ephemeral=True)
            return
        guild = ctx.guild
        async with self.config.guild(guild).game_requests() as reqs:
            reqs.append({
                "user_id": ctx.author.id,
                "game_name": name,
                "requested_at": discord.utils.utcnow().isoformat(),
            })
        await self._send_reply(ctx, f"Request for **{name}** submitted. An admin will review it.", ephemeral=True)

    @lfg_group.command(name="add")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(game_name="Name of the game to add to the masterlist")
    async def lfg_add(self, ctx: commands.Context, *, game_name: str):
        """Add a game to the server's LFG masterlist (admin)."""
        name = game_name.strip()
        if not name:
            await self._send_reply(ctx, "Please provide a game name.", ephemeral=True)
            return
        if len(name) > GAME_NAME_MAX_LENGTH:
            await self._send_reply(ctx, f"Game name is too long (max {GAME_NAME_MAX_LENGTH} characters).", ephemeral=True)
            return
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        key = _normalize_game_name(name)
        if any(_normalize_game_name(g) == key for g in games):
            await self._send_reply(ctx, f"**{name}** is already in the list.", ephemeral=True)
            return
        games.append(name)
        await self.config.guild(guild).games.set(games)
        await self._send_reply(ctx, f"Added **{name}** to the LFG list.")

    @lfg_group.command(name="remove")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(game="Game name to remove from the masterlist")
    async def lfg_remove(self, ctx: commands.Context, *, game: str):
        """Remove a game from the masterlist (admin). User availability for this game is not cleared."""
        guild = ctx.guild
        games = await self.config.guild(guild).games()
        canonical, err = self._resolve_game_from_ctx(games, game)
        if err:
            await self._send_reply(ctx, err, ephemeral=True)
            return
        key = _normalize_game_name(canonical)
        new_games = [g for g in games if _normalize_game_name(g) != key]
        await self.config.guild(guild).games.set(new_games)
        await self._send_reply(ctx, f"Removed **{canonical}** from the LFG list.")

    @lfg_group.command(name="requests")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe()
    async def lfg_requests(self, ctx: commands.Context):
        """List pending game requests (admin)."""
        guild = ctx.guild
        reqs = await self.config.guild(guild).game_requests()
        if not reqs:
            await self._send_reply(ctx, "No pending requests.", ephemeral=True)
            return
        color = await self.bot.get_embed_color(ctx.channel)
        embed = discord.Embed(title="Pending game requests", color=color)
        for i, r in enumerate(reqs, 1):
            uid = r.get("user_id")
            gname = r.get("game_name", "?")
            ts = r.get("requested_at", "")
            member = guild.get_member(uid) if uid else None
            who = member.mention if member else str(uid)
            embed.add_field(
                name=f"{i}. {gname}",
                value=f"Requested by {who} — {ts[:19] if ts else '?'}",
                inline=False,
            )
        await self._send_reply(ctx, None, embed=embed)

    @lfg_group.command(name="approve")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(game_name="Game name from the requests list to approve")
    async def lfg_approve(self, ctx: commands.Context, *, game_name: str):
        """Approve a game request and add it to the masterlist (admin)."""
        guild = ctx.guild
        reqs = await self.config.guild(guild).game_requests()
        key_input = _normalize_game_name(game_name.strip())
        if not key_input:
            await self._send_reply(ctx, "Please provide a game name.", ephemeral=True)
            return
        found = None
        for r in reqs:
            if _normalize_game_name(r.get("game_name", "")) == key_input:
                found = r
                break
        if not found:
            await self._send_reply(ctx, "No matching request found. Use `[p]lfg requests` to see the list.", ephemeral=True)
            return
        gname = found.get("game_name", "").strip()
        games = await self.config.guild(guild).games()
        if any(_normalize_game_name(g) == _normalize_game_name(gname) for g in games):
            new_reqs = [r for r in reqs if r != found]
            await self.config.guild(guild).game_requests.set(new_reqs)
            await self._send_reply(ctx, f"**{gname}** is already in the list; removed the duplicate request.")
            return
        games.append(gname)
        await self.config.guild(guild).games.set(games)
        new_reqs = [r for r in reqs if r != found]
        await self.config.guild(guild).game_requests.set(new_reqs)
        requester_id = found.get("user_id")
        if requester_id:
            member = guild.get_member(requester_id)
            if member:
                try:
                    await member.send(f"Your game **{gname}** was added to the LFG list in **{guild.name}**.")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await self._send_reply(ctx, f"Approved **{gname}** and added it to the list.")

    @lfg_group.command(name="deny")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @app_commands.describe(game_name="Game name from the requests list to deny")
    async def lfg_deny(self, ctx: commands.Context, *, game_name: str):
        """Deny a game request (admin)."""
        guild = ctx.guild
        reqs = await self.config.guild(guild).game_requests()
        key_input = _normalize_game_name(game_name.strip())
        if not key_input:
            await self._send_reply(ctx, "Please provide a game name.", ephemeral=True)
            return
        found = None
        for r in reqs:
            if _normalize_game_name(r.get("game_name", "")) == key_input:
                found = r
                break
        if not found:
            await self._send_reply(ctx, "No matching request found. Use `[p]lfg requests` to see the list.", ephemeral=True)
            return
        gname = found.get("game_name", "X")
        new_reqs = [r for r in reqs if r != found]
        await self.config.guild(guild).game_requests.set(new_reqs)
        requester_id = found.get("user_id")
        if requester_id:
            member = guild.get_member(requester_id)
            if member:
                try:
                    await member.send(f"Your request for **{gname}** was denied.")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await self._send_reply(ctx, f"Denied request for **{gname}**.")


async def setup(bot: Red):
    cog = LFG(bot)
    await bot.add_cog(cog)
    log.info("LFG cog loaded")
