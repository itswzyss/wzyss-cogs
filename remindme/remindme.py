"""
Remindme cog: timers with optional DM delivery, interactive and command mode, user/guild presets.
"""
import asyncio
import logging
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import parse_timedelta
from redbot.core.utils.chat_formatting import humanize_timedelta

log = logging.getLogger("red.wzyss-cogs.remindme")

# Duration limits for reminders
MIN_DURATION = timedelta(seconds=1)
MAX_DURATION = timedelta(days=365)
ALLOWED_UNITS = ["weeks", "days", "hours", "minutes", "seconds"]


def _parse_duration(raw: str) -> Optional[timedelta]:
    """Parse a duration string; returns None on failure."""
    if not (raw and raw.strip()):
        return None
    try:
        return parse_timedelta(
            raw.strip(),
            minimum=MIN_DURATION,
            maximum=MAX_DURATION,
            allowed_units=ALLOWED_UNITS,
        )
    except Exception:
        return None


def _normalize_preset_name(name: str) -> str:
    return name.strip().lower() if name else ""


# --- Timer storage helpers ---


async def _get_timers(config: Config) -> List[Dict[str, Any]]:
    timers = await config.timers()
    return list(timers) if timers is not None else []


async def _find_timer(config: Config, timer_id: str) -> Optional[Dict[str, Any]]:
    timers = await _get_timers(config)
    for t in timers:
        if t.get("id") == timer_id:
            return t
    return None


async def _add_timer(config: Config, timer: Dict[str, Any]) -> None:
    timers = await _get_timers(config)
    timers.append(timer)
    await config.timers.set(timers)


async def _remove_timer(config: Config, timer_id: str) -> bool:
    timers = await _get_timers(config)
    new_timers = [t for t in timers if t.get("id") != timer_id]
    if len(new_timers) == len(timers):
        return False
    await config.timers.set(new_timers)
    return True


# --- Custom duration modal ---


class CustomDurationModal(Modal, title="Custom reminder"):
    duration_input = TextInput(
        label="Duration",
        placeholder="e.g. 5m, 1h30m, 2d",
        required=True,
        max_length=50,
    )
    name_input = TextInput(
        label="Name (optional)",
        placeholder="e.g. Coffee break",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "Remindme", user_id: int, channel_id: int, guild_id: Optional[int], dm: bool):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.dm = dm

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.duration_input.value or "").strip()
        delta = _parse_duration(raw)
        if delta is None:
            await interaction.response.send_message(
                "Invalid duration. Use e.g. 5m, 1h, 2d.",
                ephemeral=True,
            )
            return
        name = (self.name_input.value or "").strip() or None
        await interaction.response.defer(ephemeral=True)
        timer_id = await self.cog._create_timer(
            user_id=self.user_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            duration_seconds=int(delta.total_seconds()),
            dm=self.dm,
            name=name,
        )
        if timer_id:
            msg = f"Reminder set for {humanize_timedelta(seconds=int(delta.total_seconds()))}."
            if name:
                msg += f" Name: {name}"
            if self.dm:
                msg += " You will be DMed."
            else:
                msg += " I will ping you here."
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send("Could not create reminder. Try again.", ephemeral=True)


# --- Interactive view ---


