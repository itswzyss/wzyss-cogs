"""
Clear cog: mod/admin message management (purge by count, after message, between messages, by user).
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Tuple

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.clear")

# Max messages to delete per invocation (avoid timeouts / abuse)
CLEAR_CAP = 500
DISCORD_BULK_DELETE_CAP = 100
DISCORD_BULK_DELETE_MAX_AGE = timedelta(days=14)

# Discord message link: https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
# Optional ptb/canary/app variants
MESSAGE_LINK_RE = re.compile(
    r"^(?:https?://)?(?:ptb\.|canary\.|staging\.)?(?:discord(?:app)?)?\.com/channels/"
    r"(?:\d{17,20}|@me)/(?P<channel_id>\d{17,20})/(?P<message_id>\d{17,20})\s*$",
    re.IGNORECASE,
)


def _message_id_from_arg(arg: str) -> Optional[int]:
    """Parse message ID from a string (raw ID or Discord message link). Returns None if invalid."""
    if not arg or not arg.strip():
        return None
    arg = arg.strip()
    # Raw ID
    if arg.isdigit() and len(arg) >= 17:
        return int(arg)
    match = MESSAGE_LINK_RE.match(arg)
    if match:
        return int(match.group("message_id"))
    return None


def _channel_id_from_link(arg: str) -> Optional[int]:
    """If arg is a message link, return the channel_id from it; otherwise None."""
    if not arg or not arg.strip():
        return None
    match = MESSAGE_LINK_RE.match(arg.strip())
    if match:
        return int(match.group("channel_id"))
    return None


class Clear(commands.Cog):
    """Clear/purge messages in a channel. Mod or Manage Messages required."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._channel_locks: dict[int, asyncio.Lock] = {}

    def _lock_for_channel(self, channel_id: int) -> asyncio.Lock:
        lock = self._channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._channel_locks[channel_id] = lock
        return lock

    def _is_bulk_deletable(self, message: discord.Message, *, now: datetime) -> bool:
        """Bulk delete cannot include messages older than 14 days."""
        created = message.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (now - created) <= DISCORD_BULK_DELETE_MAX_AGE

    async def _safe_bulk_delete(
        self, channel: discord.TextChannel, messages: List[discord.Message]
    ) -> int:
        """Delete messages in chunks; stop cleanly on rate limits/errors."""
        deleted_count = 0
        for i in range(0, len(messages), DISCORD_BULK_DELETE_CAP):
            chunk = messages[i : i + DISCORD_BULK_DELETE_CAP]
            if not chunk:
                continue
            try:
                if len(chunk) == 1:
                    await chunk[0].delete()
                    deleted_count += 1
                else:
                    await channel.delete_messages(chunk)
                    deleted_count += len(chunk)
            except discord.HTTPException as e:
                # 429s (and other HTTP errors) can happen. We stop and report partial progress.
                log.warning("Clear delete stopped due to HTTPException: %s", e)
                break
        return deleted_count

    async def _collect_history(
        self,
        channel: discord.TextChannel,
        *,
        limit: int,
        before: Optional[discord.Message] = None,
        after: Optional[discord.Message] = None,
        check: Optional[Callable[[discord.Message], bool]] = None,
    ) -> List[discord.Message]:
        msgs: List[discord.Message] = []
        async for m in channel.history(
            limit=limit, before=before, after=after, oldest_first=False
        ):
            if check is None or check(m):
                msgs.append(m)
        return msgs

    async def _bulk_clear(
        self, channel: discord.TextChannel, candidates: Iterable[discord.Message]
    ) -> Tuple[int, int]:
        """
        Bulk delete what we can. Returns (deleted_count, skipped_old_count).

        We skip messages older than 14 days to avoid per-message deletes that frequently
        hit rate limits and can keep retrying even after a cog reload.
        """
        now = datetime.now(timezone.utc)
        uniq: dict[int, discord.Message] = {}
        for m in candidates:
            uniq[m.id] = m

        recent: List[discord.Message] = []
        skipped_old = 0
        for m in uniq.values():
            if self._is_bulk_deletable(m, now=now):
                recent.append(m)
            else:
                skipped_old += 1

        # Newest-first feels most natural for moderation clears.
        recent.sort(key=lambda m: m.id, reverse=True)
        deleted = await self._safe_bulk_delete(channel, recent)
        return (deleted, skipped_old)

    async def _resolve_message(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        arg: str,
    ) -> Optional[discord.Message]:
        """Resolve a message from current channel by ID or link. Returns None if not in channel."""
        mid = _message_id_from_arg(arg)
        if mid is None:
            return None
        try:
            msg = await channel.fetch_message(mid)
            return msg
        except (discord.NotFound, discord.HTTPException):
            return None

    async def _send_reply(
        self,
        ctx: commands.Context,
        content: str,
        *,
        ephemeral: bool = False,
    ):
        """Send reply; ephemeral when invoked via slash."""
        if getattr(ctx, "interaction", None) and ephemeral:
            await ctx.send(content, ephemeral=True)
        else:
            await ctx.send(content)

    async def _parse_clear_args(
        self,
        ctx: commands.Context,
        amount: Optional[int],
        user: Optional[discord.Member],
    ) -> Tuple[Optional[int], Optional[discord.Member]]:
        """Parse prefix message content into amount and user. Used only when ctx.interaction is None."""
        after_prefix = ctx.message.content[len(ctx.prefix) :].strip().split()
        if not after_prefix:
            return (None, None)
        cmd_name = after_prefix[0].lower()
        if cmd_name != "clear":
            return (amount, user)
        rest = after_prefix[1:]
        if len(rest) == 0:
            return (None, None)
        if len(rest) == 1:
            try:
                n = int(rest[0])
                if 1 <= n <= CLEAR_CAP:
                    return (n, None)
            except ValueError:
                pass
            try:
                m = await commands.MemberConverter().convert(ctx, rest[0])
                return (None, m)
            except commands.BadArgument:
                return (None, None)
        # len(rest) >= 2: first is user, second is amount
        try:
            m = await commands.MemberConverter().convert(ctx, rest[0])
            n = int(rest[1])
            if n < 1:
                n = CLEAR_CAP
            elif n > CLEAR_CAP:
                n = CLEAR_CAP
            return (n, m)
        except (commands.BadArgument, ValueError):
            return (None, None)

    @commands.hybrid_group(name="clear", invoke_without_command=True)
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @app_commands.describe(
        amount="Number of messages to clear (1-500).",
        user="Clear messages from this user only (optionally use with amount to limit).",
    )
    async def clear_group(
        self,
        ctx: commands.Context,
        amount: Optional[int] = None,
        user: Optional[discord.Member] = None,
    ):
        """
        Clear messages in this channel.

        - `[p]clear <amount>` - Clear that many messages.
        - `[p]clear @user [amount]` - Clear messages from a user (optionally limit count).

        Use `[p]clear after <message>` and `[p]clear between <msg1> <msg2>` for more options.
        """
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_reply(ctx, "This command can only be used in text channels.", ephemeral=True)
            return

        # Prefix: parse message content to get amount/user (slash provides them as options)
        if ctx.interaction is None:
            amount, user = await self._parse_clear_args(ctx, amount, user)

        if amount is None and user is None:
            await ctx.send_help()
            return

        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        # Check bot permission
        if not channel.permissions_for(ctx.guild.me).manage_messages:
            await self._send_reply(
                ctx,
                "I need the Manage Messages permission in this channel.",
                ephemeral=True,
            )
            return

        async with self._lock_for_channel(channel.id):
            if amount is not None and user is None:
                # Clear amount: for prefix, the invoking message counts toward the fetch window.
                if amount < 1:
                    await self._send_reply(ctx, "Amount must be at least 1.", ephemeral=True)
                    return
                amount = min(amount, CLEAR_CAP)
                is_prefix = ctx.interaction is None
                fetch_limit = min(amount + 1, CLEAR_CAP) if is_prefix else amount

                candidates = await self._collect_history(channel, limit=fetch_limit)
                deleted_count, skipped_old = await self._bulk_clear(channel, candidates)

                if deleted_count == 0 and skipped_old == 0:
                    await self._send_reply(ctx, "No messages to clear.", ephemeral=True)
                    return
                msg = f"Cleared {deleted_count} message(s)."
                if skipped_old:
                    msg += f" Skipped {skipped_old} message(s) older than 14 days."
                await self._send_reply(ctx, msg, ephemeral=True)
                return

            # Clear from user
            member = user
            limit = min(amount, CLEAR_CAP) if amount is not None else CLEAR_CAP
            limit = min(limit, CLEAR_CAP)
            if limit < 1:
                await self._send_reply(ctx, "Amount must be at least 1.", ephemeral=True)
                return

            def check(m: discord.Message) -> bool:
                return m.author == member

            candidates = await self._collect_history(channel, limit=limit, check=check)
            deleted_count, skipped_old = await self._bulk_clear(channel, candidates)

            if deleted_count == 0 and skipped_old == 0:
                await self._send_reply(
                    ctx,
                    f"No messages from {member.display_name} to clear.",
                    ephemeral=True,
                )
                return
            msg = f"Cleared {deleted_count} message(s) from {member.display_name}."
            if skipped_old:
                msg += f" Skipped {skipped_old} message(s) older than 14 days."
            await self._send_reply(ctx, msg, ephemeral=True)

    # Note: We intentionally skip messages older than 14 days (Discord bulk delete limitation)
    # to avoid slow per-message deletes that frequently hit rate limits.

    @clear_group.command(name="after")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @app_commands.describe(message_id_or_link="Message ID or Discord message link (must be in this channel)")
    async def clear_after(
        self,
        ctx: commands.Context,
        message_id_or_link: str,
    ):
        """
        Clear all messages after the given message.

        Provide a message ID or a Discord message link. Message must be in this channel.
        """
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_reply(ctx, "This command can only be used in text channels.", ephemeral=True)
            return

        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        if not channel.permissions_for(ctx.guild.me).manage_messages:
            await self._send_reply(
                ctx,
                "I need the Manage Messages permission in this channel.",
                ephemeral=True,
            )
            return

        # Reject message links that point to another channel
        link_channel_id = _channel_id_from_link(message_id_or_link)
        if link_channel_id is not None and link_channel_id != channel.id:
            await self._send_reply(
                ctx,
                "That message link is from another channel. Use a message in this channel.",
                ephemeral=True,
            )
            return

        msg = await self._resolve_message(ctx, channel, message_id_or_link)
        if msg is None:
            await self._send_reply(
                ctx,
                "Message not found or not in this channel. Use a message ID or a Discord message link.",
                ephemeral=True,
            )
            return

        if msg.channel.id != channel.id:
            await self._send_reply(
                ctx,
                "That message is not in this channel.",
                ephemeral=True,
            )
            return

        async with self._lock_for_channel(channel.id):
            candidates = await self._collect_history(channel, limit=CLEAR_CAP, after=msg)
            deleted_count, skipped_old = await self._bulk_clear(channel, candidates)
            if deleted_count == 0 and skipped_old == 0:
                await self._send_reply(
                    ctx,
                    "No messages found after that message.",
                    ephemeral=True,
                )
                return
            msg_out = f"Cleared {deleted_count} message(s) after the given message."
            if skipped_old:
                msg_out += f" Skipped {skipped_old} message(s) older than 14 days."
            await self._send_reply(ctx, msg_out, ephemeral=True)

    @clear_group.command(name="between")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @app_commands.describe(
        message_1="First message ID or link (boundary)",
        message_2="Second message ID or link (boundary)",
    )
    async def clear_between(
        self,
        ctx: commands.Context,
        message_1: str,
        message_2: str,
    ):
        """
        Clear all messages between two messages (excluding those two).

        Provide two message IDs or Discord message links. Both must be in this channel.
        """
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_reply(ctx, "This command can only be used in text channels.", ephemeral=True)
            return

        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        if not channel.permissions_for(ctx.guild.me).manage_messages:
            await self._send_reply(
                ctx,
                "I need the Manage Messages permission in this channel.",
                ephemeral=True,
            )
            return

        # Reject message links that point to another channel
        for label, arg in (("First", message_1), ("Second", message_2)):
            link_channel_id = _channel_id_from_link(arg)
            if link_channel_id is not None and link_channel_id != channel.id:
                await self._send_reply(
                    ctx,
                    f"{label} message link is from another channel. Use messages in this channel.",
                    ephemeral=True,
                )
                return

        msg1 = await self._resolve_message(ctx, channel, message_1)
        msg2 = await self._resolve_message(ctx, channel, message_2)
        if msg1 is None:
            await self._send_reply(
                ctx,
                "First message not found or not in this channel.",
                ephemeral=True,
            )
            return
        if msg2 is None:
            await self._send_reply(
                ctx,
                "Second message not found or not in this channel.",
                ephemeral=True,
            )
            return
        if msg1.channel.id != channel.id or msg2.channel.id != channel.id:
            await self._send_reply(ctx, "Both messages must be in this channel.", ephemeral=True)
            return

        older = msg1 if msg1.id < msg2.id else msg2
        newer = msg2 if msg1.id < msg2.id else msg1
        if older.id == newer.id:
            await self._send_reply(ctx, "The two messages are the same; nothing to clear.", ephemeral=True)
            return

        async with self._lock_for_channel(channel.id):
            candidates = await self._collect_history(
                channel, limit=CLEAR_CAP, after=older, before=newer
            )
            deleted_count, skipped_old = await self._bulk_clear(channel, candidates)
            if deleted_count == 0 and skipped_old == 0:
                await self._send_reply(
                    ctx,
                    "No messages found between those two messages.",
                    ephemeral=True,
                )
                return
            msg_out = f"Cleared {deleted_count} message(s) between the two messages."
            if skipped_old:
                msg_out += f" Skipped {skipped_old} message(s) older than 14 days."
            await self._send_reply(ctx, msg_out, ephemeral=True)


async def setup(bot: Red):
    """Load the Clear cog."""
    cog = Clear(bot)
    await bot.add_cog(cog)
    log.info("Clear cog loaded")
