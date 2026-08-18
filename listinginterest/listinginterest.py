"""
ListingInterest cog: attach interest buttons to product listings, open private
channels, ping and/or DM configured users and role members.
"""
import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple

import discord
from discord.ui import Button, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.listinginterest")

INTEREST_PREFIX = "listinginterest:interest:"
CLOSE_PREFIX = "listinginterest:close:"

# Discord message link: https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
MESSAGE_LINK_RE = re.compile(
    r"^(?:https?://)?(?:ptb\.|canary\.|staging\.)?(?:discord(?:app)?)?\.com/channels/"
    r"(?:\d{17,20}|@me)/(?P<channel_id>\d{17,20})/(?P<message_id>\d{17,20})\s*$",
    re.IGNORECASE,
)

DEFAULT_BUTTON_LABEL = "I'm interested"
DEFAULT_DM_ROLE_MEMBER_CAP = 25


def _sanitize_channel_name(name: str) -> str:
    s = re.sub(r"[^\w\-]", "", name.replace(" ", "-"))[:100]
    return s or "interest"


def _message_id_from_arg(arg: str) -> Optional[int]:
    """Parse message ID from a raw ID or Discord message link."""
    if not arg or not arg.strip():
        return None
    arg = arg.strip()
    if arg.isdigit() and len(arg) >= 17:
        return int(arg)
    match = MESSAGE_LINK_RE.match(arg)
    if match:
        return int(match.group("message_id"))
    return None


def _channel_id_from_link(arg: str) -> Optional[int]:
    """If arg is a message link, return the channel_id; otherwise None."""
    if not arg or not arg.strip():
        return None
    match = MESSAGE_LINK_RE.match(arg.strip())
    if match:
        return int(match.group("channel_id"))
    return None


def _interest_view(label: str, listing_id: str) -> View:
    v = View(timeout=None)
    v.add_item(
        Button(
            style=discord.ButtonStyle.primary,
            label=label[:80] or DEFAULT_BUTTON_LABEL,
            custom_id=f"{INTEREST_PREFIX}{listing_id}",
        )
    )
    return v


def _close_view(channel_id: int) -> View:
    v = View(timeout=None)
    v.add_item(
        Button(
            style=discord.ButtonStyle.danger,
            label="Close",
            custom_id=f"{CLOSE_PREFIX}{channel_id}",
        )
    )
    return v


