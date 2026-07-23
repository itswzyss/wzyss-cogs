import logging
import re
import time
from typing import Dict, Optional

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.mediachannels")


def _to_bool(value: str, default: bool = False) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "t", "yes", "y", "1", "on", "enable", "enabled"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "off", "disable", "disabled"}:
        return False
    return default


def _normalize_warning_mode(value: str, fallback: str = "channel") -> str:
    normalized = (value or "").strip().lower()
    valid = {"none", "channel", "dm", "both"}
    return normalized if normalized in valid else fallback


class MediaChannelContentModal(Modal):
    """Configure core media channel behavior."""

    def __init__(
        self,
        cog: "MediaChannels",
        channel: discord.TextChannel,
        state: dict,
        setup_view: Optional["MediaChannelSetupView"] = None,
    ):
        super().__init__(title=f"Content Rules - #{channel.name}"[:45])
        self.cog = cog
        self.channel = channel
        self.state = state
        self.setup_view = setup_view

        allowed_types = []
        if state.get("allow_links", True):
            allowed_types.append("links")
        if state.get("allow_attachments", True):
            allowed_types.append("attachments")
        if state.get("allow_embeds", True):
            allowed_types.append("embeds")
        if state.get("allow_stickers", True):
            allowed_types.append("stickers")

        self.enabled_input = TextInput(
            label="Enforcement enabled (true/false)",
            default=str(state.get("enabled", True)).lower(),
            required=True,
            max_length=10,
        )
        self.add_item(self.enabled_input)

        self.delete_input = TextInput(
            label="Delete non-media messages (true/false)",
            default=str(state.get("delete_non_media", True)).lower(),
            required=True,
            max_length=10,
        )
        self.add_item(self.delete_input)

        self.types_input = TextInput(
            label="Allowed media types (comma list)",
            placeholder="links,attachments,embeds,stickers",
            default=",".join(allowed_types),
            required=True,
            max_length=120,
        )
        self.add_item(self.types_input)

        self.grace_count_input = TextInput(
            label="Reply grace message count (0-20)",
            default=str(state.get("reply_grace_messages", 2)),
            required=True,
            max_length=3,
        )
        self.add_item(self.grace_count_input)

        self.grace_window_input = TextInput(
            label="Reply grace window seconds (0-86400)",
            default=str(state.get("reply_grace_window_seconds", 300)),
            required=True,
            max_length=6,
        )
        self.add_item(self.grace_window_input)

    async def on_submit(self, interaction: discord.Interaction):
        enabled = _to_bool(self.enabled_input.value, True)
        delete_non_media = _to_bool(self.delete_input.value, True)

        raw_types = [t.strip().lower() for t in self.types_input.value.split(",") if t.strip()]
        valid_types = {"links", "attachments", "embeds", "stickers"}
        selected = set(t for t in raw_types if t in valid_types)

        if not selected:
            await interaction.response.send_message(
                "Select at least one valid media type: links, attachments, embeds, stickers.",
                ephemeral=True,
            )
            return

        try:
            grace_count = int(self.grace_count_input.value.strip())
            grace_window = int(self.grace_window_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Grace count/window must be whole numbers.",
                ephemeral=True,
            )
            return

        if grace_count < 0 or grace_count > 20:
            await interaction.response.send_message(
                "Reply grace count must be between 0 and 20.",
                ephemeral=True,
            )
            return
        if grace_window < 0 or grace_window > 86400:
            await interaction.response.send_message(
                "Reply grace window must be between 0 and 86400 seconds.",
                ephemeral=True,
            )
            return

        self.state["enabled"] = enabled
        self.state["delete_non_media"] = delete_non_media
        self.state["allow_links"] = "links" in selected
        self.state["allow_attachments"] = "attachments" in selected
        self.state["allow_embeds"] = "embeds" in selected
        self.state["allow_stickers"] = "stickers" in selected
        self.state["reply_grace_messages"] = grace_count
        self.state["reply_grace_window_seconds"] = grace_window

        await interaction.response.send_message(
            "Content rules updated. Use Preview or Save.",
            ephemeral=True,
        )
        if self.setup_view and self.setup_view.message:
            await self.setup_view._refresh()