class RemindmeView(View):
    """Embed with buttons for popular times and delivery choice."""

    def __init__(
        self,
        cog: "Remindme",
        user_id: int,
        channel_id: int,
        guild_id: Optional[int],
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.dm = False

    def _create_timer_from_duration(self, duration_seconds: int, name: Optional[str] = None):
        return self.cog._create_timer(
            user_id=self.user_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            duration_seconds=duration_seconds,
            dm=self.dm,
            name=name,
        )

    async def _handle_time(self, interaction: discord.Interaction, duration_seconds: int, label: str):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who opened this menu can set a reminder.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        timer_id = await self._create_timer_from_duration(duration_seconds)
        if timer_id:
            await interaction.followup.send(
                f"Reminder set for {label}. I will {'DM you' if self.dm else 'ping you here'} when it's done.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send("Could not create reminder. Try again.", ephemeral=True)

    @discord.ui.button(label="5m", style=discord.ButtonStyle.primary, row=0)
    async def btn_5m(self, interaction: discord.Interaction, button: Button):
        await self._handle_time(interaction, 5 * 60, "5 minutes")

    @discord.ui.button(label="15m", style=discord.ButtonStyle.primary, row=0)
    async def btn_15m(self, interaction: discord.Interaction, button: Button):
        await self._handle_time(interaction, 15 * 60, "15 minutes")

    @discord.ui.button(label="30m", style=discord.ButtonStyle.primary, row=0)
    async def btn_30m(self, interaction: discord.Interaction, button: Button):
        await self._handle_time(interaction, 30 * 60, "30 minutes")

    @discord.ui.button(label="1h", style=discord.ButtonStyle.primary, row=0)
    async def btn_1h(self, interaction: discord.Interaction, button: Button):
        await self._handle_time(interaction, 60 * 60, "1 hour")

    @discord.ui.button(label="Custom", style=discord.ButtonStyle.secondary, row=0)
    async def btn_custom(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who opened this menu can set a reminder.", ephemeral=True)
            return
        modal = CustomDurationModal(
            self.cog,
            user_id=self.user_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            dm=self.dm,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Ping here", style=discord.ButtonStyle.success, row=1)
    async def btn_ping(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who opened this menu can change options.", ephemeral=True)
            return
        self.dm = False
        await interaction.response.send_message("I will ping you in this channel when the reminder ends.", ephemeral=True)

    @discord.ui.button(label="DM me", style=discord.ButtonStyle.secondary, row=1)
    async def btn_dm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who opened this menu can change options.", ephemeral=True)
            return
        self.dm = True
        await interaction.response.send_message("I will DM you when the reminder ends.", ephemeral=True)


# --- Cog ---


class Remindme(commands.Cog):
    """Set timers and get pinged or DMed when they complete."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x5A17A7B, force_registration=True)
        self.config.register_global(timers=[])
        self.config.register_user(presets={})
        self.config.register_guild(published_presets={})  # name -> {duration: str, owner_id?: int}
        self._timer_tasks: Dict[str, asyncio.Task] = {}
        log.info("Remindme cog initialized")

    async def _create_timer(
        self,
        user_id: int,
        channel_id: int,
        guild_id: Optional[int],
        duration_seconds: int,
        dm: bool,
        name: Optional[str] = None,
    ) -> Optional[str]:
        """Create a timer, store it, schedule task. Returns timer_id or None."""
        timer_id = uuid.uuid4().hex
        end_ts = time.time() + duration_seconds
        timer = {
            "id": timer_id,
            "user_id": user_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "end_ts": end_ts,
            "dm": dm,
            "name": name,
        }
        await _add_timer(self.config, timer)
        self._schedule_timer(timer_id)
        return timer_id

    def _schedule_timer(self, timer_id: str) -> None:
        """Schedule the fire task for a timer (must exist in config)."""
        old = self._timer_tasks.get(timer_id)
        if old and not old.done():
            old.cancel()
        async def run():
            try:
                timers = await _get_timers(self.config)
                t = next((x for x in timers if x.get("id") == timer_id), None)
                if not t:
                    return
                delay = max(0.0, t["end_ts"] - time.time())
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._fire_timer(timer_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._timer_tasks.pop(timer_id, None)
        self._timer_tasks[timer_id] = self.bot.loop.create_task(run())

    async def _fire_timer(self, timer_id: str) -> None:
        """Load timer, send ping or DM, remove from config."""
        t = await _find_timer(self.config, timer_id)
        if not t:
            return
        removed = await _remove_timer(self.config, timer_id)
        if not removed:
            return
        user_id = t.get("user_id")
        channel_id = t.get("channel_id")
        guild_id = t.get("guild_id")
        dm = t.get("dm", False)
        name = t.get("name")
        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                log.warning("Remindme: user %s not found for timer %s", user_id, timer_id)
                return
        text = f"<@{user_id}>"
        if name:
            text += f" Reminder: **{name}**"
        else:
            text += " Your reminder is up."
        if dm:
            try:
                await user.send(text)
            except (discord.Forbidden, discord.HTTPException):
                channel = self.bot.get_channel(channel_id) if channel_id else None
                if channel and guild_id:
                    try:
                        await channel.send(f"Could not DM you. {text}")
                    except (discord.Forbidden, discord.HTTPException):
                        log.warning("Remindme: could not DM or fallback to channel for user %s", user_id)
                else:
                    log.warning("Remindme: could not DM user %s", user_id)
        else:
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if channel:
                try:
                    await channel.send(text)
                except (discord.Forbidden, discord.HTTPException):
                    try:
                        await user.send(f"Reminder (could not send in channel): {text}")
                    except (discord.Forbidden, discord.HTTPException):
                        log.warning("Remindme: could not send reminder for user %s", user_id)
            else:
                try:
                    await user.send(text)
                except (discord.Forbidden, discord.HTTPException):
                    log.warning("Remindme: channel gone, could not DM user %s", user_id)

    async def cog_load(self) -> None:
        timers = await _get_timers(self.config)
        now = time.time()
        for t in timers:
            tid = t.get("id")
            end_ts = t.get("end_ts", 0)
            if tid and end_ts > now:
                self._schedule_timer(tid)

    async def cog_unload(self) -> None:
        for task in self._timer_tasks.values():
            if not task.done():
                task.cancel()
        self._timer_tasks.clear()

    async def _resolve_duration(
        self,
        first: str,
        user_id: int,
        guild_id: Optional[int],
    ) -> Optional[timedelta]:
        """Resolve first token as duration string or preset (user then guild). Returns timedelta or None."""
        delta = _parse_duration(first)
        if delta is not None:
            return delta
        key = _normalize_preset_name(first)
        if not key:
            return None
        # User preset
        presets = await self.config.user_from_id(user_id).presets()
        if presets and isinstance(presets, dict):
            dur_str = presets.get(key)
            if dur_str:
                return _parse_duration(dur_str)
        # Guild published preset
        if guild_id is not None:
            guild = self.bot.get_guild(guild_id)
            if guild:
                data = await self.config.guild(guild).published_presets()
                if data and isinstance(data, dict):
                    entry = data.get(key)
                    if entry is not None:
                        if isinstance(entry, dict):
                            dur_str = entry.get("duration", entry.get("duration_str", ""))
                        else:
                            dur_str = str(entry)
                        if dur_str:
                            return _parse_duration(dur_str)
        return None

    @commands.command(name="remindme", aliases=["remind", "rm"])
    async def remindme(self, ctx: commands.Context, *, rest: str = ""):
        """
        Set a reminder (interactive or by duration/preset).

        Usage:
        - `[p]remindme` — Interactive: embed with buttons for 5m, 15m, 30m, 1h, Custom; choose Ping here or DM me.
        - `[p]remindme <duration|preset> [dm] [name...]` — e.g. 5m, 1h, or a preset name. Add `dm` to be DMed; optional name at the end.

        Examples:
        - `[p]remindme 5m` — 5 minute reminder, ping in this channel.
        - `[p]remindme 5m dm` — 5 minute reminder, DM you.
        - `[p]remindme 5m Coffee break` — 5 minute reminder named "Coffee break", ping here.
        - `[p]remindme standup` — Use your or this server's "standup" preset.
        """
        rest = (rest or "").strip()
        # No args: interactive
        if not rest:
            channel_id = ctx.channel.id
            guild_id = getattr(ctx.guild, "id", None)
            embed = discord.Embed(
                title="Set a reminder",
                description="Pick a time below, or use Custom. Choose whether to be pinged here or DMed.",
                color=await self.bot.get_embed_color(ctx.guild) if ctx.guild else discord.Color.blue(),
            )
            view = RemindmeView(self, ctx.author.id, channel_id, guild_id)
            await ctx.send(embed=embed, view=view)
            return

        # Parse: first token = duration or preset; optional "dm"; remainder = name
        parts = rest.split(maxsplit=1)
        first = parts[0]
        rest_tail = (parts[1].strip() if len(parts) > 1 else "") or ""
        dm = False
        name = None
        if rest_tail:
            if rest_tail.lower() == "dm":
                dm = True
            elif rest_tail.lower().startswith("dm "):
                dm = True
                name = rest_tail[3:].strip() or None
            else:
                name = rest_tail

        delta = await self._resolve_duration(first, ctx.author.id, getattr(ctx.guild, "id", None))
        if delta is None:
            await ctx.send("Could not parse a duration or find a preset. Use e.g. `5m`, `1h`, or a preset name.")
            return

        duration_seconds = int(delta.total_seconds())
        timer_id = await self._create_timer(
            user_id=ctx.author.id,
            channel_id=ctx.channel.id,
            guild_id=getattr(ctx.guild, "id", None),
            duration_seconds=duration_seconds,
            dm=dm,
            name=name,
        )
        if not timer_id:
            await ctx.send("Could not create reminder.")
            return
        msg = f"Reminder set for {humanize_timedelta(seconds=duration_seconds)}. I will {'DM you' if dm else 'ping you here'} when it's done."
        if name:
            msg += f" Name: **{name}**"
        await ctx.send(msg)

    @commands.group(name="remindmeset", aliases=["rmset"], invoke_without_command=True)
    async def remindmeset(self, ctx: commands.Context):
        """Manage remindme presets and list/cancel timers."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @remindmeset.command(name="set")
    async def preset_set(self, ctx: commands.Context, name: str, duration: str):
        """
        Set a personal preset (e.g. [p]remindmeset set coffee 15m).
        Then use [p]remindme coffee to start a 15m timer.
        """
        key = _normalize_preset_name(name)
        if not key:
            await ctx.send("Preset name cannot be empty.")
            return
        delta = _parse_duration(duration)
        if delta is None:
            await ctx.send("Invalid duration. Use e.g. 5m, 1h, 2d.")
            return
        async with self.config.user(ctx.author).presets() as presets:
            presets[key] = duration.strip()
        await ctx.send(f"Preset **{key}** set to `{duration.strip()}`. Use `[p]remindme {key}` to use it.")

    @remindmeset.command(name="unset")
    async def preset_unset(self, ctx: commands.Context, name: str):
        """Remove a personal preset."""
        key = _normalize_preset_name(name)
        if not key:
            await ctx.send("Preset name cannot be empty.")
            return
        async with self.config.user(ctx.author).presets() as presets:
            if key in presets:
                del presets[key]
                await ctx.send(f"Preset **{key}** removed.")
            else:
                await ctx.send(f"You have no preset named **{key}**.")

    @remindmeset.command(name="presets")
    async def preset_list(self, ctx: commands.Context):
        """List your personal presets."""
        presets = await self.config.user(ctx.author).presets()
        if not presets or not isinstance(presets, dict):
            await ctx.send("You have no presets. Use `[p]remindmeset set <name> <duration>` to add one.")
            return
        lines = [f"**{k}** → `{v}`" for k, v in sorted(presets.items())]
        await ctx.send("Your presets:\n" + "\n".join(lines))

    @remindmeset.command(name="publish")
    @commands.guild_only()
    async def publish_preset(self, ctx: commands.Context, name: str, duration: Optional[str] = None):
        """
        Publish a preset to this server so others can use it.
        [p]remindmeset publish standup 10m — or omit duration to publish your personal preset with that name.
        """
        guild = ctx.guild
        key = _normalize_preset_name(name)
        if not key:
            await ctx.send("Preset name cannot be empty.")
            return
        if duration is None:
            presets = await self.config.user(ctx.author).presets()
            if not presets or key not in presets:
                await ctx.send(f"You have no personal preset named **{key}**. Set one with `[p]remindmeset set {key} <duration>` or provide a duration here.")
                return
            duration = presets[key]
        delta = _parse_duration(duration)
        if delta is None:
            await ctx.send("Invalid duration. Use e.g. 5m, 1h.")
            return
        async with self.config.guild(guild).published_presets() as data:
            if not isinstance(data, dict):
                data = {}
            data[key] = {"duration": duration.strip(), "owner_id": ctx.author.id}
        await ctx.send(f"Published preset **{key}** for this server: `{duration.strip()}`. Anyone can use `[p]remindme {key}`.")

    @remindmeset.command(name="unpublish")
    @commands.guild_only()
    async def unpublish_preset(self, ctx: commands.Context, name: str):
        """Remove a published preset. Requires manage_guild or being the preset owner."""
        guild = ctx.guild
        key = _normalize_preset_name(name)
        if not key:
            await ctx.send("Preset name cannot be empty.")
            return
        async with self.config.guild(guild).published_presets() as data:
            if not isinstance(data, dict):
                await ctx.send("No published presets.")
                return
            entry = data.get(key)
            if entry is None:
                await ctx.send(f"There is no published preset named **{key}**.")
                return
            owner_id = entry.get("owner_id") if isinstance(entry, dict) else None
            if owner_id != ctx.author.id and not ctx.author.guild_permissions.manage_guild:
                await ctx.send("You can only unpublish your own presets, or need Manage Server.")
                return
            del data[key]
        await ctx.send(f"Unpublished **{key}**.")

    @remindmeset.command(name="published")
    @commands.guild_only()
    async def published_list(self, ctx: commands.Context):
        """List published presets for this server."""
        data = await self.config.guild(ctx.guild).published_presets()
        if not data or not isinstance(data, dict):
            await ctx.send("No published presets in this server.")
            return
        lines = []
        for k, v in sorted(data.items()):
            if isinstance(v, dict):
                dur = v.get("duration", v.get("duration_str", "?"))
            else:
                dur = str(v)
            lines.append(f"**{k}** → `{dur}`")
        await ctx.send("Published presets:\n" + "\n".join(lines))

    @remindmeset.command(name="list")
    async def timer_list(self, ctx: commands.Context):
        """List your active reminders."""
        timers = await _get_timers(self.config)
        mine = [t for t in timers if t.get("user_id") == ctx.author.id]
        if not mine:
            await ctx.send("You have no active reminders. Use `[p]remindme 5m` or the interactive `[p]remindme`.")
            return
        now = time.time()
        lines = []
        for t in mine[:20]:
            tid = t.get("id", "?")
            end_ts = t.get("end_ts", 0)
            left = max(0, int(end_ts - now))
            name = t.get("name") or "—"
            dest = "DM" if t.get("dm") else "here"
            lines.append(f"`{tid}` {humanize_timedelta(seconds=left)} — {name} ({dest})")
        if len(mine) > 20:
            lines.append(f"... and {len(mine) - 20} more")
        await ctx.send("Your active reminders:\n" + "\n".join(lines))

    @remindmeset.command(name="cancel")
    async def timer_cancel(self, ctx: commands.Context, timer_id: str):
        """Cancel a reminder by ID (use list to see IDs). You can cancel only your own."""
        timer_id = timer_id.strip().strip("`")
        t = await _find_timer(self.config, timer_id)
        if not t:
            await ctx.send("No reminder found with that ID. Use `[p]remindmeset list` to see your reminders.")
            return
        if t.get("user_id") != ctx.author.id:
            await ctx.send("You can only cancel your own reminders.")
            return
        removed = await _remove_timer(self.config, timer_id)
        if not removed:
            await ctx.send("Reminder not found or already fired.")
            return
        task = self._timer_tasks.pop(timer_id, None)
        if task and not task.done():
            task.cancel()
        await ctx.send("Reminder cancelled.")


async def setup(bot: Red) -> None:
    cog = Remindme(bot)
    await bot.add_cog(cog)
