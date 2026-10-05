import asyncio
import base64
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

log = logging.getLogger("red.wzyss-cogs.servertemplate")

TEMPLATE_VERSION = 1
MAX_TEMPLATES_PER_USER = 25
MAX_IMPORT_BYTES = 25 * 1024 * 1024
VIEW_TIMEOUT = 600.0
PROGRESS_INTERVAL = 3.0
OP_DELAY = 0.35
PAGE_SIZE = 25
TEMPLATE_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

NOT_ADMIN_MSG = "You need the **Administrator** permission in this server to use server templates."

COMP_ROLES = "roles"
COMP_CHANNELS = "channels"
COMP_SETTINGS = "settings"
COMP_IDENTITY = "identity"
COMP_EMOJIS = "emojis"

# Ordered: this is also the display order everywhere.
COMPONENTS: Dict[str, Tuple[str, str]] = {
    COMP_ROLES: ("Roles", "Names, colors, permissions, hoist, order, @everyone"),
    COMP_CHANNELS: ("Channels & permissions", "Categories, channels, settings, overwrites"),
    COMP_SETTINGS: ("Server settings", "Verification, notifications, AFK, system channel"),
    COMP_IDENTITY: ("Server name & icon", "Guild name and icon image"),
    COMP_EMOJIS: ("Emojis", "Custom emoji images, names, role limits"),
}
DEFAULT_APPLY = {COMP_ROLES, COMP_CHANNELS, COMP_SETTINGS, COMP_EMOJIS}

MODE_MERGE = "merge"
MODE_REPLACE = "replace"

TYPE_CATEGORY = "category"
TYPE_TEXT = "text"
TYPE_NEWS = "news"
TYPE_VOICE = "voice"
TYPE_STAGE = "stage"
TYPE_FORUM = "forum"
TYPE_MEDIA = "media"
CHANNEL_TYPES = {TYPE_TEXT, TYPE_NEWS, TYPE_VOICE, TYPE_STAGE, TYPE_FORUM, TYPE_MEDIA}

# Types that need Community may be created as their fallback, so matching uses families.
TYPE_FAMILY = {
    TYPE_TEXT: "text",
    TYPE_NEWS: "text",
    TYPE_VOICE: "voice",
    TYPE_STAGE: "voice",
    TYPE_FORUM: "forum",
    TYPE_MEDIA: "forum",
}

GuildChannel = Union[
    discord.TextChannel,
    discord.VoiceChannel,
    discord.CategoryChannel,
    discord.StageChannel,
    discord.ForumChannel,
]


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_admin(user: Union[discord.Member, discord.User, None]) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _position(entry: Dict[str, Any]) -> int:
    return _int(entry.get("position")) or 0


def guild_admin_only():
    """Require the Discord Administrator permission. Red admin/mod roles are not enough."""

    async def predicate(ctx: commands.Context) -> bool:
        return ctx.guild is not None and _is_admin(ctx.author)

    return commands.check(predicate)


@dataclass
class ApplyPlan:
    guild: discord.Guild
    template: Dict[str, Any]
    components: Set[str]
    mode: str
    same_guild: bool
    role_map: Dict[str, discord.Role] = field(default_factory=dict)
    role_updates: List[Tuple[Dict[str, Any], discord.Role]] = field(default_factory=list)
    role_creates: List[Dict[str, Any]] = field(default_factory=list)
    role_above_bot: List[Tuple[Dict[str, Any], discord.Role]] = field(default_factory=list)
    role_deletes: List[discord.Role] = field(default_factory=list)
    category_map: Dict[str, discord.CategoryChannel] = field(default_factory=dict)
    category_updates: List[Tuple[Dict[str, Any], discord.CategoryChannel]] = field(default_factory=list)
    category_creates: List[Dict[str, Any]] = field(default_factory=list)
    channel_map: Dict[str, GuildChannel] = field(default_factory=dict)
    channel_updates: List[Tuple[Dict[str, Any], GuildChannel]] = field(default_factory=list)
    channel_creates: List[Dict[str, Any]] = field(default_factory=list)
    channel_deletes: List[GuildChannel] = field(default_factory=list)
    emoji_creates: List[Dict[str, Any]] = field(default_factory=list)
    emoji_deletes: List[discord.Emoji] = field(default_factory=list)
    emoji_existing: int = 0
    emoji_over_limit: int = 0
    kept_channel: Optional[GuildChannel] = None
    warnings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    def total_ops(self) -> int:
        total = 0
        if COMP_IDENTITY in self.components:
            total += 1
        if COMP_ROLES in self.components:
            total += len(self.role_deletes) + len(self.role_updates) + len(self.role_creates) + 2
        if COMP_EMOJIS in self.components:
            total += len(self.emoji_deletes) + len(self.emoji_creates)
        if COMP_CHANNELS in self.components:
            total += (
                len(self.channel_deletes)
                + len(self.category_updates)
                + len(self.category_creates)
                + len(self.channel_updates)
                + len(self.channel_creates)
                + 1
            )
        if COMP_SETTINGS in self.components:
            total += 1
        return max(total, 1)


class ApplyResults:
    def __init__(self) -> None:
        self.counts: Dict[str, Dict[str, int]] = {}
        self.errors: List[str] = []
        self.failed = 0

    def add(self, section: str, action: str, amount: int = 1) -> None:
        bucket = self.counts.setdefault(section, {})
        bucket[action] = bucket.get(action, 0) + amount

    def fail(self, section: str, label: str, error: Exception) -> None:
        self.failed += 1
        self.add(section, "failed")
        if len(self.errors) < 15:
            self.errors.append(f"{section}: {label} ({type(error).__name__}: {error})")


