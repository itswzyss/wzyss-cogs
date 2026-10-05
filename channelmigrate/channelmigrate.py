import asyncio
import io
import logging
import re
import secrets
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.channelmigrate")

WEBHOOK_NAME = "Channel Migration"
PANEL_INTERVAL = 10.0
SEND_DELAY = 0.25
REPLY_MAP_SIZE = 20000
MAX_CONTENT = 2000
NOT_ADMIN_MSG = "You need the **Administrator** permission in both the source and destination servers."

STATUS_RUNNING = "running"
STATUS_LIVE = "live"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_DONE = "done"
STATUS_ERROR = "error"
ACTIVE_STATUSES = {STATUS_RUNNING, STATUS_LIVE, STATUS_PAUSED}

STATUS_LABELS = {
    STATUS_RUNNING: "Copying history",
    STATUS_LIVE: "Live mirroring",
    STATUS_PAUSED: "Paused",
    STATUS_STOPPED: "Stopped",
    STATUS_DONE: "Finished",
    STATUS_ERROR: "Error",
}

COPYABLE_TYPES = {discord.MessageType.default, discord.MessageType.reply}

SourceChannel = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]
DestChannel = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]

CHANNEL_REF_RE = re.compile(r"^(?:<#(\d+)>|https?://(?:\w+\.)?discord(?:app)?\.com/channels/\d+/(\d+)/?|(\d+))$")


def _is_admin(member: Optional[discord.abc.User]) -> bool:
    return isinstance(member, discord.Member) and member.guild_permissions.administrator


def guild_admin_only():
    """Require the Discord Administrator permission. Red admin/mod roles are not enough."""

    async def predicate(ctx: commands.Context) -> bool:
        return ctx.guild is not None and _is_admin(ctx.author)

    return commands.check(predicate)


def _webhook_username(name: str, suffix: str) -> str:
    # Discord rejects webhook usernames containing these words.
    name = re.sub(r"(?i)(disc)(ord)", "\\1\u200b\\2", name or "Unknown")
    name = re.sub(r"(?i)(cly)(de)", "\\1\u200b\\2", name)
    name = name.strip() or "Unknown"
    return (name[: 80 - len(suffix)] + suffix)[:80]


def _split_content(text: str) -> List[str]:
    chunks: List[str] = []
    while len(text) > MAX_CONTENT:
        cut = text.rfind("\n", 0, MAX_CONTENT)
        if cut < MAX_CONTENT // 2:
            cut = text.rfind(" ", 0, MAX_CONTENT)
        if cut < MAX_CONTENT // 2:
            cut = MAX_CONTENT
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