class MediaChannelWarningModal(Modal):
    """Configure warning behavior."""

    def __init__(
        self,
        cog: "MediaChannels",
        channel: discord.TextChannel,
        state: dict,
        setup_view: Optional["MediaChannelSetupView"] = None,
    ):
        super().__init__(title=f"Warning Rules - #{channel.name}"[:45])
        self.cog = cog
        self.channel = channel
        self.state = state
        self.setup_view = setup_view

        self.mode_input = TextInput(
            label="Warning mode (none/channel/dm/both)",
            default=state.get("warning_mode", "channel"),
            required=True,
            max_length=10,
        )
        self.add_item(self.mode_input)

        self.auto_delete_input = TextInput(
            label="Channel warning auto-delete seconds (0-300)",
            default=str(state.get("warning_delete_after", 10)),
            required=True,
            max_length=3,
        )
        self.add_item(self.auto_delete_input)

        self.requires_reply_input = TextInput(
            label="Grace requires reply (true/false)",
            default=str(state.get("grace_requires_reply", True)).lower(),
            required=True,
            max_length=10,
        )
        self.add_item(self.requires_reply_input)

        self.warning_message_input = TextInput(
            label="Warning message ({mention}, {channel})",
            default=state.get(
                "warning_message", "{mention} this channel is for media posts only."
            )[:500],
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.warning_message_input)

    async def on_submit(self, interaction: discord.Interaction):
        mode = _normalize_warning_mode(self.mode_input.value, "channel")
        requires_reply = _to_bool(self.requires_reply_input.value, True)
        message = (self.warning_message_input.value or "").strip()

        try:
            warning_delete_after = int(self.auto_delete_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Warning auto-delete must be a whole number.",
                ephemeral=True,
            )
            return

        if warning_delete_after < 0 or warning_delete_after > 300:
            await interaction.response.send_message(
                "Warning auto-delete seconds must be between 0 and 300.",
                ephemeral=True,
            )
            return

        if not message:
            await interaction.response.send_message(
                "Warning message cannot be empty.",
                ephemeral=True,
            )
            return

        self.state["warning_mode"] = mode
        self.state["warn_on_delete"] = mode != "none"  # backward-compatibility
        self.state["warning_delete_after"] = warning_delete_after
        self.state["grace_requires_reply"] = requires_reply
        self.state["warning_message"] = message

        await interaction.response.send_message(
            "Warning rules updated. Use Preview or Save.",
            ephemeral=True,
        )
        if self.setup_view and self.setup_view.message:
            await self.setup_view._refresh()


