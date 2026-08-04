import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import humanize_list, pagify

log = logging.getLogger("red.wzyss-cogs.channelbackup")

BACKUP_VERSION = 1
MAX_BACKUPS_PER_GUILD = 25
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")

# Channel types we back up (threads and ephemeral types are excluded).
TYPE_CATEGORY = "category"
TYPE_TEXT = "text"
TYPE_NEWS = "news"
TYPE_VOICE = "voice"
TYPE_STAGE = "stage"
TYPE_FORUM = "forum"
TYPE_MEDIA = "media"

GuildChannel = Union[
    discord.TextChannel,
    discord.VoiceChannel,
    discord.CategoryChannel,
    discord.StageChannel,
    discord.ForumChannel,
]


class ConfirmView(discord.ui.View):
    """Simple yes/no confirmation view."""

    def __init__(self, author_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command author can confirm this.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = False
        await interaction.response.defer()
        self.stop()

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class ChannelBackup(commands.Cog):
    """Backup and restore guild channel structure, settings, and permissions."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._locks: Dict[int, asyncio.Lock] = {}

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _guild_dir(self, guild_id: int) -> Path:
        path = cog_data_path(self) / str(guild_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _backup_path(self, guild_id: int, name: str) -> Path:
        return self._guild_dir(guild_id) / f"{name}.json"

    def _list_backup_names(self, guild_id: int) -> List[str]:
        directory = self._guild_dir(guild_id)
        names = sorted(p.stem for p in directory.glob("*.json") if p.is_file())
        return names

    def _validate_name(self, name: str) -> Optional[str]:
        if not NAME_RE.match(name):
            return (
                "Backup names must be 1–48 characters, start with a letter or "
                "number, and contain only letters, numbers, hyphens, or underscores."
            )
        return None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _serialize_overwrites(
        self, channel: GuildChannel
    ) -> List[Dict[str, Any]]:
        result = []
        for target, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            if isinstance(target, discord.Role):
                target_type = "role"
            elif isinstance(target, (discord.Member, discord.User)):
                target_type = "member"
            elif isinstance(target, discord.Object):
                # Unknown snowflake targets are stored as members for restore attempts
                target_type = "member"
            else:
                continue
            result.append(
                {
                    "id": str(target.id),
                    "type": target_type,
                    "allow": allow.value,
                    "deny": deny.value,
                }
            )
        return result

    def _channel_type_key(self, channel: GuildChannel) -> Optional[str]:
        if isinstance(channel, discord.CategoryChannel):
            return TYPE_CATEGORY
        if isinstance(channel, discord.StageChannel):
            return TYPE_STAGE
        if isinstance(channel, discord.VoiceChannel):
            return TYPE_VOICE
        if isinstance(channel, discord.ForumChannel):
            ch_type = getattr(channel, "type", None)
            if ch_type is not None and getattr(ch_type, "name", "") == "media":
                return TYPE_MEDIA
            layout = getattr(channel, "default_layout", None)
            layout_value = getattr(layout, "value", layout)
            if layout_value == 2:  # ForumLayoutType.media
                return TYPE_MEDIA
            return TYPE_FORUM
        if isinstance(channel, discord.TextChannel):
            if channel.is_news():
                return TYPE_NEWS
            return TYPE_TEXT
        return None

    def _serialize_channel(self, channel: GuildChannel) -> Dict[str, Any]:
        type_key = self._channel_type_key(channel)
        data: Dict[str, Any] = {
            "id": str(channel.id),
            "type": type_key,
            "name": channel.name,
            "position": channel.position,
            "overwrites": self._serialize_overwrites(channel),
        }

        if isinstance(channel, discord.CategoryChannel):
            data["nsfw"] = bool(getattr(channel, "nsfw", False))
            return data

        data["category_id"] = (
            str(channel.category_id) if channel.category_id else None
        )

        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            data["topic"] = channel.topic
            data["nsfw"] = channel.nsfw
            data["slowmode_delay"] = channel.slowmode_delay
            data["default_auto_archive_duration"] = getattr(
                channel, "default_auto_archive_duration", None
            )
            data["default_thread_slowmode_delay"] = getattr(
                channel, "default_thread_slowmode_delay", None
            )

        if isinstance(channel, discord.ForumChannel):
            layout = getattr(channel, "default_layout", None)
            sort_order = getattr(channel, "default_sort_order", None)
            data["default_layout"] = getattr(layout, "value", layout)
            data["default_sort_order"] = getattr(sort_order, "value", sort_order)
            tags = []
            for tag in getattr(channel, "available_tags", []) or []:
                tags.append(
                    {
                        "name": tag.name,
                        "moderated": bool(getattr(tag, "moderated", False)),
                        "emoji_id": (
                            str(tag.emoji.id)
                            if getattr(tag, "emoji", None)
                            and getattr(tag.emoji, "id", None)
                            else None
                        ),
                        "emoji_name": (
                            tag.emoji.name
                            if getattr(tag, "emoji", None)
                            and getattr(tag.emoji, "name", None)
                            and not getattr(tag.emoji, "id", None)
                            else None
                        ),
                    }
                )
            data["available_tags"] = tags

        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            data["bitrate"] = channel.bitrate
            data["user_limit"] = channel.user_limit
            data["rtc_region"] = channel.rtc_region
            vqm = getattr(channel, "video_quality_mode", None)
            data["video_quality_mode"] = getattr(vqm, "value", vqm)
            if isinstance(channel, discord.VoiceChannel):
                data["nsfw"] = bool(getattr(channel, "nsfw", False))
                data["slowmode_delay"] = getattr(channel, "slowmode_delay", 0)

        return data

    def _build_backup(
        self, guild: discord.Guild, *, created_by: discord.abc.User, name: str
    ) -> Dict[str, Any]:
        categories = []
        channels = []

        for category in sorted(guild.categories, key=lambda c: c.position):
            categories.append(self._serialize_channel(category))

        for channel in sorted(guild.channels, key=lambda c: c.position):
            if isinstance(channel, discord.CategoryChannel):
                continue
            type_key = self._channel_type_key(channel)
            if type_key is None:
                continue
            channels.append(self._serialize_channel(channel))

        return {
            "version": BACKUP_VERSION,
            "name": name,
            "guild_id": str(guild.id),
            "guild_name": guild.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {
                "id": str(created_by.id),
                "name": str(created_by),
            },
            "counts": {
                "categories": len(categories),
                "channels": len(channels),
            },
            "categories": categories,
            "channels": channels,
        }

    def _load_backup_file(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Backup root must be a JSON object.")
        if "categories" not in data or "channels" not in data:
            raise ValueError("Backup is missing categories/channels.")
        return data

    def _save_backup_file(self, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Overwrite helpers
    # ------------------------------------------------------------------

    def _resolve_overwrites(
        self, guild: discord.Guild, raw: List[Dict[str, Any]]
    ) -> Tuple[Dict[Any, discord.PermissionOverwrite], int]:
        """Build overwrite mapping. Returns (overwrites, skipped_count)."""
        overwrites: Dict[Any, discord.PermissionOverwrite] = {}
        skipped = 0
        for entry in raw or []:
            try:
                target_id = int(entry["id"])
                target_type = entry.get("type", "role")
                allow = discord.Permissions(int(entry.get("allow", 0)))
                deny = discord.Permissions(int(entry.get("deny", 0)))
                overwrite = discord.PermissionOverwrite.from_pair(allow, deny)
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue

            target = None
            if target_type == "role":
                target = guild.get_role(target_id)
            else:
                target = guild.get_member(target_id)

            if target is None:
                skipped += 1
                continue
            overwrites[target] = overwrite
        return overwrites, skipped

    async def _api_call_with_retry(self, coro_factory, *, max_retries: int = 5):
        """Run an async factory with rate-limit retries."""
        last_error = None
        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except discord.HTTPException as e:
                last_error = e
                if e.status == 429:
                    retry_after = getattr(e, "retry_after", None)
                    wait_time = (
                        float(retry_after) + 1.0
                        if retry_after is not None
                        else float(2 ** (attempt + 1))
                    )
                    log.debug(
                        "Rate limited during channel backup op, waiting %.1fs",
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    async def _edit_category(
        self,
        category: discord.CategoryChannel,
        data: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        reason: str,
    ) -> None:
        kwargs: Dict[str, Any] = {
            "name": data["name"],
            "overwrites": overwrites,
            "reason": reason,
        }
        if "nsfw" in data:
            kwargs["nsfw"] = bool(data["nsfw"])
        if "position" in data:
            kwargs["position"] = int(data["position"])
        await self._api_call_with_retry(lambda: category.edit(**kwargs))

    async def _create_category(
        self,
        guild: discord.Guild,
        data: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        reason: str,
    ) -> discord.CategoryChannel:
        kwargs: Dict[str, Any] = {
            "name": data["name"],
            "overwrites": overwrites,
            "reason": reason,
        }
        if "position" in data:
            kwargs["position"] = int(data["position"])
        return await self._api_call_with_retry(
            lambda: guild.create_category(**kwargs)
        )

    def _text_kwargs(self, data: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if data.get("topic") is not None:
            kwargs["topic"] = data["topic"]
        if "nsfw" in data:
            kwargs["nsfw"] = bool(data["nsfw"])
        if "slowmode_delay" in data and data["slowmode_delay"] is not None:
            kwargs["slowmode_delay"] = int(data["slowmode_delay"])
        if data.get("default_auto_archive_duration") is not None:
            kwargs["default_auto_archive_duration"] = int(
                data["default_auto_archive_duration"]
            )
        if data.get("default_thread_slowmode_delay") is not None:
            kwargs["default_thread_slowmode_delay"] = int(
                data["default_thread_slowmode_delay"]
            )
        return kwargs

    def _voice_kwargs(self, data: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if "bitrate" in data and data["bitrate"] is not None:
            kwargs["bitrate"] = int(data["bitrate"])
        if "user_limit" in data and data["user_limit"] is not None:
            kwargs["user_limit"] = int(data["user_limit"])
        if "rtc_region" in data:
            kwargs["rtc_region"] = data["rtc_region"]
        if data.get("video_quality_mode") is not None:
            try:
                kwargs["video_quality_mode"] = discord.VideoQualityMode(
                    int(data["video_quality_mode"])
                )
            except (ValueError, TypeError):
                pass
        if "nsfw" in data:
            kwargs["nsfw"] = bool(data["nsfw"])
        if "slowmode_delay" in data and data["slowmode_delay"] is not None:
            kwargs["slowmode_delay"] = int(data["slowmode_delay"])
        return kwargs

    async def _edit_channel(
        self,
        channel: GuildChannel,
        data: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        category: Optional[discord.CategoryChannel],
        reason: str,
        *,
        apply_category: bool,
    ) -> None:
        kwargs: Dict[str, Any] = {
            "name": data["name"],
            "overwrites": overwrites,
            "reason": reason,
        }
        if apply_category:
            kwargs["category"] = category
        if "position" in data:
            kwargs["position"] = int(data["position"])

        if isinstance(channel, discord.ForumChannel):
            kwargs.update(self._text_kwargs(data))
            if data.get("default_layout") is not None:
                try:
                    kwargs["default_layout"] = discord.ForumLayoutType(
                        int(data["default_layout"])
                    )
                except (ValueError, TypeError, AttributeError):
                    pass
            if data.get("default_sort_order") is not None:
                try:
                    kwargs["default_sort_order"] = discord.ForumOrderType(
                        int(data["default_sort_order"])
                    )
                except (ValueError, TypeError, AttributeError):
                    pass
        elif isinstance(channel, discord.TextChannel):
            kwargs.update(self._text_kwargs(data))
        elif isinstance(channel, discord.StageChannel):
            voice_kwargs = self._voice_kwargs(data)
            for key in ("bitrate", "user_limit", "rtc_region", "video_quality_mode"):
                if key in voice_kwargs:
                    kwargs[key] = voice_kwargs[key]
        elif isinstance(channel, discord.VoiceChannel):
            kwargs.update(self._voice_kwargs(data))

        await self._api_call_with_retry(lambda: channel.edit(**kwargs))

    async def _create_channel(
        self,
        guild: discord.Guild,
        data: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        category: Optional[discord.CategoryChannel],
        reason: str,
    ) -> GuildChannel:
        type_key = data.get("type")
        name = data["name"]
        position = data.get("position")

        if type_key == TYPE_NEWS:
            kwargs = self._text_kwargs(data)
            if position is not None:
                kwargs["position"] = int(position)
            return await self._api_call_with_retry(
                lambda: guild.create_text_channel(
                    name,
                    category=category,
                    overwrites=overwrites,
                    news=True,
                    reason=reason,
                    **kwargs,
                )
            )

        if type_key == TYPE_TEXT:
            kwargs = self._text_kwargs(data)
            if position is not None:
                kwargs["position"] = int(position)
            return await self._api_call_with_retry(
                lambda: guild.create_text_channel(
                    name,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                    **kwargs,
                )
            )

        if type_key == TYPE_VOICE:
            kwargs = self._voice_kwargs(data)
            # create_voice_channel may not accept nsfw/slowmode on older dpy
            create_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k in ("bitrate", "user_limit", "rtc_region", "video_quality_mode")
            }
            if position is not None:
                create_kwargs["position"] = int(position)
            return await self._api_call_with_retry(
                lambda: guild.create_voice_channel(
                    name,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                    **create_kwargs,
                )
            )

        if type_key == TYPE_STAGE:
            kwargs = self._voice_kwargs(data)
            create_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k in ("bitrate", "user_limit", "rtc_region", "video_quality_mode")
            }
            if position is not None:
                create_kwargs["position"] = int(position)
            return await self._api_call_with_retry(
                lambda: guild.create_stage_channel(
                    name,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                    **create_kwargs,
                )
            )

        if type_key in (TYPE_FORUM, TYPE_MEDIA):
            if not hasattr(guild, "create_forum_channel") and not hasattr(
                guild, "create_forum"
            ):
                raise RuntimeError(
                    "This discord.py version does not support creating forum channels."
                )
            kwargs = self._text_kwargs(data)
            if position is not None:
                kwargs["position"] = int(position)
            if type_key == TYPE_MEDIA:
                kwargs["media"] = True
            create_forum = getattr(guild, "create_forum", None) or getattr(
                guild, "create_forum_channel"
            )
            return await self._api_call_with_retry(
                lambda: create_forum(
                    name,
                    category=category,
                    overwrites=overwrites,
                    reason=reason,
                    **kwargs,
                )
            )

        raise ValueError(f"Unsupported channel type in backup: {type_key}")

    async def _restore_backup(
        self,
        ctx: commands.Context,
        backup: Dict[str, Any],
        *,
        create_missing: bool,
    ) -> Dict[str, Any]:
        guild = ctx.guild
        assert guild is not None
        reason = f"Channel backup restore by {ctx.author} ({ctx.author.id})"

        results = {
            "categories_updated": 0,
            "categories_created": 0,
            "categories_missing": 0,
            "channels_updated": 0,
            "channels_created": 0,
            "channels_missing": 0,
            "channels_failed": 0,
            "overwrites_skipped": 0,
            "errors": {},
        }

        category_map: Dict[str, discord.CategoryChannel] = {}
        categories = sorted(
            backup.get("categories", []), key=lambda c: c.get("position", 0)
        )
        channels = sorted(
            backup.get("channels", []), key=lambda c: c.get("position", 0)
        )
        total = len(categories) + len(channels)
        progress = await ctx.send(
            f"Restoring backup `{backup.get('name', '?')}`...\n"
            f"Progress: 0/{total}"
        )
        processed = 0

        async def bump():
            nonlocal processed
            processed += 1
            if processed == total or processed % 5 == 0:
                try:
                    await progress.edit(
                        content=(
                            f"Restoring backup `{backup.get('name', '?')}`...\n"
                            f"Progress: {processed}/{total}"
                        )
                    )
                except discord.HTTPException:
                    pass

        # Categories first
        for data in categories:
            old_id = str(data["id"])
            overwrites, skipped = self._resolve_overwrites(
                guild, data.get("overwrites", [])
            )
            results["overwrites_skipped"] += skipped
            existing = guild.get_channel(int(old_id))
            try:
                if isinstance(existing, discord.CategoryChannel):
                    await self._edit_category(existing, data, overwrites, reason)
                    category_map[old_id] = existing
                    results["categories_updated"] += 1
                elif create_missing:
                    created = await self._create_category(
                        guild, data, overwrites, reason
                    )
                    category_map[old_id] = created
                    results["categories_created"] += 1
                else:
                    results["categories_missing"] += 1
            except Exception as e:
                results["channels_failed"] += 1
                err = type(e).__name__
                results["errors"][err] = results["errors"].get(err, 0) + 1
                log.warning("Failed restoring category %s: %s", old_id, e)
            await bump()
            await asyncio.sleep(0.35)

        # Channels
        for data in channels:
            old_id = str(data["id"])
            overwrites, skipped = self._resolve_overwrites(
                guild, data.get("overwrites", [])
            )
            results["overwrites_skipped"] += skipped

            parent = None
            apply_category = True
            cat_id = data.get("category_id")
            if cat_id:
                parent = category_map.get(str(cat_id))
                if parent is None:
                    maybe = guild.get_channel(int(cat_id))
                    if isinstance(maybe, discord.CategoryChannel):
                        parent = maybe
                if parent is None:
                    # Don't yank the channel out of its current category
                    apply_category = False
            else:
                parent = None

            existing = guild.get_channel(int(old_id))
            try:
                if existing is not None and not isinstance(
                    existing, discord.CategoryChannel
                ):
                    await self._edit_channel(
                        existing,
                        data,
                        overwrites,
                        parent,
                        reason,
                        apply_category=apply_category,
                    )
                    results["channels_updated"] += 1
                elif create_missing:
                    await self._create_channel(
                        guild, data, overwrites, parent, reason
                    )
                    results["channels_created"] += 1
                else:
                    results["channels_missing"] += 1
            except Exception as e:
                results["channels_failed"] += 1
                err = type(e).__name__
                results["errors"][err] = results["errors"].get(err, 0) + 1
                log.warning(
                    "Failed restoring channel %s (%s): %s",
                    old_id,
                    data.get("name"),
                    e,
                )
            await bump()
            await asyncio.sleep(0.35)

        try:
            await progress.edit(
                content=(
                    f"Restore of `{backup.get('name', '?')}` finished.\n"
                    f"Progress: {processed}/{total}"
                )
            )
        except discord.HTTPException:
            pass

        return results

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group(name="channelbackup", aliases=["chbackup", "cbackup"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True)
    async def _channelbackup(self, ctx: commands.Context):
        """Backup and restore channel structure and permissions."""
        pass

    @_channelbackup.command(name="create", aliases=["save", "new"])
    async def _create(self, ctx: commands.Context, name: Optional[str] = None):
        """Create a backup of this server's channels, VCs, and categories.

        Optional `name` identifies the backup (letters, numbers, `-`, `_`).
        Defaults to a UTC timestamp like `20260804-171530`.
        """
        guild = ctx.guild
        assert guild is not None

        if name is None:
            name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        else:
            err = self._validate_name(name)
            if err:
                await ctx.send(err)
                return

        existing = self._list_backup_names(guild.id)
        if name in existing:
            await ctx.send(
                f"A backup named `{name}` already exists. "
                f"Delete it first or choose another name."
            )
            return
        if len(existing) >= MAX_BACKUPS_PER_GUILD:
            await ctx.send(
                f"This server already has {MAX_BACKUPS_PER_GUILD} backups. "
                f"Delete one before creating another."
            )
            return

        async with self._lock_for(guild.id):
            async with ctx.typing():
                data = self._build_backup(guild, created_by=ctx.author, name=name)
                path = self._backup_path(guild.id, name)
                self._save_backup_file(path, data)

        counts = data["counts"]
        await ctx.send(
            f"Created backup `{name}`.\n"
            f"Categories: **{counts['categories']}** · "
            f"Channels: **{counts['channels']}**\n"
            f"Use `{ctx.clean_prefix}channelbackup download {name}` to get the JSON file."
        )

    @_channelbackup.command(name="list", aliases=["ls"])
    async def _list(self, ctx: commands.Context):
        """List channel structure backups for this server."""
        guild = ctx.guild
        assert guild is not None
        names = self._list_backup_names(guild.id)
        if not names:
            await ctx.send("No backups stored for this server yet.")
            return

        lines = []
        for name in names:
            path = self._backup_path(guild.id, name)
            try:
                data = self._load_backup_file(path)
                created = data.get("created_at", "?")
                counts = data.get("counts", {})
                lines.append(
                    f"`{name}` — {created} — "
                    f"{counts.get('categories', '?')} categories, "
                    f"{counts.get('channels', '?')} channels"
                )
            except Exception:
                lines.append(f"`{name}` — (unreadable)")

        header = f"**Channel backups for {guild.name}** ({len(names)})\n"
        for page in pagify(header + "\n".join(lines), page_length=1900):
            await ctx.send(page)

    @_channelbackup.command(name="info")
    async def _info(self, ctx: commands.Context, name: str):
        """Show details about a stored backup."""
        guild = ctx.guild
        assert guild is not None
        path = self._backup_path(guild.id, name)
        if not path.is_file():
            await ctx.send(f"No backup named `{name}` found.")
            return
        try:
            data = self._load_backup_file(path)
        except Exception as e:
            await ctx.send(f"Failed to read backup: {e}")
            return

        type_counts: Dict[str, int] = {}
        for ch in data.get("channels", []):
            t = ch.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        overwrite_total = sum(
            len(c.get("overwrites", [])) for c in data.get("categories", [])
        ) + sum(len(c.get("overwrites", [])) for c in data.get("channels", []))

        created_by = data.get("created_by") or {}
        type_summary = (
            humanize_list(
                [f"{count} {kind}" for kind, count in sorted(type_counts.items())]
            )
            if type_counts
            else "none"
        )

        embed = discord.Embed(
            title=f"Backup: {data.get('name', name)}",
            color=await ctx.embed_color(),
        )
        embed.add_field(
            name="Created",
            value=str(data.get("created_at", "?")),
            inline=False,
        )
        embed.add_field(
            name="Created by",
            value=created_by.get("name", "?"),
            inline=True,
        )
        embed.add_field(
            name="Guild ID in backup",
            value=str(data.get("guild_id", "?")),
            inline=True,
        )
        embed.add_field(
            name="Categories",
            value=str(len(data.get("categories", []))),
            inline=True,
        )
        embed.add_field(
            name="Channels",
            value=str(len(data.get("channels", []))),
            inline=True,
        )
        embed.add_field(
            name="Permission overwrites",
            value=str(overwrite_total),
            inline=True,
        )
        embed.add_field(name="Channel types", value=type_summary, inline=False)
        embed.set_footer(text=f"Schema version {data.get('version', '?')}")
        await ctx.send(embed=embed)

    @_channelbackup.command(name="download", aliases=["export", "get"])
    async def _download(self, ctx: commands.Context, name: str):
        """Download a backup as a JSON file."""
        guild = ctx.guild
        assert guild is not None
        path = self._backup_path(guild.id, name)
        if not path.is_file():
            await ctx.send(f"No backup named `{name}` found.")
            return
        await ctx.send(
            content=f"Backup `{name}`:",
            file=discord.File(path, filename=f"{guild.id}-{name}.json"),
        )

    @_channelbackup.command(name="delete", aliases=["remove", "rm"])
    async def _delete(self, ctx: commands.Context, name: str):
        """Delete a stored backup."""
        guild = ctx.guild
        assert guild is not None
        path = self._backup_path(guild.id, name)
        if not path.is_file():
            await ctx.send(f"No backup named `{name}` found.")
            return

        view = ConfirmView(ctx.author.id)
        view.message = await ctx.send(
            f"Delete backup `{name}`? This cannot be undone.",
            view=view,
        )
        await view.wait()
        if view.value is not True:
            await view.message.edit(
                content="Delete cancelled." if view.value is False else "Delete timed out.",
                view=None,
            )
            return

        path.unlink(missing_ok=True)
        await view.message.edit(content=f"Deleted backup `{name}`.", view=None)

    @_channelbackup.command(name="import")
    async def _import(self, ctx: commands.Context, name: Optional[str] = None):
        """Import a backup JSON file attached to this message.

        Optional `name` overrides the filename stem. The backup must belong
        to this server (matching guild ID) to be restorable with permissions.
        """
        guild = ctx.guild
        assert guild is not None

        attachment = None
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
        elif ctx.message.reference and ctx.message.reference.resolved:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message) and ref.attachments:
                attachment = ref.attachments[0]

        if attachment is None:
            await ctx.send(
                "Attach a `.json` backup file to this command "
                "(or reply to a message that has one)."
            )
            return

        if not attachment.filename.lower().endswith(".json"):
            await ctx.send("Attachment must be a `.json` file.")
            return
        if attachment.size > 8 * 1024 * 1024:
            await ctx.send("Backup file is too large (max 8 MB).")
            return

        raw = await attachment.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            await ctx.send(f"Could not parse JSON: {e}")
            return

        if not isinstance(data, dict) or "categories" not in data or "channels" not in data:
            await ctx.send("File does not look like a channel backup.")
            return

        backup_guild = str(data.get("guild_id", ""))
        if backup_guild and backup_guild != str(guild.id):
            await ctx.send(
                f"This backup belongs to guild `{backup_guild}`, not this server "
                f"(`{guild.id}`). Import refused — role IDs would not match."
            )
            return

        if name is None:
            name = data.get("name") or Path(attachment.filename).stem
        err = self._validate_name(str(name))
        if err:
            await ctx.send(err)
            return
        name = str(name)

        existing = self._list_backup_names(guild.id)
        if name in existing:
            await ctx.send(
                f"A backup named `{name}` already exists. "
                f"Delete it first or pass a different name."
            )
            return
        if len(existing) >= MAX_BACKUPS_PER_GUILD:
            await ctx.send(
                f"This server already has {MAX_BACKUPS_PER_GUILD} backups. "
                f"Delete one before importing."
            )
            return

        data["name"] = name
        data["guild_id"] = str(guild.id)
        path = self._backup_path(guild.id, name)
        self._save_backup_file(path, data)

        counts = data.get("counts") or {
            "categories": len(data.get("categories", [])),
            "channels": len(data.get("channels", [])),
        }
        await ctx.send(
            f"Imported backup as `{name}`.\n"
            f"Categories: **{counts.get('categories', len(data.get('categories', [])))}** · "
            f"Channels: **{counts.get('channels', len(data.get('channels', [])))}**"
        )

    @_channelbackup.command(name="restore")
    async def _restore(
        self,
        ctx: commands.Context,
        name: str,
        create_missing: bool = False,
    ):
        """Restore channel settings and permissions from a backup.

        Matches channels/categories by Discord ID and updates name, topic,
        bitrate, positions, parent category, and permission overwrites.

        Pass `create_missing True` to recreate channels/categories that were
        deleted since the backup. Existing channels not in the backup are
        never deleted.
        """
        guild = ctx.guild
        assert guild is not None

        if not guild.me.guild_permissions.manage_channels:
            await ctx.send("I need **Manage Channels** to restore a backup.")
            return

        path = self._backup_path(guild.id, name)
        if not path.is_file():
            await ctx.send(f"No backup named `{name}` found.")
            return

        try:
            backup = self._load_backup_file(path)
        except Exception as e:
            await ctx.send(f"Failed to read backup: {e}")
            return

        backup_guild = str(backup.get("guild_id", ""))
        if backup_guild and backup_guild != str(guild.id):
            await ctx.send(
                f"This backup belongs to a different guild (`{backup_guild}`). "
                f"Restore refused."
            )
            return

        cat_n = len(backup.get("categories", []))
        ch_n = len(backup.get("channels", []))
        view = ConfirmView(ctx.author.id, timeout=90.0)
        view.message = await ctx.send(
            f"**Confirm channel restore**\n\n"
            f"Backup: `{name}`\n"
            f"Categories in backup: **{cat_n}**\n"
            f"Channels in backup: **{ch_n}**\n"
            f"Create missing: **{create_missing}**\n\n"
            f"Existing channels will have settings and permission overwrites "
            f"replaced to match the backup. Channels not in the backup will "
            f"not be deleted.",
            view=view,
        )
        await view.wait()
        if view.value is not True:
            await view.message.edit(
                content=(
                    "Restore cancelled."
                    if view.value is False
                    else "Restore timed out."
                ),
                view=None,
            )
            return

        await view.message.edit(content="Restore confirmed. Starting...", view=None)

        async with self._lock_for(guild.id):
            results = await self._restore_backup(
                ctx, backup, create_missing=create_missing
            )

        summary = [
            f"**Restore complete** (`{name}`)",
            "",
            f"Categories updated: **{results['categories_updated']}**",
            f"Categories created: **{results['categories_created']}**",
            f"Categories missing (not created): **{results['categories_missing']}**",
            f"Channels updated: **{results['channels_updated']}**",
            f"Channels created: **{results['channels_created']}**",
            f"Channels missing (not created): **{results['channels_missing']}**",
            f"Failed: **{results['channels_failed']}**",
            f"Overwrite targets skipped (missing role/member): **{results['overwrites_skipped']}**",
        ]
        if results["errors"]:
            summary.append("")
            summary.append("Errors:")
            for err, count in results["errors"].items():
                summary.append(f"- {err}: {count}")

        for page in pagify("\n".join(summary), page_length=2000):
            await ctx.send(page)


async def setup(bot: Red):
    await bot.add_cog(ChannelBackup(bot))
    log.info("ChannelBackup cog loaded")
