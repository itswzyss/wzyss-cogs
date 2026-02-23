import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.wzyss-cogs.boosterrole")

# Config keys for custom_role_mode
MODE_MANUAL_ONLY = "manual_only"
MODE_AUTO_SINGLE = "auto_single"
MODE_AUTO_NAME = "auto_name"
MODE_AUTO_POSITION = "auto_position"


async def setup(bot: Red) -> None:
    cog = BoosterRole(bot)
    await bot.add_cog(cog)


def _is_booster_role(role: discord.Role) -> bool:
    """Return True if this role is the guild's Server Booster (premium subscriber) role."""
    if not role.tags:
        return False
    return getattr(role.tags, "is_premium_subscriber", lambda: False)()


class BoosterRole(commands.Cog):
    """Track booster custom roles and remove them when a user stops boosting."""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x8B00F7A, force_registration=True)
        default_guild = {
            "log_channel_id": None,
            "log_ping_role_id": None,
            "booster_role_id": None,
            "custom_role_mode": MODE_MANUAL_ONLY,
            "manual_custom_roles": {},  # user_id (str) -> role_id (str)
            "name_pattern": None,
            "prefer_single_occupant": True,
        }
        self.config.register_guild(**default_guild)

    @staticmethod
    def _get_booster_role(guild: discord.Guild) -> Optional[discord.Role]:
        """Return the guild's Server Booster role by Discord tag, or None if not found."""
        for role in guild.roles:
            if _is_booster_role(role):
                return role
        return None

    async def _get_effective_booster_role(
        self, guild: discord.Guild
    ) -> Tuple[Optional[discord.Role], bool]:
        """Return the booster role to use (manual if set, else auto) and whether it is manual.
        Returns (role, is_manual). role is None if none could be resolved."""
        manual_id = await self.config.guild(guild).booster_role_id()
        if manual_id:
            role = guild.get_role(manual_id)
            if role is not None:
                return role, True
        auto_role = self._get_booster_role(guild)
        return (auto_role, False) if auto_role else (None, False)

    async def _get_guild_config(self, guild: discord.Guild) -> Dict[str, Any]:
        """Return current guild config as a dict for use in _resolve_custom_role."""
        return {
            "log_channel_id": await self.config.guild(guild).log_channel_id(),
            "log_ping_role_id": await self.config.guild(guild).log_ping_role_id(),
            "custom_role_mode": await self.config.guild(guild).custom_role_mode(),
            "manual_custom_roles": await self.config.guild(guild).manual_custom_roles(),
            "name_pattern": await self.config.guild(guild).name_pattern(),
            "prefer_single_occupant": await self.config.guild(guild).prefer_single_occupant(),
        }

    def _candidate_roles(
        self, member: discord.Member, booster_role: Optional[discord.Role]
    ) -> List[discord.Role]:
        """Roles to consider as custom role: member's roles excluding @everyone and booster role."""
        candidates = []
        for role in member.roles:
            if role.is_default():
                continue
            if booster_role and role.id == booster_role.id:
                continue
            candidates.append(role)
        return candidates

    def _single_occupant_candidates(
        self, member: discord.Member, candidate_roles: List[discord.Role]
    ) -> List[discord.Role]:
        """Among candidate_roles, return those that have exactly one member and it is this member."""
        result = []
        for role in candidate_roles:
            members = [m for m in role.members if not m.bot] if role.members else []
            if len(members) == 1 and members[0].id == member.id:
                result.append(role)
        return result

    def _name_pattern_candidates(
        self, candidate_roles: List[discord.Role], name_pattern: Optional[str]
    ) -> List[discord.Role]:
        """Among candidate_roles, return those whose name matches name_pattern (substring or regex)."""
        if not name_pattern or not name_pattern.strip():
            return []
        pattern = name_pattern.strip()
        try:
            regex = re.compile(pattern)
            match_fn = regex.search
        except re.error:
            match_fn = lambda name: pattern.lower() in name.lower()
        return [r for r in candidate_roles if match_fn(r.name)]

    def _position_below_booster_candidates(
        self,
        member: discord.Member,
        candidate_roles: List[discord.Role],
        booster_role: Optional[discord.Role],
    ) -> List[discord.Role]:
        """Among member's candidate roles, return those strictly below booster role in guild order."""
        if not booster_role:
            return []
        # guild.roles is ordered by position (highest first)
        try:
            booster_pos = booster_role.position
        except Exception:
            return []
        return [r for r in candidate_roles if r.position < booster_pos]

    def _resolve_custom_role(
        self,
        member: discord.Member,
        guild_config: Dict[str, Any],
        booster_role: Optional[discord.Role],
    ) -> Optional[discord.Role]:
        """
        Resolve the custom role for this member. Manual mapping overrides auto.
        Returns the discord.Role or None if not found.
        """
        guild = member.guild
        manual = guild_config.get("manual_custom_roles") or {}
        mode = guild_config.get("custom_role_mode") or MODE_MANUAL_ONLY
        name_pattern = guild_config.get("name_pattern")
        prefer_single = guild_config.get("prefer_single_occupant", True)

        # 1. Manual override
        role_id_str = manual.get(str(member.id))
        if role_id_str:
            try:
                role_id = int(role_id_str)
            except (TypeError, ValueError):
                pass
            else:
                role = guild.get_role(role_id)
                if role is not None:
                    return role
                # Role was deleted; fall through to auto if not manual_only

        if mode == MODE_MANUAL_ONLY:
            return None

        candidate_roles = self._candidate_roles(member, booster_role)
        if not candidate_roles:
            return None

        single = self._single_occupant_candidates(member, candidate_roles)
        by_name = self._name_pattern_candidates(candidate_roles, name_pattern) if name_pattern else []
        below = self._position_below_booster_candidates(member, candidate_roles, booster_role)

        # 2. Auto: single-occupant
        if mode == MODE_AUTO_SINGLE:
            if len(single) == 1:
                return single[0]
            if len(single) > 1 and prefer_single:
                return single[0]  # arbitrary but consistent
            return None

        # 3. Auto: name pattern
        if mode == MODE_AUTO_NAME and by_name:
            if prefer_single and single:
                single_among_names = [r for r in by_name if r in single]
                if single_among_names:
                    return single_among_names[0]
            return by_name[0]

        # 4. Auto: position below booster
        if mode == MODE_AUTO_POSITION and len(below) == 1:
            return below[0]
        if mode == MODE_AUTO_POSITION and len(below) > 1 and prefer_single:
            single_below = [r for r in below if r in single]
            if len(single_below) == 1:
                return single_below[0]

        return None

    async def _log_booster_lost(
        self,
        guild: discord.Guild,
        member: discord.Member,
        custom_role: Optional[discord.Role],
        removed: bool,
        removal_failed: bool = False,
    ) -> None:
        """Send log message to configured channel with optional role ping."""
        channel_id = await self.config.guild(guild).log_channel_id()
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        ping_role_id = await self.config.guild(guild).log_ping_role_id()
        ping_mention = ""
        if ping_role_id:
            ping_role = guild.get_role(ping_role_id)
            if ping_role:
                ping_mention = ping_role.mention + " "

        if removal_failed:
            body = (
                f"{member.mention} (`{member.id}`) no longer has the Server Booster role. "
                "Their custom role could not be removed (missing permissions or role not found)."
            )
        elif custom_role is None:
            body = (
                f"{member.mention} (`{member.id}`) no longer has the Server Booster role. "
                "No custom role was identified for this user."
            )
        elif removed:
            body = (
                f"{member.mention} (`{member.id}`) no longer has the Server Booster role and "
                f"was removed from their custom role {custom_role.mention}."
            )
        else:
            body = (
                f"{member.mention} (`{member.id}`) no longer has the Server Booster role. "
                f"They did not have the identified custom role {custom_role.mention} (already removed)."
            )

        try:
            await channel.send(ping_mention + body)
        except discord.Forbidden:
            log.warning(
                "BoosterRole: cannot send to log channel %s in guild %s",
                channel_id,
                guild.id,
            )

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """When a user loses the Server Booster role, remove their custom role and log."""
        if before.guild is None or after.guild is None:
            return
        if before.roles == after.roles:
            return

        guild = after.guild
        booster_role, _ = await self._get_effective_booster_role(guild)
        if not booster_role:
            return

        had_booster = booster_role in before.roles
        has_booster_now = booster_role in after.roles
        if not had_booster or has_booster_now:
            return

        # Only act if we have at least log channel or some config (so we don't run for every guild blindly)
        log_channel_id = await self.config.guild(guild).log_channel_id()
        mode = await self.config.guild(guild).custom_role_mode()
        manual = await self.config.guild(guild).manual_custom_roles()
        if not log_channel_id and mode == MODE_MANUAL_ONLY and not manual:
            return

        guild_config = await self._get_guild_config(guild)
        custom_role = self._resolve_custom_role(after, guild_config, booster_role)

        removed = False
        removal_failed = False
        if custom_role is not None and custom_role in after.roles:
            try:
                await after.remove_roles(custom_role, reason="Booster role removed")
                removed = True
            except discord.Forbidden:
                log.warning(
                    "BoosterRole: cannot remove role %s from %s in guild %s",
                    custom_role.id,
                    after.id,
                    guild.id,
                )
                removal_failed = True
            except Exception as e:
                log.exception("BoosterRole: error removing custom role: %s", e)
                removal_failed = True

        await self._log_booster_lost(
            guild, after, custom_role, removed, removal_failed=removal_failed
        )

    @commands.group(name="boosterrole", aliases=["boostrole"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _boosterrole(self, ctx: commands.Context) -> None:
        """Manage booster custom role tracking (remove custom role when user stops boosting)."""
        # Red's Group.invoke already sends help when no subcommand is given; do not send_help() again

    @_boosterrole.command(name="logchannel", aliases=["logchannel set"])
    async def _logchannel_set(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where booster loss / custom role removal is logged."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Booster role removal logs will be sent to {channel.mention}.")

    @_boosterrole.command(name="logchannel clear", aliases=["logchannelclear"])
    async def _logchannel_clear(self, ctx: commands.Context) -> None:
        """Clear the log channel. Events will no longer be sent to a channel."""
        await self.config.guild(ctx.guild).log_channel_id.set(None)
        await ctx.send("Log channel cleared.")

    @_boosterrole.command(name="logping", aliases=["logping set"])
    async def _logping_set(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        """Set the role to ping when logging booster loss (in addition to the log message)."""
        await self.config.guild(ctx.guild).log_ping_role_id.set(role.id)
        await ctx.send(f"Log messages will include {role.mention}.")

    @_boosterrole.command(name="logping clear", aliases=["logpingclear"])
    async def _logping_clear(self, ctx: commands.Context) -> None:
        """Clear the log ping role."""
        await self.config.guild(ctx.guild).log_ping_role_id.set(None)
        await ctx.send("Log ping role cleared.")

    @_boosterrole.command(name="setboosterrole", aliases=["boosterrole set"])
    async def _set_booster_role(
        self, ctx: commands.Context, role: discord.Role
    ) -> None:
        """Set the role used as the Server Booster role (overrides auto-detection)."""
        await self.config.guild(ctx.guild).booster_role_id.set(role.id)
        await ctx.send(f"Booster role set to {role.mention}. Loss of this role will trigger custom role removal.")

    @_boosterrole.command(name="clearboosterrole", aliases=["boosterrole clear"])
    async def _clear_booster_role(self, ctx: commands.Context) -> None:
        """Clear the manual booster role. Auto-detection (Discord Server Booster tag) will be used again."""
        await self.config.guild(ctx.guild).booster_role_id.set(None)
        await ctx.send("Manual booster role cleared. Using auto-detection.")

    @_boosterrole.command(name="mode")
    async def _mode(
        self,
        ctx: commands.Context,
        mode: str,
    ) -> None:
        """Set how custom roles are identified when no manual mapping exists.

        Modes: manual_only, auto_single, auto_name, auto_position
        - manual_only: only use manually set user->role mappings
        - auto_single: use the role that only this user has (single occupant)
        - auto_name: use the role whose name matches the configured name pattern
        - auto_position: use the single role below the Server Booster role
        """
        normalized = mode.lower().strip()
        if normalized not in (
            MODE_MANUAL_ONLY,
            MODE_AUTO_SINGLE,
            MODE_AUTO_NAME,
            MODE_AUTO_POSITION,
        ):
            await ctx.send(
                "Invalid mode. Use one of: manual_only, auto_single, auto_name, auto_position"
            )
            return
        await self.config.guild(ctx.guild).custom_role_mode.set(normalized)
        await ctx.send(f"Custom role identification mode set to `{normalized}`.")

    @_boosterrole.command(name="namepattern", aliases=["namepattern set"])
    async def _namepattern_set(
        self, ctx: commands.Context, *, pattern: str
    ) -> None:
        """Set the name pattern for auto_name mode (substring or regex)."""
        await self.config.guild(ctx.guild).name_pattern.set(pattern.strip() or None)
        await ctx.send(f"Name pattern set to: {box(pattern.strip() or '(cleared)')}")

    @_boosterrole.command(name="namepattern clear", aliases=["namepatternclear"])
    async def _namepattern_clear(self, ctx: commands.Context) -> None:
        """Clear the name pattern."""
        await self.config.guild(ctx.guild).name_pattern.set(None)
        await ctx.send("Name pattern cleared.")

    @_boosterrole.command(name="prefer_single")
    async def _prefer_single(
        self, ctx: commands.Context, value: bool
    ) -> None:
        """When multiple candidates exist, prefer the one that is single-occupant (True/False)."""
        await self.config.guild(ctx.guild).prefer_single_occupant.set(value)
        await ctx.send(f"Prefer single-occupant set to `{value}`.")

    @_boosterrole.command(name="setcustomrole", aliases=["setrole"])
    async def _set_custom_role(
        self,
        ctx: commands.Context,
        user: discord.Member,
        role: discord.Role,
    ) -> None:
        """Set the custom role for a user (manual override). Used when they lose the Server Booster role."""
        async with self.config.guild(ctx.guild).manual_custom_roles() as mapping:
            mapping[str(user.id)] = str(role.id)
        await ctx.send(
            f"When {user.display_name} loses the Server Booster role, they will be "
            f"removed from {role.mention}."
        )

    @_boosterrole.command(name="clearcustomrole", aliases=["clearrole"])
    async def _clear_custom_role(
        self, ctx: commands.Context, user: discord.Member
    ) -> None:
        """Remove the manual custom role mapping for a user."""
        async with self.config.guild(ctx.guild).manual_custom_roles() as mapping:
            mapping.pop(str(user.id), None)
        await ctx.send(f"Manual custom role for {user.display_name} cleared.")

    @_boosterrole.command(name="show", aliases=["settings"])
    async def _show(self, ctx: commands.Context) -> None:
        """Show current booster role settings and manual mappings."""
        guild = ctx.guild
        log_channel_id = await self.config.guild(guild).log_channel_id()
        log_ping_role_id = await self.config.guild(guild).log_ping_role_id()
        booster_role, booster_is_manual = await self._get_effective_booster_role(guild)
        mode = await self.config.guild(guild).custom_role_mode()
        name_pattern = await self.config.guild(guild).name_pattern()
        prefer_single = await self.config.guild(guild).prefer_single_occupant()
        manual = await self.config.guild(guild).manual_custom_roles()

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        log_ping_role = guild.get_role(log_ping_role_id) if log_ping_role_id else None
        booster_label = "manual" if booster_is_manual else "auto"
        booster_str = f"{booster_role.mention} ({booster_label})" if booster_role else "Not set"

        embed = discord.Embed(
            title="Booster role settings",
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="Booster role",
            value=booster_str,
            inline=True,
        )
        embed.add_field(
            name="Log channel",
            value=log_channel.mention if log_channel else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Log ping role",
            value=log_ping_role.mention if log_ping_role else "None",
            inline=True,
        )
        embed.add_field(
            name="Mode",
            value=box(mode),
            inline=True,
        )
        embed.add_field(
            name="Name pattern",
            value=box(name_pattern or "None"),
            inline=True,
        )
        embed.add_field(
            name="Prefer single-occupant",
            value=box(str(prefer_single)),
            inline=True,
        )
        mapping_lines = []
        for uid, rid in list(manual.items())[:20]:
            mem = guild.get_member(int(uid)) if uid.isdigit() else None
            role = guild.get_role(int(rid)) if rid.isdigit() else None
            user_part = mem.mention if mem else f"User `{uid}`"
            role_part = role.mention if role else f"`{rid}`"
            mapping_lines.append(f"{user_part} \u2192 {role_part}")
        if len(manual) > 20:
            mapping_lines.append(f"*... and {len(manual) - 20} more*")
        mappings_value = "\n".join(mapping_lines) if mapping_lines else "None"
        if len(mappings_value) > 1024:
            mappings_value = mappings_value[:1021] + "..."
        embed.add_field(
            name=f"Manual custom role mappings ({len(manual)})",
            value=mappings_value,
            inline=False,
        )
        await ctx.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False),
        )

    @_boosterrole.group(name="dev")
    @commands.is_owner()
    @commands.guild_only()
    async def _boosterrole_dev(self, ctx: commands.Context) -> None:
        """Temporary dev commands to test booster role and custom role removal. Bot owner only."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @_boosterrole_dev.command(name="setup")
    async def _dev_setup(
        self,
        ctx: commands.Context,
        booster_role: discord.Role,
        custom_role: discord.Role,
    ) -> None:
        """Set test booster role and custom role, then add both to you.

        Config: sets manual booster role and your manual custom role mapping, then
        adds both roles to you. Use `boosterrole dev trigger` to simulate losing
        the booster role and test removal + logging. Set a log channel first if
        you want to see the log message.
        """
        guild = ctx.guild
        member = ctx.author
        await self.config.guild(guild).booster_role_id.set(booster_role.id)
        async with self.config.guild(guild).manual_custom_roles() as mapping:
            mapping[str(member.id)] = str(custom_role.id)
        try:
            await member.add_roles(booster_role, custom_role, reason="BoosterRole dev setup")
        except discord.Forbidden:
            await ctx.send(
                "Config was set, but I could not add the roles to you (missing Manage Roles or role hierarchy)."
            )
            return
        await ctx.send(
            f"Test setup done. Booster role set to {booster_role.mention}, your custom role to {custom_role.mention}. "
            f"You now have both roles. Use `{ctx.clean_prefix}boosterrole dev trigger` to remove your booster role and test removal + logging."
        )

    @_boosterrole_dev.command(name="trigger")
    async def _dev_trigger(self, ctx: commands.Context) -> None:
        """Remove your booster role to trigger the cog (removal of custom role + log)."""
        guild = ctx.guild
        member = ctx.author
        booster_role, _ = await self._get_effective_booster_role(guild)
        if not booster_role:
            await ctx.send("No booster role is configured. Use `boosterrole setboosterrole` or dev setup first.")
            return
        if booster_role not in member.roles:
            await ctx.send("You do not have the booster role. Nothing to remove.")
            return
        try:
            await member.remove_roles(booster_role, reason="BoosterRole dev trigger")
        except discord.Forbidden:
            await ctx.send("I could not remove the booster role from you (missing Manage Roles or role hierarchy).")
            return
        await ctx.send(
            "Removed your booster role. The cog should have removed your custom role and sent a log (if a log channel is set). Check the log channel and your roles."
        )

    @_boosterrole_dev.command(name="setloghere")
    async def _dev_setloghere(self, ctx: commands.Context) -> None:
        """Set the log channel to this channel (for testing)."""
        await self.config.guild(ctx.guild).log_channel_id.set(ctx.channel.id)
        await ctx.send(f"Log channel set to {ctx.channel.mention} for this guild.")