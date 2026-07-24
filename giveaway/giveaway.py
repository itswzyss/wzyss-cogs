import asyncio
import difflib
import logging
import random
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import get_timedelta_converter, parse_timedelta
from redbot.core.utils.chat_formatting import humanize_timedelta

# For type of _end_tasks (keys are (guild_id, message_id) or (guild_id, message_id, "claim"))
_TaskKey = Tuple[int, ...]

DurationConverter = get_timedelta_converter(
    maximum=timedelta(days=365),
    minimum=timedelta(seconds=60),
    default_unit="minutes",
)

log = logging.getLogger("red.wzyss-cogs.giveaway")

# Custom ID prefix for persistent claim button (must be stable across restarts)
CLAIM_CUSTOM_ID_PREFIX = "giveaway:claim:"


class ClaimButton(Button):
    """Persistent button for winner to claim. Only winner can use it.

    Works from the giveaway channel message or from a winner DM.
    custom_id includes guild_id so claims from DMs can resolve the server.
    """

    def __init__(self, cog: "Giveaway", guild_id: int, message_id: int):
        super().__init__(
            label="Claim",
            style=discord.ButtonStyle.success,
            emoji="\u2705",
            custom_id=f"{CLAIM_CUSTOM_ID_PREFIX}{guild_id}:{message_id}",
        )
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog._handle_claim_click(interaction, self.guild_id, self.message_id)


class ClaimView(View):
    """View with a single Claim button; timeout=None for persistence."""

    def __init__(self, cog: "Giveaway", guild_id: int, message_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.add_item(ClaimButton(cog, guild_id, message_id))


# --- Modals for interactive builder ---


def _parse_prizes_list(raw: str) -> List[str]:
    """Parse comma or newline separated prizes into a list of non-empty strings."""
    prizes = []
    for part in raw.replace(",", "\n").split("\n"):
        p = part.strip()
        if p:
            prizes.append(p[:256])
    return prizes


class SetPrizeModal(Modal, title="Set Prizes"):
    prize_input = TextInput(
        label="Prizes (one per line or comma-separated)",
        placeholder="Nitro 1 month\nGame key\nHoodie",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )
    description_input = TextInput(
        label="Description (optional)",
        placeholder="Extra details...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.prize_input.value or ""
        description = self.description_input.value.strip() or None
        prizes = _parse_prizes_list(raw)
        if not prizes:
            await interaction.response.send_message("At least one prize is required.", ephemeral=True)
            return
        await self.cog._set_draft_field(self.guild_id, self.user_id, "prizes", prizes)
        await self.cog._set_draft_field(self.guild_id, self.user_id, "prize", prizes[0])
        await self.cog._set_draft_field(self.guild_id, self.user_id, "description", description)
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


class SetWinnersModal(Modal, title="Set Number of Winners"):
    winners_input = TextInput(
        label="Number of winners",
        placeholder="1",
        required=True,
        max_length=5,
    )

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.winners_input.value or "").strip()
        try:
            n = int(raw)
        except ValueError:
            await interaction.response.send_message("Enter a valid number (1-50).", ephemeral=True)
            return
        if n < 1 or n > 50:
            await interaction.response.send_message("Number of winners must be between 1 and 50.", ephemeral=True)
            return
        await self.cog._set_draft_field(self.guild_id, self.user_id, "winner_count", n)
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


class SetDurationModal(Modal, title="Set Duration"):
    duration_input = TextInput(
        label="Duration",
        placeholder="e.g. 1d, 2h, 30m",
        required=True,
        max_length=50,
    )

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.duration_input.value.strip() if self.duration_input.value else ""
        if not raw:
            await interaction.response.send_message("Duration cannot be empty.", ephemeral=True)
            return
        try:
            delta = parse_timedelta(
                raw,
                minimum=timedelta(seconds=60),
                maximum=timedelta(days=365),
                allowed_units=["weeks", "days", "hours", "minutes", "seconds"],
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Invalid duration. Use e.g. 1d, 2h, 30m. ({e})",
                ephemeral=True,
            )
            return
        if delta is None:
            await interaction.response.send_message("Could not parse duration. Use e.g. 1d, 2h, 30m.", ephemeral=True)
            return
        await self.cog._set_draft_field(
            self.guild_id, self.user_id, "duration_seconds", int(delta.total_seconds())
        )
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


# Popular giveaway durations: (label, seconds, optional description)
_DURATION_PRESETS: List[Tuple[str, int, str]] = [
    ("1 day", 24 * 60 * 60, ""),
    ("2 days", 2 * 24 * 60 * 60, ""),
    ("3 days", 3 * 24 * 60 * 60, ""),
    ("5 days", 5 * 24 * 60 * 60, ""),
    ("1 week", 7 * 24 * 60 * 60, ""),
    ("2 weeks", 14 * 24 * 60 * 60, ""),
]


class DurationSelectDropdown(Select["DurationSelectView"]):
    """Dropdown of popular durations, plus Custom to open the modal."""

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int, builder_view: "GiveawayBuilderView"):
        self._cog = cog
        self._guild_id = guild_id
        self._user_id = user_id
        self._builder_view = builder_view
        options: List[discord.SelectOption] = []
        for label, seconds, description in _DURATION_PRESETS:
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(seconds),
                    description=description[:100] if description else None,
                )
            )
        options.append(
            discord.SelectOption(
                label="Custom",
                value="custom",
                description="Enter a custom duration",
            )
        )
        super().__init__(
            placeholder="Select a duration...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        value = self.values[0] if self.values else ""
        if value == "custom":
            modal = SetDurationModal(self._cog, self._guild_id, self._user_id)
            modal.builder_view = self._builder_view
            await interaction.response.send_modal(modal)
            return
        try:
            seconds = int(value)
        except ValueError:
            await interaction.response.send_message("Invalid duration selection.", ephemeral=True)
            return
        if seconds < 60:
            await interaction.response.send_message("Duration must be at least 1 minute.", ephemeral=True)
            return
        await self._cog._set_draft_field(self._guild_id, self._user_id, "duration_seconds", seconds)
        await interaction.response.defer(ephemeral=True)
        await self._builder_view.refresh()
        await interaction.followup.send(
            f"Duration set to {humanize_timedelta(seconds=seconds)}.",
            ephemeral=True,
        )


class DurationSelectView(View):
    """Temporary view holding the duration dropdown (ephemeral)."""

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int, builder_view: "GiveawayBuilderView"):
        super().__init__(timeout=60)
        self.add_item(DurationSelectDropdown(cog, guild_id, user_id, builder_view))


class SetEmojiModal(Modal, title="Set Emoji"):
    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int, default: str = "\U0001f389"):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.emoji_input = TextInput(
            label="Emoji",
            placeholder="e.g. \U0001f389",
            required=True,
            max_length=100,
            default=default,
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        emoji = (self.emoji_input.value or "").strip() or "\U0001f389"
        if not emoji:
            emoji = "\U0001f389"
        await self.cog._set_draft_field(self.guild_id, self.user_id, "emoji", emoji)
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


class SetImagesModal(Modal, title="Set Images"):
    def __init__(
        self,
        cog: "Giveaway",
        guild_id: int,
        user_id: int,
        *,
        thumbnail_default: str = "",
        image_default: str = "",
    ):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.thumbnail_input = TextInput(
            label="Thumbnail URL (optional)",
            placeholder="https://example.com/thumb.png — leave blank to clear",
            required=False,
            max_length=2000,
            default=thumbnail_default[:2000] if thumbnail_default else None,
        )
        self.image_input = TextInput(
            label="Main Image URL (optional)",
            placeholder="https://example.com/image.png — leave blank to clear",
            required=False,
            max_length=2000,
            default=image_default[:2000] if image_default else None,
        )
        self.add_item(self.thumbnail_input)
        self.add_item(self.image_input)

    @staticmethod
    def _normalize_url(raw: str, label: str) -> Tuple[Optional[str], Optional[str]]:
        value = (raw or "").strip()
        if not value:
            return None, None
        if not (value.startswith("http://") or value.startswith("https://")):
            return None, f"{label} URL must start with http:// or https://."
        return value, None

    async def on_submit(self, interaction: discord.Interaction):
        thumbnail_url, thumb_err = self._normalize_url(self.thumbnail_input.value or "", "Thumbnail")
        if thumb_err:
            await interaction.response.send_message(thumb_err, ephemeral=True)
            return
        image_url, image_err = self._normalize_url(self.image_input.value or "", "Image")
        if image_err:
            await interaction.response.send_message(image_err, ephemeral=True)
            return
        await self.cog._set_draft_field(self.guild_id, self.user_id, "thumbnail_url", thumbnail_url)
        await self.cog._set_draft_field(self.guild_id, self.user_id, "image_url", image_url)
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


class SetClaimDurationModal(Modal, title="Set Claim Window"):
    claim_duration_input = TextInput(
        label="Claim window (e.g. 24h, 2d)",
        placeholder="24h",
        required=True,
        max_length=50,
    )

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.claim_duration_input.value or "").strip()
        try:
            delta = parse_timedelta(
                raw,
                minimum=timedelta(seconds=60),
                maximum=timedelta(days=30),
                allowed_units=["days", "hours", "minutes", "seconds"],
            )
        except Exception:
            delta = None
        if not delta:
            await interaction.response.send_message(
                "Invalid claim window. Use e.g. 24h, 2d, 30m.",
                ephemeral=True,
            )
            return
        claim_seconds = int(delta.total_seconds())
        await self.cog._set_draft_field(self.guild_id, self.user_id, "claim_enabled", True)
        await self.cog._set_draft_field(self.guild_id, self.user_id, "claim_seconds", claim_seconds)
        await interaction.response.defer(ephemeral=True)
        if getattr(self, "builder_view", None):
            await self.builder_view.refresh(interaction)