class SetupView(discord.ui.View):
    def __init__(self, cog: "ChannelMigrate", author_id: int, source: SourceChannel, dest: DestChannel):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.source = source
        self.dest = dest
        self.dates = True
        self.live = False
        self.message: Optional[discord.Message] = None
        self._sync_labels()

    def _sync_labels(self) -> None:
        self.dates_button.label = f"Dates in names: {'On' if self.dates else 'Off'}"
        self.dates_button.style = discord.ButtonStyle.success if self.dates else discord.ButtonStyle.secondary
        self.live_button.label = f"Live mirror after history: {'On' if self.live else 'Off'}"
        self.live_button.style = discord.ButtonStyle.success if self.live else discord.ButtonStyle.secondary

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Set up channel migration",
            description=(
                "Every message in the source channel is reposted, oldest first, in the destination "
                "channel through a webhook that uses the original author's name and avatar.\n\n"
                "**Dates in names** adds the original date to the poster name (e.g. `Alice - 2024-03-01`), "
                "since reposted messages show the time they were copied.\n"
                "**Live mirror** keeps forwarding new messages after the history copy finishes, "
                "until you stop the job."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Source", value=self.cog.channel_label(self.source), inline=False)
        embed.add_field(name="Destination", value=self.cog.channel_label(self.dest), inline=False)
        embed.set_footer(text="Mentions are converted to plain text and never ping anyone.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who ran the command can use this.", ephemeral=True)
            return False
        if not self.cog.user_can_manage(interaction.user, self.source.guild.id, self.dest.guild.id):
            await interaction.response.send_message(NOT_ADMIN_MSG, ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Dates", row=0)
    async def dates_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.dates = not self.dates
        self._sync_labels()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Live", row=0)
    async def live_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.live = not self.live
        self._sync_labels()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Start Migration", style=discord.ButtonStyle.danger, row=1)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        problem = self.cog.preflight(self.source, self.dest)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        self.stop()
        job = await self.cog.create_job(
            self.source, self.dest, interaction.user, dates=self.dates, live=self.live
        )
        view = self.cog.new_panel_view(job["id"])
        await interaction.response.edit_message(embed=self.cog.job_embed(job), view=view)
        await self.cog.set_panel_message(job["id"], self.message or interaction.message)
        self.cog.start_runner(job["id"])

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = discord.Embed(title="Migration cancelled", color=discord.Color.dark_grey())
        await interaction.response.edit_message(embed=embed, view=None)


class JobControlView(discord.ui.View):
    """Persistent controls for one job; custom IDs survive restarts."""

    def __init__(self, cog: "ChannelMigrate", job_id: str, status: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.job_id = job_id
        self.buttons: Dict[str, discord.ui.Button] = {}
        for action, label, style in (
            ("pause", "Pause", discord.ButtonStyle.secondary),
            ("resume", "Resume", discord.ButtonStyle.success),
            ("stop", "Stop", discord.ButtonStyle.danger),
            ("refresh", "Refresh", discord.ButtonStyle.primary),
        ):
            button = discord.ui.Button(label=label, style=style, custom_id=f"chmigrate:{action}:{job_id}")
            button.callback = self._make_callback(action)
            self.buttons[action] = button
            self.add_item(button)
        self.refresh(status)

    def refresh(self, status: str) -> None:
        self.buttons["pause"].disabled = status not in (STATUS_RUNNING, STATUS_LIVE)
        self.buttons["resume"].disabled = status not in (STATUS_PAUSED, STATUS_ERROR)
        self.buttons["stop"].disabled = status not in ACTIVE_STATUSES | {STATUS_ERROR}

    def _make_callback(self, action: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.handle_control(interaction, self.job_id, action)

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        job = self.cog.jobs.get(self.job_id)
        if job is None:
            await interaction.response.send_message("This migration job no longer exists.", ephemeral=True)
            return False
        if not self.cog.user_can_manage(interaction.user, job["source_guild_id"], job["dest_guild_id"]):
            await interaction.response.send_message(NOT_ADMIN_MSG, ephemeral=True)
            return False
        return True


class ChannelMigrate(commands.Cog):
    """Copy every message from a channel to a channel in another server."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xC4A11E6, force_registration=True)
        self.config.register_global(jobs={})
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._views: Dict[str, JobControlView] = {}
        self._webhooks: Dict[str, discord.Webhook] = {}
        self._reply_maps: Dict[str, "OrderedDict[int, int]"] = {}
        self._last_panel_edit: Dict[str, float] = {}

    async def cog_load(self) -> None:
        self.jobs = await self.config.jobs()
        for job_id, job in self.jobs.items():
            if job.get("panel_message_id"):
                view = JobControlView(self, job_id, job["status"])
                self._views[job_id] = view
                self.bot.add_view(view, message_id=job["panel_message_id"])
        asyncio.create_task(self._resume_jobs())

    async def cog_unload(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for view in self._views.values():
            view.stop()

    async def _resume_jobs(self) -> None:
        await self.bot.wait_until_red_ready()
        for job_id, job in list(self.jobs.items()):
            if job["status"] in (STATUS_RUNNING, STATUS_LIVE):
                log.info("Resuming migration job %s", job_id)
                self.start_runner(job_id)

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        for job in self.jobs.values():
            if job.get("created_by") == user_id:
                job["created_by"] = 0
                await self._save(job)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lock(self, job_id: str) -> asyncio.Lock:
        if job_id not in self._locks:
            self._locks[job_id] = asyncio.Lock()
        return self._locks[job_id]

    async def _save(self, job: Dict[str, Any]) -> None:
        await self.config.jobs.set_raw(job["id"], value=job)

    def user_can_manage(self, user: discord.abc.User, source_guild_id: int, dest_guild_id: int) -> bool:
        for guild_id in (source_guild_id, dest_guild_id):
            guild = self.bot.get_guild(guild_id)
            if guild is None or not _is_admin(guild.get_member(user.id)):
                return False
        return True

    @staticmethod
    def channel_label(channel: Union[SourceChannel, DestChannel]) -> str:
        return f"**{channel.guild.name}** - {channel.mention} (`#{channel.name}`, `{channel.id}`)"

    async def resolve_channel(self, reference: Union[str, int]) -> Optional[discord.abc.GuildChannel]:
        if isinstance(reference, int):
            channel_id = reference
        else:
            match = CHANNEL_REF_RE.match(reference.strip())
            if not match:
                return None
            channel_id = int(next(g for g in match.groups() if g))
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel

    def preflight(self, source: Any, dest: Any) -> Optional[str]:
        if not isinstance(source, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
            return "The source must be a text, announcement, voice, or stage channel, or a thread."
        if not isinstance(dest, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
            return "The destination must be a text, announcement, voice, or stage channel, or a thread."
        if source.id == dest.id:
            return "The source and destination must be different channels."
        if isinstance(dest, discord.Thread) and not isinstance(dest.parent, (discord.TextChannel, discord.ForumChannel)):
            return "Destination threads must belong to a text or forum channel."
        src_perms = source.permissions_for(source.guild.me)
        if not (src_perms.view_channel and src_perms.read_message_history):
            return f"I need **View Channel** and **Read Message History** in {source.mention}."
        webhook_home = dest.parent if isinstance(dest, discord.Thread) else dest
        dest_perms = webhook_home.permissions_for(dest.guild.me)
        if not (dest_perms.view_channel and dest_perms.manage_webhooks):
            return f"I need **View Channel** and **Manage Webhooks** in {webhook_home.mention}."
        for job in self.jobs.values():
            if (
                job["status"] in ACTIVE_STATUSES
                and job["source_channel_id"] == source.id
                and job["dest_channel_id"] == dest.id
            ):
                return f"Job `{job['id']}` is already migrating this channel pair."
        return None

    def _panel_message_ids(self) -> Set[int]:
        return {job["panel_message_id"] for job in self.jobs.values() if job.get("panel_message_id")}

    def _webhook_ids(self) -> Set[int]:
        return {job["webhook_id"] for job in self.jobs.values() if job.get("webhook_id")}

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    async def create_job(
        self, source: SourceChannel, dest: DestChannel, user: discord.abc.User, *, dates: bool, live: bool
    ) -> Dict[str, Any]:
        job_id = secrets.token_hex(3)
        while job_id in self.jobs:
            job_id = secrets.token_hex(3)
        job = {
            "id": job_id,
            "source_guild_id": source.guild.id,
            "source_channel_id": source.id,
            "source_name": f"{source.guild.name} / #{source.name}",
            "dest_guild_id": dest.guild.id,
            "dest_channel_id": dest.id,
            "dest_name": f"{dest.guild.name} / #{dest.name}",
            "webhook_id": None,
            "last_message_id": None,
            "last_message_at": None,
            "copied": 0,
            "skipped": 0,
            "failed": 0,
            "status": STATUS_RUNNING,
            "dates": dates,
            "live": live,
            "created_by": user.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
            "panel_channel_id": None,
            "panel_message_id": None,
        }
        self.jobs[job_id] = job
        await self._save(job)
        return job

    def new_panel_view(self, job_id: str) -> JobControlView:
        """Create the view for a new panel message. The old one must stop before the new one is stored."""
        old = self._views.pop(job_id, None)
        if old is not None:
            old.stop()
        view = JobControlView(self, job_id, self.jobs[job_id]["status"])
        self._views[job_id] = view
        return view

    async def set_panel_message(self, job_id: str, message: Optional[discord.Message]) -> None:
        if message is None:
            return
        job = self.jobs[job_id]
        job["panel_channel_id"] = message.channel.id
        job["panel_message_id"] = message.id
        await self._save(job)

    def start_runner(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def _set_status(self, job: Dict[str, Any], status: str, error: Optional[str] = None) -> None:
        job["status"] = status
        job["error"] = error
        await self._save(job)
        await self.update_panel(job["id"], force=True)

    async def _run(self, job_id: str) -> None:
        """Copy history from the checkpoint, then either finish or switch to live mirroring."""
        job = self.jobs[job_id]
        try:
            source = await self.resolve_channel(job["source_channel_id"])
            dest = await self.resolve_channel(job["dest_channel_id"])
            if source is None or dest is None:
                await self._set_status(job, STATUS_ERROR, "The source or destination channel is no longer reachable.")
                return
            while job["status"] == STATUS_RUNNING:
                copied_any = await self._copy_history(job, source, dest)
                if job["status"] != STATUS_RUNNING or copied_any:
                    continue
                async with self._lock(job_id):
                    if job["status"] != STATUS_RUNNING:
                        break
                    # Switch first so on_message queues behind this lock, then sweep once more
                    # so nothing posted between the last page and the switch is missed.
                    job["status"] = STATUS_LIVE if job["live"] else STATUS_DONE
                    await self._copy_history(job, source, dest, locked=True)
                    await self._save(job)
                await self.update_panel(job_id, force=True)
        except asyncio.CancelledError:
            raise
        except discord.Forbidden as e:
            await self._set_status(job, STATUS_ERROR, f"Missing permissions: {e.text or e}")
        except Exception as e:
            log.exception("Migration job %s crashed", job_id)
            await self._set_status(job, STATUS_ERROR, f"{type(e).__name__}: {e}")

    async def _copy_history(self, job: Dict[str, Any], source: SourceChannel, dest: DestChannel, *, locked: bool = False) -> bool:
        after = discord.Object(id=job["last_message_id"]) if job["last_message_id"] else None
        copied_any = False
        async for message in source.history(limit=None, oldest_first=True, after=after):
            if job["status"] not in (STATUS_RUNNING, STATUS_LIVE, STATUS_DONE):
                return copied_any
            if locked:
                await self._forward(job, message, dest)
            else:
                async with self._lock(job["id"]):
                    if job["status"] != STATUS_RUNNING:
                        return copied_any
                    await self._forward(job, message, dest)
            copied_any = True
            await self.update_panel(job["id"])
        return copied_any

    async def handle_control(self, interaction: discord.Interaction, job_id: str, action: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            await interaction.response.send_message("This migration job no longer exists.", ephemeral=True)
            return
        if action == "pause" and job["status"] in (STATUS_RUNNING, STATUS_LIVE):
            async with self._lock(job_id):
                job["status"] = STATUS_PAUSED
                await self._save(job)
        elif action == "resume" and job["status"] in (STATUS_PAUSED, STATUS_ERROR):
            job["status"] = STATUS_RUNNING
            job["error"] = None
            await self._save(job)
            self.start_runner(job_id)
        elif action == "stop" and job["status"] in ACTIVE_STATUSES | {STATUS_ERROR}:
            async with self._lock(job_id):
                job["status"] = STATUS_STOPPED
                await self._save(job)
        view = self._views.get(job_id) or self.new_panel_view(job_id)
        view.refresh(job["status"])
        await interaction.response.edit_message(embed=self.job_embed(job), view=view)
        self._last_panel_edit[job_id] = time.monotonic()

    async def update_panel(self, job_id: str, force: bool = False) -> None:
        job = self.jobs.get(job_id)
        if job is None or not job.get("panel_message_id"):
            return
        now = time.monotonic()
        if not force and now - self._last_panel_edit.get(job_id, 0.0) < PANEL_INTERVAL:
            return
        self._last_panel_edit[job_id] = now
        channel = self.bot.get_channel(job["panel_channel_id"])
        if channel is None:
            return
        view = self._views.get(job_id) or self.new_panel_view(job_id)
        view.refresh(job["status"])
        try:
            await channel.get_partial_message(job["panel_message_id"]).edit(embed=self.job_embed(job), view=view)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------
    # Forwarding
    # ------------------------------------------------------------------

    async def _get_webhook(self, job: Dict[str, Any], dest: DestChannel, *, refresh: bool = False) -> discord.Webhook:
        if not refresh and job["id"] in self._webhooks:
            return self._webhooks[job["id"]]
        home = dest.parent if isinstance(dest, discord.Thread) else dest
        webhook = None
        for candidate in await home.webhooks():
            if candidate.id == job.get("webhook_id") or (
                candidate.name == WEBHOOK_NAME and candidate.user and candidate.user.id == self.bot.user.id
            ):
                if candidate.token:
                    webhook = candidate
                    break
        if webhook is None:
            webhook = await home.create_webhook(name=WEBHOOK_NAME, reason=f"Channel migration job {job['id']}")
        if job.get("webhook_id") != webhook.id:
            job["webhook_id"] = webhook.id
            await self._save(job)
        self._webhooks[job["id"]] = webhook
        return webhook

    def _reply_header(self, job: Dict[str, Any], message: discord.Message, dest: DestChannel) -> Optional[str]:
        ref = message.reference
        if message.type != discord.MessageType.reply or ref is None or ref.message_id is None:
            return None
        resolved = ref.resolved
        if isinstance(resolved, discord.Message):
            name = discord.utils.escape_markdown(resolved.author.display_name)
            snippet = resolved.clean_content.replace("\n", " ").strip()
            snippet = discord.utils.escape_markdown(snippet[:80] + ("..." if len(snippet) > 80 else ""))
            if not snippet and resolved.attachments:
                snippet = "*attachment*"
        else:
            name, snippet = "a deleted message", ""
        dest_id = self._reply_maps.get(job["id"], {}).get(ref.message_id)
        target = f"[{name}](https://discord.com/channels/{dest.guild.id}/{dest.id}/{dest_id})" if dest_id else f"**{name}**"
        return f"-# Replying to {target}" + (f": {snippet}" if snippet else "")

    def _message_text(self, job: Dict[str, Any], message: discord.Message, dest: DestChannel) -> Tuple[str, List[discord.Attachment]]:
        lines: List[str] = []
        attachments = list(message.attachments)
        header = self._reply_header(job, message, dest)
        if header:
            lines.append(header)
        if message.clean_content:
            lines.append(message.clean_content)
        for snapshot in getattr(message, "message_snapshots", None) or []:
            lines.append("-# Forwarded message")
            content = getattr(snapshot, "content", "") or ""
            if content:
                lines.append("\n".join(f"> {line}" for line in content.splitlines()))
            attachments.extend(getattr(snapshot, "attachments", []) or [])
        for sticker in message.stickers:
            lines.append(f"-# Sticker: {sticker.name}")
        poll = getattr(message, "poll", None)
        if poll is not None:
            question = getattr(poll.question, "text", poll.question)
            answers = ", ".join(str(getattr(a, "text", a)) for a in getattr(poll, "answers", []))
            lines.append(f"-# Poll: {question}" + (f" ({answers})" if answers else ""))
        return "\n".join(lines), attachments

    async def _forward(self, job: Dict[str, Any], message: discord.Message, dest: DestChannel) -> None:
        """Copy one message, then advance the checkpoint. Caller holds the job lock."""
        if job["last_message_id"] and message.id <= job["last_message_id"]:
            return
        skip = (
            message.type not in COPYABLE_TYPES
            or message.id in self._panel_message_ids()
            or (message.webhook_id is not None and message.webhook_id in self._webhook_ids())
        )
        if skip:
            job["skipped"] += 1
        else:
            try:
                sent_id = await self._send_copy(job, message, dest)
                if sent_id is None:
                    job["skipped"] += 1
                else:
                    job["copied"] += 1
                    reply_map = self._reply_maps.setdefault(job["id"], OrderedDict())
                    reply_map[message.id] = sent_id
                    while len(reply_map) > REPLY_MAP_SIZE:
                        reply_map.popitem(last=False)
            except discord.HTTPException as e:
                # Only a rejected payload is per-message; anything else halts without advancing the checkpoint.
                if e.status not in (400, 413):
                    raise
                job["failed"] += 1
                log.warning("Job %s could not copy message %s: %s", job["id"], message.id, e)
            except Exception as e:
                job["failed"] += 1
                log.exception("Job %s failed to copy message %s: %s", job["id"], message.id, e)
        job["last_message_id"] = message.id
        job["last_message_at"] = message.created_at.isoformat()
        await self._save(job)

    async def _send_copy(self, job: Dict[str, Any], message: discord.Message, dest: DestChannel) -> Optional[int]:
        text, attachments = self._message_text(job, message, dest)
        embeds = [discord.Embed.from_dict(e.to_dict()) for e in message.embeds if e.type == "rich"][:10]

        limit = dest.guild.filesize_limit
        downloads: List[Tuple[str, bytes, bool]] = []
        for attachment in attachments:
            if attachment.size > limit:
                text += f"\n-# Attachment too large to copy: [{attachment.filename}]({attachment.url})"
                continue
            try:
                downloads.append((attachment.filename, await attachment.read(), attachment.is_spoiler()))
            except discord.HTTPException:
                text += f"\n-# Attachment could not be downloaded: {attachment.filename}"

        if not text.strip() and not downloads and not embeds:
            return None

        # Batch files so each request stays within the destination's upload limit.
        batches: List[List[Tuple[str, bytes, bool]]] = []
        current: List[Tuple[str, bytes, bool]] = []
        current_size = 0
        for item in downloads:
            if current and (len(current) == 10 or current_size + len(item[1]) > limit):
                batches.append(current)
                current, current_size = [], 0
            current.append(item)
            current_size += len(item[1])
        if current:
            batches.append(current)

        chunks = _split_content(text) or [""]
        suffix = f" - {message.created_at.strftime('%Y-%m-%d')}" if job["dates"] else ""
        base = {
            "username": _webhook_username(message.author.display_name, suffix),
            "avatar_url": message.author.display_avatar.url,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if isinstance(dest, discord.Thread):
            base["thread"] = dest

        first_id: Optional[int] = None
        for index, chunk in enumerate(chunks):
            payload: Dict[str, Any] = dict(base)
            if chunk:
                payload["content"] = chunk
            last = index == len(chunks) - 1
            if last:
                if embeds:
                    payload["embeds"] = embeds
                if batches:
                    payload["files"] = batches[0]
            sent = await self._webhook_send(job, dest, payload)
            first_id = first_id or sent
        for batch in batches[1:]:
            sent = await self._webhook_send(job, dest, {**base, "files": batch})
            first_id = first_id or sent
        return first_id

    async def _webhook_send(self, job: Dict[str, Any], dest: DestChannel, payload: Dict[str, Any]) -> int:
        file_specs = payload.pop("files", None)

        def build(include_embeds: bool = True) -> Dict[str, Any]:
            kwargs = dict(payload)
            if not include_embeds:
                kwargs.pop("embeds", None)
            if file_specs:
                kwargs["files"] = [
                    discord.File(io.BytesIO(data), filename=name, spoiler=spoiler) for name, data, spoiler in file_specs
                ]
            if not kwargs.get("content") and not kwargs.get("embeds") and not kwargs.get("files"):
                kwargs["content"] = "-# (embed could not be copied)"
            return kwargs

        webhook = await self._get_webhook(job, dest)
        try:
            sent = await webhook.send(wait=True, **build())
        except discord.NotFound:
            webhook = await self._get_webhook(job, dest, refresh=True)
            sent = await webhook.send(wait=True, **build())
        except discord.HTTPException as e:
            if e.status != 400 or "embeds" not in payload:
                raise
            # Some bot embeds are not valid for webhooks; keep the text and files.
            sent = await webhook.send(wait=True, **build(include_embeds=False))
        await asyncio.sleep(SEND_DELAY)
        return sent.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        live_jobs = [
            job
            for job in self.jobs.values()
            if job["status"] == STATUS_LIVE and job["source_channel_id"] == message.channel.id
        ]
        if not live_jobs:
            return
        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return
        for job in live_jobs:
            dest = await self.resolve_channel(job["dest_channel_id"])
            if dest is None:
                await self._set_status(job, STATUS_ERROR, "The destination channel is no longer reachable.")
                continue
            async with self._lock(job["id"]):
                if job["status"] != STATUS_LIVE:
                    continue
                try:
                    await self._forward(job, message, dest)
                except discord.HTTPException as e:
                    # Resume catches up from the checkpoint through history, so nothing is lost.
                    await self._set_status(job, STATUS_ERROR, f"{type(e).__name__}: {e.text or e}")
                    continue
            await self.update_panel(job["id"])

    # ------------------------------------------------------------------
    # Embeds
    # ------------------------------------------------------------------

    def job_embed(self, job: Dict[str, Any]) -> discord.Embed:
        colors = {
            STATUS_RUNNING: discord.Color.orange(),
            STATUS_LIVE: discord.Color.blurple(),
            STATUS_PAUSED: discord.Color.gold(),
            STATUS_DONE: discord.Color.green(),
            STATUS_STOPPED: discord.Color.dark_grey(),
            STATUS_ERROR: discord.Color.red(),
        }
        embed = discord.Embed(
            title=f"Channel migration `{job['id']}`",
            description=f"**Status:** {STATUS_LABELS.get(job['status'], job['status'])}",
            color=colors.get(job["status"], discord.Color.blurple()),
        )
        embed.add_field(name="Source", value=f"{job['source_name']}\n<#{job['source_channel_id']}>", inline=True)
        embed.add_field(name="Destination", value=f"{job['dest_name']}\n<#{job['dest_channel_id']}>", inline=True)
        embed.add_field(
            name="Progress",
            value=f"Copied: **{job['copied']}**\nSkipped: **{job['skipped']}**\nFailed: **{job['failed']}**",
            inline=True,
        )
        if job.get("last_message_at"):
            ts = int(datetime.fromisoformat(job["last_message_at"]).timestamp())
            embed.add_field(name="Copied up to", value=f"<t:{ts}:f>", inline=True)
        embed.add_field(
            name="Options",
            value=f"Dates in names: **{'On' if job['dates'] else 'Off'}**\nLive mirror: **{'On' if job['live'] else 'Off'}**",
            inline=True,
        )
        if job.get("error"):
            embed.add_field(name="Error", value=job["error"][:1024], inline=False)
        embed.set_footer(text="Skipped = system messages and empty messages. Progress is saved after every message.")
        return embed

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group(name="migrate", aliases=["chmigrate"])
    @commands.guild_only()
    @guild_admin_only()
    async def migrate(self, ctx: commands.Context):
        """Copy all messages from one channel to another, across servers.

        Requires the Discord **Administrator** permission in both servers.
        """
        pass

    @migrate.command(name="start")
    @commands.bot_has_permissions(embed_links=True)
    async def migrate_start(self, ctx: commands.Context, source: str, destination: str):
        """Set up a migration from `source` to `destination`.

        Each channel can be a mention, an ID, or a channel link, and may be in any
        server the bot is in. Example: `[p]migrate start 123456789012345678 #archive`
        """
        source_channel = await self.resolve_channel(source)
        dest_channel = await self.resolve_channel(destination)
        if source_channel is None:
            await ctx.send("I could not find the source channel. Use an ID, mention, or channel link.")
            return
        if dest_channel is None:
            await ctx.send("I could not find the destination channel. Use an ID, mention, or channel link.")
            return
        if not hasattr(source_channel, "guild") or not hasattr(dest_channel, "guild"):
            await ctx.send("Both channels must be server channels.")
            return
        if not self.user_can_manage(ctx.author, source_channel.guild.id, dest_channel.guild.id):
            await ctx.send(NOT_ADMIN_MSG)
            return
        problem = self.preflight(source_channel, dest_channel)
        if problem:
            await ctx.send(problem)
            return
        view = SetupView(self, ctx.author.id, source_channel, dest_channel)
        view.message = await ctx.send(embed=view.build_embed(), view=view)

    @migrate.command(name="list")
    async def migrate_list(self, ctx: commands.Context):
        """List migration jobs you can manage."""
        jobs = [
            job
            for job in self.jobs.values()
            if self.user_can_manage(ctx.author, job["source_guild_id"], job["dest_guild_id"])
        ]
        if not jobs:
            await ctx.send("There are no migration jobs you can manage.")
            return
        lines = [
            f"`{job['id']}` - {STATUS_LABELS.get(job['status'], job['status'])} - "
            f"{job['source_name']} -> {job['dest_name']} - {job['copied']} copied"
            for job in sorted(jobs, key=lambda j: j["created_at"], reverse=True)
        ]
        embed = discord.Embed(title="Migration jobs", description="\n".join(lines)[:4000], color=discord.Color.blurple())
        embed.set_footer(text="Open controls for a job with: migrate panel <job_id>")
        await ctx.send(embed=embed)

    @migrate.command(name="panel", aliases=["status"])
    @commands.bot_has_permissions(embed_links=True)
    async def migrate_panel(self, ctx: commands.Context, job_id: str):
        """Post the control panel for a job here (replaces the previous panel)."""
        job = self.jobs.get(job_id.lower())
        if job is None or not self.user_can_manage(ctx.author, job["source_guild_id"], job["dest_guild_id"]):
            await ctx.send("No job with that ID that you can manage.")
            return
        old_channel = self.bot.get_channel(job.get("panel_channel_id") or 0)
        if old_channel is not None and job.get("panel_message_id"):
            try:
                await old_channel.get_partial_message(job["panel_message_id"]).edit(view=None)
            except discord.HTTPException:
                pass
        view = self.new_panel_view(job["id"])
        message = await ctx.send(embed=self.job_embed(job), view=view)
        await self.set_panel_message(job["id"], message)

    @migrate.command(name="remove", aliases=["delete"])
    async def migrate_remove(self, ctx: commands.Context, job_id: str):
        """Delete a finished, stopped, or failed job record. Copied messages are not touched."""
        job = self.jobs.get(job_id.lower())
        if job is None or not self.user_can_manage(ctx.author, job["source_guild_id"], job["dest_guild_id"]):
            await ctx.send("No job with that ID that you can manage.")
            return
        if job["status"] in ACTIVE_STATUSES:
            await ctx.send("Stop the job before removing it.")
            return
        view = self._views.pop(job["id"], None)
        if view is not None:
            view.stop()
        self.jobs.pop(job["id"], None)
        self._reply_maps.pop(job["id"], None)
        self._webhooks.pop(job["id"], None)
        await self.config.jobs.clear_raw(job["id"])
        await ctx.send(f"Removed job `{job['id']}`.")


async def setup(bot: Red):
    await bot.add_cog(ChannelMigrate(bot))