class Progress:
    def __init__(self, message: discord.Message, title: str, total: int) -> None:
        self.message = message
        self.title = title
        self.total = total
        self.done = 0
        self.stage = "Starting"
        self._last_edit = 0.0

    async def set_stage(self, stage: str) -> None:
        self.stage = stage
        await self._render(force=True)

    async def tick(self) -> None:
        self.done = min(self.done + 1, self.total)
        await self._render()

    async def _render(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_edit < PROGRESS_INTERVAL:
            return
        self._last_edit = now
        filled = int(20 * self.done / self.total) if self.total else 20
        bar = "#" * filled + "-" * (20 - filled)
        embed = discord.Embed(
            title=self.title,
            description=(
                f"**Stage:** {self.stage}\n"
                f"`[{bar}]` {self.done}/{self.total}\n\n"
                "Do not delete this channel while the template is being applied."
            ),
            color=discord.Color.orange(),
        )
        try:
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass


# ----------------------------------------------------------------------
# Views and modals
# ----------------------------------------------------------------------


class AdminView(discord.ui.View):
    """Base view: only the panel owner, and only while they hold Administrator."""

    def __init__(self, cog: "ServerTemplate", author_id: int, message: Optional[discord.Message] = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This panel belongs to someone else. Open your own with the panel command.",
                ephemeral=True,
            )
            return False
        if not _is_admin(interaction.user):
            await interaction.response.send_message(NOT_ADMIN_MSG, ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    async def swap(self, interaction: discord.Interaction, embed: discord.Embed, view: Optional["AdminView"]) -> None:
        if view is not None:
            view.message = self.message or interaction.message
        self.stop()
        await interaction.response.edit_message(embed=embed, view=view)

    async def edit_panel(self, embed: discord.Embed, view: Optional["AdminView"]) -> None:
        if view is not None:
            view.message = self.message
        self.stop()
        if self.message:
            try:
                await self.message.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass

    async def deny(self, interaction: discord.Interaction, text: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class MainView(AdminView):
    @discord.ui.button(label="Create Template", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CreateView(self.cog, self.author_id, interaction.user)
        await self.swap(interaction, view.build_embed(), view)

    @discord.ui.button(label="Browse Templates", style=discord.ButtonStyle.primary)
    async def browse_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BrowseView(self.cog, self.author_id, interaction.user)
        await self.swap(interaction, view.build_embed(), view)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Server Templates", description="Panel closed.", color=discord.Color.dark_grey())
        await self.swap(interaction, embed, None)


class SourceGuildSelect(discord.ui.Select):
    def __init__(self, guilds: List[discord.Guild], selected_id: int):
        options = [
            discord.SelectOption(
                label=_truncate(g.name, 100),
                value=str(g.id),
                description=f"{len(g.roles) - 1} roles, {len(g.channels)} channels",
                default=g.id == selected_id,
            )
            for g in guilds
        ]
        super().__init__(placeholder="Server to capture", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: CreateView = self.view  # type: ignore[assignment]
        view.source_guild_id = int(self.values[0])
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class ComponentSelect(discord.ui.Select):
    def __init__(self, available: List[str], selected: Set[str], placeholder: str, row: int):
        options = [
            discord.SelectOption(
                label=COMPONENTS[key][0],
                value=key,
                description=COMPONENTS[key][1],
                default=key in selected,
            )
            for key in available
        ]
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=len(options),
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.components = set(self.values)  # type: ignore[union-attr]
        for option in self.options:
            option.default = option.value in view.components  # type: ignore[union-attr]
        await view.on_selection_changed(interaction)  # type: ignore[union-attr]


class CreateView(AdminView):
    def __init__(self, cog: "ServerTemplate", author_id: int, member: discord.Member):
        super().__init__(cog, author_id)
        self.guilds = cog.admin_guilds(member)
        self.source_guild_id = member.guild.id
        self.components: Set[str] = set(COMPONENTS)
        self.add_item(SourceGuildSelect(self.guilds, self.source_guild_id))
        self.add_item(ComponentSelect(list(COMPONENTS), self.components, "What to capture", row=1))

    def build_embed(self) -> discord.Embed:
        guild = self.cog.bot.get_guild(self.source_guild_id)
        embed = discord.Embed(
            title="Create Template",
            description=(
                "Pick the server to capture and what to include, then press **Capture** "
                "to name the template.\n\n"
                "Only servers where you are an Administrator are listed. Message content, "
                "members, bans, and integrations are never captured."
            ),
            color=discord.Color.blurple(),
        )
        if guild:
            embed.add_field(name="Source server", value=f"{guild.name} (`{guild.id}`)", inline=False)
        embed.add_field(
            name="Included",
            value="\n".join(f"- {COMPONENTS[k][0]}" for k in COMPONENTS if k in self.components) or "Nothing",
            inline=False,
        )
        if COMP_EMOJIS in self.components:
            embed.set_footer(text="Emoji images are stored in the template, which makes it larger.")
        return embed

    async def on_selection_changed(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Capture", style=discord.ButtonStyle.success, row=2)
    async def capture_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cog.count_user_templates(interaction.user.id) >= MAX_TEMPLATES_PER_USER:
            await self.deny(
                interaction,
                f"You already own {MAX_TEMPLATES_PER_USER} templates. Delete one before creating another.",
            )
            return
        guild = self.cog.bot.get_guild(self.source_guild_id)
        await interaction.response.send_modal(TemplateNameModal(self, default_name=guild.name if guild else ""))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainView(self.cog, self.author_id)
        await self.swap(interaction, self.cog.main_embed(interaction.user), view)


class TemplateNameModal(discord.ui.Modal):
    def __init__(self, parent: CreateView, default_name: str):
        super().__init__(title="Name this template")
        self.parent = parent
        self.name_input = discord.ui.TextInput(
            label="Template name",
            max_length=64,
            default=_truncate(default_name, 64) or None,
        )
        self.description_input = discord.ui.TextInput(
            label="Description (optional)",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=False,
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.parent.cog
        if not _is_admin(interaction.user):
            await interaction.response.send_message(NOT_ADMIN_MSG, ephemeral=True)
            return
        guild = cog.bot.get_guild(self.parent.source_guild_id)
        member = guild.get_member(interaction.user.id) if guild else None
        if guild is None or not _is_admin(member):
            await interaction.response.send_message(
                "You must be an Administrator in the source server to capture it.", ephemeral=True
            )
            return
        if cog.count_user_templates(interaction.user.id) >= MAX_TEMPLATES_PER_USER:
            await interaction.response.send_message(
                f"You already own {MAX_TEMPLATES_PER_USER} templates.", ephemeral=True
            )
            return

        waiting = discord.Embed(
            title="Capturing...",
            description=f"Reading the structure of **{guild.name}**. This can take a moment with many emojis.",
            color=discord.Color.orange(),
        )
        self.parent.stop()
        await interaction.response.edit_message(embed=waiting, view=None)
        try:
            data = await cog.capture_guild(
                guild,
                self.parent.components,
                name=self.name_input.value.strip() or guild.name,
                description=(self.description_input.value or "").strip(),
                author=interaction.user,
            )
            meta = await cog.write_template(data)
        except Exception as e:
            log.exception("Failed to capture guild %s", guild.id)
            error_embed = discord.Embed(
                title="Capture failed",
                description=f"`{type(e).__name__}: {e}`",
                color=discord.Color.red(),
            )
            await self.parent.edit_panel(error_embed, MainView(cog, self.parent.author_id))
            return

        view = DetailView(cog, self.parent.author_id, meta["id"])
        await self.parent.edit_panel(cog.template_embed(meta, header="Template saved."), view)


class TemplateSelect(discord.ui.Select):
    def __init__(self, metas: List[Dict[str, Any]]):
        options = [
            discord.SelectOption(
                label=_truncate(meta["name"], 100),
                value=meta["id"],
                description=_truncate(
                    f"{meta['source_guild_name']} - {meta['created_at'][:10]} - {meta['id']}", 100
                ),
            )
            for meta in metas
        ]
        super().__init__(placeholder="Select a template", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: BrowseView = self.view  # type: ignore[assignment]
        meta = view.cog.get_meta(self.values[0])
        if meta is None or not view.cog.can_access(interaction.user, meta):
            await view.deny(interaction, "That template no longer exists or you no longer have access to it.")
            return
        detail = DetailView(view.cog, view.author_id, meta["id"])
        await view.swap(interaction, view.cog.template_embed(meta), detail)


class BrowseView(AdminView):
    def __init__(self, cog: "ServerTemplate", author_id: int, user: discord.Member, page: int = 0):
        super().__init__(cog, author_id)
        self.metas = cog.accessible_templates(user)
        self.pages = max(1, (len(self.metas) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(page, self.pages - 1))
        page_metas = self.page_metas()
        if page_metas:
            self.add_item(TemplateSelect(page_metas))
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.pages - 1

    def page_metas(self) -> List[Dict[str, Any]]:
        start = self.page * PAGE_SIZE
        return self.metas[start : start + PAGE_SIZE]

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Templates", color=discord.Color.blurple())
        if not self.metas:
            embed.description = (
                "You have no accessible templates yet. Templates are visible to their creator "
                "and to Administrators of the server they were captured from."
            )
            return embed
        lines = [
            f"**{meta['name']}** (`{meta['id']}`)\n"
            f"from {meta['source_guild_name']} - <t:{meta['created_ts']}:d> - by {meta['created_by_name']}"
            for meta in self.page_metas()
        ]
        embed.description = _truncate("\n".join(lines), 4000)
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages} - {len(self.metas)} template(s)")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BrowseView(self.cog, self.author_id, interaction.user, self.page - 1)
        await self.swap(interaction, view.build_embed(), view)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BrowseView(self.cog, self.author_id, interaction.user, self.page + 1)
        await self.swap(interaction, view.build_embed(), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainView(self.cog, self.author_id)
        await self.swap(interaction, self.cog.main_embed(interaction.user), view)


class DetailView(AdminView):
    def __init__(self, cog: "ServerTemplate", author_id: int, template_id: str):
        super().__init__(cog, author_id)
        self.template_id = template_id

    async def _meta_or_deny(self, interaction: discord.Interaction) -> Optional[Dict[str, Any]]:
        meta = self.cog.get_meta(self.template_id)
        if meta is None or not self.cog.can_access(interaction.user, meta):
            await self.deny(interaction, "That template no longer exists or you no longer have access to it.")
            return None
        return meta

    @discord.ui.button(label="Apply to This Server", style=discord.ButtonStyle.danger, row=0)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = await self._meta_or_deny(interaction)
        if meta is None:
            return
        view = ApplyView(self.cog, self.author_id, meta)
        await self.swap(interaction, view.build_embed(interaction.guild), view)

    @discord.ui.button(label="Export JSON", style=discord.ButtonStyle.primary, row=0)
    async def export_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = await self._meta_or_deny(interaction)
        if meta is None:
            return
        path = self.cog.template_path(meta["id"])
        limit = interaction.guild.filesize_limit if interaction.guild else 8 * 1024 * 1024
        if not path.is_file():
            await self.deny(interaction, "The template file is missing on disk.")
            return
        if path.stat().st_size > limit:
            await self.deny(
                interaction,
                "This template is larger than this server's upload limit. Recapture it without emojis to export it.",
            )
            return
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", meta["name"]).strip("-") or "template"
        await interaction.response.send_message(
            f"Template **{meta['name']}**. Import it elsewhere with the `servertemplate import` command.",
            file=discord.File(str(path), filename=f"{safe_name}-{meta['id']}.json"),
            ephemeral=True,
        )

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, row=0)
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = await self._meta_or_deny(interaction)
        if meta is None:
            return
        await interaction.response.send_modal(RenameModal(self, meta))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = await self._meta_or_deny(interaction)
        if meta is None:
            return
        view = DeleteConfirmView(self.cog, self.author_id, meta["id"])
        embed = discord.Embed(
            title="Delete template?",
            description=f"**{meta['name']}** (`{meta['id']}`) will be permanently deleted for everyone.",
            color=discord.Color.red(),
        )
        await self.swap(interaction, embed, view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BrowseView(self.cog, self.author_id, interaction.user)
        await self.swap(interaction, view.build_embed(), view)


class RenameModal(discord.ui.Modal):
    def __init__(self, parent: DetailView, meta: Dict[str, Any]):
        super().__init__(title="Edit template details")
        self.parent = parent
        self.name_input = discord.ui.TextInput(label="Template name", max_length=64, default=meta["name"])
        self.description_input = discord.ui.TextInput(
            label="Description (optional)",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=False,
            default=meta.get("description") or None,
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.parent.cog
        meta = cog.get_meta(self.parent.template_id)
        if not _is_admin(interaction.user) or meta is None or not cog.can_access(interaction.user, meta):
            await interaction.response.send_message("You no longer have access to this template.", ephemeral=True)
            return
        data = await cog.read_template(meta["id"])
        if data is None:
            await interaction.response.send_message("The template file is missing on disk.", ephemeral=True)
            return
        data["name"] = self.name_input.value.strip() or data["name"]
        data["description"] = (self.description_input.value or "").strip()
        meta = await cog.write_template(data)
        await interaction.response.edit_message(embed=cog.template_embed(meta, header="Details updated."), view=self.parent)


class DeleteConfirmView(AdminView):
    def __init__(self, cog: "ServerTemplate", author_id: int, template_id: str):
        super().__init__(cog, author_id)
        self.template_id = template_id

    @discord.ui.button(label="Delete Permanently", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = self.cog.get_meta(self.template_id)
        if meta is None or not self.cog.can_access(interaction.user, meta):
            await self.deny(interaction, "That template no longer exists or you no longer have access to it.")
            return
        await self.cog.delete_template(self.template_id)
        view = BrowseView(self.cog, self.author_id, interaction.user)
        embed = view.build_embed()
        embed.title = f"Deleted {meta['name']}"
        await self.swap(interaction, embed, view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = self.cog.get_meta(self.template_id)
        if meta is None:
            view = BrowseView(self.cog, self.author_id, interaction.user)
            await self.swap(interaction, view.build_embed(), view)
            return
        await self.swap(interaction, self.cog.template_embed(meta), DetailView(self.cog, self.author_id, self.template_id))


class ModeSelect(discord.ui.Select):
    def __init__(self, selected: str):
        options = [
            discord.SelectOption(
                label="Merge (keep existing)",
                value=MODE_MERGE,
                description="Update matching items, create missing ones, delete nothing",
                default=selected == MODE_MERGE,
            ),
            discord.SelectOption(
                label="Replace (mirror exactly)",
                value=MODE_REPLACE,
                description="Also delete roles/channels/emojis not in the template",
                default=selected == MODE_REPLACE,
            ),
        ]
        super().__init__(placeholder="Apply mode", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: ApplyView = self.view  # type: ignore[assignment]
        view.mode = self.values[0]
        for option in self.options:
            option.default = option.value == view.mode
        await view.on_selection_changed(interaction)


class ApplyView(AdminView):
    def __init__(
        self,
        cog: "ServerTemplate",
        author_id: int,
        meta: Dict[str, Any],
        components: Optional[Set[str]] = None,
        mode: str = MODE_MERGE,
    ):
        super().__init__(cog, author_id)
        self.meta = meta
        available = [k for k in COMPONENTS if k in meta["components"]]
        self.components: Set[str] = (
            set(components) if components is not None else {k for k in available if k in DEFAULT_APPLY}
        ) or set(available[:1])
        self.mode = mode
        self.add_item(ComponentSelect(available, self.components, "What to apply", row=0))
        self.add_item(ModeSelect(self.mode))

    def build_embed(self, guild: Optional[discord.Guild], plan_embed: Optional[discord.Embed] = None) -> discord.Embed:
        if plan_embed is not None:
            return plan_embed
        mode_text = (
            "**Merge** - existing roles, channels, and emojis are matched by name (or ID on the "
            "source server) and updated; missing ones are created; nothing is deleted."
            if self.mode == MODE_MERGE
            else "**Replace** - like Merge, but roles, channels, and emojis that are not in the "
            "template are **deleted**. Roles you hold and the channel this panel is in are kept."
        )
        embed = discord.Embed(
            title=f"Apply: {self.meta['name']}",
            description=(
                f"Target server: **{guild.name if guild else '?'}**\n\n{mode_text}\n\n"
                "Press **Preview Plan** to see exactly what will change before applying."
            ),
            color=discord.Color.red() if self.mode == MODE_REPLACE else discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected",
            value="\n".join(f"- {COMPONENTS[k][0]}" for k in COMPONENTS if k in self.components),
            inline=False,
        )
        return embed

    async def on_selection_changed(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.build_embed(interaction.guild), view=self)

    async def _prepare_plan(self, interaction: discord.Interaction) -> Optional[ApplyPlan]:
        meta = self.cog.get_meta(self.meta["id"])
        if meta is None or not self.cog.can_access(interaction.user, meta):
            await self.deny(interaction, "That template no longer exists or you no longer have access to it.")
            return None
        template = await self.cog.read_template(meta["id"])
        if template is None:
            await self.deny(interaction, "The template file is missing on disk.")
            return None
        return self.cog.build_plan(
            interaction.guild,
            template,
            self.components,
            self.mode,
            keep_channel_id=interaction.channel_id,
            protected_role_ids={r.id for r in interaction.user.roles},
        )

    @discord.ui.button(label="Preview Plan", style=discord.ButtonStyle.primary, row=2)
    async def preview_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        plan = await self._prepare_plan(interaction)
        if plan is None:
            return
        await interaction.response.edit_message(embed=self.cog.plan_embed(plan), view=self)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.danger, row=2)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        plan = await self._prepare_plan(interaction)
        if plan is None:
            return
        if plan.blockers:
            await interaction.response.edit_message(embed=self.cog.plan_embed(plan), view=self)
            return
        if self.cog.apply_running(interaction.guild.id):
            await self.deny(interaction, "A template is already being applied to this server.")
            return
        if self.mode == MODE_REPLACE:
            await interaction.response.send_modal(ReplaceConfirmModal(self))
            return
        view = ApplyConfirmView(self.cog, self.author_id, self.meta, self.components, self.mode)
        await self.swap(interaction, self.cog.plan_embed(plan, confirming=True), view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        meta = self.cog.get_meta(self.meta["id"])
        if meta is None:
            view = BrowseView(self.cog, self.author_id, interaction.user)
            await self.swap(interaction, view.build_embed(), view)
            return
        await self.swap(interaction, self.cog.template_embed(meta), DetailView(self.cog, self.author_id, meta["id"]))


class ApplyConfirmView(AdminView):
    def __init__(self, cog: "ServerTemplate", author_id: int, meta: Dict[str, Any], components: Set[str], mode: str):
        super().__init__(cog, author_id)
        self.meta = meta
        self.components = components
        self.mode = mode

    @discord.ui.button(label="Confirm and Apply", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_apply(interaction, self, self.meta["id"], self.components, self.mode)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ApplyView(self.cog, self.author_id, self.meta, self.components, self.mode)
        await self.swap(interaction, view.build_embed(interaction.guild), view)


class ReplaceConfirmModal(discord.ui.Modal):
    def __init__(self, parent: ApplyView):
        super().__init__(title="Confirm destructive replace")
        self.parent = parent
        self.confirm_input = discord.ui.TextInput(
            label="Type this server's name to confirm",
            placeholder="Items not in the template will be deleted",
            max_length=100,
        )
        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_admin(interaction.user):
            await interaction.response.send_message(NOT_ADMIN_MSG, ephemeral=True)
            return
        if self.confirm_input.value.strip() != interaction.guild.name:
            await interaction.response.send_message(
                "The name did not match. Nothing was changed.", ephemeral=True
            )
            return
        await self.parent.cog.start_apply(
            interaction, self.parent, self.parent.meta["id"], self.parent.components, self.parent.mode
        )


# ----------------------------------------------------------------------
# Cog
# ----------------------------------------------------------------------


class ServerTemplate(commands.Cog):
    """Capture server structure into templates and apply them to any server."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._index: Dict[str, Dict[str, Any]] = {}
        self._io_lock = asyncio.Lock()
        self._apply_locks: Dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        loop = asyncio.get_running_loop()
        self._index = await loop.run_in_executor(None, self._scan_index)
        log.info("Loaded %d server templates", len(self._index))

    async def red_delete_data_for_user(self, *, requester, user_id: int) -> None:
        uid = str(user_id)
        for template_id in list(self._index):
            data = await self.read_template(template_id)
            if data is None:
                continue
            changed = False
            if str(data.get("created_by", {}).get("id")) == uid:
                data["created_by"] = {"id": "0", "name": "Deleted User"}
                changed = True
            for entry in data.get("categories", []) + data.get("channels", []):
                before = len(entry.get("overwrites", []))
                entry["overwrites"] = [
                    ow
                    for ow in entry.get("overwrites", [])
                    if not (ow.get("type") == "member" and str(ow.get("id")) == uid)
                ]
                changed = changed or len(entry["overwrites"]) != before
            if changed:
                await self.write_template(data)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _templates_dir(self) -> Path:
        path = cog_data_path(self) / "templates"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def template_path(self, template_id: str) -> Path:
        if not TEMPLATE_ID_RE.match(template_id):
            raise ValueError("Invalid template ID.")
        return self._templates_dir() / f"{template_id}.json"

    @staticmethod
    def _build_meta(data: Dict[str, Any], size: int) -> Dict[str, Any]:
        created_at = str(data.get("created_at") or "")
        try:
            created_ts = int(datetime.fromisoformat(created_at).timestamp())
        except ValueError:
            created_ts = 0
        source = data.get("source_guild") or {}
        creator = data.get("created_by") or {}
        return {
            "id": data["id"],
            "name": str(data.get("name") or data["id"]),
            "description": str(data.get("description") or ""),
            "created_at": created_at,
            "created_ts": created_ts,
            "created_by_id": str(creator.get("id", "0")),
            "created_by_name": str(creator.get("name", "Unknown")),
            "source_guild_id": str(source.get("id", "0")),
            "source_guild_name": str(source.get("name", "Unknown server")),
            "components": [k for k in COMPONENTS if k in (data.get("components") or [])],
            "counts": {
                "roles": len([r for r in data.get("roles", []) if not r.get("default")]),
                "categories": len(data.get("categories", [])),
                "channels": len(data.get("channels", [])),
                "emojis": len(data.get("emojis", [])),
            },
            "size": size,
        }

    def _scan_index(self) -> Dict[str, Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        for path in self._templates_dir().glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data["id"] = path.stem
                index[path.stem] = self._build_meta(data, path.stat().st_size)
            except Exception:
                log.exception("Skipping unreadable template file %s", path)
        return index

    def _read_file(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write_file(self, path: Path, data: Dict[str, Any]) -> int:
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        tmp.replace(path)
        return path.stat().st_size

    async def read_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        try:
            path = self.template_path(template_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, self._read_file, path)
        except Exception:
            log.exception("Failed to read template %s", template_id)
            return None
        data["id"] = template_id
        return data

    async def write_template(self, data: Dict[str, Any]) -> Dict[str, Any]:
        path = self.template_path(data["id"])
        loop = asyncio.get_running_loop()
        async with self._io_lock:
            size = await loop.run_in_executor(None, self._write_file, path, data)
        meta = self._build_meta(data, size)
        self._index[data["id"]] = meta
        return meta

    async def delete_template(self, template_id: str) -> None:
        async with self._io_lock:
            self.template_path(template_id).unlink(missing_ok=True)
        self._index.pop(template_id, None)

    def _new_id(self) -> str:
        while True:
            template_id = secrets.token_hex(4)
            if template_id not in self._index:
                return template_id

    def get_meta(self, template_id: str) -> Optional[Dict[str, Any]]:
        return self._index.get(template_id)

    def count_user_templates(self, user_id: int) -> int:
        return sum(1 for m in self._index.values() if m["created_by_id"] == str(user_id))

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def can_access(self, user: discord.abc.User, meta: Dict[str, Any]) -> bool:
        """Creator, or a current Administrator of the template's source server."""
        if meta["created_by_id"] == str(user.id):
            return True
        source = self.bot.get_guild(_int(meta["source_guild_id"]) or 0)
        if source is None:
            return False
        return _is_admin(source.get_member(user.id))

    def accessible_templates(self, user: discord.abc.User) -> List[Dict[str, Any]]:
        metas = [m for m in self._index.values() if self.can_access(user, m)]
        metas.sort(key=lambda m: m["created_ts"], reverse=True)
        return metas

    def admin_guilds(self, member: discord.Member) -> List[discord.Guild]:
        guilds = [member.guild]
        for guild in sorted(self.bot.guilds, key=lambda g: g.name.lower()):
            if guild.id == member.guild.id:
                continue
            if _is_admin(guild.get_member(member.id)):
                guilds.append(guild)
            if len(guilds) >= 25:
                break
        return guilds

    def apply_running(self, guild_id: int) -> bool:
        lock = self._apply_locks.get(guild_id)
        return lock is not None and lock.locked()

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    @staticmethod
    def _channel_type_key(channel: GuildChannel) -> Optional[str]:
        if isinstance(channel, discord.CategoryChannel):
            return TYPE_CATEGORY
        if isinstance(channel, discord.StageChannel):
            return TYPE_STAGE
        if isinstance(channel, discord.VoiceChannel):
            return TYPE_VOICE
        if isinstance(channel, discord.ForumChannel):
            if getattr(getattr(channel, "type", None), "name", "") == "media":
                return TYPE_MEDIA
            return TYPE_FORUM
        if isinstance(channel, discord.TextChannel):
            return TYPE_NEWS if channel.is_news() else TYPE_TEXT
        return None

    @staticmethod
    def _serialize_overwrites(channel: GuildChannel) -> List[Dict[str, Any]]:
        result = []
        for target, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            if isinstance(target, discord.Role):
                target_type = "role"
            elif isinstance(target, discord.Object) and getattr(target, "type", None) is discord.Role:
                target_type = "role"
            else:
                target_type = "member"
            result.append({"id": str(target.id), "type": target_type, "allow": allow.value, "deny": deny.value})
        return result

    def _serialize_channel(self, channel: GuildChannel) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": str(channel.id),
            "type": self._channel_type_key(channel),
            "name": channel.name,
            "position": channel.position,
            "overwrites": self._serialize_overwrites(channel),
        }
        if isinstance(channel, discord.CategoryChannel):
            data["nsfw"] = bool(getattr(channel, "nsfw", False))
            return data

        data["category_id"] = str(channel.category_id) if channel.category_id else None

        if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            data["topic"] = channel.topic
            data["nsfw"] = channel.nsfw
            data["slowmode_delay"] = channel.slowmode_delay
            data["default_auto_archive_duration"] = getattr(channel, "default_auto_archive_duration", None)
            data["default_thread_slowmode_delay"] = getattr(channel, "default_thread_slowmode_delay", None)

        if isinstance(channel, discord.ForumChannel):
            layout = getattr(channel, "default_layout", None)
            sort_order = getattr(channel, "default_sort_order", None)
            data["default_layout"] = getattr(layout, "value", layout)
            data["default_sort_order"] = getattr(sort_order, "value", sort_order)
            tags = []
            for tag in getattr(channel, "available_tags", []) or []:
                emoji = getattr(tag, "emoji", None)
                tags.append(
                    {
                        "name": tag.name,
                        "moderated": bool(getattr(tag, "moderated", False)),
                        "emoji_name": getattr(emoji, "name", None) if emoji else None,
                        "emoji_custom": bool(emoji and getattr(emoji, "id", None)),
                    }
                )
            data["available_tags"] = tags

        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            data["bitrate"] = channel.bitrate
            data["user_limit"] = channel.user_limit
            data["rtc_region"] = str(channel.rtc_region) if channel.rtc_region else None
            vqm = getattr(channel, "video_quality_mode", None)
            data["video_quality_mode"] = getattr(vqm, "value", vqm)
            if isinstance(channel, discord.VoiceChannel) and not isinstance(channel, discord.StageChannel):
                data["nsfw"] = bool(getattr(channel, "nsfw", False))
                data["slowmode_delay"] = getattr(channel, "slowmode_delay", 0)
        return data

    async def capture_guild(
        self,
        guild: discord.Guild,
        components: Set[str],
        *,
        name: str,
        description: str,
        author: discord.abc.User,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "version": TEMPLATE_VERSION,
            "id": self._new_id(),
            "name": _truncate(name, 64),
            "description": _truncate(description, 300),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {"id": str(author.id), "name": str(author)},
            "source_guild": {"id": str(guild.id), "name": guild.name},
            "components": [k for k in COMPONENTS if k in components],
            "roles": [],
            "categories": [],
            "channels": [],
            "settings": {},
            "identity": {},
            "emojis": [],
        }

        # Role data is also needed to map channel overwrites onto another server by name.
        if components & {COMP_ROLES, COMP_CHANNELS, COMP_EMOJIS}:
            for role in sorted(guild.roles, key=lambda r: r.position):
                if role.managed:
                    continue
                data["roles"].append(
                    {
                        "id": str(role.id),
                        "name": role.name,
                        "default": role.is_default(),
                        "permissions": role.permissions.value,
                        "color": role.colour.value,
                        "hoist": role.hoist,
                        "mentionable": role.mentionable,
                        "position": role.position,
                        "unicode_emoji": getattr(role, "unicode_emoji", None),
                    }
                )

        if components & {COMP_CHANNELS, COMP_SETTINGS}:
            for category in sorted(guild.categories, key=lambda c: c.position):
                data["categories"].append(self._serialize_channel(category))
            for channel in sorted(guild.channels, key=lambda c: (c.position, c.id)):
                if isinstance(channel, discord.CategoryChannel):
                    continue
                if self._channel_type_key(channel) in CHANNEL_TYPES:
                    data["channels"].append(self._serialize_channel(channel))
            if COMP_CHANNELS not in components:
                # Settings only need channel identities to resolve AFK/system channels.
                for entry in data["categories"] + data["channels"]:
                    entry["overwrites"] = []

        if COMP_SETTINGS in components:
            locale = guild.preferred_locale
            data["settings"] = {
                "verification_level": guild.verification_level.value,
                "default_notifications": guild.default_notifications.value,
                "explicit_content_filter": guild.explicit_content_filter.value,
                "afk_timeout": guild.afk_timeout,
                "afk_channel_id": str(guild.afk_channel.id) if guild.afk_channel else None,
                "system_channel_id": str(guild.system_channel.id) if guild.system_channel else None,
                "system_channel_flags": guild.system_channel_flags.value,
                "rules_channel_id": str(guild.rules_channel.id) if guild.rules_channel else None,
                "public_updates_channel_id": (
                    str(guild.public_updates_channel.id) if guild.public_updates_channel else None
                ),
                "preferred_locale": str(getattr(locale, "value", locale)),
                "premium_progress_bar_enabled": bool(getattr(guild, "premium_progress_bar_enabled", False)),
            }

        if COMP_IDENTITY in components:
            icon = None
            if guild.icon:
                try:
                    icon = base64.b64encode(await guild.icon.read()).decode("ascii")
                except discord.HTTPException:
                    log.warning("Could not download icon for guild %s", guild.id)
            data["identity"] = {"name": guild.name, "icon": icon}

        if COMP_EMOJIS in components:
            for emoji in guild.emojis:
                if emoji.managed:
                    continue
                try:
                    image = await emoji.read()
                except discord.HTTPException:
                    log.warning("Could not download emoji %s in guild %s", emoji.id, guild.id)
                    continue
                data["emojis"].append(
                    {
                        "name": emoji.name,
                        "animated": emoji.animated,
                        "image": base64.b64encode(image).decode("ascii"),
                        "roles": [str(r.id) for r in emoji.roles],
                    }
                )
        return data

    # ------------------------------------------------------------------
    # Import validation
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_template(raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("The file is not a template object.")
        version = _int(raw.get("version"))
        if version is None or version > TEMPLATE_VERSION:
            raise ValueError("Unsupported or missing template version.")
        source = raw.get("source_guild")
        if not isinstance(source, dict) or _int(source.get("id")) is None:
            raise ValueError("Template is missing its source server.")

        def entries(key: str, required: Tuple[str, ...]) -> List[Dict[str, Any]]:
            value = raw.get(key) or []
            if not isinstance(value, list):
                raise ValueError(f"`{key}` must be a list.")
            return [
                e
                for e in value
                if isinstance(e, dict) and all(isinstance(e.get(k), (str, int)) for k in required)
            ]

        components = [k for k in COMPONENTS if k in (raw.get("components") or [])]
        if not components:
            raise ValueError("Template contains no components.")
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
        return {
            "version": version,
            "name": _truncate(str(raw.get("name") or "Imported template"), 64),
            "description": _truncate(str(raw.get("description") or ""), 300),
            "created_at": str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "source_guild": {"id": str(source["id"]), "name": _truncate(str(source.get("name") or "?"), 100)},
            "components": components,
            "roles": entries("roles", ("id", "name")),
            "categories": entries("categories", ("id", "name")),
            "channels": [c for c in entries("channels", ("id", "name")) if c.get("type") in CHANNEL_TYPES],
            "settings": settings,
            "identity": identity,
            "emojis": [e for e in entries("emojis", ("name", "image")) if isinstance(e.get("image"), str)],
        }

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def build_plan(
        self,
        guild: discord.Guild,
        template: Dict[str, Any],
        components: Set[str],
        mode: str,
        *,
        keep_channel_id: Optional[int],
        protected_role_ids: Set[int],
    ) -> ApplyPlan:
        source_id = str(template.get("source_guild", {}).get("id"))
        components = {c for c in components if c in template.get("components", [])}
        plan = ApplyPlan(
            guild=guild,
            template=template,
            components=components,
            mode=mode,
            same_guild=source_id == str(guild.id),
        )
        me = guild.me
        bot_top = me.top_role
        replace = mode == MODE_REPLACE

        if not me.guild_permissions.administrator:
            plan.blockers.append("The bot needs the **Administrator** permission in this server to apply templates.")
        if not components:
            plan.blockers.append("Select at least one component that exists in this template.")

        # Roles: matches are always computed so channel overwrites can be mapped.
        plan.role_map[source_id] = guild.default_role
        candidates = [r for r in guild.roles if not r.is_default() and not r.managed]
        used_roles: Set[int] = set()
        for entry in sorted((r for r in template.get("roles", []) if not r.get("default")), key=_position):
            match: Optional[discord.Role] = None
            if plan.same_guild:
                role = guild.get_role(_int(entry.get("id")) or 0)
                if role and not role.is_default() and not role.managed and role.id not in used_roles:
                    match = role
            if match is None:
                same_name = [r for r in candidates if r.id not in used_roles and r.name == entry.get("name")]
                same_name.sort(key=lambda r: (r >= bot_top, r.position))
                match = same_name[0] if same_name else None
            if match is not None:
                used_roles.add(match.id)
                plan.role_map[str(entry["id"])] = match
                if match >= bot_top:
                    plan.role_above_bot.append((entry, match))
                else:
                    plan.role_updates.append((entry, match))
            else:
                plan.role_creates.append(entry)
        if replace and COMP_ROLES in components:
            plan.role_deletes = [
                r
                for r in candidates
                if r.id not in used_roles and r < bot_top and r.id not in protected_role_ids
            ]
        if COMP_ROLES in components and plan.role_above_bot:
            plan.warnings.append(
                f"{len(plan.role_above_bot)} matched role(s) sit above the bot's highest role and will not be "
                "edited. Move the bot's role to the top of the role list to fix this."
            )

        # Categories and channels.
        used_channels: Set[int] = set()
        for entry in sorted(template.get("categories", []), key=_position):
            match_cat: Optional[discord.CategoryChannel] = None
            if plan.same_guild:
                existing = guild.get_channel(_int(entry.get("id")) or 0)
                if isinstance(existing, discord.CategoryChannel) and existing.id not in used_channels:
                    match_cat = existing
            if match_cat is None:
                match_cat = next(
                    (
                        c
                        for c in guild.categories
                        if c.id not in used_channels and c.name.lower() == str(entry.get("name")).lower()
                    ),
                    None,
                )
            if match_cat is not None:
                used_channels.add(match_cat.id)
                plan.category_map[str(entry["id"])] = match_cat
                plan.category_updates.append((entry, match_cat))
            else:
                plan.category_creates.append(entry)

        for entry in sorted(template.get("channels", []), key=_position):
            family = TYPE_FAMILY.get(entry.get("type"))
            if family is None:
                continue
            match_ch: Optional[GuildChannel] = None
            if plan.same_guild:
                existing = guild.get_channel(_int(entry.get("id")) or 0)
                if (
                    existing is not None
                    and existing.id not in used_channels
                    and TYPE_FAMILY.get(self._channel_type_key(existing)) == family
                ):
                    match_ch = existing
            if match_ch is None:
                wanted_parent = plan.category_map.get(str(entry.get("category_id")))
                same_name = [
                    c
                    for c in guild.channels
                    if not isinstance(c, discord.CategoryChannel)
                    and c.id not in used_channels
                    and c.name == entry.get("name")
                    and TYPE_FAMILY.get(self._channel_type_key(c)) == family
                ]
                same_name.sort(key=lambda c: (c.category != wanted_parent, c.position))
                match_ch = same_name[0] if same_name else None
            if match_ch is not None:
                used_channels.add(match_ch.id)
                plan.channel_map[str(entry["id"])] = match_ch
                plan.channel_updates.append((entry, match_ch))
            else:
                plan.channel_creates.append(entry)

        if replace and COMP_CHANNELS in components:
            protected = {keep_channel_id}
            for special in (
                guild.rules_channel,
                guild.public_updates_channel,
                getattr(guild, "safety_alerts_channel", None),
            ):
                if special is not None:
                    protected.add(special.id)
            for channel in guild.channels:
                if channel.id in used_channels or channel.id in protected:
                    continue
                plan.channel_deletes.append(channel)
            kept = guild.get_channel(keep_channel_id or 0)
            if kept is not None and kept.id not in used_channels:
                plan.kept_channel = kept

        # Emojis.
        if COMP_EMOJIS in components:
            template_names = {e["name"] for e in template.get("emojis", [])}
            if replace:
                plan.emoji_deletes = [e for e in guild.emojis if not e.managed and e.name not in template_names]
            deleted_ids = {e.id for e in plan.emoji_deletes}
            remaining = [e for e in guild.emojis if e.id not in deleted_ids]
            existing_names = {e.name for e in remaining}
            free_static = guild.emoji_limit - len([e for e in remaining if not e.animated])
            free_animated = guild.emoji_limit - len([e for e in remaining if e.animated])
            for entry in template.get("emojis", []):
                if entry["name"] in existing_names:
                    plan.emoji_existing += 1
                    continue
                if entry.get("animated"):
                    if free_animated <= 0:
                        plan.emoji_over_limit += 1
                        continue
                    free_animated -= 1
                else:
                    if free_static <= 0:
                        plan.emoji_over_limit += 1
                        continue
                    free_static -= 1
                existing_names.add(entry["name"])
                plan.emoji_creates.append(entry)
            if plan.emoji_over_limit:
                plan.warnings.append(
                    f"{plan.emoji_over_limit} emoji(s) exceed this server's emoji limit and will be skipped."
                )

        if COMP_IDENTITY in components and template.get("identity", {}).get("icon") and not plan.same_guild:
            plan.warnings.append("The server name and icon will be overwritten with the template's.")
        return plan

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._apply_locks:
            self._apply_locks[guild_id] = asyncio.Lock()
        return self._apply_locks[guild_id]

    async def start_apply(
        self,
        interaction: discord.Interaction,
        source_view: AdminView,
        template_id: str,
        components: Set[str],
        mode: str,
    ) -> None:
        guild = interaction.guild
        member = interaction.user
        meta = self.get_meta(template_id)
        if guild is None or not _is_admin(member):
            await source_view.deny(interaction, NOT_ADMIN_MSG)
            return
        if meta is None or not self.can_access(member, meta):
            await source_view.deny(interaction, "That template no longer exists or you no longer have access to it.")
            return
        template = await self.read_template(template_id)
        if template is None:
            await source_view.deny(interaction, "The template file is missing on disk.")
            return
        lock = self._lock_for(guild.id)
        if lock.locked():
            await source_view.deny(interaction, "A template is already being applied to this server.")
            return

        message = source_view.message or interaction.message
        source_view.stop()
        starting = discord.Embed(
            title=f"Applying {meta['name']}",
            description="Starting...",
            color=discord.Color.orange(),
        )
        await interaction.response.edit_message(embed=starting, view=None)
        if message is None:
            message = await interaction.original_response()

        async with lock:
            plan = self.build_plan(
                guild,
                template,
                components,
                mode,
                keep_channel_id=interaction.channel_id,
                protected_role_ids={r.id for r in member.roles},
            )
            if plan.blockers:
                embed = self.plan_embed(plan)
                results = None
            else:
                progress = Progress(message, f"Applying {meta['name']}", plan.total_ops())
                try:
                    results = await self._execute_plan(plan, member, progress)
                except Exception as e:
                    log.exception("Template apply crashed in guild %s", guild.id)
                    results = ApplyResults()
                    results.fail("General", "apply aborted", e)
                embed = self.results_embed(plan, results)

        view = MainView(self, member.id, message)
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            try:
                await member.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _execute_plan(self, plan: ApplyPlan, member: discord.Member, progress: Progress) -> ApplyResults:
        guild = plan.guild
        template = plan.template
        results = ApplyResults()
        reason = _truncate(f"Server template '{template.get('name')}' applied by {member} ({member.id})", 512)

        async def op(section: str, action: str, label: str, factory: Callable[[], Any]) -> Any:
            try:
                value = await factory()
                results.add(section, action)
                return value
            except Exception as e:
                results.fail(section, label, e)
                log.warning("Template apply: %s %s failed: %s", section, label, e)
                return None
            finally:
                await progress.tick()
                await asyncio.sleep(OP_DELAY)

        if COMP_IDENTITY in plan.components:
            await progress.set_stage("Server name and icon")
            await op("Identity", "updated", "server name/icon", lambda: self._apply_identity(guild, template, reason))

        if COMP_ROLES in plan.components:
            await progress.set_stage("Roles")
            deleted_role_ids: Set[int] = set()
            for role in plan.role_deletes:
                await op("Roles", "deleted", role.name, lambda r=role: r.delete(reason=reason))
                deleted_role_ids.add(role.id)
            default_entry = next((r for r in template.get("roles", []) if r.get("default")), None)
            if default_entry is not None:
                await op(
                    "Roles",
                    "updated",
                    "@everyone",
                    lambda: guild.default_role.edit(
                        permissions=discord.Permissions(_int(default_entry.get("permissions")) or 0), reason=reason
                    ),
                )
            else:
                await progress.tick()
            for entry, role in plan.role_updates:
                await op(
                    "Roles",
                    "updated",
                    role.name,
                    lambda e=entry, r=role: r.edit(reason=reason, **self._role_kwargs(guild, e)),
                )
            for entry in plan.role_creates:
                created = await op(
                    "Roles",
                    "created",
                    str(entry.get("name")),
                    lambda e=entry: guild.create_role(reason=reason, **self._role_kwargs(guild, e)),
                )
                if created is not None:
                    plan.role_map[str(entry["id"])] = created
            if plan.role_above_bot:
                results.add("Roles", "skipped (above bot)", len(plan.role_above_bot))
            await op(
                "Roles",
                "reordered",
                "role order",
                lambda: self._apply_role_positions(plan, deleted_role_ids, reason),
            )

        if COMP_EMOJIS in plan.components:
            await progress.set_stage("Emojis")
            for emoji in plan.emoji_deletes:
                await op("Emojis", "deleted", emoji.name, lambda e=emoji: e.delete(reason=reason))
            for entry in plan.emoji_creates:
                await op(
                    "Emojis",
                    "created",
                    entry["name"],
                    lambda e=entry: self._create_emoji(plan, e, reason),
                )
            if plan.emoji_existing:
                results.add("Emojis", "already present", plan.emoji_existing)
            if plan.emoji_over_limit:
                results.add("Emojis", "skipped (limit)", plan.emoji_over_limit)

        if COMP_CHANNELS in plan.components:
            await progress.set_stage("Channels")
            skipped_overwrites = 0
            for channel in plan.channel_deletes:
                await op("Channels", "deleted", channel.name, lambda c=channel: c.delete(reason=reason))

            for entry, category in plan.category_updates:
                overwrites, skipped = self._resolve_overwrites(plan, entry)
                skipped_overwrites += skipped
                await op(
                    "Categories",
                    "updated",
                    category.name,
                    lambda e=entry, c=category, o=overwrites: c.edit(
                        name=_truncate(str(e["name"]), 100), overwrites=o, reason=reason
                    ),
                )
            for entry in plan.category_creates:
                overwrites, skipped = self._resolve_overwrites(plan, entry)
                skipped_overwrites += skipped
                created = await op(
                    "Categories",
                    "created",
                    str(entry["name"]),
                    lambda e=entry, o=overwrites: guild.create_category(
                        _truncate(str(e["name"]), 100), overwrites=o, reason=reason
                    ),
                )
                if created is not None:
                    plan.category_map[str(entry["id"])] = created

            for entry, channel in plan.channel_updates:
                overwrites, skipped = self._resolve_overwrites(plan, entry)
                skipped_overwrites += skipped
                await op(
                    "Channels",
                    "updated",
                    channel.name,
                    lambda e=entry, c=channel, o=overwrites: self._edit_channel(plan, c, e, o, reason),
                )
            for entry in plan.channel_creates:
                overwrites, skipped = self._resolve_overwrites(plan, entry)
                skipped_overwrites += skipped
                created = await op(
                    "Channels",
                    "created",
                    str(entry["name"]),
                    lambda e=entry, o=overwrites: self._create_channel(plan, e, o, reason),
                )
                if created is not None:
                    plan.channel_map[str(entry["id"])] = created
            await op("Channels", "reordered", "channel order", lambda: self._apply_channel_positions(plan, reason))
            if skipped_overwrites:
                results.add("Channels", "overwrites skipped (missing role/member)", skipped_overwrites)

        if COMP_SETTINGS in plan.components:
            await progress.set_stage("Server settings")
            try:
                failed = await self._apply_settings(plan, reason)
                results.add("Settings", "applied")
                for key, error in failed:
                    results.fail("Settings", key, error)
            except Exception as e:
                results.fail("Settings", "server settings", e)
            await progress.tick()

        return results

    @staticmethod
    def _role_kwargs(guild: discord.Guild, entry: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "name": _truncate(str(entry.get("name") or "role"), 100),
            "permissions": discord.Permissions(_int(entry.get("permissions")) or 0),
            "colour": discord.Colour(_int(entry.get("color")) or 0),
            "hoist": bool(entry.get("hoist")),
            "mentionable": bool(entry.get("mentionable")),
        }
        if entry.get("unicode_emoji") and "ROLE_ICONS" in guild.features:
            kwargs["display_icon"] = str(entry["unicode_emoji"])
        return kwargs

    async def _apply_role_positions(self, plan: ApplyPlan, deleted_ids: Set[int], reason: str) -> None:
        guild = plan.guild
        bot_top = guild.me.top_role
        ordered: List[discord.Role] = []
        seen: Set[int] = set(deleted_ids)
        for entry in sorted((r for r in plan.template.get("roles", []) if not r.get("default")), key=_position):
            role = plan.role_map.get(str(entry["id"]))
            if role is None or role.id in seen or role.is_default() or not role < bot_top:
                continue
            ordered.append(role)
            seen.add(role.id)
        # Roles outside the template (bots, integrations, kept roles) stay above the template's roles.
        others = sorted(
            (r for r in guild.roles if not r.is_default() and r.id not in seen and r < bot_top),
            key=lambda r: r.position,
        )
        final = ordered + others
        if not final:
            return
        await guild.edit_role_positions(positions={role: i + 1 for i, role in enumerate(final)}, reason=reason)

    async def _create_emoji(self, plan: ApplyPlan, entry: Dict[str, Any], reason: str) -> discord.Emoji:
        image = base64.b64decode(entry["image"])
        roles = [plan.role_map[str(rid)] for rid in entry.get("roles", []) if str(rid) in plan.role_map]
        kwargs: Dict[str, Any] = {"name": entry["name"], "image": image, "reason": reason}
        roles = [r for r in roles if not r.is_default()]
        if roles:
            kwargs["roles"] = roles
        return await plan.guild.create_custom_emoji(**kwargs)

    async def _apply_identity(self, guild: discord.Guild, template: Dict[str, Any], reason: str) -> None:
        identity = template.get("identity") or {}
        kwargs: Dict[str, Any] = {}
        name = str(identity.get("name") or "").strip()
        if 2 <= len(name) <= 100:
            kwargs["name"] = name
        if identity.get("icon"):
            kwargs["icon"] = base64.b64decode(identity["icon"])
        if not kwargs:
            return
        try:
            await guild.edit(reason=reason, **kwargs)
        except discord.HTTPException:
            # Animated icons need boosts; retry with the name only.
            if "icon" not in kwargs or "name" not in kwargs:
                raise
            kwargs.pop("icon")
            await guild.edit(reason=reason, **kwargs)

    def _resolve_overwrites(
        self, plan: ApplyPlan, entry: Dict[str, Any]
    ) -> Tuple[Dict[Any, discord.PermissionOverwrite], int]:
        guild = plan.guild
        overwrites: Dict[Any, discord.PermissionOverwrite] = {}
        skipped = 0
        for raw in entry.get("overwrites") or []:
            target_id = _int(raw.get("id"))
            if target_id is None:
                skipped += 1
                continue
            target: Optional[Union[discord.Role, discord.Member]] = None
            if raw.get("type") == "role":
                target = plan.role_map.get(str(target_id))
                if target is None and plan.same_guild:
                    target = guild.get_role(target_id)
            else:
                target = guild.get_member(target_id)
            if target is None:
                skipped += 1
                continue
            allow = discord.Permissions(_int(raw.get("allow")) or 0)
            deny = discord.Permissions(_int(raw.get("deny")) or 0)
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        return overwrites, skipped

    @staticmethod
    def _text_kwargs(entry: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if entry.get("topic") is not None:
            kwargs["topic"] = str(entry["topic"])
        if "nsfw" in entry:
            kwargs["nsfw"] = bool(entry["nsfw"])
        if _int(entry.get("slowmode_delay")) is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, _int(entry["slowmode_delay"])))
        if _int(entry.get("default_auto_archive_duration")) is not None:
            kwargs["default_auto_archive_duration"] = _int(entry["default_auto_archive_duration"])
        if _int(entry.get("default_thread_slowmode_delay")) is not None:
            kwargs["default_thread_slowmode_delay"] = _int(entry["default_thread_slowmode_delay"])
        return kwargs

    @staticmethod
    def _voice_kwargs(guild: discord.Guild, entry: Dict[str, Any], *, stage: bool) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        bitrate = _int(entry.get("bitrate"))
        if bitrate:
            kwargs["bitrate"] = max(8000, min(bitrate, int(guild.bitrate_limit)))
        user_limit = _int(entry.get("user_limit"))
        if user_limit is not None:
            kwargs["user_limit"] = max(0, min(user_limit, 10000 if stage else 99))
        if "rtc_region" in entry:
            kwargs["rtc_region"] = entry["rtc_region"] or None
        vqm = _int(entry.get("video_quality_mode"))
        if vqm is not None:
            try:
                kwargs["video_quality_mode"] = discord.VideoQualityMode(vqm)
            except ValueError:
                pass
        return kwargs

    @staticmethod
    def _forum_tags(guild: discord.Guild, entry: Dict[str, Any]) -> List[Any]:
        tags = []
        for raw in (entry.get("available_tags") or [])[:20]:
            emoji: Any = None
            emoji_name = raw.get("emoji_name")
            if emoji_name and raw.get("emoji_custom"):
                emoji = discord.utils.get(guild.emojis, name=emoji_name)
            elif emoji_name:
                emoji = emoji_name
            tags.append(
                discord.ForumTag(
                    name=_truncate(str(raw.get("name") or "tag"), 20),
                    emoji=emoji,
                    moderated=bool(raw.get("moderated")),
                )
            )
        return tags

    def _forum_kwargs(self, guild: discord.Guild, entry: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if entry.get("type") == TYPE_FORUM and _int(entry.get("default_layout")) is not None:
            try:
                kwargs["default_layout"] = discord.ForumLayoutType(_int(entry["default_layout"]))
            except (ValueError, AttributeError):
                pass
        if _int(entry.get("default_sort_order")) is not None:
            try:
                kwargs["default_sort_order"] = discord.ForumOrderType(_int(entry["default_sort_order"]))
            except (ValueError, AttributeError):
                pass
        tags = self._forum_tags(guild, entry)
        if tags:
            kwargs["available_tags"] = tags
        return kwargs

    def _parent_for(self, plan: ApplyPlan, entry: Dict[str, Any]) -> Tuple[bool, Optional[discord.CategoryChannel]]:
        """Return (apply_category, category). A missing parent leaves the current category alone."""
        category_id = entry.get("category_id")
        if not category_id:
            return True, None
        parent = plan.category_map.get(str(category_id))
        if parent is None and plan.same_guild:
            maybe = plan.guild.get_channel(_int(category_id) or 0)
            if isinstance(maybe, discord.CategoryChannel):
                parent = maybe
        return parent is not None, parent

    async def _edit_channel(
        self,
        plan: ApplyPlan,
        channel: GuildChannel,
        entry: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        reason: str,
    ) -> None:
        kwargs: Dict[str, Any] = {
            "name": _truncate(str(entry["name"]), 100),
            "overwrites": overwrites,
            "reason": reason,
        }
        apply_category, parent = self._parent_for(plan, entry)
        if apply_category:
            kwargs["category"] = parent
        if isinstance(channel, discord.ForumChannel):
            kwargs.update(self._text_kwargs(entry))
            kwargs.update(self._forum_kwargs(plan.guild, entry))
        elif isinstance(channel, discord.TextChannel):
            kwargs.update(self._text_kwargs(entry))
        elif isinstance(channel, discord.StageChannel):
            kwargs.update(self._voice_kwargs(plan.guild, entry, stage=True))
        elif isinstance(channel, discord.VoiceChannel):
            kwargs.update(self._voice_kwargs(plan.guild, entry, stage=False))
            if "nsfw" in entry:
                kwargs["nsfw"] = bool(entry["nsfw"])
            if _int(entry.get("slowmode_delay")) is not None:
                kwargs["slowmode_delay"] = max(0, min(21600, _int(entry["slowmode_delay"])))
        await channel.edit(**kwargs)

    async def _create_channel(
        self,
        plan: ApplyPlan,
        entry: Dict[str, Any],
        overwrites: Dict[Any, discord.PermissionOverwrite],
        reason: str,
    ) -> GuildChannel:
        guild = plan.guild
        type_key = entry.get("type")
        name = _truncate(str(entry["name"]), 100)
        _, parent = self._parent_for(plan, entry)
        base: Dict[str, Any] = {"category": parent, "overwrites": overwrites, "reason": reason}
        community = "COMMUNITY" in guild.features

        if type_key in (TYPE_FORUM, TYPE_MEDIA):
            kwargs = {**base, **self._text_kwargs(entry), **self._forum_kwargs(guild, entry)}
            try:
                if type_key == TYPE_MEDIA:
                    try:
                        return await guild.create_forum(name, media=True, **kwargs)
                    except TypeError:
                        pass
                return await guild.create_forum(name, **kwargs)
            except (discord.HTTPException, TypeError):
                log.info("Forum creation failed in guild %s, falling back to a text channel", guild.id)
            type_key = TYPE_TEXT

        if type_key == TYPE_STAGE:
            if community:
                try:
                    return await guild.create_stage_channel(
                        name, **base, **self._voice_kwargs(guild, entry, stage=True)
                    )
                except (discord.HTTPException, TypeError):
                    log.info("Stage creation failed in guild %s, falling back to voice", guild.id)
            type_key = TYPE_VOICE

        if type_key == TYPE_VOICE:
            return await guild.create_voice_channel(name, **base, **self._voice_kwargs(guild, entry, stage=False))

        text_kwargs = self._text_kwargs(entry)
        if type_key == TYPE_NEWS and community:
            try:
                return await guild.create_text_channel(name, news=True, **base, **text_kwargs)
            except discord.HTTPException:
                log.info("Announcement channel creation failed in guild %s, falling back to text", guild.id)
        return await guild.create_text_channel(name, **base, **text_kwargs)

    async def _apply_channel_positions(self, plan: ApplyPlan, reason: str) -> None:
        payload: List[Dict[str, Any]] = []
        for index, entry in enumerate(sorted(plan.template.get("categories", []), key=_position)):
            category = plan.category_map.get(str(entry["id"]))
            if category is not None:
                payload.append({"id": category.id, "position": index})
        for index, entry in enumerate(sorted(plan.template.get("channels", []), key=_position)):
            channel = plan.channel_map.get(str(entry["id"]))
            if channel is not None:
                payload.append({"id": channel.id, "position": index})
        if payload:
            await self.bot.http.bulk_channel_update(plan.guild.id, payload, reason=reason)

    def _map_channel(self, plan: ApplyPlan, old_id: Any) -> Optional[GuildChannel]:
        key = str(old_id)
        channel = plan.channel_map.get(key) or plan.category_map.get(key)
        if channel is None and plan.same_guild:
            channel = plan.guild.get_channel(_int(old_id) or 0)
        return channel

    async def _apply_settings(self, plan: ApplyPlan, reason: str) -> List[Tuple[str, Exception]]:
        guild = plan.guild
        settings = plan.template.get("settings") or {}
        kwargs: Dict[str, Any] = {}

        def set_enum(key: str, enum_cls: Any) -> None:
            value = _int(settings.get(key))
            if value is None:
                return
            try:
                kwargs[key] = enum_cls(value)
            except ValueError:
                pass

        set_enum("verification_level", discord.VerificationLevel)
        set_enum("default_notifications", discord.NotificationLevel)
        set_enum("explicit_content_filter", discord.ContentFilter)
        if _int(settings.get("afk_timeout")) is not None:
            kwargs["afk_timeout"] = _int(settings["afk_timeout"])
        if _int(settings.get("system_channel_flags")) is not None:
            flags = discord.SystemChannelFlags()
            flags.value = _int(settings["system_channel_flags"])
            kwargs["system_channel_flags"] = flags
        if settings.get("preferred_locale"):
            try:
                kwargs["preferred_locale"] = discord.Locale(str(settings["preferred_locale"]))
            except ValueError:
                pass
        if "premium_progress_bar_enabled" in settings:
            kwargs["premium_progress_bar_enabled"] = bool(settings["premium_progress_bar_enabled"])

        def set_channel(key: str, setting_key: str, channel_cls: Any, nullable: bool) -> None:
            if setting_key not in settings:
                return
            old_id = settings.get(setting_key)
            if old_id is None:
                if nullable:
                    kwargs[key] = None
                return
            channel = self._map_channel(plan, old_id)
            if isinstance(channel, channel_cls):
                kwargs[key] = channel

        set_channel("afk_channel", "afk_channel_id", discord.VoiceChannel, True)
        set_channel("system_channel", "system_channel_id", discord.TextChannel, True)
        if "COMMUNITY" in guild.features:
            set_channel("rules_channel", "rules_channel_id", discord.TextChannel, False)
            set_channel("public_updates_channel", "public_updates_channel_id", discord.TextChannel, False)

        if not kwargs:
            return []
        try:
            await guild.edit(reason=reason, **kwargs)
            return []
        except discord.HTTPException:
            pass
        # One field was rejected (e.g. Community rules); apply the rest individually.
        failed: List[Tuple[str, Exception]] = []
        for key, value in kwargs.items():
            try:
                await guild.edit(reason=reason, **{key: value})
            except discord.HTTPException as e:
                failed.append((key, e))
            await asyncio.sleep(OP_DELAY)
        return failed

    # ------------------------------------------------------------------
    # Embeds
    # ------------------------------------------------------------------

    def main_embed(self, user: discord.Member) -> discord.Embed:
        accessible = len(self.accessible_templates(user))
        owned = self.count_user_templates(user.id)
        embed = discord.Embed(
            title="Server Templates",
            description=(
                "Capture a server's roles, channels, permissions, settings, and emojis into a "
                "template, then apply it to any server where you and the bot are Administrators.\n\n"
                "**Create Template** - capture this server or another server you administer.\n"
                "**Browse Templates** - view, apply, export, rename, or delete templates."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Templates you can access", value=str(accessible), inline=True)
        embed.add_field(name="Templates you own", value=f"{owned}/{MAX_TEMPLATES_PER_USER}", inline=True)
        embed.add_field(
            name="Access",
            value=(
                "A template is visible to its creator and to Administrators of the server it was "
                "captured from. Every action requires the Administrator permission."
            ),
            inline=False,
        )
        return embed

    def template_embed(self, meta: Dict[str, Any], header: Optional[str] = None) -> discord.Embed:
        description = meta.get("description") or "*No description.*"
        if header:
            description = f"**{header}**\n\n{description}"
        embed = discord.Embed(title=meta["name"], description=description, color=discord.Color.blurple())
        counts = meta["counts"]
        embed.add_field(name="Template ID", value=f"`{meta['id']}`", inline=True)
        embed.add_field(
            name="Source server",
            value=f"{meta['source_guild_name']}\n`{meta['source_guild_id']}`",
            inline=True,
        )
        embed.add_field(
            name="Created",
            value=(f"<t:{meta['created_ts']}:f>" if meta["created_ts"] else "?") + f"\nby {meta['created_by_name']}",
            inline=True,
        )
        embed.add_field(
            name="Contents",
            value=(
                f"Roles: **{counts['roles']}**\n"
                f"Categories: **{counts['categories']}**\n"
                f"Channels: **{counts['channels']}**\n"
                f"Emojis: **{counts['emojis']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Components",
            value="\n".join(f"- {COMPONENTS[k][0]}" for k in meta["components"]) or "None",
            inline=True,
        )
        embed.set_footer(text=f"Size: {meta['size'] / 1024:.1f} KB")
        return embed

    def plan_embed(self, plan: ApplyPlan, confirming: bool = False) -> discord.Embed:
        mode = "Replace" if plan.mode == MODE_REPLACE else "Merge"
        title = "Confirm apply" if confirming else "Apply plan"
        embed = discord.Embed(
            title=f"{title}: {plan.template.get('name')}",
            description=(
                f"Target: **{plan.guild.name}** - Mode: **{mode}**"
                + (" - same server as the source (matching by ID)" if plan.same_guild else "")
            ),
            color=discord.Color.red() if plan.blockers or plan.mode == MODE_REPLACE else discord.Color.blurple(),
        )
        if COMP_IDENTITY in plan.components:
            embed.add_field(name="Server name & icon", value="Will be replaced", inline=True)
        if COMP_ROLES in plan.components:
            embed.add_field(
                name="Roles",
                value=(
                    f"Create: **{len(plan.role_creates)}**\n"
                    f"Update: **{len(plan.role_updates)}**\n"
                    f"Delete: **{len(plan.role_deletes)}**\n"
                    f"Above bot (skipped): **{len(plan.role_above_bot)}**"
                ),
                inline=True,
            )
        if COMP_CHANNELS in plan.components:
            embed.add_field(
                name="Channels",
                value=(
                    f"Categories create/update: **{len(plan.category_creates)}** / **{len(plan.category_updates)}**\n"
                    f"Channels create/update: **{len(plan.channel_creates)}** / **{len(plan.channel_updates)}**\n"
                    f"Delete: **{len(plan.channel_deletes)}**"
                ),
                inline=True,
            )
        if COMP_EMOJIS in plan.components:
            embed.add_field(
                name="Emojis",
                value=(
                    f"Create: **{len(plan.emoji_creates)}**\n"
                    f"Already present: **{plan.emoji_existing}**\n"
                    f"Delete: **{len(plan.emoji_deletes)}**"
                ),
                inline=True,
            )
        if COMP_SETTINGS in plan.components:
            embed.add_field(name="Server settings", value="Will be applied", inline=True)

        if plan.mode == MODE_REPLACE and (plan.role_deletes or plan.channel_deletes or plan.emoji_deletes):
            names = [f"@{r.name}" for r in plan.role_deletes[:10]] + [f"#{c.name}" for c in plan.channel_deletes[:10]]
            embed.add_field(
                name="Will be deleted (sample)",
                value=_truncate(", ".join(names) or "Emojis only", 1024),
                inline=False,
            )
        if plan.kept_channel is not None:
            embed.add_field(
                name="Kept",
                value=f"{plan.kept_channel.mention} is kept because this panel is running in it.",
                inline=False,
            )
        if plan.warnings:
            embed.add_field(name="Warnings", value=_truncate("\n".join(f"- {w}" for w in plan.warnings), 1024), inline=False)
        if plan.blockers:
            embed.add_field(name="Cannot apply", value=_truncate("\n".join(f"- {b}" for b in plan.blockers), 1024), inline=False)
        if confirming:
            embed.set_footer(text="This cannot be undone. Capture a backup of this server first if you may need to roll back.")
        return embed

    def results_embed(self, plan: ApplyPlan, results: Optional[ApplyResults]) -> discord.Embed:
        if results is None:
            return self.plan_embed(plan)
        embed = discord.Embed(
            title=f"Applied: {plan.template.get('name')}",
            description=f"Finished applying to **{plan.guild.name}**.",
            color=discord.Color.orange() if results.failed else discord.Color.green(),
        )
        for section, counts in results.counts.items():
            embed.add_field(
                name=section,
                value="\n".join(f"{action.capitalize()}: **{n}**" for action, n in counts.items()),
                inline=True,
            )
        if results.errors:
            embed.add_field(
                name=f"Errors ({results.failed})",
                value=_truncate("\n".join(f"- {e}" for e in results.errors), 1024),
                inline=False,
            )
        return embed

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.group(name="servertemplate", aliases=["stemplate", "serverbackup"])
    @commands.guild_only()
    @guild_admin_only()
    async def servertemplate(self, ctx: commands.Context):
        """Capture and apply server templates (roles, channels, permissions, settings, emojis).

        Requires the Discord **Administrator** permission.
        """
        pass

    @servertemplate.command(name="panel", aliases=["menu", "open"])
    @commands.bot_has_permissions(embed_links=True)
    async def servertemplate_panel(self, ctx: commands.Context):
        """Open the interactive server template panel."""
        view = MainView(self, ctx.author.id)
        view.message = await ctx.send(embed=self.main_embed(ctx.author), view=view)

    @servertemplate.command(name="import")
    @commands.bot_has_permissions(embed_links=True)
    async def servertemplate_import(self, ctx: commands.Context, *, name: Optional[str] = None):
        """Import a template JSON file attached to this message (or the replied-to message).

        The imported copy is owned by you. Optional `name` overrides the template name.
        """
        attachment = ctx.message.attachments[0] if ctx.message.attachments else None
        if attachment is None and ctx.message.reference and isinstance(ctx.message.reference.resolved, discord.Message):
            ref = ctx.message.reference.resolved
            attachment = ref.attachments[0] if ref.attachments else None
        if attachment is None:
            await ctx.send("Attach a template `.json` file (or reply to a message that has one).")
            return
        if not attachment.filename.lower().endswith(".json"):
            await ctx.send("The attachment must be a `.json` file.")
            return
        if attachment.size > MAX_IMPORT_BYTES:
            await ctx.send(f"The file is too large (max {MAX_IMPORT_BYTES // (1024 * 1024)} MB).")
            return
        if self.count_user_templates(ctx.author.id) >= MAX_TEMPLATES_PER_USER:
            await ctx.send(f"You already own {MAX_TEMPLATES_PER_USER} templates. Delete one before importing.")
            return

        try:
            raw = json.loads((await attachment.read()).decode("utf-8"))
            data = self.normalize_template(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            await ctx.send(f"Could not parse the file as JSON: {e}")
            return
        except ValueError as e:
            await ctx.send(f"That file is not a valid server template: {e}")
            return

        data["id"] = self._new_id()
        data["created_by"] = {"id": str(ctx.author.id), "name": str(ctx.author)}
        if name:
            data["name"] = _truncate(name.strip(), 64)
        meta = await self.write_template(data)

        view = DetailView(self, ctx.author.id, meta["id"])
        view.message = await ctx.send(embed=self.template_embed(meta, header="Template imported."), view=view)


async def setup(bot: Red):
    await bot.add_cog(ServerTemplate(bot))