# Claim window presets: (label, value, description)
# value "off" disables claim; "custom" opens the modal; otherwise seconds.
_CLAIM_PRESETS: List[Tuple[str, str, str]] = [
    ("Off", "off", "Do not require winners to claim"),
    ("1 day", str(24 * 60 * 60), "Winners must claim within 1 day"),
    ("2 days", str(2 * 24 * 60 * 60), "Winners must claim within 2 days"),
]


class ClaimSelectDropdown(Select["ClaimSelectView"]):
    """Dropdown to enable/disable claim and pick a claim window."""

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int, builder_view: "GiveawayBuilderView"):
        self._cog = cog
        self._guild_id = guild_id
        self._user_id = user_id
        self._builder_view = builder_view
        options: List[discord.SelectOption] = [
            discord.SelectOption(label=label, value=value, description=description[:100])
            for label, value, description in _CLAIM_PRESETS
        ]
        options.append(
            discord.SelectOption(
                label="Custom",
                value="custom",
                description="Enter a custom claim window",
            )
        )
        super().__init__(
            placeholder="Select claim settings...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        value = self.values[0] if self.values else "off"
        if value == "custom":
            modal = SetClaimDurationModal(self._cog, self._guild_id, self._user_id)
            modal.builder_view = self._builder_view
            await interaction.response.send_modal(modal)
            return
        if value == "off":
            await self._cog._set_draft_field(self._guild_id, self._user_id, "claim_enabled", False)
            await self._cog._set_draft_field(self._guild_id, self._user_id, "claim_seconds", 0)
            await interaction.response.defer(ephemeral=True)
            await self._builder_view.refresh()
            await interaction.followup.send("Claim is now disabled.", ephemeral=True)
            return
        try:
            seconds = int(value)
        except ValueError:
            await interaction.response.send_message("Invalid claim selection.", ephemeral=True)
            return
        if seconds < 60:
            await interaction.response.send_message("Claim window must be at least 1 minute.", ephemeral=True)
            return
        await self._cog._set_draft_field(self._guild_id, self._user_id, "claim_enabled", True)
        await self._cog._set_draft_field(self._guild_id, self._user_id, "claim_seconds", seconds)
        await interaction.response.defer(ephemeral=True)
        await self._builder_view.refresh()
        await interaction.followup.send(
            f"Claim enabled ({humanize_timedelta(seconds=seconds)}).",
            ephemeral=True,
        )


class ClaimSelectView(View):
    """Temporary view holding the claim settings dropdown (ephemeral)."""

    def __init__(self, cog: "Giveaway", guild_id: int, user_id: int, builder_view: "GiveawayBuilderView"):
        super().__init__(timeout=60)
        self.add_item(ClaimSelectDropdown(cog, guild_id, user_id, builder_view))


class ChannelSelectDropdown(Select["ChannelSelectView"]):
    """Dropdown to choose the giveaway channel from fuzzy results."""

    def __init__(
        self,
        cog: "Giveaway",
        guild: discord.Guild,
        user_id: int,
        builder_view: "GiveawayBuilderView",
        channels: List[discord.TextChannel],
    ):
        self._cog = cog
        self._guild_id = guild.id
        self._user_id = user_id
        self._builder_view = builder_view
        options: List[discord.SelectOption] = [
            discord.SelectOption(label="Current channel", value="0", description="Post in the channel where you run the command"),
        ]
        for ch in channels[:24]:
            options.append(
                discord.SelectOption(
                    label=ch.name[:100],
                    value=str(ch.id),
                    description=f"#{ch.name}"[:100],
                )
            )
        super().__init__(
            placeholder="Select a channel...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        value = self.values[0] if self.values else "0"
        if value == "0":
            await self._cog._set_draft_field(self._guild_id, self._user_id, "channel_id", None)
            channel_str = "Current channel"
        else:
            channel_id = int(value)
            await self._cog._set_draft_field(self._guild_id, self._user_id, "channel_id", channel_id)
            ch = interaction.guild.get_channel(channel_id)
            channel_str = ch.mention if ch else str(channel_id)
        await interaction.response.defer(ephemeral=True)
        await self._builder_view.refresh()
        await interaction.followup.send(f"Channel set to {channel_str}.", ephemeral=True)


class ChannelSelectView(View):
    """Temporary view holding the channel dropdown of fuzzy matches (ephemeral)."""

    def __init__(
        self,
        cog: "Giveaway",
        guild: discord.Guild,
        user_id: int,
        builder_view: "GiveawayBuilderView",
        channels: List[discord.TextChannel],
    ):
        super().__init__(timeout=60)
        self.add_item(ChannelSelectDropdown(cog, guild, user_id, builder_view, channels))


class ChannelSearchModal(Modal, title="Find Channel"):
    query_input = TextInput(
        label="Channel search",
        placeholder="giveaway, events, announcements...",
        required=True,
        max_length=100,
    )

    def __init__(self, cog: "Giveaway", guild: discord.Guild, user_id: int, builder_view: "GiveawayBuilderView"):
        super().__init__()
        self.cog = cog
        self.guild = guild
        self.user_id = user_id
        self.builder_view = builder_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        query = (self.query_input.value or "").strip()
        matches = self.cog._fuzzy_match_channels(self.guild, query, limit=24)
        if not matches:
            await interaction.response.send_message(
                "No matching channels found. Try a different search.",
                ephemeral=True,
            )
            return
        view = ChannelSelectView(self.cog, self.guild, self.user_id, self.builder_view, matches)
        await interaction.response.send_message(
            f"Showing {len(matches)} channel match(es). Select one:",
            view=view,
            ephemeral=True,
        )


class GiveawayBuilderView(View):
    """Interactive builder: buttons open modals; refresh updates the message like selfroles."""

    def __init__(self, cog: "Giveaway", guild: discord.Guild, user_id: int, edit_message_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild
        self.user_id = user_id
        self.edit_message_id = edit_message_id
        self.message: Optional[discord.Message] = None
        if edit_message_id is not None:
            for child in list(self.children):
                if getattr(child, "label", None) == "Launch":
                    self.remove_item(child)
                    break
            save_btn = Button(label="Save", style=discord.ButtonStyle.success, emoji="\u2705", row=2)

            async def _save_callback(interaction: discord.Interaction):
                await self._save_edit(interaction)

            save_btn.callback = _save_callback
            self.add_item(save_btn)

    async def _save_edit(self, interaction: discord.Interaction):
        """Save draft to the existing giveaway (edit mode)."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        if not self.edit_message_id or not interaction.guild:
            await interaction.response.send_message("Edit session is invalid.", ephemeral=True)
            return
        draft = await self.cog._get_draft(interaction.guild.id, self.user_id)
        success = await self.cog._save_giveaway_from_draft(
            interaction, interaction.guild, self.user_id, self.edit_message_id, draft, builder_message=self.message
        )
        if success:
            self.stop()

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        """Refresh the builder display from draft (like selfroles RoleBuilderView.refresh)."""
        guild = interaction.guild if interaction else self.guild
        user_id = interaction.user.id if interaction else self.user_id
        if not guild:
            return
        draft = await self.cog._get_draft(guild.id, user_id)
        prizes = self.cog._prizes_list(draft)
        prizes_str = ", ".join(p[:40] for p in prizes[:5]) if prizes else "Not set"
        if len(prizes) > 5:
            prizes_str += f" (+{len(prizes) - 5} more)"
        winner_count = self.cog._winner_count(draft)
        duration_seconds = draft.get("duration_seconds") or 0
        duration_str = humanize_timedelta(seconds=duration_seconds) if duration_seconds else "Not set"
        emoji = draft.get("emoji") or "\U0001f389"
        claim_enabled = draft.get("claim_enabled", False)
        claim_seconds = draft.get("claim_seconds") or 0
        claim_str = humanize_timedelta(seconds=claim_seconds) if claim_seconds else "N/A"
        dm_winners = bool(draft.get("dm_winners", False))
        ch_id = draft.get("channel_id")
        channel_str = guild.get_channel(ch_id).mention if ch_id else "Current channel"
        thumbnail_url = draft.get("thumbnail_url")
        thumbnail_str = thumbnail_url if thumbnail_url else "Not set"
        if thumbnail_url and len(thumbnail_str) > 60:
            thumbnail_str = thumbnail_str[:57] + "..."
        image_url = draft.get("image_url")
        image_str = image_url if image_url else "Not set"
        if image_url and len(image_str) > 60:
            image_str = image_str[:57] + "..."

        is_editing = self.edit_message_id is not None
        embed = discord.Embed(
            title="\U0001f3aa Giveaway Builder (Editing)" if is_editing else "\U0001f3aa Giveaway Builder",
            description="Use the buttons below to update the giveaway." if is_editing else "Use the buttons below to configure your giveaway.",
            color=await self.cog.bot.get_embed_color(guild),
        )
        embed.add_field(
            name="\U0001f4dd Giveaway Configuration",
            value=(
                f"**Prizes:** {prizes_str[:500]}\n"
                f"**Winners:** {winner_count}\n"
                f"**Duration:** {duration_str}\n"
                f"**Emoji:** {emoji}\n"
                f"**Claim:** {'Yes' if claim_enabled else 'No'} ({claim_str})\n"
                f"**DM Winners:** {'Yes' if dm_winners else 'No'}\n"
                f"**Thumbnail:** {thumbnail_str}\n"
                f"**Image:** {image_str}\n"
                f"**Channel:** {channel_str}"
            ),
            inline=False,
        )

        try:
            if interaction:
                if interaction.response.is_done():
                    if self.message:
                        await self.message.edit(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)
            elif self.message:
                await self.message.edit(embed=embed, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Prizes", style=discord.ButtonStyle.primary, emoji="\U0001f389", row=0)
    async def set_prize(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        modal = SetPrizeModal(self.cog, self.guild.id, self.user_id)
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Winners", style=discord.ButtonStyle.primary, emoji="\U0001f3c6", row=0)
    async def set_winners(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        modal = SetWinnersModal(self.cog, self.guild.id, self.user_id)
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Duration", style=discord.ButtonStyle.primary, emoji="\u23f1\ufe0f", row=0)
    async def set_duration(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        view = DurationSelectView(self.cog, self.guild.id, self.user_id, self)
        await interaction.response.send_message(
            "Select a duration for the giveaway:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Channel", style=discord.ButtonStyle.primary, emoji="#\u20e3", row=0)
    async def set_channel(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("Could not resolve the server.", ephemeral=True)
            return
        modal = ChannelSearchModal(self.cog, interaction.guild, self.user_id, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, emoji="\u2705", row=1)
    async def set_claim(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        view = ClaimSelectView(self.cog, self.guild.id, self.user_id, self)
        await interaction.response.send_message(
            "Select claim settings:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="DM", style=discord.ButtonStyle.secondary, emoji="\U0001f4e8", row=1)
    async def set_winner_dm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        draft = await self.cog._get_draft(self.guild.id, self.user_id)
        dm_winners = bool(draft.get("dm_winners", False))
        new_state = not dm_winners
        await self.cog._set_draft_field(self.guild.id, self.user_id, "dm_winners", new_state)
        await self.refresh(interaction)
        await interaction.followup.send(
            f"Winner DM is now {'enabled' if new_state else 'disabled'}.",
            ephemeral=True,
        )

    @discord.ui.button(label="Emoji", style=discord.ButtonStyle.secondary, emoji="\U0001f4ac", row=1)
    async def set_emoji(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        draft = await self.cog._get_draft(self.guild.id, self.user_id)
        modal = SetEmojiModal(self.cog, self.guild.id, self.user_id, draft.get("emoji") or "\U0001f389")
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Images", style=discord.ButtonStyle.secondary, emoji="\U0001f5bc\ufe0f", row=1)
    async def set_images(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        draft = await self.cog._get_draft(self.guild.id, self.user_id)
        modal = SetImagesModal(
            self.cog,
            self.guild.id,
            self.user_id,
            thumbnail_default=draft.get("thumbnail_url") or "",
            image_default=draft.get("image_url") or "",
        )
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="\U0001f441\ufe0f", row=2)
    async def preview(self, interaction: discord.Interaction, button: Button):
        """Preview the giveaway as it will appear when launched."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        draft = await self.cog._get_draft(self.guild.id, self.user_id)
        prizes = self.cog._prizes_list(draft)
        if not prizes:
            await interaction.response.send_message(
                "Set at least one prize to preview.",
                ephemeral=True,
            )
            return
        duration_seconds = draft.get("duration_seconds") or 0
        if duration_seconds < 60:
            await interaction.response.send_message(
                "Set a duration of at least 1 minute to preview.",
                ephemeral=True,
            )
            return
        end_ts = time.time() + duration_seconds
        emoji = draft.get("emoji") or "\U0001f389"
        description = draft.get("description")
        winner_count = self.cog._winner_count(draft)
        claim_seconds = int(draft.get("claim_seconds") or 0)
        preview_embed = await self.cog._make_giveaway_embed(
            self.guild,
            prizes,
            description,
            end_ts,
            emoji,
            0,
            self.user_id,
            "active",
            winner_count=winner_count,
            claim_seconds=claim_seconds,
            thumbnail_url=draft.get("thumbnail_url"),
            image_url=draft.get("image_url"),
        )
        await interaction.response.send_message(
            "Preview of your giveaway:",
            embed=preview_embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Launch", style=discord.ButtonStyle.success, emoji="\u27a1\ufe0f", row=2)
    async def launch(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        draft = await self.cog._get_draft(self.guild.id, self.user_id)
        prizes = self.cog._prizes_list(draft)
        duration_seconds = draft.get("duration_seconds")
        if not prizes:
            await interaction.response.send_message("Set at least one prize first.", ephemeral=True)
            return
        if not duration_seconds or duration_seconds < 60:
            await interaction.response.send_message("Set a duration of at least 1 minute first.", ephemeral=True)
            return
        channel_id = draft.get("channel_id")
        channel = self.guild.get_channel(channel_id) if channel_id else interaction.channel
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Invalid channel; use current or set a valid channel.", ephemeral=True)
            return
        await self.cog._launch_from_draft(
            interaction, self.guild, self.user_id, channel, draft, builder_message=self.message
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="\u274c", row=2)
    async def cancel_builder(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who started the builder can do this.", ephemeral=True)
            return
        await self.cog._clear_draft(self.guild.id, self.user_id)
        await interaction.response.send_message("Giveaway builder cancelled.", ephemeral=True)
        self.stop()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass


class Giveaway(commands.Cog):
    """Reaction-based giveaways with optional claim system."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x5A17A7A, force_registration=True)
        default_guild = {
            "giveaways": {},  # message_id -> giveaway_data
            "giveaway_drafts": {},  # user_id -> draft_data
        }
        self.config.register_guild(**default_guild)
        self._end_tasks: Dict[_TaskKey, asyncio.Task] = {}
        log.info("Giveaway cog initialized")

    async def _get_draft(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {}
        drafts = await self.config.guild(guild).giveaway_drafts()
        return drafts.get(str(user_id), {}).copy()

    async def _set_draft_field(self, guild_id: int, user_id: int, key: str, value: Any):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        drafts = await self.config.guild(guild).giveaway_drafts()
        entry = drafts.get(str(user_id), {})
        entry[key] = value
        drafts[str(user_id)] = entry
        await self.config.guild(guild).giveaway_drafts.set(drafts)

    async def _clear_draft(self, guild_id: int, user_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        drafts = await self.config.guild(guild).giveaway_drafts()
        drafts.pop(str(user_id), None)
        await self.config.guild(guild).giveaway_drafts.set(drafts)

    async def _load_giveaway_into_draft(self, guild_id: int, user_id: int, message_id: int) -> bool:
        """Load an active giveaway's data into the user's draft. Returns True on success."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(message_id))
        if not data or data.get("status") != "active":
            return False
        end_ts = data.get("end_ts") or 0
        duration_seconds = max(60, int(end_ts - time.time()))
        prizes = self._prizes_list(data)
        draft = {
            "prizes": prizes,
            "prize": prizes[0] if prizes else "",
            "winner_count": self._winner_count(data),
            "duration_seconds": duration_seconds,
            "emoji": data.get("emoji") or "\U0001f389",
            "claim_enabled": bool(data.get("claim_enabled")),
            "claim_seconds": int(data.get("claim_seconds") or 0),
            "dm_winners": bool(data.get("dm_winners", False)),
            "channel_id": data.get("channel_id"),
            "description": data.get("description"),
            "thumbnail_url": data.get("thumbnail_url"),
            "image_url": data.get("image_url"),
        }
        drafts = await self.config.guild(guild).giveaway_drafts()
        drafts[str(user_id)] = draft
        await self.config.guild(guild).giveaway_drafts.set(drafts)
        return True

    async def _can_manage(self, user: discord.Member, host_id: int) -> bool:
        if user.id == host_id:
            return True
        return user.guild_permissions.manage_guild

    def _task_key(self, guild_id: int, message_id: int) -> Tuple[int, int]:
        return (guild_id, message_id)

    def _cancel_tasks_for(self, guild_id: int, message_id: int):
        for key in (self._task_key(guild_id, message_id), (guild_id, message_id, "claim")):
            task = self._end_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()

    def _schedule_end_task(self, guild_id: int, message_id: int, delay: float):
        self._cancel_tasks_for(guild_id, message_id)
        async def run():
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._end_giveaway_task(guild_id, message_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._end_tasks.pop(self._task_key(guild_id, message_id), None)

        self._end_tasks[self._task_key(guild_id, message_id)] = self.bot.loop.create_task(run())

    async def _add_reaction_safe(self, message: discord.Message, emoji: str):
        try:
            await message.add_reaction(emoji)
        except (discord.HTTPException, discord.InvalidArgument) as e:
            log.warning("Could not add reaction %s to message %s: %s", emoji, message.id, e)

    @staticmethod
    def _prizes_list(data: Dict[str, Any]) -> List[str]:
        """Return list of prizes from giveaway/draft data (handles legacy single prize)."""
        prizes = data.get("prizes")
        if prizes and isinstance(prizes, list):
            return [str(p).strip() for p in prizes if str(p).strip()]
        p = data.get("prize")
        if p and str(p).strip():
            return [str(p).strip()]
        return []

    @staticmethod
    def _winner_count(data: Dict[str, Any]) -> int:
        """Return winner count (default 1)."""
        n = data.get("winner_count")
        if n is not None and isinstance(n, int) and n >= 1:
            return min(n, 50)
        return 1

    @staticmethod
    def _winner_ids_list(data: Dict[str, Any]) -> List[int]:
        """Return list of winner user ids (handles legacy winner_id)."""
        ids = data.get("winner_ids")
        if ids and isinstance(ids, list):
            return [int(x) for x in ids if x is not None]
        wid = data.get("winner_id")
        if wid is not None:
            return [int(wid)]
        return []

    @staticmethod
    def _fuzzy_match_channels(
        guild: discord.Guild,
        query: str,
        *,
        limit: int = 24,
    ) -> List[discord.TextChannel]:
        text_channels = [c for c in guild.text_channels if c.id is not None]
        if not query:
            return text_channels[:limit]
        q = query.strip().lower()
        if not q:
            return text_channels[:limit]
        scored: List[Tuple[float, discord.TextChannel]] = []
        for ch in text_channels:
            name = ch.name.lower()
            if name == q:
                score = 1.0
            elif q in name:
                score = 0.92
            else:
                score = difflib.SequenceMatcher(a=q, b=name).ratio()
            if score >= 0.35:
                scored.append((score, ch))
        scored.sort(key=lambda item: (-item[0], item[1].position))
        return [channel for _, channel in scored[:limit]]

    def _format_prizes_field(self, prizes: List[str]) -> str:
        if not prizes:
            return "None"
        prizes_text = "\n".join(f"• {p[:200]}" for p in prizes[:10])
        if len(prizes) > 10:
            prizes_text += f"\n• ... and {len(prizes) - 10} more"
        return prizes_text[:1024]

    @staticmethod
    def _winner_mentions(guild: discord.Guild, winner_ids: List[int]) -> List[str]:
        mentions: List[str] = []
        for wid in winner_ids:
            member = guild.get_member(wid)
            mentions.append(member.mention if member else f"<@{wid}>")
        return mentions

    @staticmethod
    def _claim_followup_text(giveaway_data: Dict[str, Any]) -> str:
        if giveaway_data.get("dm_winners"):
            return " Check your DMs to claim your prize."
        return " Claim your prize using the button on the giveaway message above."

    @staticmethod
    def _chunk_winner_announcement(
        mentions: List[str],
        *,
        prefix: str,
        end_punct: str = "!",
        suffix: str = "",
        limit: int = 2000,
    ) -> List[str]:
        """Build one or more announcement messages; splits only if over the character limit."""
        if not mentions:
            return []
        full = f"{prefix}{', '.join(mentions)}{end_punct}{suffix}"
        if len(full) <= limit:
            return [full]

        chunks: List[str] = []
        current_parts: List[str] = []
        is_first_chunk = True

        for i, mention in enumerate(mentions):
            is_last = i == len(mentions) - 1
            trial_parts = current_parts + [mention]
            body = ", ".join(trial_parts)
            text = f"{prefix}{body}" if is_first_chunk else body
            if is_last:
                text = f"{text}{end_punct}{suffix}"

            if len(text) <= limit:
                current_parts.append(mention)
                continue

            if current_parts:
                body = ", ".join(current_parts)
                chunks.append(f"{prefix}{body}" if is_first_chunk else body)
                is_first_chunk = False
                current_parts = [mention]
            else:
                # Extremely long single token; hard-truncate rather than fail.
                overflow = f"{prefix}{mention}" if is_first_chunk else mention
                if is_last:
                    overflow = f"{overflow}{end_punct}{suffix}"
                chunks.append(overflow[:limit])
                is_first_chunk = False
                current_parts = []

        if current_parts:
            body = ", ".join(current_parts)
            text = f"{prefix}{body}" if is_first_chunk else body
            chunks.append(f"{text}{end_punct}{suffix}")
        return chunks

    async def _send_winner_announcement(
        self,
        channel: discord.abc.Messageable,
        guild: discord.Guild,
        winner_ids: List[int],
        *,
        prefix: str,
        end_punct: str = "!",
        suffix: str = "",
    ):
        mentions = self._winner_mentions(guild, winner_ids)
        for chunk in self._chunk_winner_announcement(
            mentions,
            prefix=prefix,
            end_punct=end_punct,
            suffix=suffix,
        ):
            await channel.send(chunk)

    async def _dm_winners(
        self,
        guild: discord.Guild,
        channel: Optional[discord.TextChannel],
        message_id: int,
        winner_ids: List[int],
        giveaway_data: Dict[str, Any],
        *,
        is_reroll: bool = False,
    ):
        if not giveaway_data.get("dm_winners") or not winner_ids:
            return
        prize_names = self._prizes_list(giveaway_data)
        claim_enabled = bool(giveaway_data.get("claim_enabled") and giveaway_data.get("claim_seconds"))
        claim_seconds = int(giveaway_data.get("claim_seconds") or 0)
        color = await self.bot.get_embed_color(guild)
        for winner_id in winner_ids:
            member = guild.get_member(winner_id)
            if not member:
                continue
            if is_reroll:
                description = f"You have been re-rolled as a winner in **{guild.name}**."
            else:
                description = f"You won a giveaway in **{guild.name}**!"
            embed = discord.Embed(
                title="You Won!",
                description=description,
                color=color,
            )
            embed.add_field(name="Prizes", value=self._format_prizes_field(prize_names), inline=False)
            view = None
            if claim_enabled:
                embed.add_field(
                    name="Claim",
                    value=(
                        f"Click **Claim** below within {humanize_timedelta(seconds=claim_seconds)}. "
                        "If you do not claim in time, your prize will be re-rolled."
                    ),
                    inline=False,
                )
                # Same custom_id as the channel Claim button; the persistent view already handles it.
                view = ClaimView(self, guild.id, message_id)
            if channel:
                jump_url = f"https://discord.com/channels/{guild.id}/{channel.id}/{message_id}"
                embed.add_field(name="Giveaway", value=jump_url, inline=False)
            else:
                embed.set_footer(text=f"Hosted in {guild.name}")
            try:
                await member.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def _make_giveaway_embed(
        self,
        guild: discord.Guild,
        prizes: List[str],
        description: Optional[str],
        end_ts: float,
        emoji: str,
        entries_count: int,
        host_id: int,
        status: str,
        winner_count: int = 1,
        winner_ids: Optional[List[int]] = None,
        claimed_winner_ids: Optional[List[int]] = None,
        claim_deadline_ts: Optional[float] = None,
        claim_seconds: int = 0,
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None,
    ) -> discord.Embed:
        winner_ids = winner_ids or []
        claimed_winner_ids = claimed_winner_ids or []
        title = "Giveaway"
        color = await self.bot.get_embed_color(guild)
        if status == "cancelled":
            embed = discord.Embed(
                title="Giveaway Cancelled",
                description="This giveaway has been cancelled.",
                color=discord.Color.dark_gray(),
            )
            return embed
        if status == "active":
            base_desc = description or f"React with {emoji} to enter!"
            if claim_seconds > 0:
                claim_note = (
                    f"\n\n**Winners:** You must claim your prize by clicking the Claim button within "
                    f"{humanize_timedelta(seconds=claim_seconds)} of the giveaway ending. "
                    "If you do not claim in time, your prize will be re-rolled to another entrant."
                )
                base_desc = base_desc + claim_note
            embed = discord.Embed(
                title=title,
                description=base_desc[:4096],
                color=color,
            )
            embed.add_field(name="Prizes", value=self._format_prizes_field(prizes), inline=False)
            embed.add_field(name="Ends", value=f"<t:{int(end_ts)}:R>", inline=True)
            embed.add_field(name="Winners", value=str(winner_count), inline=True)
            host = guild.get_member(host_id)
            embed.set_footer(text=f"Hosted by {host.display_name if host else 'Unknown'} | React with {emoji} to enter")
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            if image_url:
                embed.set_image(url=image_url)
            return embed
        # ended or claimed
        embed = discord.Embed(
            title=title,
            description=description or "Giveaway ended.",
            color=color,
        )
        embed.add_field(name="Prizes", value=self._format_prizes_field(prizes), inline=False)
        if winner_ids:
            winner_mentions = []
            for wid in winner_ids:
                m = guild.get_member(wid)
                winner_mentions.append(m.mention if m else f"<@{wid}>")
            winners_value = "\n".join(winner_mentions) if len(winner_mentions) <= 10 else ", ".join(winner_mentions[:10]) + f" (+{len(winner_mentions) - 10} more)"
            embed.add_field(name="Winner" + ("s" if len(winner_ids) != 1 else ""), value=winners_value[:1024], inline=True)
            if claim_seconds > 0:
                status_lines = []
                for wid in winner_ids:
                    status_lines.append("Claimed" if wid in claimed_winner_ids else "Unclaimed")
                status_value = "\n".join(status_lines) if len(status_lines) <= 10 else "\n".join(status_lines[:10]) + f"\n(+{len(status_lines) - 10} more)"
                embed.add_field(name="Claim status", value=status_value[:1024], inline=True)
            if status == "ended" and claim_deadline_ts and claim_seconds:
                embed.set_footer(
                    text=(
                        f"Winners: You must claim your prize by clicking the button below within {humanize_timedelta(seconds=claim_seconds)}. "
                        "If you do not claim in time, your prize will be re-rolled to another entrant."
                    )
                )
            elif status == "claimed":
                embed.set_footer(text="All prizes have been claimed.")
        else:
            embed.add_field(name="Winner" + ("s" if winner_count != 1 else ""), value="No valid entries.", inline=True)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if image_url:
            embed.set_image(url=image_url)
        return embed

    async def _end_giveaway_task(self, guild_id: int, message_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(message_id))
        if not data or data.get("status") != "active":
            return
        channel_id = data.get("channel_id")
        channel = guild.get_channel(channel_id)
        if not channel:
            async with self.config.guild(guild).giveaways() as gws:
                gws.pop(str(message_id), None)
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            async with self.config.guild(guild).giveaways() as gws:
                gws.pop(str(message_id), None)
            return
        entries = data.get("entries") or []
        winner_count = self._winner_count(data)
        winner_ids = []
        if entries:
            k = min(winner_count, len(entries))
            winner_ids = random.sample(entries, k)
        prizes = self._prizes_list(data)
        async with self.config.guild(guild).giveaways() as gws:
            g = gws.get(str(message_id))
            if not g or g.get("status") != "active":
                return
            g["status"] = "ended"
            g["winner_id"] = winner_ids[0] if winner_ids else None
            g["winner_ids"] = winner_ids
            g["claimed_winner_ids"] = []
            g["entries"] = entries
            claim_enabled = g.get("claim_enabled", False)
            claim_seconds = g.get("claim_seconds") or 0
            if claim_enabled and claim_seconds and winner_ids:
                g["claim_deadline_ts"] = time.time() + claim_seconds
            else:
                g["claim_deadline_ts"] = None
        embed = await self._make_giveaway_embed(
            guild,
            prizes,
            data.get("description"),
            data["end_ts"],
            data.get("emoji", "\U0001f389"),
            len(entries),
            data["host_id"],
            "ended",
            winner_count=winner_count,
            winner_ids=winner_ids,
            claimed_winner_ids=[],
            claim_deadline_ts=time.time() + claim_seconds if claim_enabled and claim_seconds and winner_ids else None,
            claim_seconds=claim_seconds,
            thumbnail_url=data.get("thumbnail_url"),
            image_url=data.get("image_url"),
        )
        view = None
        if claim_enabled and claim_seconds and winner_ids:
            view = ClaimView(self, guild_id, message_id)
            self.bot.add_view(view, message_id=message_id)
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass
        if winner_ids:
            try:
                prefix = (
                    "Congratulations to the winners: "
                    if len(winner_ids) != 1
                    else "Congratulations to the winner: "
                )
                suffix = self._claim_followup_text(data) if claim_enabled else ""
                await self._send_winner_announcement(
                    channel,
                    guild,
                    winner_ids,
                    prefix=prefix,
                    end_punct="!",
                    suffix=suffix,
                )
            except (discord.HTTPException, discord.Forbidden):
                pass
            await self._dm_winners(guild, channel, message_id, winner_ids, data)
        if claim_enabled and claim_seconds and winner_ids:
            delay = claim_seconds
            self._schedule_claim_task_impl(guild_id, message_id, delay)

    def _schedule_claim_task_impl(self, guild_id: int, message_id: int, delay: float):
        key = (guild_id, message_id, "claim")
        old = self._end_tasks.get(key)
        if old and not old.done():
            old.cancel()
        async def run():
            try:
                await asyncio.sleep(delay)
                await self._claim_timeout_task(guild_id, message_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._end_tasks.pop((guild_id, message_id, "claim"), None)
        self._end_tasks[key] = self.bot.loop.create_task(run())

    def _schedule_claim_task(self, guild_id: int, message_id: int, delay: float):
        self._schedule_claim_task_impl(guild_id, message_id, delay)

    async def _claim_timeout_task(self, guild_id: int, message_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(message_id))
        if not data or data.get("status") != "ended":
            return
        claimed_ids = [int(x) for x in (data.get("claimed_winner_ids") or [])]
        # Preserve claimed winners in their original order.
        winner_ids = self._winner_ids_list(data)
        claimed_ids = [wid for wid in winner_ids if wid in claimed_ids]
        if len(claimed_ids) >= len(winner_ids):
            return

        unclaimed_count = len(winner_ids) - len(claimed_ids)
        entries = data.get("entries") or []
        # Do not re-pick anyone who currently holds a winner seat (claimed or unclaimed).
        pool = [u for u in entries if u not in winner_ids]
        replacements = []
        if pool and unclaimed_count > 0:
            replacements = random.sample(pool, min(unclaimed_count, len(pool)))

        new_winner_ids = claimed_ids + replacements
        claim_seconds = int(data.get("claim_seconds") or 0)
        has_unclaimed = bool(replacements)
        claim_deadline_ts = (time.time() + claim_seconds) if has_unclaimed and claim_seconds > 0 else None
        status = "claimed" if new_winner_ids and not has_unclaimed else "ended"
        claimed = status == "claimed"

        async with self.config.guild(guild).giveaways() as gws:
            g = gws.get(str(message_id))
            if not g or g.get("status") != "ended":
                return
            g["winner_id"] = new_winner_ids[0] if new_winner_ids else None
            g["winner_ids"] = new_winner_ids
            g["claimed_winner_ids"] = list(claimed_ids)
            g["claim_deadline_ts"] = claim_deadline_ts
            g["status"] = status
            g["claimed"] = claimed

        channel_id = data.get("channel_id")
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        prizes = self._prizes_list(data)
        embed = await self._make_giveaway_embed(
            guild,
            prizes,
            data.get("description"),
            data["end_ts"],
            data.get("emoji", "\U0001f389"),
            len(entries),
            data["host_id"],
            status,
            winner_count=max(len(new_winner_ids), 1),
            winner_ids=new_winner_ids,
            claimed_winner_ids=claimed_ids,
            claim_deadline_ts=claim_deadline_ts,
            claim_seconds=claim_seconds if has_unclaimed else 0,
            thumbnail_url=data.get("thumbnail_url"),
            image_url=data.get("image_url"),
        )
        view = None
        if has_unclaimed:
            view = ClaimView(self, guild_id, message_id)
            self.bot.add_view(view, message_id=message_id)
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

        forfeited_slots = unclaimed_count - len(replacements)
        try:
            if replacements:
                prefix = (
                    "Prize re-rolled. New winners: "
                    if len(replacements) != 1
                    else "Prize re-rolled. New winner: "
                )
                await self._send_winner_announcement(
                    channel,
                    guild,
                    replacements,
                    prefix=prefix,
                    end_punct=".",
                    suffix=self._claim_followup_text(data),
                )
                await self._dm_winners(guild, channel, message_id, replacements, data, is_reroll=True)
            if forfeited_slots > 0:
                if not claimed_ids and not replacements:
                    forfeit_msg = (
                        "Claim window expired. The prize was not claimed and there were "
                        "no alternate entrants to re-roll to."
                    )
                elif claimed_ids and not replacements:
                    forfeit_msg = (
                        "Claim window expired. Unclaimed prize(s) were forfeited "
                        "(no alternate entrants remaining)."
                    )
                else:
                    forfeit_msg = (
                        f"{forfeited_slots} unclaimed prize(s) were forfeited "
                        "(not enough alternate entrants remaining)."
                    )
                await channel.send(forfeit_msg)
        except (discord.HTTPException, discord.Forbidden):
            pass

        if has_unclaimed and claim_seconds > 0:
            self._schedule_claim_task_impl(guild_id, message_id, claim_seconds)

    async def _handle_claim_click(self, interaction: discord.Interaction, guild_id: int, message_id: int):
        guild = self.bot.get_guild(guild_id) or interaction.guild
        if not guild:
            await interaction.response.send_message("Could not find the server for this giveaway.", ephemeral=True)
            return
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(message_id))
        if not data or data.get("status") != "ended":
            await interaction.response.send_message("This giveaway is no longer active.", ephemeral=True)
            return
        deadline = data.get("claim_deadline_ts")
        if deadline is not None and time.time() > float(deadline):
            await interaction.response.send_message("The claim window has expired.", ephemeral=True)
            return
        winner_ids = self._winner_ids_list(data)
        claimed_winner_ids = data.get("claimed_winner_ids") or []
        if interaction.user.id not in winner_ids:
            await interaction.response.send_message("Only a winner can claim.", ephemeral=True)
            return
        if interaction.user.id in claimed_winner_ids:
            await interaction.response.send_message("You have already claimed.", ephemeral=True)
            return
        async with self.config.guild(guild).giveaways() as gws:
            g = gws.get(str(message_id))
            if not g:
                await interaction.response.send_message("Giveaway no longer found.", ephemeral=True)
                return
            # Re-check deadline inside the lock against a possible timeout race.
            deadline = g.get("claim_deadline_ts")
            if deadline is not None and time.time() > float(deadline):
                await interaction.response.send_message("The claim window has expired.", ephemeral=True)
                return
            if g.get("status") != "ended":
                await interaction.response.send_message("This giveaway is no longer active.", ephemeral=True)
                return
            current_winners = self._winner_ids_list(g)
            if interaction.user.id not in current_winners:
                await interaction.response.send_message("Only a winner can claim.", ephemeral=True)
                return
            g["claimed_winner_ids"] = g.get("claimed_winner_ids") or []
            if interaction.user.id in g["claimed_winner_ids"]:
                await interaction.response.send_message("You have already claimed.", ephemeral=True)
                return
            g["claimed_winner_ids"].append(interaction.user.id)
            if len(g["claimed_winner_ids"]) >= len(current_winners):
                g["status"] = "claimed"
                g["claimed"] = True
        key = (guild.id, message_id, "claim")
        task = self._end_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        # If claimed from DM and not all winners have claimed yet, keep scheduling for remaining.
        data_after = await self.config.guild(guild).giveaways()
        d = data_after.get(str(message_id)) or data
        claimed_ids = d.get("claimed_winner_ids") or []
        winner_ids_updated = self._winner_ids_list(d)
        if d.get("status") == "ended" and d.get("claim_enabled") and d.get("claim_deadline_ts"):
            remaining = max(0.0, float(d["claim_deadline_ts"]) - time.time())
            if remaining > 0 and len(claimed_ids) < len(winner_ids_updated):
                self._schedule_claim_task_impl(guild.id, message_id, remaining)
        channel = guild.get_channel(data["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                prizes = self._prizes_list(d)
                claim_deadline_ts = d.get("claim_deadline_ts")
                claim_seconds = d.get("claim_seconds") or 0
                embed = await self._make_giveaway_embed(
                    guild,
                    prizes,
                    d.get("description"),
                    d["end_ts"],
                    d.get("emoji", "\U0001f389"),
                    len(d.get("entries") or []),
                    d["host_id"],
                    "claimed" if d.get("status") == "claimed" else "ended",
                    winner_count=len(winner_ids_updated),
                    winner_ids=winner_ids_updated,
                    claimed_winner_ids=claimed_ids,
                    claim_deadline_ts=claim_deadline_ts if d.get("status") == "ended" else None,
                    claim_seconds=claim_seconds,
                    thumbnail_url=d.get("thumbnail_url"),
                    image_url=d.get("image_url"),
                )
                if d.get("status") == "claimed":
                    await msg.edit(embed=embed, view=None)
                else:
                    await msg.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                pass
        await interaction.response.send_message("You have claimed your prize!", ephemeral=True)
        # Disable the Claim button on the DM message if they claimed from there.
        if interaction.message and interaction.guild is None:
            try:
                await interaction.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _launch_from_draft(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        user_id: int,
        channel: discord.TextChannel,
        draft: Dict[str, Any],
        builder_message: Optional[discord.Message] = None,
    ):
        prizes = self._prizes_list(draft)
        if not prizes:
            await interaction.response.send_message("At least one prize is required.", ephemeral=True)
            return
        duration_seconds = int(draft.get("duration_seconds") or 0)
        description = draft.get("description") or None
        if description:
            description = description.strip() or None
        emoji = draft.get("emoji") or "\U0001f389"
        claim_enabled = bool(draft.get("claim_enabled"))
        claim_seconds = int(draft.get("claim_seconds") or 0)
        dm_winners = bool(draft.get("dm_winners", False))
        winner_count = self._winner_count(draft)
        thumbnail_url = draft.get("thumbnail_url") or None
        image_url = draft.get("image_url") or None
        end_ts = time.time() + duration_seconds
        embed = await self._make_giveaway_embed(
            guild, prizes, description, end_ts, emoji, 0, user_id, "active",
            winner_count=winner_count, claim_seconds=claim_seconds,
            thumbnail_url=thumbnail_url, image_url=image_url,
        )
        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"Failed to send giveaway: {e}", ephemeral=True)
            return
        await self._add_reaction_safe(message, emoji)
        giveaway_data = {
            "channel_id": channel.id,
            "message_id": message.id,
            "host_id": user_id,
            "prize": prizes[0],
            "prizes": prizes,
            "description": description,
            "end_ts": end_ts,
            "emoji": emoji,
            "entries": [],
            "winner_id": None,
            "winner_ids": [],
            "winner_count": winner_count,
            "claimed_winner_ids": [],
            "status": "active",
            "claim_enabled": claim_enabled,
            "claim_seconds": claim_seconds,
            "dm_winners": dm_winners,
            "thumbnail_url": thumbnail_url,
            "image_url": image_url,
            "claim_deadline_ts": None,
            "claimed": False,
        }
        async with self.config.guild(guild).giveaways() as gws:
            gws[str(message.id)] = giveaway_data
        await self._clear_draft(guild.id, user_id)
        delay = max(0.0, end_ts - time.time())
        self._schedule_end_task(guild.id, message.id, delay)
        if interaction.response.is_done():
            await interaction.followup.send(
                f"Giveaway started: {message.jump_url}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Giveaway started: {message.jump_url}",
                ephemeral=True,
            )
        if builder_message:
            try:
                await builder_message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

    async def _save_giveaway_from_draft(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        user_id: int,
        message_id: int,
        draft: Dict[str, Any],
        builder_message: Optional[discord.Message] = None,
    ) -> bool:
        """Apply draft to an existing active giveaway. Returns True on success."""
        prizes = self._prizes_list(draft)
        if not prizes:
            await interaction.response.send_message("At least one prize is required.", ephemeral=True)
            return False
        duration_seconds = int(draft.get("duration_seconds") or 0)
        if duration_seconds < 60:
            await interaction.response.send_message("Duration must be at least 1 minute.", ephemeral=True)
            return False
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(message_id))
        if not data or data.get("status") != "active":
            await interaction.response.send_message("That giveaway is no longer active.", ephemeral=True)
            return False
        description = (draft.get("description") or "").strip() or None
        emoji = draft.get("emoji") or "\U0001f389"
        winner_count = self._winner_count(draft)
        claim_enabled = bool(draft.get("claim_enabled"))
        claim_seconds = int(draft.get("claim_seconds") or 0)
        dm_winners = bool(draft.get("dm_winners", False))
        thumbnail_url = draft.get("thumbnail_url") or None
        image_url = draft.get("image_url") or None
        end_ts = time.time() + duration_seconds
        entries = data.get("entries") or []
        channel_id = draft.get("channel_id")
        target_channel = guild.get_channel(channel_id) if channel_id else interaction.channel
        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("Invalid channel; use current or set a valid channel.", ephemeral=True)
            return False
        current_channel_id = data.get("channel_id")
        embed = await self._make_giveaway_embed(
            guild, prizes, description, end_ts, emoji, len(entries), data["host_id"], "active",
            winner_count=winner_count, claim_seconds=claim_seconds,
            thumbnail_url=thumbnail_url, image_url=image_url,
        )
        if target_channel.id == current_channel_id:
            async with self.config.guild(guild).giveaways() as gws:
                g = gws.get(str(message_id))
                if not g or g.get("status") != "active":
                    await interaction.response.send_message("Giveaway was modified or ended.", ephemeral=True)
                    return False
                g["prize"] = prizes[0]
                g["prizes"] = prizes
                g["description"] = description
                g["end_ts"] = end_ts
                g["emoji"] = emoji
                g["winner_count"] = winner_count
                g["claim_enabled"] = claim_enabled
                g["claim_seconds"] = claim_seconds
                g["dm_winners"] = dm_winners
                g["thumbnail_url"] = thumbnail_url
                g["image_url"] = image_url
            self._cancel_tasks_for(guild.id, message_id)
            self._schedule_end_task(guild.id, message_id, max(0.0, end_ts - time.time()))
            try:
                msg = await target_channel.fetch_message(message_id)
                await msg.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                pass
        else:
            try:
                old_ch = guild.get_channel(current_channel_id)
                if old_ch:
                    old_msg = await old_ch.fetch_message(message_id)
                    await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            try:
                new_msg = await target_channel.send(embed=embed)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"Failed to post giveaway in new channel: {e}", ephemeral=True)
                return False
            await self._add_reaction_safe(new_msg, emoji)
            async with self.config.guild(guild).giveaways() as gws:
                gws.pop(str(message_id), None)
                gws[str(new_msg.id)] = {
                    "channel_id": target_channel.id,
                    "message_id": new_msg.id,
                    "host_id": data["host_id"],
                    "prize": prizes[0],
                    "prizes": prizes,
                    "description": description,
                    "end_ts": end_ts,
                    "emoji": emoji,
                    "entries": entries,
                    "winner_id": None,
                    "winner_ids": [],
                    "winner_count": winner_count,
                    "claimed_winner_ids": [],
                    "status": "active",
                    "claim_enabled": claim_enabled,
                    "claim_seconds": claim_seconds,
                    "dm_winners": dm_winners,
                    "thumbnail_url": thumbnail_url,
                    "image_url": image_url,
                    "claim_deadline_ts": None,
                    "claimed": False,
                }
            self._cancel_tasks_for(guild.id, message_id)
            delay = max(0.0, end_ts - time.time())
            self._schedule_end_task(guild.id, new_msg.id, delay)
        await self._clear_draft(guild.id, user_id)
        if interaction.response.is_done():
            await interaction.followup.send("Giveaway updated.", ephemeral=True)
        else:
            await interaction.response.send_message("Giveaway updated.", ephemeral=True)
        if builder_message:
            try:
                await builder_message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
        return True

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(payload.message_id))
        if not data or data.get("status") != "active":
            return
        if str(payload.emoji) != data.get("emoji", "\U0001f389"):
            return
        entries = data.get("entries") or []
        if payload.user_id in entries:
            return
        entries = list(entries)
        entries.append(payload.user_id)
        async with self.config.guild(guild).giveaways() as gws:
            g = gws.get(str(payload.message_id))
            if g and g.get("status") == "active":
                g["entries"] = entries

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        giveaways = await self.config.guild(guild).giveaways()
        data = giveaways.get(str(payload.message_id))
        if not data or data.get("status") != "active":
            return
        if str(payload.emoji) != data.get("emoji", "\U0001f389"):
            return
        entries = data.get("entries") or []
        if payload.user_id not in entries:
            return
        entries = [u for u in entries if u != payload.user_id]
        async with self.config.guild(guild).giveaways() as gws:
            g = gws.get(str(payload.message_id))
            if g and g.get("status") == "active":
                g["entries"] = entries

    async def cog_load(self):
        for guild in self.bot.guilds:
            giveaways = await self.config.guild(guild).giveaways()
            now = time.time()
            for mid, data in list(giveaways.items()):
                if data.get("status") == "active":
                    end_ts = data.get("end_ts") or 0
                    delay = max(0.0, end_ts - now)
                    self._schedule_end_task(guild.id, int(mid), delay)
                elif data.get("status") == "ended" and not data.get("claimed"):
                    if data.get("claim_enabled") and data.get("claim_deadline_ts"):
                        dl = data["claim_deadline_ts"]
                        view = ClaimView(self, guild.id, int(mid))
                        self.bot.add_view(view, message_id=int(mid))
                        delay = max(0.0, dl - now)
                        self._schedule_claim_task_impl(guild.id, int(mid), delay)

    async def cog_unload(self):
        for task in self._end_tasks.values():
            if not task.done():
                task.cancel()
        self._end_tasks.clear()

    def _resolve_message_id(self, ctx: commands.Context, message_id: Optional[int]) -> Optional[int]:
        if message_id is not None:
            return message_id
        if ctx.message.reference and ctx.message.reference.message_id:
            return ctx.message.reference.message_id
        return None

    @commands.group(name="giveaway", aliases=["gw"])
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context):
        """Run and manage giveaways."""
        pass

    @giveaway.command(name="create", aliases=["setup"])
    @commands.admin_or_permissions(manage_guild=True)
    async def create(self, ctx: commands.Context):
        """Start the interactive giveaway builder (like selfroles build)."""
        guild = ctx.guild
        if not guild:
            return
        view = GiveawayBuilderView(self, guild, ctx.author.id)
        # Build initial embed same format as refresh (one "Giveaway Configuration" field)
        draft = await self._get_draft(guild.id, ctx.author.id)
        prizes = self._prizes_list(draft)
        prizes_str = ", ".join(p[:40] for p in prizes[:5]) if prizes else "Not set"
        if len(prizes) > 5:
            prizes_str += f" (+{len(prizes) - 5} more)"
        winner_count = self._winner_count(draft)
        duration_seconds = draft.get("duration_seconds") or 0
        duration_str = humanize_timedelta(seconds=duration_seconds) if duration_seconds else "Not set"
        emoji = draft.get("emoji") or "\U0001f389"
        claim_enabled = draft.get("claim_enabled", False)
        claim_seconds = draft.get("claim_seconds") or 0
        claim_str = humanize_timedelta(seconds=claim_seconds) if claim_seconds else "N/A"
        dm_winners = bool(draft.get("dm_winners", False))
        ch_id = draft.get("channel_id")
        channel_str = guild.get_channel(ch_id).mention if ch_id else "Current channel"
        thumbnail_url = draft.get("thumbnail_url")
        thumbnail_str = thumbnail_url if thumbnail_url else "Not set"
        if thumbnail_url and len(thumbnail_str) > 60:
            thumbnail_str = thumbnail_str[:57] + "..."
        image_url = draft.get("image_url")
        image_str = image_url if image_url else "Not set"
        if image_url and len(image_str) > 60:
            image_str = image_str[:57] + "..."
        embed = discord.Embed(
            title="\U0001f3aa Giveaway Builder",
            description="Use the buttons below to configure your giveaway.",
            color=await self.bot.get_embed_color(guild),
        )
        embed.add_field(
            name="\U0001f4dd Giveaway Configuration",
            value=(
                f"**Prizes:** {prizes_str[:500]}\n"
                f"**Winners:** {winner_count}\n"
                f"**Duration:** {duration_str}\n"
                f"**Emoji:** {emoji}\n"
                f"**Claim:** {'Yes' if claim_enabled else 'No'} ({claim_str})\n"
                f"**DM Winners:** {'Yes' if dm_winners else 'No'}\n"
                f"**Thumbnail:** {thumbnail_str}\n"
                f"**Image:** {image_str}\n"
                f"**Channel:** {channel_str}"
            ),
            inline=False,
        )
        view.message = await ctx.send(embed=embed, view=view)

    def _parse_start_options(self, rest: str) -> Dict[str, Any]:
        """Parse optional flags from rest string."""
        rest = (rest or "").strip()
        options = {
            "prizes": [],
            "winners": 1,
            "channel_raw": None,
            "claim_seconds": 0,
            "emoji": "\U0001f389",
            "description": None,
            "dm_winners": False,
            "thumbnail_url": None,
            "image_url": None,
        }
        if not rest:
            return options
        parts = rest.split(" --")
        prize_part = parts[0].strip()
        if prize_part:
            options["prizes"] = _parse_prizes_list(prize_part)
        for i in range(1, len(parts)):
            segment = parts[i].strip()
            if not segment:
                continue
            if " " in segment:
                key, _, value = segment.partition(" ")
                key = key.lower().strip()
                value = value.strip()
            else:
                key = segment.lower()
                value = ""
            if key == "winners":
                try:
                    n = int(value)
                    options["winners"] = max(1, min(50, n))
                except ValueError:
                    pass
            elif key == "claim":
                if value:
                    try:
                        delta = parse_timedelta(
                            value,
                            minimum=timedelta(seconds=60),
                            maximum=timedelta(days=30),
                            allowed_units=["days", "hours", "minutes", "seconds"],
                        )
                        if delta:
                            options["claim_seconds"] = int(delta.total_seconds())
                    except Exception:
                        pass
            elif key == "channel":
                if value:
                    options["channel_raw"] = value
            elif key == "emoji":
                if value:
                    options["emoji"] = value[:100]
            elif key == "description":
                options["description"] = value or None
            elif key == "thumbnail":
                if value and (value.startswith("http://") or value.startswith("https://")):
                    options["thumbnail_url"] = value[:2000]
            elif key == "image":
                if value and (value.startswith("http://") or value.startswith("https://")):
                    options["image_url"] = value[:2000]
            elif key in ("dm", "dmwinners"):
                if not value:
                    options["dm_winners"] = True
                else:
                    options["dm_winners"] = value.lower() in ("yes", "y", "true", "1", "on")
        return options

    @giveaway.command(name="start")
    @commands.admin_or_permissions(manage_guild=True)
    async def start(
        self,
        ctx: commands.Context,
        duration: DurationConverter,
        *,
        rest: str,
    ):
        """Start a giveaway with optional flags.

        **Usage:** `[p]giveaway start <duration> <prize> [--winners N] [--channel #channel] [--claim 24h] [--dm yes] [--emoji EMOJI] [--thumbnail URL] [--image URL] [--description "text"]`

        **Examples:**
        - `[p]giveaway start 1d Nitro`
        - `[p]giveaway start 2h Game key --winners 3 --claim 24h`
        - `[p]giveaway start 1d Nitro, Hoodie --channel #giveaways --claim 48h`
        """
        if not rest or not rest.strip():
            await ctx.send("Please provide a prize. Example: `giveaway start 1d Nitro`")
            return
        if duration.total_seconds() < 60:
            await ctx.send("Duration must be at least 1 minute (e.g. 1h, 1d).")
            return
        opts = self._parse_start_options(rest)
        prizes_list = opts["prizes"]
        if not prizes_list:
            await ctx.send("Please provide at least one prize. Example: `giveaway start 1d Nitro` or `giveaway start 1d Prize1, Prize2`")
            return
        winner_count = opts["winners"]
        channel_raw = opts.get("channel_raw")
        if channel_raw:
            channel = None
            if channel_raw.startswith("<#") and channel_raw.endswith(">"):
                try:
                    cid = int(channel_raw[2:-1])
                    channel = ctx.guild.get_channel(cid)
                except ValueError:
                    pass
            if not channel:
                try:
                    cid = int(channel_raw)
                    channel = ctx.guild.get_channel(cid)
                except ValueError:
                    pass
            if not channel or not isinstance(channel, discord.TextChannel):
                await ctx.send("Invalid or missing channel. Use a channel mention or ID with `--channel`.")
                return
        else:
            channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("This command can only be used in a text channel.")
            return
        end_ts = time.time() + duration.total_seconds()
        emoji = opts["emoji"] or "\U0001f389"
        description = opts["description"]
        claim_seconds = opts["claim_seconds"]
        claim_enabled = claim_seconds >= 60
        dm_winners = bool(opts.get("dm_winners"))
        thumbnail_url = opts.get("thumbnail_url")
        image_url = opts.get("image_url")
        embed = await self._make_giveaway_embed(
            ctx.guild, prizes_list, description, end_ts, emoji, 0, ctx.author.id, "active",
            winner_count=winner_count, claim_seconds=claim_seconds,
            thumbnail_url=thumbnail_url, image_url=image_url,
        )
        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as e:
            await ctx.send(f"Failed to send giveaway: {e}")
            return
        await self._add_reaction_safe(message, emoji)
        giveaway_data = {
            "channel_id": channel.id,
            "message_id": message.id,
            "host_id": ctx.author.id,
            "prize": prizes_list[0],
            "prizes": prizes_list,
            "description": description,
            "end_ts": end_ts,
            "emoji": emoji,
            "entries": [],
            "winner_id": None,
            "winner_ids": [],
            "winner_count": winner_count,
            "claimed_winner_ids": [],
            "status": "active",
            "claim_enabled": claim_enabled,
            "claim_seconds": claim_seconds,
            "dm_winners": dm_winners,
            "thumbnail_url": thumbnail_url,
            "image_url": image_url,
            "claim_deadline_ts": None,
            "claimed": False,
        }
        async with self.config.guild(ctx.guild).giveaways() as gws:
            gws[str(message.id)] = giveaway_data
        delay = max(0.0, end_ts - time.time())
        self._schedule_end_task(ctx.guild.id, message.id, delay)
        await ctx.send(f"Giveaway started: {message.jump_url}")

    @giveaway.command(name="reroll")
    @commands.admin_or_permissions(manage_guild=True)
    async def reroll(self, ctx: commands.Context, message_id: Optional[int] = None):
        """Reroll the winner. Reply to the giveaway message or pass message_id."""
        mid = self._resolve_message_id(ctx, message_id)
        if mid is None:
            await ctx.send("Provide a message ID or reply to the giveaway message.")
            return
        giveaways = await self.config.guild(ctx.guild).giveaways()
        data = giveaways.get(str(mid))
        if not data:
            await ctx.send("That message is not a giveaway.")
            return
        if not await self._can_manage(ctx.author, data["host_id"]):
            await ctx.send("You cannot manage this giveaway.")
            return
        if data.get("status") not in ("ended", "claimed"):
            await ctx.send("You can only reroll an ended giveaway.")
            return
        entries = data.get("entries") or []
        prev_winner_ids = self._winner_ids_list(data)
        pool = [u for u in entries if u not in prev_winner_ids]
        if not pool:
            await ctx.send("No other entries to reroll.")
            return
        winner_count = self._winner_count(data)
        k = min(winner_count, len(pool))
        new_winner_ids = random.sample(pool, k)
        async with self.config.guild(ctx.guild).giveaways() as gws:
            g = gws.get(str(mid))
            if not g:
                return
            g["winner_id"] = new_winner_ids[0] if new_winner_ids else None
            g["winner_ids"] = new_winner_ids
            g["claimed_winner_ids"] = []
            g["status"] = "ended"
            g["claimed"] = False
            if g.get("claim_enabled") and g.get("claim_seconds"):
                g["claim_deadline_ts"] = time.time() + g["claim_seconds"]
        channel = ctx.guild.get_channel(data["channel_id"])
        prizes = self._prizes_list(data)
        if channel:
            try:
                msg = await channel.fetch_message(mid)
                embed = await self._make_giveaway_embed(
                    ctx.guild,
                    prizes,
                    data.get("description"),
                    data["end_ts"],
                    data.get("emoji", "\U0001f389"),
                    len(entries),
                    data["host_id"],
                    "ended",
                    winner_count=winner_count,
                    winner_ids=new_winner_ids,
                    claimed_winner_ids=[],
                    claim_deadline_ts=time.time() + data.get("claim_seconds", 0),
                    claim_seconds=data.get("claim_seconds", 0),
                    thumbnail_url=data.get("thumbnail_url"),
                    image_url=data.get("image_url"),
                )
                view = ClaimView(self, ctx.guild.id, mid)
                self.bot.add_view(view, message_id=mid)
                await msg.edit(embed=embed, view=view)
                if new_winner_ids and data.get("claim_enabled") and data.get("claim_seconds"):
                    try:
                        prefix = (
                            "Rerolled! New winners: "
                            if len(new_winner_ids) != 1
                            else "Rerolled! New winner: "
                        )
                        await self._send_winner_announcement(
                            channel,
                            ctx.guild,
                            new_winner_ids,
                            prefix=prefix,
                            end_punct=".",
                            suffix=self._claim_followup_text(data),
                        )
                    except (discord.HTTPException, discord.Forbidden):
                        pass
                await self._dm_winners(ctx.guild, channel, mid, new_winner_ids, data, is_reroll=True)
            except (discord.NotFound, discord.HTTPException):
                pass
        if data.get("claim_enabled") and data.get("claim_seconds"):
            self._schedule_claim_task(ctx.guild.id, mid, data["claim_seconds"])
        if len(new_winner_ids) == 1:
            w = ctx.guild.get_member(new_winner_ids[0])
            await ctx.send(f"Rerolled! New winner: {w.mention if w else new_winner_ids[0]}.")
        else:
            await ctx.send(f"Rerolled! New winners: {len(new_winner_ids)}.")

    @giveaway.command(name="end")
    @commands.admin_or_permissions(manage_guild=True)
    async def end_cmd(self, ctx: commands.Context, message_id: Optional[int] = None):
        """End a giveaway early. Reply to the message or pass message_id."""
        mid = self._resolve_message_id(ctx, message_id)
        if mid is None:
            await ctx.send("Provide a message ID or reply to the giveaway message.")
            return
        giveaways = await self.config.guild(ctx.guild).giveaways()
        data = giveaways.get(str(mid))
        if not data:
            await ctx.send("That message is not a giveaway.")
            return
        if not await self._can_manage(ctx.author, data["host_id"]):
            await ctx.send("You cannot manage this giveaway.")
            return
        if data.get("status") != "active":
            await ctx.send("That giveaway is not active.")
            return
        self._cancel_tasks_for(ctx.guild.id, mid)
        await self._end_giveaway_task(ctx.guild.id, mid)
        await ctx.send("Giveaway ended.")

    @giveaway.command(name="edit")
    @commands.admin_or_permissions(manage_guild=True)
    async def edit_cmd(
        self,
        ctx: commands.Context,
        message_id: Optional[int],
        prize: Optional[str] = None,
        duration: Optional[DurationConverter] = None,
        *,
        description: Optional[str] = None,
    ):
        """Edit an active giveaway. Reply to the message or pass message_id. With only message_id, opens the interactive builder to edit."""
        mid = self._resolve_message_id(ctx, message_id)
        if mid is None:
            await ctx.send("Provide a message ID or reply to the giveaway message.")
            return
        giveaways = await self.config.guild(ctx.guild).giveaways()
        data = giveaways.get(str(mid))
        if not data:
            await ctx.send("That message is not a giveaway.")
            return
        if not await self._can_manage(ctx.author, data["host_id"]):
            await ctx.send("You cannot manage this giveaway.")
            return
        if data.get("status") != "active":
            await ctx.send("You can only edit an active giveaway.")
            return
        updates = {}
        if prize is not None and prize.strip():
            p = prize.strip()
            updates["prize"] = p
            old_prizes = self._prizes_list(data)
            updates["prizes"] = [p] + (old_prizes[1:] if len(old_prizes) > 1 else [])
        if description is not None:
            updates["description"] = description.strip() or None
        if duration is not None and duration.total_seconds() >= 60:
            updates["end_ts"] = time.time() + duration.total_seconds()
            self._cancel_tasks_for(ctx.guild.id, mid)
            self._schedule_end_task(ctx.guild.id, mid, duration.total_seconds())
        if not updates:
            loaded = await self._load_giveaway_into_draft(ctx.guild.id, ctx.author.id, mid)
            if not loaded:
                await ctx.send("That giveaway is no longer active or could not be loaded.")
                return
            view = GiveawayBuilderView(self, ctx.guild, ctx.author.id, edit_message_id=mid)
            draft = await self._get_draft(ctx.guild.id, ctx.author.id)
            prizes = self._prizes_list(draft)
            prizes_str = ", ".join(p[:40] for p in prizes[:5]) if prizes else "Not set"
            if len(prizes) > 5:
                prizes_str += f" (+{len(prizes) - 5} more)"
            winner_count = self._winner_count(draft)
            duration_seconds = draft.get("duration_seconds") or 0
            duration_str = humanize_timedelta(seconds=duration_seconds) if duration_seconds else "Not set"
            emoji = draft.get("emoji") or "\U0001f389"
            claim_enabled = draft.get("claim_enabled", False)
            claim_seconds = draft.get("claim_seconds") or 0
            claim_str = humanize_timedelta(seconds=claim_seconds) if claim_seconds else "N/A"
            dm_winners = bool(draft.get("dm_winners", False))
            ch_id = draft.get("channel_id")
            channel_str = ctx.guild.get_channel(ch_id).mention if ch_id else "Current channel"
            thumbnail_url = draft.get("thumbnail_url")
            thumbnail_str = thumbnail_url if thumbnail_url else "Not set"
            if thumbnail_url and len(thumbnail_str) > 60:
                thumbnail_str = thumbnail_str[:57] + "..."
            image_url = draft.get("image_url")
            image_str = image_url if image_url else "Not set"
            if image_url and len(image_str) > 60:
                image_str = image_str[:57] + "..."
            embed = discord.Embed(
                title="\U0001f3aa Giveaway Builder (Editing)",
                description="Use the buttons below to update the giveaway.",
                color=await self.bot.get_embed_color(ctx.guild),
            )
            embed.add_field(
                name="\U0001f4dd Giveaway Configuration",
                value=(
                    f"**Prizes:** {prizes_str[:500]}\n"
                    f"**Winners:** {winner_count}\n"
                    f"**Duration:** {duration_str}\n"
                    f"**Emoji:** {emoji}\n"
                    f"**Claim:** {'Yes' if claim_enabled else 'No'} ({claim_str})\n"
                    f"**DM Winners:** {'Yes' if dm_winners else 'No'}\n"
                    f"**Thumbnail:** {thumbnail_str}\n"
                    f"**Image:** {image_str}\n"
                    f"**Channel:** {channel_str}"
                ),
                inline=False,
            )
            view.message = await ctx.send(embed=embed, view=view)
            return
        async with self.config.guild(ctx.guild).giveaways() as gws:
            g = gws.get(str(mid))
            if g:
                g.update(updates)
        channel = ctx.guild.get_channel(data["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(mid)
                d = giveaways.get(str(mid)) or data
                d = {**data, **d, **updates}
                prizes = self._prizes_list(d)
                claim_seconds = int(d.get("claim_seconds") or 0)
                embed = await self._make_giveaway_embed(
                    ctx.guild,
                    prizes,
                    d.get("description"),
                    d["end_ts"],
                    d.get("emoji", "\U0001f389"),
                    len(d.get("entries") or []),
                    d["host_id"],
                    "active",
                    winner_count=self._winner_count(d),
                    claim_seconds=claim_seconds,
                    thumbnail_url=d.get("thumbnail_url"),
                    image_url=d.get("image_url"),
                )
                await msg.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                pass
        await ctx.send("Giveaway updated.")

    @giveaway.command(name="cancel")
    @commands.admin_or_permissions(manage_guild=True)
    async def cancel_cmd(self, ctx: commands.Context, message_id: Optional[int] = None):
        """Cancel a giveaway. Reply to the message or pass message_id."""
        mid = self._resolve_message_id(ctx, message_id)
        if mid is None:
            await ctx.send("Provide a message ID or reply to the giveaway message.")
            return
        giveaways = await self.config.guild(ctx.guild).giveaways()
        data = giveaways.get(str(mid))
        if not data:
            await ctx.send("That message is not a giveaway.")
            return
        if not await self._can_manage(ctx.author, data["host_id"]):
            await ctx.send("You cannot manage this giveaway.")
            return
        if data.get("status") not in ("active", "ended"):
            await ctx.send("That giveaway cannot be cancelled.")
            return
        self._cancel_tasks_for(ctx.guild.id, mid)
        self._end_tasks.pop((ctx.guild.id, mid, "claim"), None)
        async with self.config.guild(ctx.guild).giveaways() as gws:
            g = gws.get(str(mid))
            if g:
                g["status"] = "cancelled"
        channel = ctx.guild.get_channel(data["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(mid)
                prizes = self._prizes_list(data)
                embed = await self._make_giveaway_embed(
                    ctx.guild,
                    prizes,
                    data.get("description"),
                    data["end_ts"],
                    data.get("emoji", "\U0001f389"),
                    len(data.get("entries") or []),
                    data["host_id"],
                    "cancelled",
                )
                await msg.edit(embed=embed, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass
        await ctx.send("Giveaway cancelled.")


async def setup(bot: Red):
    cog = Giveaway(bot)
    await bot.add_cog(cog)
    log.info("Giveaway cog loaded")
