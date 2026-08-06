import asyncio
import logging
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.autovc")

PANEL_CUSTOM_ID_PREFIX = "autovc:panel:"
ACCESS_CUSTOM_ID_PREFIX = "autovc:access:"


class SetLimitModal(Modal, title="Set user limit"):
    """Modal for setting VC user limit from the panel."""

    limit_input = TextInput(
        label="User limit",
        placeholder="0 = no limit (1-99)",
        required=True,
        min_length=1,
        max_length=2,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.limit_input.value.strip()
        try:
            limit = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a number between 0 and 99.",
                ephemeral=True,
            )
            return
        if limit < 0 or limit > 99:
            await interaction.response.send_message(
                "Limit must be between 0 and 99 (0 = no limit).",
                ephemeral=True,
            )
            return
        await self.cog._panel_set_limit(interaction, limit)


class RenameVCModal(Modal, title="Rename VC"):
    """Modal for renaming the current owned VC from the panel."""

    name_input = TextInput(
        label="New VC name",
        placeholder="Leave blank to reset to default (e.g. Chill VC)",
        required=False,
        min_length=0,
        max_length=100,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        await self.cog._panel_rename_vc(interaction, name[:100] if name else None)


class VCPanelView(View):
    """Panel view with buttons for VC control. Persistent (timeout=None)."""

    def __init__(self, cog: "AutoVC"):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def _dispatch(self, interaction: discord.Interaction, action: str):
        if action == "limit":
            await interaction.response.send_modal(SetLimitModal(self.cog))
            return
        if action == "rename":
            await interaction.response.send_modal(RenameVCModal(self.cog))
            return
        await self.cog._panel_action(interaction, action)

    @discord.ui.button(
        label="Lock VC",
        style=discord.ButtonStyle.primary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}lock",
        row=0,
    )
    async def lock_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "lock")

    @discord.ui.button(
        label="Unlock VC",
        style=discord.ButtonStyle.primary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}unlock",
        row=0,
    )
    async def unlock_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "unlock")

    @discord.ui.button(
        label="Hide VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}hide",
        row=0,
    )
    async def hide_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "hide")

    @discord.ui.button(
        label="Show VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}show",
        row=0,
    )
    async def show_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "show")

    @discord.ui.button(
        label="Set user limit",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}limit",
        row=1,
    )
    async def limit_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "limit")

    @discord.ui.button(
        label="Rename VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}rename",
        row=1,
    )
    async def rename_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "rename")