class ListingInterest(commands.Cog):
    """Interest buttons on listings that open private channels and notify sellers."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x11571E57, force_registration=True)
        default_guild = {
            "category_id": None,
            "ping_user_ids": [],
            "ping_role_ids": [],
            "dm_user_ids": [],
            "dm_role_ids": [],
            "button_label": DEFAULT_BUTTON_LABEL,
            "manager_role_ids": [],
            "dm_role_member_cap": DEFAULT_DM_ROLE_MEMBER_CAP,
            "listings": {},
            "open_interests": {},
            "next_listing_id": 1,
        }
        self.config.register_guild(**default_guild)
        log.info("ListingInterest cog initialized")

    async def can_manage_interests(self, user: discord.Member) -> bool:
        if user.guild_permissions.manage_guild or user.guild_permissions.administrator:
            return True
        role_ids = await self.config.guild(user.guild).manager_role_ids()
        return any(rid in [r.id for r in user.roles] for rid in role_ids)

    async def _resolve_message(
        self, ctx: commands.Context, message_id_or_link: str
    ) -> Optional[discord.Message]:
        """Fetch a message from ID or link in this guild."""
        msg_id = _message_id_from_arg(message_id_or_link)
        if not msg_id:
            await ctx.send("Invalid message ID or link.")
            return None

        link_channel_id = _channel_id_from_link(message_id_or_link)
        channels_to_try: List[discord.abc.Messageable] = []
        if link_channel_id:
            ch = ctx.guild.get_channel(link_channel_id)
            if ch and isinstance(ch, discord.TextChannel):
                channels_to_try.append(ch)
        if ctx.channel not in channels_to_try and isinstance(ctx.channel, discord.TextChannel):
            channels_to_try.append(ctx.channel)

        for ch in channels_to_try:
            try:
                return await ch.fetch_message(msg_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        # Broader search if raw ID and not found in current/link channel
        if not link_channel_id:
            for ch in ctx.guild.text_channels:
                if ch in channels_to_try:
                    continue
                try:
                    return await ch.fetch_message(msg_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue

        await ctx.send("Message not found. Use a message link or run the command in that channel.")
        return None

    def _find_listing_by_button_message(
        self, listings: Dict, channel_id: int, message_id: int
    ) -> Optional[Tuple[str, Dict]]:
        for lid, data in listings.items():
            if (
                data.get("button_channel_id") == channel_id
                and data.get("button_message_id") == message_id
            ):
                return str(lid), data
        return None

    def _find_listing_by_id_or_message(
        self, listings: Dict, arg: str
    ) -> Optional[Tuple[str, Dict]]:
        # Direct listing id
        if arg in listings:
            return arg, listings[arg]
        if arg.isdigit() and arg in listings:
            return arg, listings[arg]

        msg_id = _message_id_from_arg(arg)
        ch_id = _channel_id_from_link(arg)
        if msg_id is None:
            return None
        for lid, data in listings.items():
            if data.get("button_message_id") == msg_id:
                if ch_id is None or data.get("button_channel_id") == ch_id:
                    return str(lid), data
            if data.get("source_message_id") == msg_id:
                if ch_id is None or data.get("source_channel_id") == ch_id:
                    return str(lid), data
        return None

    # --- Interest / close handlers ---

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if getattr(interaction.type, "value", interaction.type) != 3:
            return
        custom_id = (interaction.data or {}).get("custom_id") or ""
        if not custom_id.startswith("listinginterest:"):
            return
        if interaction.response.is_done():
            return

        if custom_id.startswith(INTEREST_PREFIX):
            listing_id = custom_id[len(INTEREST_PREFIX) :].strip()
            await self._handle_interest(interaction, listing_id)
            return
        if custom_id.startswith(CLOSE_PREFIX):
            try:
                channel_id = int(custom_id[len(CLOSE_PREFIX) :].strip())
            except ValueError:
                return
            await self._handle_close(interaction, channel_id)
            return

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        async with self.config.guild(channel.guild).open_interests() as oi:
            oi.pop(str(channel.id), None)

    async def _handle_interest(self, interaction: discord.Interaction, listing_id: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        user = interaction.user
        listings = await self.config.guild(guild).listings()
        listing = listings.get(listing_id)
        if not listing:
            await interaction.response.send_message(
                "This listing is no longer available.",
                ephemeral=True,
            )
            return

        open_interests = await self.config.guild(guild).open_interests()
        for cid_str, data in open_interests.items():
            if data.get("listing_id") == listing_id and data.get("buyer_id") == user.id:
                ch = guild.get_channel(int(cid_str))
                mention = ch.mention if ch else "your existing channel"
                await interaction.response.send_message(
                    f"You already have an open interest for this listing: {mention}",
                    ephemeral=True,
                )
                return

        category_id = await self.config.guild(guild).category_id()
        if not category_id:
            await interaction.response.send_message(
                "Interest channels are not configured yet. Ask an admin to set a category.",
                ephemeral=True,
            )
            return
        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "The interest category is missing. Ask an admin to reconfigure it.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        channel = await self._create_interest_channel(guild, category, user, listing_id)
        if not channel:
            await interaction.followup.send(
                "Failed to create the interest channel. Check category and bot permissions.",
                ephemeral=True,
            )
            return

        now = time.time()
        async with self.config.guild(guild).open_interests() as oi:
            oi[str(channel.id)] = {
                "listing_id": listing_id,
                "buyer_id": user.id,
                "created_at": now,
            }

        await self._send_opener(channel, user, listing)
        await channel.send("Staff can close this channel when done.", view=_close_view(channel.id))
        await self._send_dms(guild, user, listing, channel)

        await interaction.followup.send(
            f"Interest channel created: {channel.mention}",
            ephemeral=True,
        )

    async def _create_interest_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        buyer: discord.Member,
        listing_id: str,
    ) -> Optional[discord.TextChannel]:
        ping_user_ids = await self.config.guild(guild).ping_user_ids()
        ping_role_ids = await self.config.guild(guild).ping_role_ids()
        dm_user_ids = await self.config.guild(guild).dm_user_ids()
        dm_role_ids = await self.config.guild(guild).dm_role_ids()

        overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            buyer: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            )

        user_ids = set(ping_user_ids) | set(dm_user_ids)
        for uid in user_ids:
            member = guild.get_member(uid)
            if member and member.id != buyer.id:
                overwrites[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        role_ids = set(ping_role_ids) | set(dm_role_ids)
        for rid in role_ids:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        manager_role_ids = await self.config.guild(guild).manager_role_ids()
        for rid in manager_role_ids:
            role = guild.get_role(rid)
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        name_base = _sanitize_channel_name(buyer.display_name)
        channel_name = f"interest-{name_base}-{listing_id}"[:100]
        try:
            return await category.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                reason=f"Listing interest from {buyer.display_name}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Failed to create interest channel: %s", e)
            return None

    async def _send_opener(
        self,
        channel: discord.TextChannel,
        buyer: discord.Member,
        listing: Dict,
    ):
        guild = channel.guild
        source_ch_id = listing.get("source_channel_id")
        source_msg_id = listing.get("source_message_id")
        jump = (
            f"https://discord.com/channels/{guild.id}/{source_ch_id}/{source_msg_id}"
            if source_ch_id and source_msg_id
            else None
        )

        ping_user_ids = await self.config.guild(guild).ping_user_ids()
        ping_role_ids = await self.config.guild(guild).ping_role_ids()

        mention_parts: List[str] = [buyer.mention]
        allowed_users = [buyer]
        allowed_roles: List[discord.Role] = []

        for uid in ping_user_ids:
            member = guild.get_member(uid)
            if member:
                mention_parts.append(member.mention)
                allowed_users.append(member)

        for rid in ping_role_ids:
            role = guild.get_role(rid)
            if role:
                mention_parts.append(role.mention)
                allowed_roles.append(role)

        desc = f"{buyer.mention} is interested in a listing."
        if jump:
            desc += f"\n\n[View listing]({jump})"

        embed = discord.Embed(
            title="Listing interest",
            description=desc,
            color=await self.bot.get_embed_color(guild),
            timestamp=discord.utils.utcnow(),
        )

        content = " ".join(mention_parts) if (ping_user_ids or ping_role_ids) else None
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                users=allowed_users,
                roles=allowed_roles,
            ),
        )

    async def _collect_dm_recipients(
        self, guild: discord.Guild, buyer: discord.Member
    ) -> List[discord.Member]:
        dm_user_ids = await self.config.guild(guild).dm_user_ids()
        dm_role_ids = await self.config.guild(guild).dm_role_ids()
        cap = await self.config.guild(guild).dm_role_member_cap()
        if cap is None or cap < 1:
            cap = DEFAULT_DM_ROLE_MEMBER_CAP

        seen: Set[int] = set()
        recipients: List[discord.Member] = []

        for uid in dm_user_ids:
            if uid in seen or uid == buyer.id:
                continue
            member = guild.get_member(uid)
            if member and not member.bot:
                seen.add(uid)
                recipients.append(member)

        for rid in dm_role_ids:
            role = guild.get_role(rid)
            if not role:
                continue
            count = 0
            for member in role.members:
                if count >= cap:
                    log.info(
                        "DM role %s capped at %s members in guild %s",
                        rid,
                        cap,
                        guild.id,
                    )
                    break
                if member.id in seen or member.id == buyer.id or member.bot:
                    continue
                seen.add(member.id)
                recipients.append(member)
                count += 1

        return recipients

    async def _send_dms(
        self,
        guild: discord.Guild,
        buyer: discord.Member,
        listing: Dict,
        interest_channel: discord.TextChannel,
    ):
        recipients = await self._collect_dm_recipients(guild, buyer)
        if not recipients:
            return

        source_ch_id = listing.get("source_channel_id")
        source_msg_id = listing.get("source_message_id")
        jump = (
            f"https://discord.com/channels/{guild.id}/{source_ch_id}/{source_msg_id}"
            if source_ch_id and source_msg_id
            else interest_channel.jump_url
        )
        text = (
            f"**{buyer.display_name}** is interested in a listing in **{guild.name}**.\n"
            f"Interest channel: {interest_channel.mention}\n"
            f"Listing: {jump}"
        )

        for member in recipients:
            try:
                await member.send(text)
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("Failed to DM %s for listing interest: %s", member.id, e)
            await asyncio.sleep(0.35)

    async def _handle_close(self, interaction: discord.Interaction, channel_id: int):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not await self.can_manage_interests(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to close interest channels.",
                ephemeral=True,
            )
            return

        open_interests = await self.config.guild(interaction.guild).open_interests()
        if str(channel_id) not in open_interests:
            await interaction.response.send_message(
                "This interest channel is no longer tracked.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Closing interest channel...", ephemeral=False)
        await self._close_interest(interaction.guild, channel_id, f"Closed by {interaction.user}")

    async def _close_interest(
        self, guild: discord.Guild, channel_id: int, reason: str = "Closed"
    ):
        channel = guild.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=reason)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        async with self.config.guild(guild).open_interests() as oi:
            oi.pop(str(channel_id), None)

    # --- User-facing attach commands ---

    @commands.group(name="listinginterest", aliases=["li"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def listinginterest(self, ctx: commands.Context):
        """Attach interest buttons to product listings."""
        pass

    @listinginterest.command(name="reply")
    async def listinginterest_reply(
        self,
        ctx: commands.Context,
        message_id_or_link: str,
        *,
        label: Optional[str] = None,
    ):
        """Reply to a listing message with an interest button.

        Provide a message ID or Discord message link. Optional custom button label.
        """
        source = await self._resolve_message(ctx, message_id_or_link)
        if not source:
            return

        button_label = (label or await self.config.guild(ctx.guild).button_label() or DEFAULT_BUTTON_LABEL)[:80]
        listing_id = str(await self.config.guild(ctx.guild).next_listing_id())
        await self.config.guild(ctx.guild).next_listing_id.set(int(listing_id) + 1)

        view = _interest_view(button_label, listing_id)
        try:
            button_msg = await source.reply(
                content="Interested in this listing? Click below.",
                view=view,
                mention_author=False,
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            await ctx.send(f"Could not reply to that message: {e}")
            return

        async with self.config.guild(ctx.guild).listings() as listings:
            listings[listing_id] = {
                "source_channel_id": source.channel.id,
                "source_message_id": source.id,
                "button_channel_id": button_msg.channel.id,
                "button_message_id": button_msg.id,
                "label": button_label,
            }

        await ctx.send(f"Interest button attached (listing `{listing_id}`): {button_msg.jump_url}")

    @listinginterest.command(name="bind")
    async def listinginterest_bind(
        self,
        ctx: commands.Context,
        message_id_or_link: str,
        *,
        label: Optional[str] = None,
    ):
        """Add an interest button to a bot-owned message (e.g. a reposted listing).

        The target message must have been sent by this bot.
        """
        target = await self._resolve_message(ctx, message_id_or_link)
        if not target:
            return
        if target.author.id != ctx.guild.me.id:
            await ctx.send("That message was not sent by this bot. Use `reply` for human posts, or repost with the bot then `bind`.")
            return

        button_label = (label or await self.config.guild(ctx.guild).button_label() or DEFAULT_BUTTON_LABEL)[:80]

        # Re-bind: reuse existing listing id if this button message already tracked
        listings = await self.config.guild(ctx.guild).listings()
        existing = self._find_listing_by_button_message(listings, target.channel.id, target.id)
        if existing:
            listing_id, _ = existing
        else:
            listing_id = str(await self.config.guild(ctx.guild).next_listing_id())
            await self.config.guild(ctx.guild).next_listing_id.set(int(listing_id) + 1)

        view = _interest_view(button_label, listing_id)
        try:
            await target.edit(view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            await ctx.send(f"Could not edit that message: {e}")
            return

        async with self.config.guild(ctx.guild).listings() as listings:
            listings[listing_id] = {
                "source_channel_id": target.channel.id,
                "source_message_id": target.id,
                "button_channel_id": target.channel.id,
                "button_message_id": target.id,
                "label": button_label,
            }

        await ctx.send(f"Interest button bound (listing `{listing_id}`): {target.jump_url}")

    @listinginterest.command(name="unbind")
    async def listinginterest_unbind(self, ctx: commands.Context, message_id_or_link_or_id: str):
        """Remove an interest button and listing config.

        Accepts a listing id, or the button/source message ID or link.
        Does not delete the original human listing.
        """
        listings = await self.config.guild(ctx.guild).listings()
        found = self._find_listing_by_id_or_message(listings, message_id_or_link_or_id.strip())
        if not found:
            await ctx.send("No listing found for that id, message ID, or link.")
            return

        listing_id, data = found
        button_ch = ctx.guild.get_channel(data.get("button_channel_id"))
        if button_ch and isinstance(button_ch, discord.TextChannel):
            try:
                msg = await button_ch.fetch_message(data.get("button_message_id"))
                # Only clear view if this is a bind (same as source) or our reply
                if msg.author.id == ctx.guild.me.id:
                    await msg.edit(view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        async with self.config.guild(ctx.guild).listings() as listings:
            listings.pop(listing_id, None)

        await ctx.send(f"Unbound listing `{listing_id}`.")

    @listinginterest.command(name="list")
    async def listinginterest_list(self, ctx: commands.Context):
        """List configured interest listings in this server."""
        listings = await self.config.guild(ctx.guild).listings()
        if not listings:
            await ctx.send("No listings configured.")
            return
        lines = []
        for lid, data in sorted(listings.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            jump = (
                f"https://discord.com/channels/{ctx.guild.id}/"
                f"{data.get('source_channel_id')}/{data.get('source_message_id')}"
            )
            lines.append(f"`{lid}` — {data.get('label') or DEFAULT_BUTTON_LABEL} — {jump}")
        # Discord message limit
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 1900:
                await ctx.send(chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            await ctx.send(chunk)

    # --- Config group ---

    @commands.group(name="listinginterestset", aliases=["liset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def listinginterestset(self, ctx: commands.Context):
        """Configure listing interest defaults."""
        pass

    @listinginterestset.command(name="category")
    async def liset_category(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Set the category where interest channels are created."""
        await self.config.guild(ctx.guild).category_id.set(category.id)
        await ctx.send(f"Interest category set to **{category.name}**.")

    @listinginterestset.command(name="buttonlabel")
    async def liset_buttonlabel(self, ctx: commands.Context, *, label: str):
        """Set the default interest button label."""
        label = label.strip()[:80]
        if not label:
            await ctx.send("Label cannot be empty.")
            return
        await self.config.guild(ctx.guild).button_label.set(label)
        await ctx.send(f"Default button label set to **{label}**.")

    @listinginterestset.command(name="pingusers")
    async def liset_pingusers(self, ctx: commands.Context, *users: discord.Member):
        """Set users to ping in new interest channels. Omit to clear."""
        await self.config.guild(ctx.guild).ping_user_ids.set([u.id for u in users])
        if users:
            await ctx.send(f"Ping users set to: {', '.join(u.mention for u in users)}.")
        else:
            await ctx.send("Ping users cleared.")

    @listinginterestset.command(name="pingroles")
    async def liset_pingroles(self, ctx: commands.Context, *roles: discord.Role):
        """Set roles to ping in new interest channels. Omit to clear."""
        await self.config.guild(ctx.guild).ping_role_ids.set([r.id for r in roles])
        if roles:
            await ctx.send(f"Ping roles set to: {', '.join(r.mention for r in roles)}.")
        else:
            await ctx.send("Ping roles cleared.")

    @listinginterestset.command(name="dmusers")
    async def liset_dmusers(self, ctx: commands.Context, *users: discord.Member):
        """Set users to DM when someone expresses interest. Omit to clear."""
        await self.config.guild(ctx.guild).dm_user_ids.set([u.id for u in users])
        if users:
            await ctx.send(f"DM users set to: {', '.join(u.mention for u in users)}.")
        else:
            await ctx.send("DM users cleared.")

    @listinginterestset.command(name="dmroles")
    async def liset_dmroles(self, ctx: commands.Context, *roles: discord.Role):
        """Set roles whose members are DMed on interest (expanded up to the member cap). Omit to clear.

        Large roles can hit Discord rate limits. Use `dmcap` to limit members DMed per role.
        """
        await self.config.guild(ctx.guild).dm_role_ids.set([r.id for r in roles])
        if roles:
            cap = await self.config.guild(ctx.guild).dm_role_member_cap()
            await ctx.send(
                f"DM roles set to: {', '.join(r.mention for r in roles)}. "
                f"Up to {cap} members per role will be DMed."
            )
        else:
            await ctx.send("DM roles cleared.")

    @listinginterestset.command(name="dmcap")
    async def liset_dmcap(self, ctx: commands.Context, cap: int):
        """Set max members DMed per DM role (default 25). High values risk rate limits."""
        if cap < 1:
            await ctx.send("Cap must be at least 1.")
            return
        if cap > 100:
            await ctx.send(
                "Warning: capping above 100 may hit Discord rate limits when expanding roles."
            )
        await self.config.guild(ctx.guild).dm_role_member_cap.set(cap)
        await ctx.send(f"DM role member cap set to **{cap}**.")

    @listinginterestset.command(name="managerroles")
    async def liset_managerroles(self, ctx: commands.Context, *roles: discord.Role):
        """Set roles that can close interest channels. Omit to clear (admins only)."""
        await self.config.guild(ctx.guild).manager_role_ids.set([r.id for r in roles])
        if roles:
            await ctx.send(f"Manager roles set to: {', '.join(r.mention for r in roles)}.")
        else:
            await ctx.send("Manager roles cleared. Only server admins can close interest channels.")

    @listinginterestset.command(name="settings")
    async def liset_settings(self, ctx: commands.Context):
        """Show current listing interest settings."""
        conf = self.config.guild(ctx.guild)
        category_id = await conf.category_id()
        category = ctx.guild.get_channel(category_id) if category_id else None
        ping_users = await conf.ping_user_ids()
        ping_roles = await conf.ping_role_ids()
        dm_users = await conf.dm_user_ids()
        dm_roles = await conf.dm_role_ids()
        managers = await conf.manager_role_ids()
        label = await conf.button_label()
        cap = await conf.dm_role_member_cap()
        listings = await conf.listings()

        def fmt_users(ids):
            return ", ".join(f"<@{i}>" for i in ids) or "None"

        def fmt_roles(ids):
            return ", ".join(f"<@&{i}>" for i in ids) or "None"

        embed = discord.Embed(
            title="Listing Interest settings",
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="Category",
            value=category.mention if category else "Not set",
            inline=False,
        )
        embed.add_field(name="Button label", value=label or DEFAULT_BUTTON_LABEL, inline=True)
        embed.add_field(name="DM role cap", value=str(cap), inline=True)
        embed.add_field(name="Listings", value=str(len(listings)), inline=True)
        embed.add_field(name="Ping users", value=fmt_users(ping_users), inline=False)
        embed.add_field(name="Ping roles", value=fmt_roles(ping_roles), inline=False)
        embed.add_field(name="DM users", value=fmt_users(dm_users), inline=False)
        embed.add_field(name="DM roles", value=fmt_roles(dm_roles), inline=False)
        embed.add_field(name="Manager roles", value=fmt_roles(managers), inline=False)
        await ctx.send(embed=embed)


async def setup(bot: Red):
    await bot.add_cog(ListingInterest(bot))
