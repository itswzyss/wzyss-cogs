import logging

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.announcer")

PENDING_EMOJI = "📢"
SENT_EMOJI = "✅"


class Announcer(commands.Cog):
    """Forward channel messages to subscribed role members via DM, with a confirmation step."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847261938)
        self.config.register_guild(subscriptions={})
        # message_id -> True, cleared on restart; ✅ reaction prevents double-sends across restarts
        self._sent_messages: set[int] = set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(cfg) -> dict:
        """Coerce legacy string entries (role_id only) to the current dict format."""
        if isinstance(cfg, str):
            return {"role_id": cfg, "jump_link": True}
        return cfg

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def announcer(self, ctx: commands.Context):
        """Manage DM announcement subscriptions."""
        pass

    @announcer.command(name="add")
    async def announcer_add(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        role: discord.Role,
    ):
        """Subscribe a role to DM notifications from a channel.

        When a message is posted in `channel`, the bot reacts with 📢.
        The message author or any member with Manage Messages can click 📢
        to broadcast the message to all members with `role` via DM.
        """
        async with self.config.guild(ctx.guild).subscriptions() as subs:
            subs[str(channel.id)] = {"role_id": str(role.id), "jump_link": True}
        await ctx.send(
            f"Members with {role.mention} will receive DMs for messages in "
            f"{channel.mention} after the 📢 reaction is confirmed."
        )

    @announcer.command(name="remove")
    async def announcer_remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove DM notifications for a channel."""
        async with self.config.guild(ctx.guild).subscriptions() as subs:
            if str(channel.id) not in subs:
                await ctx.send("No subscription found for that channel.")
                return
            del subs[str(channel.id)]
        await ctx.send(f"Removed subscription for {channel.mention}.")

    @announcer.command(name="jumplink")
    async def announcer_jumplink(self, ctx: commands.Context, channel: discord.TextChannel):
        """Toggle whether DMs include a jump link to the original message.

        Disable this when subscribers may not have access to the source channel.
        """
        async with self.config.guild(ctx.guild).subscriptions() as subs:
            key = str(channel.id)
            if key not in subs:
                await ctx.send("No subscription found for that channel.")
                return
            subs[key] = self._normalize(subs[key])
            current = subs[key]["jump_link"]
            subs[key]["jump_link"] = not current
            new_state = subs[key]["jump_link"]
        state_str = "enabled" if new_state else "disabled"
        await ctx.send(f"Jump link {state_str} for {channel.mention}.")

    @announcer.command(name="list")
    async def announcer_list(self, ctx: commands.Context):
        """List all configured channel → role subscription mappings."""
        subs = await self.config.guild(ctx.guild).subscriptions()
        if not subs:
            await ctx.send("No subscriptions configured.")
            return
        lines = []
        for channel_id, cfg in subs.items():
            cfg = self._normalize(cfg)
            channel = ctx.guild.get_channel(int(channel_id))
            role = ctx.guild.get_role(int(cfg["role_id"]))
            ch_str = channel.mention if channel else f"<#{channel_id}>"
            role_str = role.mention if role else f"<@&{cfg['role_id']}>"
            link_icon = "🔗" if cfg["jump_link"] else "🚫"
            lines.append(f"{ch_str} → {role_str} {link_icon}")
        embed = discord.Embed(
            title="Announcement Subscriptions",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="🔗 = jump link included in DM  |  🚫 = jump link hidden")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        subs = await self.config.guild(message.guild).subscriptions()
        if str(message.channel.id) not in subs:
            return
        try:
            await message.add_reaction(PENDING_EMOJI)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Ignore the bot's own reactions
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != PENDING_EMOJI:
            return
        if payload.guild_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        subs = await self.config.guild(guild).subscriptions()
        key = str(payload.channel_id)
        if key not in subs:
            return

        # Already broadcast this message
        if payload.message_id in self._sent_messages:
            return

        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # Guard against re-broadcast across restarts: if ✅ is already on the message, skip
        for reaction in message.reactions:
            if str(reaction.emoji) == SENT_EMOJI:
                self._sent_messages.add(payload.message_id)
                return

        # Only the message author or someone with Manage Messages can confirm
        is_author = message.author.id == payload.user_id
        has_perms = channel.permissions_for(member).manage_messages
        if not is_author and not has_perms:
            return

        # Mark sent before async work to prevent race conditions
        self._sent_messages.add(payload.message_id)

        cfg = self._normalize(subs[key])
        role = guild.get_role(int(cfg["role_id"]))
        if role is None:
            log.warning("Announcer: role %s not found in guild %s", cfg["role_id"], guild.id)
            return

        # Build embed for the DM
        embed = discord.Embed(
            description=message.content or "*No text content*",
            color=discord.Color.blue(),
            timestamp=message.created_at,
        )
        embed.set_author(
            name=str(message.author),
            icon_url=message.author.display_avatar.url,
        )
        embed.add_field(name="Server", value=guild.name, inline=True)
        if cfg["jump_link"]:
            embed.add_field(
                name="Jump to message",
                value=f"[Click here]({message.jump_url})",
                inline=False,
            )
        # Attach the first image attachment, if any
        first_image = next(
            (
                a
                for a in message.attachments
                if a.content_type and a.content_type.startswith("image/")
            ),
            None,
        )
        if first_image:
            embed.set_image(url=first_image.url)

        sent = 0
        failed = 0
        for target in role.members:
            if target.bot:
                continue
            try:
                await target.send(embed=embed)
                sent += 1
            except discord.HTTPException:
                failed += 1

        # Swap 📢 → ✅ to visually confirm broadcast
        try:
            await message.clear_reaction(PENDING_EMOJI)
            await message.add_reaction(SENT_EMOJI)
        except discord.HTTPException:
            pass

        log.info(
            "Announcer broadcast message %s in %s: %d sent, %d failed.",
            message.id,
            guild.name,
            sent,
            failed,
        )