class MediaChannelSetupView(View):
    """Interactive setup view for one channel."""

    def __init__(
        self,
        cog: "MediaChannels",
        guild_id: int,
        channel_id: int,
        owner_id: int,
        state: dict,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.state = state
        self.message: Optional[discord.Message] = None

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def _deny_if_not_owner(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction):
            return False
        await interaction.response.send_message(
            "Only the admin who started this setup can use these controls.",
            ephemeral=True,
        )
        return True

    def _status_embed(self, guild: discord.Guild) -> discord.Embed:
        channel = guild.get_channel(self.channel_id)
        title = f"Media Setup - #{channel.name}" if channel else "Media Setup"
        mode = self.state.get("warning_mode", "channel")
        description = (
            f"Channel: {channel.mention if channel else self.channel_id}\n"
            f"Enabled: {self.state.get('enabled', True)}\n"
            f"Delete non-media: {self.state.get('delete_non_media', True)}\n"
            f"Allow links: {self.state.get('allow_links', True)}\n"
            f"Allow attachments: {self.state.get('allow_attachments', True)}\n"
            f"Allow embeds: {self.state.get('allow_embeds', True)}\n"
            f"Allow stickers: {self.state.get('allow_stickers', True)}\n"
            f"Reply grace messages: {self.state.get('reply_grace_messages', 2)}\n"
            f"Reply grace window: {self.state.get('reply_grace_window_seconds', 300)}s\n"
            f"Grace requires reply: {self.state.get('grace_requires_reply', True)}\n"
            f"Warning mode: {mode}\n"
            f"Warning auto-delete: {self.state.get('warning_delete_after', 10)}s\n"
            f"Warning text: {self.state.get('warning_message', '')[:140]}"
        )
        return discord.Embed(
            title=title,
            description=description,
            color=guild.me.color if guild and guild.me else discord.Color.blurple(),
        )

    async def _refresh(self, interaction: Optional[discord.Interaction] = None):
        guild = interaction.guild if interaction else (self.message.guild if self.message else None)
        if not guild:
            return
        embed = self._status_embed(guild)
        try:
            if interaction:
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)
            elif self.message:
                await self.message.edit(embed=embed, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Content Rules", style=discord.ButtonStyle.primary, emoji="🧩", row=0)
    async def content_rules(self, interaction: discord.Interaction, button: Button):
        if await self._deny_if_not_owner(interaction):
            return
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return
        channel = guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Channel no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(
            MediaChannelContentModal(self.cog, channel, self.state, self)
        )

    @discord.ui.button(label="Warning Rules", style=discord.ButtonStyle.secondary, emoji="⚠️", row=0)
    async def warning_rules(self, interaction: discord.Interaction, button: Button):
        if await self._deny_if_not_owner(interaction):
            return
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return
        channel = guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Channel no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(
            MediaChannelWarningModal(self.cog, channel, self.state, self)
        )

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👁️", row=1)
    async def preview(self, interaction: discord.Interaction, button: Button):
        if await self._deny_if_not_owner(interaction):
            return
        await interaction.response.send_message(
            embed=self._status_embed(interaction.guild),
            ephemeral=True,
        )

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def save(self, interaction: discord.Interaction, button: Button):
        if await self._deny_if_not_owner(interaction):
            return
        await self.cog._save_channel_settings(interaction.guild, self.channel_id, self.state)
        await interaction.response.send_message("Media channel settings saved.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await self._refresh()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖", row=1)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if await self._deny_if_not_owner(interaction):
            return
        await interaction.response.send_message("Setup cancelled.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await self._refresh()


class MediaChannels(commands.Cog):
    """Enforce media-focused posting in configured channels."""

    URL_PATTERN = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654324, force_registration=True
        )

        default_guild = {
            # channel_id -> settings dict
            "channels": {}
        }
        self.config.register_guild(**default_guild)

        # Runtime-only state:
        # {guild_id: {channel_id: {"last_media_ts": float, "last_media_message_id": int, "grace_remaining": int}}}
        self._channel_state: Dict[int, Dict[int, Dict[str, Optional[float]]]] = {}
        self._setup_states: Dict[int, Dict[int, dict]] = {}

    @staticmethod
    def _default_channel_settings() -> dict:
        return {
            "enabled": True,
            "delete_non_media": True,
            "warn_on_delete": False,
            "warning_mode": "none",
            "warning_message": "{mention} this channel is for media posts only.",
            "warning_delete_after": 10,
            "allow_links": True,
            "allow_attachments": True,
            "allow_embeds": True,
            "allow_stickers": True,
            "reply_grace_messages": 2,
            "reply_grace_window_seconds": 300,
            "grace_requires_reply": True,
            "exempt_role_ids": [],
        }

    def _merged_channel_settings(self, raw: dict) -> dict:
        merged = self._default_channel_settings()
        merged.update(raw or {})
        # Backward compatibility for old bool warning flag.
        if "warning_mode" not in merged or not merged.get("warning_mode"):
            merged["warning_mode"] = "channel" if merged.get("warn_on_delete") else "none"
        merged["warning_mode"] = _normalize_warning_mode(
            merged.get("warning_mode", "none"), "none"
        )
        merged["warn_on_delete"] = merged["warning_mode"] != "none"
        return merged

    async def _save_channel_settings(
        self, guild: discord.Guild, channel_id: int, settings: dict
    ) -> None:
        channels = await self.config.guild(guild).channels()
        key = str(channel_id)
        merged = self._merged_channel_settings(settings)
        channels[key] = merged
        await self.config.guild(guild).channels.set(channels)
        state = self._state_for_channel(guild.id, channel_id, merged)
        state["grace_remaining"] = int(merged.get("reply_grace_messages", 0))

    def _state_for_channel(self, guild_id: int, channel_id: int, cfg: dict) -> dict:
        guild_state = self._channel_state.setdefault(guild_id, {})
        state = guild_state.get(channel_id)
        if state is None:
            state = {
                "last_media_ts": None,
                "last_media_message_id": None,
                "grace_remaining": cfg.get("reply_grace_messages", 0),
            }
            guild_state[channel_id] = state
        return state

    @staticmethod
    def _is_exempt(member: discord.Member, cfg: dict) -> bool:
        if member.guild_permissions.manage_messages or member.guild_permissions.manage_guild:
            return True

        exempt_role_ids = set(cfg.get("exempt_role_ids", []))
        if exempt_role_ids and any(role.id in exempt_role_ids for role in member.roles):
            return True
        return False

    def _message_has_link(self, message: discord.Message) -> bool:
        if not message.content:
            return False
        return bool(self.URL_PATTERN.search(message.content))

    def _is_media_message(self, message: discord.Message, cfg: dict) -> bool:
        if cfg.get("allow_attachments", True) and message.attachments:
            return True

        if cfg.get("allow_stickers", True) and message.stickers:
            return True

        # Links are allowed because many sites create embeds asynchronously.
        if cfg.get("allow_links", True) and self._message_has_link(message):
            return True

        if cfg.get("allow_embeds", True) and message.embeds:
            return True

        return False

    def _consume_reply_grace_if_allowed(
        self,
        message: discord.Message,
        cfg: dict,
        state: dict,
        now: float,
    ) -> bool:
        grace_remaining = int(state.get("grace_remaining") or 0)
        if grace_remaining <= 0:
            return False

        last_media_ts = state.get("last_media_ts")
        if not last_media_ts:
            return False

        window = max(0, int(cfg.get("reply_grace_window_seconds", 0)))
        if window == 0 or now - float(last_media_ts) > window:
            return False

        requires_reply = cfg.get("grace_requires_reply", True)
        if requires_reply and message.reference is None:
            return False

        state["grace_remaining"] = grace_remaining - 1
        return True

    @commands.group(name="mediachannel", aliases=["mcs"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def mediachannel(self, ctx: commands.Context):
        """Configure media-only channel moderation."""
        pass

    @mediachannel.command(name="setup")
    async def _setup_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Open interactive setup UI for a media channel."""
        channels = await self.config.guild(ctx.guild).channels()
        existing = self._merged_channel_settings(channels.get(str(channel.id), {}))
        if ctx.author.id not in self._setup_states:
            self._setup_states[ctx.author.id] = {}
        self._setup_states[ctx.author.id][channel.id] = dict(existing)

        view = MediaChannelSetupView(
            self,
            ctx.guild.id,
            channel.id,
            ctx.author.id,
            self._setup_states[ctx.author.id][channel.id],
        )
        embed = view._status_embed(ctx.guild)
        view.message = await ctx.send(embed=embed, view=view)

    @mediachannel.command(name="add")
    async def _add_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Add a channel to media enforcement."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
            await self.config.guild(ctx.guild).channels.set(channels)
            await ctx.send(f"Now enforcing media rules in {channel.mention}.")
            return

        channels[key]["enabled"] = True
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"Media enforcement re-enabled in {channel.mention}.")

    @mediachannel.command(name="remove", aliases=["delete", "del"])
    async def _remove_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from media enforcement."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            await ctx.send(f"{channel.mention} is not configured.")
            return

        del channels[key]
        await self.config.guild(ctx.guild).channels.set(channels)

        guild_state = self._channel_state.get(ctx.guild.id, {})
        guild_state.pop(channel.id, None)
        await ctx.send(f"Removed media enforcement from {channel.mention}.")

    @mediachannel.command(name="toggle")
    async def _toggle_channel(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Enable or disable enforcement in a configured channel."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()

        channels[key]["enabled"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Media enforcement {'enabled' if enabled else 'disabled'} in {channel.mention}."
        )

    @mediachannel.command(name="warn")
    async def _set_warn(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Toggle warning users when messages are removed."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()

        channels[key]["warn_on_delete"] = enabled
        channels[key]["warning_mode"] = "channel" if enabled else "none"
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Warnings {'enabled' if enabled else 'disabled'} in {channel.mention}."
        )

    @mediachannel.command(name="deletenonmedia")
    async def _set_delete_non_media(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Toggle deleting non-media messages."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()

        channels[key]["delete_non_media"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Deleting non-media is {'enabled' if enabled else 'disabled'} in {channel.mention}."
        )

    @mediachannel.command(name="warningmessage")
    async def _set_warning_message(
        self, ctx: commands.Context, channel: discord.TextChannel, *, message: str
    ):
        """Set warning text used after deleting non-media.

        Available placeholders: {mention}, {channel}
        """
        if len(message) > 500:
            await ctx.send("Warning message must be 500 characters or less.")
            return

        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["warning_message"] = message
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"Warning message updated for {channel.mention}.")

    @mediachannel.command(name="warningdeleteafter")
    async def _set_warning_delete_after(
        self, ctx: commands.Context, channel: discord.TextChannel, seconds: int
    ):
        """Set how long warning messages stay before auto-delete (0 disables auto-delete)."""
        if seconds < 0 or seconds > 300:
            await ctx.send("Seconds must be between 0 and 300.")
            return

        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["warning_delete_after"] = seconds
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"Warning auto-delete set to {seconds}s for {channel.mention}.")

    @mediachannel.command(name="replygrace")
    async def _set_reply_grace(
        self, ctx: commands.Context, channel: discord.TextChannel, count: int
    ):
        """Set how many non-media messages are allowed after a media post."""
        if count < 0 or count > 20:
            await ctx.send("Reply grace count must be between 0 and 20.")
            return

        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()

        channels[key]["reply_grace_messages"] = count
        await self.config.guild(ctx.guild).channels.set(channels)

        state = self._state_for_channel(ctx.guild.id, channel.id, channels[key])
        state["grace_remaining"] = count
        await ctx.send(f"Reply grace set to {count} message(s) in {channel.mention}.")

    @mediachannel.command(name="gracewindow")
    async def _set_grace_window(
        self, ctx: commands.Context, channel: discord.TextChannel, seconds: int
    ):
        """Set grace window duration after media messages."""
        if seconds < 0 or seconds > 86400:
            await ctx.send("Grace window must be between 0 and 86400 seconds.")
            return

        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["reply_grace_window_seconds"] = seconds
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"Grace window set to {seconds}s in {channel.mention}.")

    @mediachannel.command(name="gracerequiresreply")
    async def _set_grace_requires_reply(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Require grace-allowed text to be replies."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["grace_requires_reply"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Grace now {'requires' if enabled else 'does not require'} replies in {channel.mention}."
        )

    @mediachannel.command(name="allowlinks")
    async def _allow_links(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Allow links to count as media content."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["allow_links"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Links are now {'allowed' if enabled else 'not allowed'} in {channel.mention}."
        )

    @mediachannel.command(name="allowattachments")
    async def _allow_attachments(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Allow attachments to count as media content."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["allow_attachments"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Attachments are now {'allowed' if enabled else 'not allowed'} in {channel.mention}."
        )

    @mediachannel.command(name="allowembeds")
    async def _allow_embeds(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Allow embeds to count as media content."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["allow_embeds"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Embeds are now {'allowed' if enabled else 'not allowed'} in {channel.mention}."
        )

    @mediachannel.command(name="allowstickers")
    async def _allow_stickers(
        self, ctx: commands.Context, channel: discord.TextChannel, enabled: bool
    ):
        """Allow stickers to count as media content."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()
        channels[key]["allow_stickers"] = enabled
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(
            f"Stickers are now {'allowed' if enabled else 'not allowed'} in {channel.mention}."
        )

    @mediachannel.group(name="exemptrole")
    async def _exemptrole_group(self, ctx: commands.Context):
        """Manage roles exempt from enforcement."""
        pass

    @_exemptrole_group.command(name="add")
    async def _exemptrole_add(
        self, ctx: commands.Context, channel: discord.TextChannel, role: discord.Role
    ):
        """Add an exempt role for a channel."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            channels[key] = self._default_channel_settings()

        exempt_ids = set(channels[key].get("exempt_role_ids", []))
        exempt_ids.add(role.id)
        channels[key]["exempt_role_ids"] = list(exempt_ids)
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"{role.mention} is now exempt in {channel.mention}.")

    @_exemptrole_group.command(name="remove", aliases=["del", "delete"])
    async def _exemptrole_remove(
        self, ctx: commands.Context, channel: discord.TextChannel, role: discord.Role
    ):
        """Remove an exempt role for a channel."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            await ctx.send(f"{channel.mention} is not configured.")
            return

        exempt_ids = set(channels[key].get("exempt_role_ids", []))
        if role.id not in exempt_ids:
            await ctx.send(f"{role.mention} is not exempt in {channel.mention}.")
            return

        exempt_ids.remove(role.id)
        channels[key]["exempt_role_ids"] = list(exempt_ids)
        await self.config.guild(ctx.guild).channels.set(channels)
        await ctx.send(f"{role.mention} is no longer exempt in {channel.mention}.")

    @_exemptrole_group.command(name="list")
    async def _exemptrole_list(self, ctx: commands.Context, channel: discord.TextChannel):
        """List exempt roles for a channel."""
        channels = await self.config.guild(ctx.guild).channels()
        key = str(channel.id)
        if key not in channels:
            await ctx.send(f"{channel.mention} is not configured.")
            return

        exempt_ids = channels[key].get("exempt_role_ids", [])
        if not exempt_ids:
            await ctx.send(f"No exempt roles set for {channel.mention}.")
            return

        roles = [ctx.guild.get_role(rid) for rid in exempt_ids]
        roles = [r for r in roles if r]
        if not roles:
            await ctx.send(f"No valid exempt roles remain for {channel.mention}.")
            return

        role_mentions = ", ".join(role.mention for role in roles)
        await ctx.send(f"Exempt roles for {channel.mention}: {role_mentions}")

    @mediachannel.command(name="status")
    async def _status(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Show configured media channel settings."""
        channels = await self.config.guild(ctx.guild).channels()
        color = await ctx.embed_color()

        if not channels:
            await ctx.send(
                embed=discord.Embed(
                    title="Media channel status",
                    description="No channels are configured.",
                    color=color,
                )
            )
            return

        if channel:
            key = str(channel.id)
            if key not in channels:
                await ctx.send(
                    embed=discord.Embed(
                        title="Media channel status",
                        description=f"{channel.mention} is not configured.",
                        color=color,
                    )
                )
                return
            cfg = self._merged_channel_settings(channels[key])
            await ctx.send(embed=self._format_channel_status(channel, cfg, color))
            return

        lines = []
        for channel_id_str, raw in channels.items():
            try:
                channel_id = int(channel_id_str)
            except ValueError:
                continue
            ch = ctx.guild.get_channel(channel_id)
            name = ch.mention if ch else f"`{channel_id}` (missing)"
            cfg = self._merged_channel_settings(raw)
            state = "enabled" if cfg.get("enabled", True) else "disabled"
            lines.append(
                f"{name}: {state}, grace={cfg.get('reply_grace_messages', 0)} "
                f"in {cfg.get('reply_grace_window_seconds', 0)}s"
            )

        description = "\n".join(lines) if lines else "No channels are configured."
        await ctx.send(
            embed=discord.Embed(
                title="Configured media channels",
                description=description,
                color=color,
            )
        )

    def _format_channel_status(
        self, channel: discord.TextChannel, cfg: dict, color: discord.Color
    ) -> discord.Embed:
        return discord.Embed(
            title=f"Media settings - #{channel.name}",
            description=(
                f"Enabled: {cfg.get('enabled', True)}\n"
                f"Delete non-media: {cfg.get('delete_non_media', True)}\n"
                f"Warn on delete: {cfg.get('warn_on_delete', False)}\n"
                f"Warning mode: {cfg.get('warning_mode', 'none')}\n"
                f"Warning delete after: {cfg.get('warning_delete_after', 10)}s\n"
                f"Allow links: {cfg.get('allow_links', True)}\n"
                f"Allow attachments: {cfg.get('allow_attachments', True)}\n"
                f"Allow embeds: {cfg.get('allow_embeds', True)}\n"
                f"Allow stickers: {cfg.get('allow_stickers', True)}\n"
                f"Reply grace messages: {cfg.get('reply_grace_messages', 2)}\n"
                f"Reply grace window: {cfg.get('reply_grace_window_seconds', 300)}s\n"
                f"Grace requires reply: {cfg.get('grace_requires_reply', True)}\n"
                f"Exempt role count: {len(cfg.get('exempt_role_ids', []))}"
            ),
            color=color,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Delete non-media messages in configured media channels."""
        if message.author.bot or not message.guild:
            return

        if not isinstance(message.author, discord.Member):
            return

        channels = await self.config.guild(message.guild).channels()
        raw_cfg = channels.get(str(message.channel.id))
        if not raw_cfg:
            return

        cfg = self._merged_channel_settings(raw_cfg)
        if not cfg.get("enabled", True):
            return

        if self._is_exempt(message.author, cfg):
            return

        now = time.time()
        state = self._state_for_channel(message.guild.id, message.channel.id, cfg)

        if self._is_media_message(message, cfg):
            state["last_media_ts"] = now
            state["last_media_message_id"] = message.id
            state["grace_remaining"] = max(0, int(cfg.get("reply_grace_messages", 0)))
            return

        if self._consume_reply_grace_if_allowed(message, cfg, state, now):
            return

        if not cfg.get("delete_non_media", True):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            return
        except discord.HTTPException:
            log.warning(
                "Failed to delete non-media message %s in guild %s channel %s",
                message.id,
                message.guild.id,
                message.channel.id,
            )
            return

        warning_mode = _normalize_warning_mode(cfg.get("warning_mode", "none"), "none")
        if warning_mode == "none":
            return

        warning_message = cfg.get(
            "warning_message", "{mention} this channel is for media posts only."
        )
        rendered = warning_message.replace("{mention}", message.author.mention).replace(
            "{channel}", message.channel.mention
        )
        delete_after = max(0, int(cfg.get("warning_delete_after", 10)))

        if warning_mode in {"channel", "both"}:
            try:
                await message.channel.send(
                    rendered,
                    allowed_mentions=discord.AllowedMentions(users=True),
                    delete_after=delete_after if delete_after > 0 else None,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if warning_mode in {"dm", "both"}:
            try:
                await message.author.send(rendered)
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot: Red):
    """Load the MediaChannels cog."""
    await bot.add_cog(MediaChannels(bot))