class UserSearchModal(Modal, title="Grant VC Access"):
    search_input = TextInput(
        label="Search for a user",
        placeholder="Enter a username or display name",
        required=True,
        min_length=1,
        max_length=100,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            await interaction.response.send_message("Could not determine the voice channel.", ephemeral=True)
            return

        query = self.search_input.value.strip().lower()

        def rank(m: discord.Member) -> int:
            dn = m.display_name.lower()
            un = m.name.lower()
            if dn == query or un == query:
                return 0
            if dn.startswith(query) or un.startswith(query):
                return 1
            return 2

        candidates = [
            m for m in interaction.guild.members
            if not m.bot
            and m.id != interaction.user.id
            and (query in m.display_name.lower() or query in m.name.lower())
        ]
        candidates.sort(key=rank)
        truncated = len(candidates) > 25
        results = candidates[:25]

        if not results:
            await interaction.response.send_message(
                f"No members found matching **{discord.utils.escape_markdown(self.search_input.value.strip())}**.",
                ephemeral=True,
            )
            return

        note = "\n*Showing first 25 results — refine your search to narrow down.*" if truncated else ""
        view = UserSearchResultView(self.cog, results, vc)
        await interaction.response.send_message(
            f"Select a user to grant access:{note}",
            view=view,
            ephemeral=True,
        )


class UserSearchResultView(View):
    def __init__(self, cog: "AutoVC", members: List[discord.Member], vc: discord.VoiceChannel):
        super().__init__(timeout=60)
        self.cog = cog
        self.vc = vc

        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=(f"@{m.name}" if m.display_name != m.name else None),
            )
            for m in members
        ]
        self._select = discord.ui.Select(
            placeholder="Choose a user...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return
        member = interaction.guild.get_member(int(self._select.values[0]))
        if not member:
            await interaction.followup.send("User not found — they may have left the server.", ephemeral=True)
            return
        try:
            overwrite = self.vc.overwrites_for(member)
            overwrite.view_channel = True
            overwrite.connect = True
            await self.vc.set_permissions(member, overwrite=overwrite)
            await interaction.followup.send(
                f"Granted {member.mention} access to {self.vc.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning(f"Failed to grant VC access: {e}")
            await interaction.followup.send("Failed to grant access. Check bot permissions.", ephemeral=True)


class RevokeSelectView(View):
    def __init__(self, cog: "AutoVC", members: List[discord.Member], vc: discord.VoiceChannel):
        super().__init__(timeout=60)
        self.cog = cog
        self.vc = vc

        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=(f"@{m.name}" if m.display_name != m.name else None),
            )
            for m in members
        ]
        self._select = discord.ui.Select(
            placeholder="Choose a user to remove...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return
        member = interaction.guild.get_member(int(self._select.values[0]))
        if not member:
            await interaction.followup.send("User not found — they may have left the server.", ephemeral=True)
            return
        try:
            await self.vc.set_permissions(member, overwrite=None)
            await interaction.followup.send(
                f"Revoked {member.mention}'s access to {self.vc.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning(f"Failed to revoke VC access: {e}")
            await interaction.followup.send("Failed to revoke access. Check bot permissions.", ephemeral=True)


class AccessManagementView(View):
    """Persistent view sent in VC chat on creation. Lets the owner grant/revoke user access."""

    def __init__(self, cog: "AutoVC"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Grant Access",
        style=discord.ButtonStyle.success,
        custom_id=f"{ACCESS_CUSTOM_ID_PREFIX}grant",
    )
    async def grant_btn(self, interaction: discord.Interaction, button: Button):
        err = await self.cog._check_access_owner(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.send_modal(UserSearchModal(self.cog))

    @discord.ui.button(
        label="Revoke Access",
        style=discord.ButtonStyle.danger,
        custom_id=f"{ACCESS_CUSTOM_ID_PREFIX}revoke",
    )
    async def revoke_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        err = await self.cog._check_access_owner(interaction)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            await interaction.followup.send("Could not determine the voice channel.", ephemeral=True)
            return

        created_vcs = await self.cog.config.guild(interaction.guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        owner_id = vc_data.get("owner_id") if vc_data else 0

        whitelisted = self.cog._get_whitelisted_members(vc, owner_id or 0)
        if not whitelisted:
            await interaction.followup.send(
                "No users have been granted access to this VC.", ephemeral=True
            )
            return

        view = RevokeSelectView(self.cog, whitelisted, vc)
        await interaction.followup.send("Select a user to revoke access from:", view=view, ephemeral=True)


def _bound_button(
    label: str,
    style: discord.ButtonStyle,
    emoji: Optional[str],
    callback,
    row: Optional[int] = None,
) -> Button:
    """Build a Button with a pre-bound async callback (for dynamically assembled views)."""
    btn = Button(label=label, style=style, emoji=emoji, row=row)
    btn.callback = callback
    return btn


class _GhostChannel:
    """Stand-in for a configured source VC whose Discord channel was deleted, so it can still be selected."""

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.name = f"Deleted channel ({channel_id})"
        self.mention = f"`{channel_id}`"
        self.category = None


class TypeSelect(discord.ui.Select):
    """Static dropdown for the three AutoVC source types."""

    def __init__(self, on_choose, current: Optional[str] = None):
        options = [
            discord.SelectOption(
                label="Public", value="public", emoji="🔓",
                description="Anyone can join, no owner",
                default=(current == "public"),
            ),
            discord.SelectOption(
                label="Personal", value="personal", emoji="🙋",
                description="Owner-controlled, visible by default",
                default=(current == "personal"),
            ),
            discord.SelectOption(
                label="Private", value="private", emoji="🔒",
                description="Owner-controlled, hidden by default",
                default=(current == "private"),
            ),
        ]
        super().__init__(placeholder="Choose a VC type...", options=options)
        self._on_choose = on_choose

    async def callback(self, interaction: discord.Interaction):
        await self._on_choose(interaction, self.values[0])


class ModeSelect(discord.ui.Select):
    """Static dropdown for name pool selection mode."""

    def __init__(self, on_choose, current: str = "sequential"):
        options = [
            discord.SelectOption(
                label="Sequential", value="sequential",
                description="Use names in order", default=(current == "sequential"),
            ),
            discord.SelectOption(
                label="Random", value="random",
                description="Pick a random name each time", default=(current == "random"),
            ),
        ]
        super().__init__(placeholder="Pool selection mode...", options=options)
        self._on_choose = on_choose

    async def callback(self, interaction: discord.Interaction):
        await self._on_choose(interaction, self.values[0])


class CategorySelect(discord.ui.Select):
    """Dropdown of guild categories, optionally with a 'use this VC's own category' option."""

    def __init__(
        self,
        categories: List[discord.CategoryChannel],
        on_choose,
        own_category: Optional[discord.CategoryChannel] = None,
        current_category_id: Optional[int] = None,
    ):
        options = []
        if own_category is not None:
            options.append(
                discord.SelectOption(
                    label=f"Use this VC's category ({own_category.name})"[:100],
                    value="__own__",
                    default=(current_category_id is None),
                )
            )
        remaining = 25 - len(options)
        for c in categories[:remaining]:
            options.append(
                discord.SelectOption(
                    label=c.name[:100],
                    value=str(c.id),
                    default=(current_category_id is not None and current_category_id == c.id),
                )
            )
        super().__init__(placeholder="Choose a category...", options=options)
        self._on_choose = on_choose
        self._own_category = own_category

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        category = self._own_category if value == "__own__" else interaction.guild.get_channel(int(value))
        await self._on_choose(interaction, category)


class PickerSelect(discord.ui.Select):
    """Generic dropdown of arbitrary objects (channels, roles, ...) by id."""

    def __init__(self, items: List, item_label, on_select, allow_clear: bool = False, clear_label: str = "Clear"):
        options = []
        if allow_clear:
            options.append(discord.SelectOption(label=clear_label[:100], value="__clear__", emoji="🚫"))
        max_items = 25 - len(options)
        for item in items[:max_items]:
            options.append(discord.SelectOption(label=item_label(item)[:100], value=str(item.id)))
        super().__init__(placeholder="Choose one...", options=options, min_values=1, max_values=1)
        self._items_by_id = {str(item.id): item for item in items}
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        obj = None if value == "__clear__" else self._items_by_id.get(value)
        await self._on_select(interaction, obj)


class PickerResultView(View):
    """Ephemeral view holding a single PickerSelect."""

    def __init__(self, items: List, item_label, on_select, allow_clear: bool = False, clear_label: str = "Clear", timeout: int = 60):
        super().__init__(timeout=timeout)
        self.add_item(PickerSelect(items, item_label, on_select, allow_clear=allow_clear, clear_label=clear_label))


class PickerSearchModal(Modal):
    """Generic 'search then select' modal, used when a candidate list may exceed 25 entries."""

    def __init__(
        self,
        title: str,
        items: List,
        item_label,
        on_select,
        allow_clear: bool = False,
        clear_label: str = "Clear",
        empty_msg: str = "No matches found.",
    ):
        super().__init__(title=title[:45])
        self.items = items
        self.item_label = item_label
        self.on_select = on_select
        self.allow_clear = allow_clear
        self.clear_label = clear_label
        self.empty_msg = empty_msg
        self.query_input = TextInput(
            label="Search (leave blank to show all)",
            required=False,
            max_length=100,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction):
        query = self.query_input.value.strip().lower()
        matches = [i for i in self.items if not query or query in self.item_label(i).lower()]
        if not matches and not self.allow_clear:
            await interaction.response.send_message(self.empty_msg, ephemeral=True)
            return
        max_items = 25 - (1 if self.allow_clear else 0)
        truncated = len(matches) > max_items
        note = "\n*Showing first results — refine your search to narrow down.*" if truncated else ""
        view = PickerResultView(matches, self.item_label, self.on_select, allow_clear=self.allow_clear, clear_label=self.clear_label)
        await interaction.response.send_message(f"Select one:{note}", view=view, ephemeral=True)


class ConfirmView(View):
    """Generic Yes/No confirmation view, restricted to the user who triggered it."""

    def __init__(self, user_id: int, on_confirm, on_cancel=None, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if self.on_cancel:
            await self.on_cancel(interaction)
        else:
            await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class NameTemplateModal(Modal):
    """Modal for setting/clearing a source VC's name template."""

    def __init__(self, editor: "SourceEditorView", current: str):
        super().__init__(title="Set Name Template")
        self.editor = editor
        self.template_input = TextInput(
            label="Template ({num}, {user})",
            placeholder="e.g. Squad {num}  (blank = default naming)",
            required=False,
            max_length=100,
            default=current or None,
        )
        self.add_item(self.template_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        editor = self.editor
        source_vcs, cfg = await editor._get_cfg(guild)
        if not cfg:
            await interaction.response.send_message("This source VC no longer exists.", ephemeral=True)
            return
        cfg["name_template"] = self.template_input.value.strip() or None
        source_vcs[str(editor.source_vc_id)] = cfg
        await editor.cog.config.guild(guild).source_vcs.set(source_vcs)
        embed, view = await editor.build(guild)
        await interaction.response.edit_message(embed=embed, view=view)
        await editor.dashboard.refresh()


class AddNameModal(Modal, title="Add Name to Pool"):
    """Modal for adding a single name to a source VC's name pool."""

    name_input = TextInput(label="Name", max_length=100, required=True)

    def __init__(self, pool_view: "NamePoolView"):
        super().__init__()
        self.pool_view = pool_view

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        editor = self.pool_view.editor
        source_vcs, cfg = await editor._get_cfg(guild)
        if not cfg:
            await interaction.response.send_message("This source VC no longer exists.", ephemeral=True)
            return
        pool: List[str] = cfg.setdefault("name_pool", [])
        pool.append(self.name_input.value.strip()[:100])
        source_vcs[str(editor.source_vc_id)] = cfg
        await editor.cog.config.guild(guild).source_vcs.set(source_vcs)
        embed, view = await self.pool_view.build(guild)
        await interaction.response.edit_message(embed=embed, view=view)


class NamePoolView(View):
    """Sub-view for managing a source VC's name pool, reached from SourceEditorView."""

    def __init__(self, editor: "SourceEditorView"):
        super().__init__(timeout=300)
        self.editor = editor

    async def build(self, guild: discord.Guild):
        _, cfg = await self.editor._get_cfg(guild)
        self.clear_items()

        if not cfg:
            embed = discord.Embed(title="Source VC no longer exists", color=discord.Color.red())
            self.add_item(_bound_button("Close", discord.ButtonStyle.secondary, "↩️", self._back_to_editor_cb()))
            return embed, self

        pool: List[str] = cfg.get("name_pool", [])
        mode = cfg.get("name_pool_mode", "sequential")
        vc = guild.get_channel(self.editor.source_vc_id)

        embed = discord.Embed(
            title=f"Name Pool: {vc.name if vc else self.editor.source_vc_id}",
            color=await self.editor.cog.bot.get_embed_color(guild),
        )
        if pool:
            lines = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(pool))
            if len(lines) > 1024:
                lines = lines[:1000] + "\n…(truncated)"
            embed.add_field(name=f"Names ({len(pool)})", value=lines, inline=False)
        else:
            embed.add_field(name="Names", value="No names in pool yet. Add one below.", inline=False)
        embed.add_field(name="Mode", value=mode, inline=False)
        embed.set_footer(text="If the pool is empty, the name template (or default naming) is used instead.")

        self.add_item(ModeSelect(self._on_mode_change, current=mode))
        self.add_item(_bound_button("Add Name", discord.ButtonStyle.success, "➕", self._on_add_name))
        if pool:
            self.add_item(_bound_button("Remove Name", discord.ButtonStyle.secondary, "➖", self._on_remove_name))
            self.add_item(_bound_button("Clear Pool", discord.ButtonStyle.danger, "🗑️", self._on_clear_pool))
        self.add_item(_bound_button("Back", discord.ButtonStyle.secondary, "↩️", self._back_to_editor_cb()))
        return embed, self

    def _back_to_editor_cb(self):
        async def cb(interaction: discord.Interaction):
            embed, view = await self.editor.build(interaction.guild)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        return cb

    async def _on_mode_change(self, interaction: discord.Interaction, mode: str):
        guild = interaction.guild
        source_vcs, cfg = await self.editor._get_cfg(guild)
        if not cfg:
            await interaction.response.send_message("This source VC no longer exists.", ephemeral=True)
            return
        cfg["name_pool_mode"] = mode
        source_vcs[str(self.editor.source_vc_id)] = cfg
        await self.editor.cog.config.guild(guild).source_vcs.set(source_vcs)
        embed, view = await self.build(guild)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_add_name(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddNameModal(self))

    async def _on_remove_name(self, interaction: discord.Interaction):
        guild = interaction.guild
        _, cfg = await self.editor._get_cfg(guild)
        pool: List[str] = (cfg or {}).get("name_pool", [])
        if not pool:
            await interaction.response.send_message("Pool is empty.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=name[:100], value=str(i))
            for i, name in enumerate(pool[:25])
        ]
        select = discord.ui.Select(placeholder="Choose a name to remove...", options=options)
        pool_view = self

        async def on_remove(sel_interaction: discord.Interaction):
            idx = int(select.values[0])
            source_vcs, cfg2 = await pool_view.editor._get_cfg(guild)
            pool2: List[str] = (cfg2 or {}).get("name_pool", [])
            if cfg2 and idx < len(pool2):
                removed = pool2.pop(idx)
                cfg2["name_pool"] = pool2
                cfg2["name_pool_counter"] = 0
                source_vcs[str(pool_view.editor.source_vc_id)] = cfg2
                await pool_view.editor.cog.config.guild(guild).source_vcs.set(source_vcs)
                log.info(f"Removed name '{removed}' from pool for source VC {pool_view.editor.source_vc_id}")
            embed, view = await pool_view.build(guild)
            await sel_interaction.response.edit_message(content=None, embed=embed, view=view)

        select.callback = on_remove
        remove_view = View(timeout=120)
        remove_view.add_item(select)
        remove_view.add_item(_bound_button("Cancel", discord.ButtonStyle.secondary, "↩️", self._back_to_pool_cb()))
        await interaction.response.edit_message(content="Select a name to remove:", embed=None, view=remove_view)

    def _back_to_pool_cb(self):
        async def cb(interaction: discord.Interaction):
            embed, view = await self.build(interaction.guild)
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        return cb

    async def _on_clear_pool(self, interaction: discord.Interaction):
        guild = interaction.guild

        async def on_confirm(confirm_interaction: discord.Interaction):
            source_vcs, cfg = await self.editor._get_cfg(guild)
            if cfg:
                cfg["name_pool"] = []
                cfg["name_pool_counter"] = 0
                source_vcs[str(self.editor.source_vc_id)] = cfg
                await self.editor.cog.config.guild(guild).source_vcs.set(source_vcs)
            embed, view = await self.build(guild)
            await confirm_interaction.response.edit_message(content=None, embed=embed, view=view)

        async def on_cancel(cancel_interaction: discord.Interaction):
            embed, view = await self.build(guild)
            await cancel_interaction.response.edit_message(content=None, embed=embed, view=view)

        view = ConfirmView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.edit_message(
            content="Clear the entire name pool? This cannot be undone.", embed=None, view=view
        )


class SourceEditorView(View):
    """Interactive editor for a single configured source VC, reached from AutoVCDashboardView."""

    def __init__(self, cog: "AutoVC", dashboard: "AutoVCDashboardView", guild_id: int, source_vc_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.dashboard = dashboard
        self.guild_id = guild_id
        self.source_vc_id = source_vc_id

    async def _get_cfg(self, guild: discord.Guild):
        source_vcs = await self.cog.config.guild(guild).source_vcs()
        return source_vcs, source_vcs.get(str(self.source_vc_id))

    async def build(self, guild: discord.Guild):
        source_vcs, cfg = await self._get_cfg(guild)
        vc = guild.get_channel(self.source_vc_id)
        self.clear_items()

        if not cfg:
            embed = discord.Embed(
                title="Source VC no longer exists",
                description="This configuration was already removed.",
                color=discord.Color.red(),
            )
            self.add_item(_bound_button("Close", discord.ButtonStyle.secondary, "↩️", self._on_close))
            return embed, self

        if not vc:
            embed = discord.Embed(
                title="Source channel deleted",
                description=(
                    f"The channel (ID `{self.source_vc_id}`) no longer exists. "
                    "You can remove this stale configuration."
                ),
                color=discord.Color.orange(),
            )
            self.add_item(_bound_button("Delete Source", discord.ButtonStyle.danger, "🗑️", self._on_delete))
            self.add_item(_bound_button("Close", discord.ButtonStyle.secondary, "↩️", self._on_close))
            return embed, self

        category = guild.get_channel(cfg.get("category_id")) if cfg.get("category_id") else None
        name_template = cfg.get("name_template")
        name_pool: List[str] = cfg.get("name_pool", [])
        name_pool_mode = cfg.get("name_pool_mode", "sequential")

        embed = discord.Embed(
            title=f"Editing: {vc.name}",
            color=await self.cog.bot.get_embed_color(guild),
        )
        embed.add_field(name="Type", value=cfg.get("type", "unknown"), inline=True)
        embed.add_field(name="Category", value=category.mention if category else "Unknown", inline=True)
        if name_pool:
            preview = ", ".join(f"`{n}`" for n in name_pool[:10])
            if len(name_pool) > 10:
                preview += f" +{len(name_pool) - 10} more"
            embed.add_field(name=f"Name Pool ({name_pool_mode})", value=preview, inline=False)
        elif name_template:
            embed.add_field(name="Name Template", value=f"`{name_template}`", inline=False)
        else:
            embed.add_field(name="Naming", value="Default (Username's VC)", inline=False)
        embed.set_footer(text="Changes apply immediately.")

        self.add_item(TypeSelect(self._on_type_change, current=cfg.get("type")))

        categories = sorted(guild.categories, key=lambda c: c.position)
        if categories:
            self.add_item(
                CategorySelect(categories, self._on_category_change, current_category_id=cfg.get("category_id"))
            )

        self.add_item(_bound_button("Set Name Template", discord.ButtonStyle.secondary, "📝", self._on_set_template))
        self.add_item(_bound_button("Manage Name Pool", discord.ButtonStyle.secondary, "🎲", self._on_manage_pool))
        self.add_item(_bound_button("Delete Source", discord.ButtonStyle.danger, "🗑️", self._on_delete))
        self.add_item(_bound_button("Close", discord.ButtonStyle.secondary, "↩️", self._on_close))
        return embed, self

    async def _refresh(self, interaction: discord.Interaction):
        embed, view = await self.build(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_type_change(self, interaction: discord.Interaction, vc_type: str):
        guild = interaction.guild
        source_vcs, cfg = await self._get_cfg(guild)
        if not cfg:
            await interaction.response.send_message("This source VC no longer exists.", ephemeral=True)
            return
        cfg["type"] = vc_type
        source_vcs[str(self.source_vc_id)] = cfg
        await self.cog.config.guild(guild).source_vcs.set(source_vcs)
        await self._refresh(interaction)
        await self.dashboard.refresh()

    async def _on_category_change(self, interaction: discord.Interaction, category: Optional[discord.CategoryChannel]):
        if category is None:
            await interaction.response.send_message("No category selected.", ephemeral=True)
            return
        guild = interaction.guild
        source_vcs, cfg = await self._get_cfg(guild)
        if not cfg:
            await interaction.response.send_message("This source VC no longer exists.", ephemeral=True)
            return
        cfg["category_id"] = category.id
        source_vcs[str(self.source_vc_id)] = cfg
        await self.cog.config.guild(guild).source_vcs.set(source_vcs)
        await self._refresh(interaction)
        await self.dashboard.refresh()

    async def _on_set_template(self, interaction: discord.Interaction):
        guild = interaction.guild
        _, cfg = await self._get_cfg(guild)
        current = (cfg or {}).get("name_template") or ""
        await interaction.response.send_modal(NameTemplateModal(self, current))

    async def _on_manage_pool(self, interaction: discord.Interaction):
        pool_view = NamePoolView(self)
        embed, view = await pool_view.build(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_delete(self, interaction: discord.Interaction):
        guild = interaction.guild
        vc = guild.get_channel(self.source_vc_id)
        name = vc.mention if vc else f"`{self.source_vc_id}`"
        dashboard = self.dashboard

        async def on_confirm(confirm_interaction: discord.Interaction):
            g = confirm_interaction.guild
            source_vcs = await self.cog.config.guild(g).source_vcs()
            source_vcs.pop(str(self.source_vc_id), None)
            await self.cog.config.guild(g).source_vcs.set(source_vcs)
            await confirm_interaction.response.edit_message(
                content=f"🗑️ Removed {name} as a source VC.", embed=None, view=None
            )
            await dashboard.refresh()

        async def on_cancel(cancel_interaction: discord.Interaction):
            embed, view = await self.build(cancel_interaction.guild)
            await cancel_interaction.response.edit_message(content=None, embed=embed, view=view)

        view = ConfirmView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.edit_message(
            content=f"Remove {name} as a source VC? This cannot be undone.", embed=None, view=view
        )

    async def _on_close(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Closed.", embed=None, view=None)


class AddSourceTypeView(View):
    """Second step of the Add Source VC wizard: pick a type, then a category."""

    def __init__(self, cog: "AutoVC", dashboard: "AutoVCDashboardView", source_vc: discord.VoiceChannel):
        super().__init__(timeout=180)
        self.cog = cog
        self.dashboard = dashboard
        self.source_vc = source_vc
        self.add_item(TypeSelect(self._on_type))

    async def _on_type(self, interaction: discord.Interaction, vc_type: str):
        guild = interaction.guild
        categories = sorted(guild.categories, key=lambda c: c.position)
        own_category = self.source_vc.category
        if not categories and not own_category:
            await interaction.response.send_message(
                "This server has no categories and this VC isn't in one — a category is required.",
                ephemeral=True,
            )
            return

        cog, dashboard, source_vc = self.cog, self.dashboard, self.source_vc

        async def finalize(cat_interaction: discord.Interaction, category: Optional[discord.CategoryChannel]):
            chosen = category or own_category
            source_vcs = await cog.config.guild(guild).source_vcs()
            source_vcs[str(source_vc.id)] = {"type": vc_type, "category_id": chosen.id}
            await cog.config.guild(guild).source_vcs.set(source_vcs)
            await cat_interaction.response.edit_message(
                content=f"✅ Added {source_vc.mention} as a **{vc_type}** source VC → {chosen.mention}.",
                view=None,
            )
            await dashboard.refresh()

        view = View(timeout=120)
        view.add_item(CategorySelect(categories, finalize, own_category=own_category))
        await interaction.response.edit_message(content="Select a category for created VCs:", view=view)


class AutoVCDashboardView(View):
    """Main interactive setup dashboard for AutoVC admins."""

    def __init__(self, cog: "AutoVC", guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.guild_id = guild_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if (
            member.guild_permissions.manage_guild
            or await self.cog.bot.is_owner(member)
            or await self.cog.bot.is_admin(member)
        ):
            return True
        await interaction.response.send_message(
            "You need the Manage Server permission to use this.", ephemeral=True
        )
        return False

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        source_vcs = await self.cog.config.guild(guild).source_vcs()
        created_vcs = await self.cog.config.guild(guild).created_vcs()
        member_role_id = await self.cog.config.guild(guild).member_role_id()

        embed = discord.Embed(
            title="🔧 AutoVC Setup",
            description="Configure the voice channels that automatically spawn new VCs when joined.",
            color=await self.cog.bot.get_embed_color(guild),
        )

        if not source_vcs:
            embed.add_field(
                name="Source VCs",
                value="None configured yet. Use **Add Source VC** to get started.",
                inline=False,
            )
        else:
            lines = [
                self.cog._source_summary_line(guild, int(vc_id), cfg)
                for vc_id, cfg in source_vcs.items()
            ]
            value = "\n".join(lines)
            if len(value) > 1024:
                value = value[:1000] + "\n…(truncated)"
            embed.add_field(name=f"Source VCs ({len(source_vcs)})", value=value, inline=False)

        if member_role_id:
            role = guild.get_role(member_role_id)
            role_str = role.mention if role else f"Role ID {member_role_id} (not found)"
        else:
            role_str = "@everyone (default)"
        embed.add_field(name="Member Role", value=role_str, inline=True)
        embed.add_field(name="Active VCs", value=str(len(created_vcs)), inline=True)
        embed.set_footer(text="Buttons below apply changes immediately.")
        return embed

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        guild = self.cog.bot.get_guild(self.guild_id)
        if not guild:
            return
        embed = await self.build_embed(guild)
        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Add Source VC", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_source(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        source_vcs = await self.cog.config.guild(guild).source_vcs()
        available = [vc for vc in guild.voice_channels if str(vc.id) not in source_vcs]
        if not available:
            await interaction.response.send_message(
                "Every voice channel in this server is already configured as a source VC.",
                ephemeral=True,
            )
            return
        dashboard = self

        async def on_choose(vc_interaction: discord.Interaction, vc):
            if vc is None:
                return
            view = AddSourceTypeView(self.cog, dashboard, vc)
            await vc_interaction.response.edit_message(content=f"Selected {vc.mention}. Choose a type:", view=view)

        modal = PickerSearchModal(
            title="Select Source VC",
            items=available,
            item_label=lambda c: c.name,
            on_select=on_choose,
            empty_msg="No matching voice channels found.",
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Source VC", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def edit_source(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        source_vcs = await self.cog.config.guild(guild).source_vcs()
        if not source_vcs:
            await interaction.response.send_message("No source VCs are configured yet.", ephemeral=True)
            return
        items = [guild.get_channel(int(vc_id)) or _GhostChannel(int(vc_id)) for vc_id in source_vcs]
        dashboard = self

        async def on_choose(sel_interaction: discord.Interaction, vc):
            editor = SourceEditorView(self.cog, dashboard, guild.id, vc.id)
            embed, view = await editor.build(sel_interaction.guild)
            await sel_interaction.response.edit_message(content=None, embed=embed, view=view)

        view = PickerResultView(items, lambda c: c.name, on_choose)
        await interaction.response.send_message("Select a source VC to edit:", view=view, ephemeral=True)

    @discord.ui.button(label="Remove Source VC", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def remove_source(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        source_vcs = await self.cog.config.guild(guild).source_vcs()
        if not source_vcs:
            await interaction.response.send_message("No source VCs are configured yet.", ephemeral=True)
            return
        items = [guild.get_channel(int(vc_id)) or _GhostChannel(int(vc_id)) for vc_id in source_vcs]
        dashboard = self

        async def on_choose(sel_interaction: discord.Interaction, vc):
            name = vc.mention

            async def on_confirm(confirm_interaction: discord.Interaction):
                g = confirm_interaction.guild
                svcs = await self.cog.config.guild(g).source_vcs()
                svcs.pop(str(vc.id), None)
                await self.cog.config.guild(g).source_vcs.set(svcs)
                await confirm_interaction.response.edit_message(
                    content=f"🗑️ Removed {name} as a source VC.", view=None
                )
                await dashboard.refresh()

            async def on_cancel(cancel_interaction: discord.Interaction):
                await cancel_interaction.response.edit_message(content="Cancelled.", view=None)

            confirm_view = ConfirmView(sel_interaction.user.id, on_confirm, on_cancel)
            await sel_interaction.response.edit_message(
                content=f"Remove {name} as a source VC? This cannot be undone.", view=confirm_view
            )

        view = PickerResultView(items, lambda c: c.name, on_choose)
        await interaction.response.send_message("Select a source VC to remove:", view=view, ephemeral=True)

    @discord.ui.button(label="Member Role", style=discord.ButtonStyle.primary, emoji="🎭", row=1)
    async def member_role(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        roles = [r for r in guild.roles if not r.is_default() and not r.managed]
        dashboard = self

        async def on_choose(sel_interaction: discord.Interaction, role: Optional[discord.Role]):
            await self.cog.config.guild(guild).member_role_id.set(role.id if role else None)
            msg = f"Member role set to {role.mention}." if role else "Member role cleared. Using @everyone."
            await sel_interaction.response.edit_message(content=msg, view=None)
            await dashboard.refresh()

        if len(roles) <= 24:
            view = PickerResultView(roles, lambda r: r.name, on_choose, allow_clear=True, clear_label="Use @everyone")
            await interaction.response.send_message("Select the member role:", view=view, ephemeral=True)
        else:
            modal = PickerSearchModal(
                title="Search Member Role",
                items=roles,
                item_label=lambda r: r.name,
                on_select=on_choose,
                allow_clear=True,
                clear_label="Use @everyone",
            )
            await interaction.response.send_modal(modal)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        await self.refresh(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class AutoVC(commands.Cog):
    """Automatically create voice channels when members join source VCs."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654323, force_registration=True
        )

        default_guild = {
            "source_vcs": {},  # {source_vc_id: {"type": "public|personal|private", "category_id": int}}
            "created_vcs": {},  # {vc_id: {"source_vc_id": int, "owner_id": int|None, "role_id": int|None, "type": str, "created_at": timestamp}}
            "claimable_vcs": {},  # {vc_id: {"owner_left_at": timestamp, "original_owner": int}}
            "member_role_id": None,  # Optional: role that grants member access (for @Member scenarios)
        }

        self.config.register_guild(**default_guild)

        # In-memory rate limiting: {user_id: [timestamp1, timestamp2, ...]}
        self.rate_limit: Dict[int, List[datetime]] = {}

        # Track members currently being processed to prevent duplicate VC creation
        # {user_id: timestamp} - tracks when we started processing a move
        self.processing_members: Dict[int, datetime] = {}
        
        # Lock to prevent concurrent processing of the same member
        self.processing_lock = asyncio.Lock()

        # Background tasks
        self.cleanup_task: Optional[asyncio.Task] = None

        log.info("AutoVC cog initialized")

    async def _ctx_send(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        ephemeral: bool = False,
        **kwargs,
    ):
        """Send a message; ephemeral only when invoked as a slash command."""
        if getattr(ctx, "interaction", None) and ephemeral:
            return await ctx.send(content, embed=embed, ephemeral=True, **kwargs)
        return await ctx.send(content, embed=embed, **kwargs)

    def _source_summary_line(self, guild: discord.Guild, source_vc_id: int, cfg: dict) -> str:
        """Build a one-line summary of a configured source VC for lists/embeds."""
        source_vc = guild.get_channel(source_vc_id)
        if not source_vc:
            return f"❌ VC ID `{source_vc_id}` (channel not found)"
        vc_type = cfg.get("type", "unknown")
        category_id = cfg.get("category_id")
        category = guild.get_channel(category_id) if category_id else None
        category_str = category.mention if category else "Unknown category"
        name_template = cfg.get("name_template")
        name_pool: List[str] = cfg.get("name_pool", [])
        name_pool_mode = cfg.get("name_pool_mode", "sequential")
        if name_pool:
            naming_str = f" — pool ({name_pool_mode}, {len(name_pool)} name(s))"
        elif name_template:
            naming_str = f" — template: `{name_template}`"
        else:
            naming_str = ""
        return f"{source_vc.mention}: **{vc_type}** → {category_str}{naming_str}"

    async def _get_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        """Build the VC panel embed."""
        embed = discord.Embed(
            title="VC controls",
            description=(
                "Use the buttons below to control your AutoVC voice channel. "
                "You must be in your owned personal or private VC for the buttons to work."
            ),
            color=await self.bot.get_embed_color(guild),
        )
        return embed

    async def _check_access_owner(self, interaction: discord.Interaction) -> Optional[str]:
        """Return an error string if the user is not the AutoVC owner of the interaction's VC."""
        if not interaction.guild:
            return "This can only be used in a server."
        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            return "This can only be used in a voice channel's chat."
        created_vcs = await self.config.guild(interaction.guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        if not vc_data:
            return "This VC is not managed by AutoVC."
        if vc_data.get("owner_id") != interaction.user.id:
            return "You are not the owner of this VC."
        return None

    def _get_whitelisted_members(
        self, vc: discord.VoiceChannel, owner_id: int
    ) -> List[discord.Member]:
        """Return members with an explicit connect=True overwrite, excluding the owner."""
        return [
            target
            for target, overwrite in vc.overwrites.items()
            if isinstance(target, discord.Member)
            and target.id != owner_id
            and overwrite.connect is True
        ]

    async def _send_vc_welcome(self, vc: discord.VoiceChannel) -> None:
        """Send the owner control panel and access controls to the VC's text chat."""
        panel_embed = await self._get_panel_embed(vc.guild)
        panel_view = VCPanelView(self)

        access_embed = discord.Embed(
            title="Manage VC Access",
            description=(
                "Use **Grant Access** to let a specific user join this voice channel, "
                "or **Revoke Access** to remove someone you previously invited."
            ),
            color=await self.bot.get_embed_color(vc.guild),
        )
        access_view = AccessManagementView(self)
        try:
            panel_msg = await vc.send(embed=panel_embed, view=panel_view)
            self.bot.add_view(panel_view, message_id=panel_msg.id)
            access_msg = await vc.send(embed=access_embed, view=access_view)
            self.bot.add_view(access_view, message_id=access_msg.id)
        except discord.HTTPException as e:
            log.warning(f"Failed to send welcome message to VC {vc.id}: {e}")

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_loop())
        self.bot.add_view(VCPanelView(self))
        self.bot.add_view(AccessManagementView(self))

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        log.info("AutoVC cog unloaded")

    def check_rate_limit(self, user_id: int) -> bool:
        """Check if user has exceeded rate limit (3 creations per 30 seconds).
        
        Returns True if rate limit exceeded, False otherwise.
        Does NOT add timestamp - that should be done after successful VC creation.
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=30)

        # Filter out timestamps older than 30 seconds
        if user_id in self.rate_limit:
            self.rate_limit[user_id] = [
                ts for ts in self.rate_limit[user_id] if ts > cutoff
            ]
        else:
            self.rate_limit[user_id] = []

        # Check if user has 3+ creations in last 30 seconds
        return len(self.rate_limit[user_id]) >= 3

    def record_vc_creation(self, user_id: int):
        """Record a successful VC creation for rate limiting."""
        now = datetime.utcnow()
        if user_id not in self.rate_limit:
            self.rate_limit[user_id] = []
        self.rate_limit[user_id].append(now)

    @commands.hybrid_group(name="autovcset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _autovcset(self, ctx: commands.Context):
        """AutoVC admin: use `setup` for the interactive dashboard."""
        pass

    @_autovcset.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def _setup(self, ctx: commands.Context):
        """Open the interactive AutoVC setup dashboard.

        Add, edit, and remove source VCs, and configure the member role,
        all from a single menu-driven panel.
        """
        view = AutoVCDashboardView(self, ctx.guild.id)
        embed = await view.build_embed(ctx.guild)
        view.message = await self._ctx_send(ctx, embed=embed, view=view, ephemeral=True)

    @_autovcset.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def _list_source_vcs(self, ctx: commands.Context):
        """List all configured source VCs and their types."""
        guild = ctx.guild
        source_vcs = await self.config.guild(guild).source_vcs()

        if not source_vcs:
            await ctx.send("No source VCs are configured.")
            return

        lines = []
        for source_vc_id_str, config in source_vcs.items():
            try:
                lines.append(self._source_summary_line(guild, int(source_vc_id_str), config))
            except (ValueError, KeyError) as e:
                log.error(f"Error processing source VC config: {e}")
                continue

        await ctx.send("**Configured Source VCs:**\n\n" + "\n".join(lines))

    @_autovcset.command(name="settings")
    @commands.admin_or_permissions(manage_guild=True)
    async def _settings(self, ctx: commands.Context):
        """Show current AutoVC configuration."""
        guild = ctx.guild
        source_vcs = await self.config.guild(guild).source_vcs()
        member_role_id = await self.config.guild(guild).member_role_id()
        created_vcs = await self.config.guild(guild).created_vcs()

        embed = discord.Embed(
            title="AutoVC Settings",
            color=await self.bot.get_embed_color(ctx.guild),
        )

        # Source VCs count
        embed.add_field(
            name="Source VCs",
            value=f"{len(source_vcs)} configured",
            inline=True,
        )

        # Created VCs count
        embed.add_field(
            name="Active VCs",
            value=f"{len(created_vcs)} created",
            inline=True,
        )

        # Member role
        if member_role_id:
            member_role = guild.get_role(member_role_id)
            if member_role:
                embed.add_field(
                    name="Member Role",
                    value=member_role.mention,
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Member Role",
                    value=f"Role ID {member_role_id} (not found)",
                    inline=True,
                )
        else:
            embed.add_field(
                name="Member Role",
                value="Using @everyone",
                inline=True,
            )

        await ctx.send(embed=embed)

    @_autovcset.command(name="memberrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def _set_member_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the @Member role for permission handling.
        
        Use without a role to clear and use @everyone instead.
        Example: [p]autovcset memberrole @Member
        """
        guild = ctx.guild

        if role is None:
            await self.config.guild(guild).member_role_id.set(None)
            await ctx.send(
                "Member role cleared. Using @everyone for permission handling."
            )
        else:
            await self.config.guild(guild).member_role_id.set(role.id)
            await ctx.send(
                f"Member role set to {role.mention}. This role will be used for base permissions."
            )

    @commands.hybrid_group(name="autovc", aliases=["avc"])
    @commands.guild_only()
    async def _autovc(self, ctx: commands.Context):
        """AutoVC user commands: lock, unlock, hide, show, limit, name, claim."""
        pass

    async def _vc_lock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Lock your VC so others cannot connect."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        base_role = (
            ctx.guild.get_role(member_role_id)
            if member_role_id
            else ctx.guild.default_role
        )
        overwrite = vc.overwrites_for(base_role)
        overwrite.connect = False
        await vc.set_permissions(base_role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now locked.", ephemeral=True)

    async def _vc_unlock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Unlock your VC so others can connect."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        base_role = (
            ctx.guild.get_role(member_role_id)
            if member_role_id
            else ctx.guild.default_role
        )
        overwrite = vc.overwrites_for(base_role)
        overwrite.connect = True
        await vc.set_permissions(base_role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now unlocked.", ephemeral=True)

    async def _vc_hide(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Hide your VC from the channel list."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        roles_to_update = [ctx.guild.default_role]
        if member_role_id:
            r = ctx.guild.get_role(member_role_id)
            if r:
                roles_to_update.append(r)
        for role in roles_to_update:
            overwrite = vc.overwrites_for(role)
            overwrite.view_channel = False
            await vc.set_permissions(role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now hidden.", ephemeral=True)

    async def _vc_show(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Show your VC in the channel list."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        roles_to_update = [ctx.guild.default_role]
        if member_role_id:
            r = ctx.guild.get_role(member_role_id)
            if r:
                roles_to_update.append(r)
        for role in roles_to_update:
            overwrite = vc.overwrites_for(role)
            overwrite.view_channel = True
            await vc.set_permissions(role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now visible.", ephemeral=True)

    async def _vc_limit(
        self,
        ctx: commands.Context,
        limit: int,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Set the user limit for your VC (0 = no limit)."""
        if limit < 0 or limit > 99:
            await self._ctx_send(
                ctx, "Limit must be between 0 and 99 (0 = no limit).", ephemeral=True
            )
            return
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        await vc.edit(user_limit=limit)
        msg = "User limit removed." if limit == 0 else f"User limit set to {limit}."
        await self._ctx_send(ctx, msg, ephemeral=True)

    @_autovc.command(name="lock")
    @commands.guild_only()
    async def _autovc_lock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Lock your VC so others cannot connect."""
        await self._vc_lock(ctx, vc)

    @_autovc.command(name="unlock")
    @commands.guild_only()
    async def _autovc_unlock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Unlock your VC so others can connect."""
        await self._vc_unlock(ctx, vc)

    @_autovc.command(name="hide")
    @commands.guild_only()
    async def _autovc_hide(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Hide your VC from the channel list."""
        await self._vc_hide(ctx, vc)

    @_autovc.command(name="show")
    @commands.guild_only()
    async def _autovc_show(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Show your VC in the channel list."""
        await self._vc_show(ctx, vc)

    @_autovc.command(name="limit")
    @commands.guild_only()
    async def _autovc_limit(
        self,
        ctx: commands.Context,
        limit: int,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Set the user limit for your VC (0 = no limit)."""
        await self._vc_limit(ctx, limit, vc)

    @_autovc.command(name="name")
    @commands.guild_only()
    async def _autovc_name(
        self,
        ctx: commands.Context,
        new_name: Optional[str] = None,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Rename your VC. Leave name blank to reset to default (e.g. YourName's VC)."""
        vc_resolved, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        assert vc_resolved is not None
        if not new_name or not new_name.strip():
            username = ctx.author.display_name[:20]
            name_to_set = f"{username}'s VC"
        else:
            name_to_set = new_name.strip()[:100]
        try:
            await vc_resolved.edit(name=name_to_set)
            await self._ctx_send(
                ctx,
                f"VC renamed to **{discord.utils.escape_markdown(name_to_set)}**.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            log.warning(f"autovc name failed: {e}")
            await self._ctx_send(
                ctx,
                "Failed to rename the VC. Check bot permissions.",
                ephemeral=True,
            )

    @_autovc.command(name="claim")
    @commands.guild_only()
    async def _claim_vc(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Claim ownership of a VC whose owner left.
        
        If VC not specified, claims the VC you are currently in.
        """
        guild = ctx.guild
        member = ctx.author

        # Determine which VC to claim
        if vc is None:
            if member.voice and member.voice.channel:
                vc = member.voice.channel
            else:
                await self._ctx_send(
                    ctx,
                    "You must be in a voice channel or specify one to claim.",
                    ephemeral=True,
                )
                return

        # Check if VC is tracked
        created_vcs = await self.config.guild(guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))

        if not vc_data:
            await self._ctx_send(ctx, "This VC is not managed by AutoVC.", ephemeral=True)
            return

        # Check if VC is claimable
        claimable_vcs = await self.config.guild(guild).claimable_vcs()
        claim_data = claimable_vcs.get(str(vc.id))

        if not claim_data:
            owner_id = vc_data.get("owner_id")
            if owner_id:
                owner = guild.get_member(owner_id)
                if owner and owner.voice and owner.voice.channel == vc:
                    await self._ctx_send(
                        ctx,
                        "This VC already has an owner who is still in the channel.",
                        ephemeral=True,
                    )
                    return
            await self._ctx_send(
                ctx,
                "This VC is not available for claiming. The owner may still be present, "
                "or the 5-minute waiting period hasn't passed yet.",
                ephemeral=True,
            )
            return

        # Check if 5 minutes have passed
        owner_left_at = datetime.fromisoformat(claim_data["owner_left_at"])
        now = datetime.utcnow()
        time_passed = (now - owner_left_at).total_seconds()

        if time_passed < 300:  # 5 minutes
            remaining = int(300 - time_passed)
            await self._ctx_send(
                ctx,
                f"You must wait {remaining} more seconds before claiming this VC.",
                ephemeral=True,
            )
            return

        # Transfer ownership
        await self._transfer_ownership(guild, vc, member, vc_data)

        # Remove from claimable list
        del claimable_vcs[str(vc.id)]
        await self.config.guild(guild).claimable_vcs.set(claimable_vcs)

        await self._ctx_send(
            ctx,
            f"You have successfully claimed ownership of {vc.mention}!",
            ephemeral=True,
        )

    async def _get_owned_vc_for_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        vc: Optional[discord.VoiceChannel] = None,
    ) -> Tuple[Optional[discord.VoiceChannel], Optional[dict], Optional[str]]:
        """Resolve VC from optional channel or member's current channel. Check ownership.
        Returns (vc, vc_data, error_message). If error_message is set, vc and vc_data are None.
        """
        if vc is None:
            vc = member.voice.channel if member.voice else None
        if not vc:
            return (None, None, "You must be in a voice channel or specify one.")
        created_vcs = await self.config.guild(guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        if not vc_data:
            return (None, None, "This VC is not managed by AutoVC.")
        if vc_data.get("owner_id") != member.id:
            return (None, None, "You are not the owner of this VC.")
        if vc_data.get("type") not in ("personal", "private"):
            return (None, None, "This VC has no owner.")
        return (vc, vc_data, None)

    async def _panel_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        """Handle panel button click: lock, unlock, hide, show."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None and vc_data is not None
        member_role_id = await self.config.guild(interaction.guild).member_role_id()
        base_role = (
            interaction.guild.get_role(member_role_id)
            if member_role_id
            else interaction.guild.default_role
        )
        try:
            if action == "lock":
                overwrite = vc.overwrites_for(base_role)
                overwrite.connect = False
                await vc.set_permissions(base_role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now locked.", ephemeral=True
                )
            elif action == "unlock":
                overwrite = vc.overwrites_for(base_role)
                overwrite.connect = True
                await vc.set_permissions(base_role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now unlocked.", ephemeral=True
                )
            elif action == "hide":
                roles_to_update = [interaction.guild.default_role]
                if member_role_id:
                    r = interaction.guild.get_role(member_role_id)
                    if r:
                        roles_to_update.append(r)
                for role in roles_to_update:
                    overwrite = vc.overwrites_for(role)
                    overwrite.view_channel = False
                    await vc.set_permissions(role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now hidden.", ephemeral=True
                )
            elif action == "show":
                roles_to_update = [interaction.guild.default_role]
                if member_role_id:
                    r = interaction.guild.get_role(member_role_id)
                    if r:
                        roles_to_update.append(r)
                for role in roles_to_update:
                    overwrite = vc.overwrites_for(role)
                    overwrite.view_channel = True
                    await vc.set_permissions(role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now visible.", ephemeral=True
                )
        except discord.HTTPException as e:
            log.warning(f"Panel action {action} failed: {e}")
            await interaction.followup.send(
                "Failed to update the channel. Check bot permissions.",
                ephemeral=True,
            )

    async def _panel_set_limit(
        self, interaction: discord.Interaction, limit: int
    ) -> None:
        """Handle panel modal submit for user limit."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None
        try:
            await vc.edit(user_limit=limit)
            msg = (
                "User limit removed."
                if limit == 0
                else f"User limit set to {limit}."
            )
            await interaction.followup.send(msg, ephemeral=True)
        except discord.HTTPException as e:
            log.warning(f"Panel set limit failed: {e}")
            await interaction.followup.send(
                "Failed to set user limit. Check bot permissions.",
                ephemeral=True,
            )

    async def _panel_rename_vc(
        self, interaction: discord.Interaction, name: Optional[str]
    ) -> None:
        """Handle panel modal submit for VC rename."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None
        if name is None:
            # Match initial VC naming behavior
            username = member.display_name[:20]
            new_name = f"{username}'s VC"
        else:
            new_name = name.strip()[:100]
            if not new_name:
                username = member.display_name[:20]
                new_name = f"{username}'s VC"
        try:
            await vc.edit(name=new_name)
            await interaction.followup.send(
                f"VC renamed to **{discord.utils.escape_markdown(new_name)}**.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            log.warning(f"Panel rename VC failed: {e}")
            await interaction.followup.send(
                "Failed to rename the VC. Check bot permissions.",
                ephemeral=True,
            )

    async def _transfer_ownership(
        self,
        guild: discord.Guild,
        vc: discord.VoiceChannel,
        new_owner: discord.Member,
        vc_data: dict,
    ):
        """Transfer ownership of a VC to a new owner. No new role is created."""
        old_role_id = vc_data.get("role_id")

        # Delete old owner role if it exists (migrate existing VCs off roles)
        if old_role_id:
            old_role = guild.get_role(old_role_id)
            if old_role:
                try:
                    await old_role.delete(reason="VC ownership transferred")
                except discord.HTTPException:
                    log.warning(f"Failed to delete old owner role {old_role_id}")

        vc_data["owner_id"] = new_owner.id
        vc_data["role_id"] = None

        created_vcs = await self.config.guild(guild).created_vcs()
        created_vcs[str(vc.id)] = vc_data
        await self.config.guild(guild).created_vcs.set(created_vcs)

    async def _create_owner_role(
        self, guild: discord.Guild, owner: discord.Member, vc: discord.VoiceChannel
    ) -> Optional[discord.Role]:
        """Create a temporary role for VC owner with manage_channels permission."""
        try:
            # Create role with unique name
            role_name = f"VC-{vc.id}-Owner"
            role = await guild.create_role(
                name=role_name,
                reason=f"Temporary role for VC owner {owner.display_name}",
                mentionable=False,
            )

            # Grant manage_channels and manage_permissions on the VC
            overwrites = vc.overwrites.copy()
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                manage_channels=True,
                manage_permissions=True,
            )
            await vc.edit(overwrites=overwrites)

            # Assign role to owner
            await owner.add_roles(role, reason="VC owner role assignment")

            log.info(f"Created owner role {role.id} for VC {vc.id}")
            return role

        except discord.Forbidden:
            log.error(f"Permission denied creating owner role in {guild.name}")
            return None
        except discord.HTTPException as e:
            log.error(f"HTTP error creating owner role in {guild.name}: {e}")
            return None

    async def _create_vc(
        self,
        guild: discord.Guild,
        source_vc: discord.VoiceChannel,
        source_config: dict,
        member: discord.Member,
    ) -> Optional[discord.VoiceChannel]:
        """Create a new VC based on source VC configuration."""
        vc_type = source_config.get("type", "public")
        category_id = source_config.get("category_id")
        name_template = source_config.get("name_template")

        # Get category
        category = guild.get_channel(category_id) if category_id else None
        if not category or not isinstance(category, discord.CategoryChannel):
            category = source_vc.category
            if not category:
                log.warning(f"No category found for source VC {source_vc.id}")
                return None

        # Generate VC name
        username = member.display_name[:20]
        name_num: Optional[int] = None
        name_pool: List[str] = source_config.get("name_pool", [])
        if name_pool:
            mode = source_config.get("name_pool_mode", "sequential")
            if mode == "random":
                vc_name = random.choice(name_pool)
            else:
                counter = source_config.get("name_pool_counter", 0)
                vc_name = name_pool[counter % len(name_pool)]
                source_vcs_fresh = await self.config.guild(guild).source_vcs()
                if str(source_vc.id) in source_vcs_fresh:
                    source_vcs_fresh[str(source_vc.id)]["name_pool_counter"] = counter + 1
                    await self.config.guild(guild).source_vcs.set(source_vcs_fresh)
        elif name_template:
            created_vcs_snap = await self.config.guild(guild).created_vcs()
            used_nums = {
                v["name_num"]
                for v in created_vcs_snap.values()
                if v.get("source_vc_id") == source_vc.id and v.get("name_num") is not None
            }
            name_num = 1
            while name_num in used_nums:
                name_num += 1
            vc_name = name_template.replace("{num}", str(name_num)).replace("{user}", username)[:100]
        else:
            vc_name = f"{username}'s VC"

        # Get member role for permissions
        member_role_id = await self.config.guild(guild).member_role_id()
        member_role = guild.get_role(member_role_id) if member_role_id else None

        # Set up permission overwrites
        overwrites = {}

        # Base role (@everyone or @Member)
        base_role = member_role if member_role else guild.default_role

        if vc_type == "public":
            # Public: everyone can view/connect
            overwrites[base_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

        elif vc_type == "personal":
            # Personal: everyone can view/connect by default, owner can change
            overwrites[base_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )
            # Explicit owner overwrite so the text chat is always visible to them
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

        elif vc_type == "private":
            # Private: hidden from everyone by default
            overwrites[guild.default_role] = discord.PermissionOverwrite(
                view_channel=False
            )
            if member_role:
                overwrites[member_role] = discord.PermissionOverwrite(
                    view_channel=False
                )
            # Owner must have an explicit allow or they inherit the deny above
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

        # Create the VC
        try:
            vc = await category.create_voice_channel(
                name=vc_name,
                overwrites=overwrites,
                reason=f"AutoVC: {vc_type} VC for {member.display_name}",
            )

            # No owner role: control is via bot commands and panel only (avoids 2FA-for-mods)

            # Track the VC
            created_vcs = await self.config.guild(guild).created_vcs()
            created_vcs[str(vc.id)] = {
                "source_vc_id": source_vc.id,
                "owner_id": member.id if vc_type in ["personal", "private"] else None,
                "role_id": None,
                "type": vc_type,
                "created_at": datetime.utcnow().isoformat(),
                "name_num": name_num,
            }
            await self.config.guild(guild).created_vcs.set(created_vcs)

            log.info(f"Created {vc_type} VC {vc.id} for {member.display_name}")
            return vc

        except discord.Forbidden:
            log.error(f"Permission denied creating VC in {guild.name}")
            return None
        except discord.HTTPException as e:
            log.error(f"HTTP error creating VC in {guild.name}: {e}")
            return None

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Handle voice state updates (join/leave events)."""
        if member.bot:
            return

        guild = member.guild

        # Handle joining a source VC
        if after.channel:
            source_vcs = await self.config.guild(guild).source_vcs()
            source_vc_id = str(after.channel.id)

            if source_vc_id in source_vcs:
                # Use lock to prevent concurrent processing of the same member
                # This ensures only one VC creation process runs at a time per member
                async with self.processing_lock:
                    # Check if we're currently processing this member (prevent race conditions)
                    # This is the primary protection against duplicate VC creation
                    now = datetime.utcnow()
                    if member.id in self.processing_members:
                        processing_start = self.processing_members[member.id]
                        time_since_start = (now - processing_start).total_seconds()
                        if time_since_start < 5.0:  # Still processing within last 5 seconds
                            log.debug(
                                f"Member {member.display_name} is already being processed (started {time_since_start:.2f}s ago), skipping"
                            )
                            return
                        else:
                            # Clean up old entry
                            del self.processing_members[member.id]

                    # Mark member as being processed
                    self.processing_members[member.id] = now

                    # Check rate limit
                    if self.check_rate_limit(member.id):
                        del self.processing_members[member.id]
                        log.warning(
                            f"Rate limit exceeded for {member.display_name} in {guild.name}"
                        )
                        try:
                            await member.send(
                                "You're creating VCs too quickly! Please wait a moment before creating another one."
                            )
                        except discord.HTTPException:
                            pass
                        return

                    # Create new VC
                    source_config = source_vcs[source_vc_id]
                    new_vc = await self._create_vc(
                        guild, after.channel, source_config, member
                    )

                    if new_vc:
                        # Record successful creation for rate limiting
                        self.record_vc_creation(member.id)
                        
                        # Move user to new VC
                        try:
                            await member.move_to(new_vc, reason="AutoVC: Created new VC")
                            if source_config.get("type") in ("personal", "private"):
                                await self._send_vc_welcome(new_vc)
                        except discord.HTTPException as e:
                            log.error(f"Failed to move user to new VC: {e}")
                        finally:
                            # Clean up processing flag after a short delay to allow move to complete
                            # Use a task to remove the flag after move completes
                            async def cleanup_processing():
                                await asyncio.sleep(2.0)  # Wait 2 seconds for move to complete
                                async with self.processing_lock:
                                    if member.id in self.processing_members:
                                        del self.processing_members[member.id]
                            
                            self.bot.loop.create_task(cleanup_processing())
                    else:
                        # VC creation failed, remove processing flag
                        del self.processing_members[member.id]

        # Handle leaving a created VC
        if before.channel:
            created_vcs = await self.config.guild(guild).created_vcs()
            vc_data = created_vcs.get(str(before.channel.id))

            if vc_data:
                # Don't delete if someone is being moved TO this VC
                # (prevents race condition where VC is deleted before user is moved in)
                if after.channel and after.channel.id == before.channel.id:
                    # User is staying in the same channel (e.g., mute/deafen change)
                    return
                
                # Additional safety: check if VC was just created (within last 2 seconds)
                # This prevents deletion of VCs that are in the process of having users moved to them
                created_at_str = vc_data.get("created_at")
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str)
                    time_since_creation = (datetime.utcnow() - created_at).total_seconds()
                    if time_since_creation < 2.0:
                        # VC was just created, don't delete it yet (user is being moved)
                        log.debug(f"Skipping deletion of newly created VC {before.channel.id} (created {time_since_creation:.2f}s ago)")
                        return
                
                # Check if VC is empty - delete immediately
                try:
                    # Get current member count
                    current_members = len(before.channel.members)
                    
                    if current_members == 0:
                        await self._delete_vc_immediately(guild, before.channel, vc_data)
                    else:
                        # Check if owner left
                        owner_id = vc_data.get("owner_id")
                        if owner_id and owner_id == member.id:
                            # Owner left, start claim timer
                            claimable_vcs = await self.config.guild(guild).claimable_vcs()
                            claimable_vcs[str(before.channel.id)] = {
                                "owner_left_at": datetime.utcnow().isoformat(),
                                "original_owner": owner_id,
                            }
                            await self.config.guild(guild).claimable_vcs.set(claimable_vcs)
                except Exception as e:
                    log.error(f"Error checking VC {before.channel.id} for deletion: {e}")

    async def _delete_vc_immediately(
        self,
        guild: discord.Guild,
        vc: discord.VoiceChannel,
        vc_data: dict,
    ):
        """Immediately delete a VC and clean up its owner role."""
        vc_id = vc.id
        vc_id_str = str(vc_id)

        # Delete owner role if it exists
        role_id = vc_data.get("role_id")
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await role.delete(reason="VC deleted, cleaning up owner role")
                except discord.HTTPException:
                    pass

        # Delete VC
        try:
            await vc.delete(reason="AutoVC: VC is empty")
            log.info(f"Deleted empty VC {vc_id} immediately")
        except discord.Forbidden:
            log.warning(f"Permission denied deleting VC {vc_id}")
        except discord.HTTPException as e:
            log.error(f"Error deleting VC {vc_id}: {e}")

        # Remove from configs
        created_vcs = await self.config.guild(guild).created_vcs()
        claimable_vcs = await self.config.guild(guild).claimable_vcs()

        if vc_id_str in created_vcs:
            del created_vcs[vc_id_str]
        if vc_id_str in claimable_vcs:
            del claimable_vcs[vc_id_str]

        await self.config.guild(guild).created_vcs.set(created_vcs)
        await self.config.guild(guild).claimable_vcs.set(claimable_vcs)

    async def cleanup_loop(self):
        """Background task to clean up empty VCs and manage claim timers."""
        await self.bot.wait_until_ready()

        while True:
            try:
                await asyncio.sleep(30)  # Run every 30 seconds

                for guild in self.bot.guilds:
                    try:
                        await self._cleanup_guild_vcs(guild)
                    except Exception as e:
                        log.error(f"Error cleaning up VCs in {guild.name}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in cleanup loop: {e}")

    async def _cleanup_guild_vcs(self, guild: discord.Guild):
        """Clean up empty VCs for a specific guild."""
        created_vcs = await self.config.guild(guild).created_vcs()
        claimable_vcs = await self.config.guild(guild).claimable_vcs()

        vcs_to_delete = []

        for vc_id_str, vc_data in list(created_vcs.items()):
            try:
                vc_id = int(vc_id_str)
                vc = guild.get_channel(vc_id)

                if not vc:
                    # VC was deleted, clean up config
                    vcs_to_delete.append(vc_id_str)
                    continue

                # Check if VC is empty
                if len(vc.members) == 0:
                    vcs_to_delete.append(vc_id_str)

            except (ValueError, KeyError) as e:
                log.error(f"Error processing VC {vc_id_str}: {e}")
                vcs_to_delete.append(vc_id_str)

        # Delete empty VCs
        for vc_id_str in vcs_to_delete:
            vc_id = int(vc_id_str)
            vc = guild.get_channel(vc_id)
            vc_data = created_vcs.get(vc_id_str, {})

            # Delete owner role if it exists
            role_id = vc_data.get("role_id")
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await role.delete(reason="VC deleted, cleaning up owner role")
                    except discord.HTTPException:
                        pass

            # Delete VC if it still exists
            if vc:
                try:
                    await vc.delete(reason="AutoVC: VC is empty")
                    log.info(f"Deleted empty VC {vc_id}")
                except discord.Forbidden:
                    log.warning(f"Permission denied deleting VC {vc_id}")
                except discord.HTTPException as e:
                    log.error(f"Error deleting VC {vc_id}: {e}")

            # Remove from configs
            if vc_id_str in created_vcs:
                del created_vcs[vc_id_str]
            if vc_id_str in claimable_vcs:
                del claimable_vcs[vc_id_str]

        # Update configs
        if vcs_to_delete:
            await self.config.guild(guild).created_vcs.set(created_vcs)
            await self.config.guild(guild).claimable_vcs.set(claimable_vcs)


async def setup(bot: Red):
    """Load the AutoVC cog."""
    cog = AutoVC(bot)
    await bot.add_cog(cog)
    log.info("AutoVC cog loaded")
