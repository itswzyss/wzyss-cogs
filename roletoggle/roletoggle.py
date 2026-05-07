import logging
import re
from typing import Any, Dict, Optional, Set

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.roletoggle")

DEFAULT_MESSAGE_TEMPLATE = (
    "Need these updates? Click the button below to toggle {role}."
)
DEFAULT_BUTTON_LABEL_TEMPLATE = "Toggle {toggle_role_name}"
DEFAULT_BUTTON_STYLE = "secondary"
ALLOWED_STYLE_NAMES = {"primary", "secondary", "success", "danger"}
TEMPLATE_HELP = (
    "{ping_role}, {role}, {ping_role_name}, {toggle_role_name}, "
    "{ping_role_id}, {toggle_role_id}"
)


class RoleToggleButton(Button):
    """Persistent button used to toggle a configured role."""

    CUSTOM_ID_PREFIX = "roletoggle:role:"

    def __init__(
        self,
        cog: "RoleToggle",
        role_id: int,
        label: str = "Toggle Role",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: Optional[str] = None,
    ):
        super().__init__(
            label=label[:80] if label else "Toggle Role",
            style=style,
            emoji=emoji,
            custom_id=f"{self.CUSTOM_ID_PREFIX}{role_id}",
        )
        self.cog = cog
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_role_toggle(interaction, self.role_id)


class RoleToggleView(View):
    """Persistent one-button view bound to a single role."""

    def __init__(
        self,
        cog: "RoleToggle",
        role_id: int,
        label: str = "Toggle Role",
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: Optional[str] = None,
    ):
        super().__init__(timeout=None)
        self.add_item(RoleToggleButton(cog, role_id, label=label, style=style, emoji=emoji))


class SetupBaseView(View):
    """Base view for interactive setup sessions."""

    def __init__(self, cog: "RoleToggle", guild: discord.Guild, author_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=self.cog.make_embed(
                    "Only the admin who launched this setup panel can use it.",
                    title="RoleToggle Setup",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class AddOrUpdateMappingModal(Modal):
    def __init__(
        self,
        cog: "RoleToggle",
        guild: discord.Guild,
        refresh_view: Optional["RoleToggleMenuView"] = None,
        *,
        title: str = "Add or Update Mapping",
        default_ping_role: str = "",
        default_toggle_role: str = "",
    ):
        super().__init__(title=title)
        self.cog = cog
        self.guild = guild
        self.refresh_view = refresh_view
        self.ping_role_input = TextInput(
            label="Ping Role (mention, ID, or exact name)",
            placeholder="@Announcements",
            default=default_ping_role,
            required=True,
            max_length=100,
        )
        self.toggle_role_input = TextInput(
            label="Toggle Role (optional, blank = same role)",
            placeholder="@Announcements",
            default=default_toggle_role,
            required=False,
            max_length=100,
        )
        self.add_item(self.ping_role_input)
        self.add_item(self.toggle_role_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ping_role = self.cog.parse_role_input(self.guild, self.ping_role_input.value)
        if not ping_role:
            await interaction.response.send_message(
                embed=self.cog.make_embed("Could not find the ping role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        toggle_raw = self.toggle_role_input.value.strip()
        toggle_role = self.cog.parse_role_input(self.guild, toggle_raw) if toggle_raw else ping_role
        if not toggle_role:
            await interaction.response.send_message(
                embed=self.cog.make_embed("Could not find the toggle role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        valid, error_msg = self.cog.can_manage_toggle_role(self.guild, toggle_role)
        if not valid:
            await interaction.response.send_message(
                embed=self.cog.make_embed(error_msg, color=discord.Color.red()),
                ephemeral=True,
            )
            return

        mappings = await self.cog.config.guild(self.guild).mappings()
        existing = mappings.get(str(ping_role.id), {})
        mappings[str(ping_role.id)] = {
            "toggle_role_id": toggle_role.id,
            "message_template": existing.get("message_template"),
            "button_label_template": existing.get("button_label_template"),
        }
        await self.cog.config.guild(self.guild).mappings.set(mappings)
        self.cog._register_persistent_view(toggle_role.id)

        if self.refresh_view and self.refresh_view.message:
            try:
                await self.refresh_view.message.edit(
                    embed=await self.refresh_view.build_embed(),
                    view=self.refresh_view,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        await interaction.response.send_message(
            embed=self.cog.make_embed(f"Saved mapping: {ping_role.mention} -> {toggle_role.mention}"),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class EditRoleToggleModal(Modal):
    def __init__(
        self,
        cog: "RoleToggle",
        guild: discord.Guild,
        selected_ping_role_id: int,
        refresh_view: Optional["RoleToggleMenuView"] = None,
        *,
        default_ping_role: str = "",
        default_toggle_role: str = "",
        default_button_label: str = "",
        default_message_text: str = "",
    ):
        super().__init__(title="Edit Role Toggle")
        self.cog = cog
        self.guild = guild
        self.selected_ping_role_id = selected_ping_role_id
        self.refresh_view = refresh_view

        self.ping_role_input = TextInput(
            label="Ping Role (mention, ID, or exact name)",
            placeholder="@Announcements",
            default=default_ping_role,
            required=True,
            max_length=100,
        )
        self.toggle_role_input = TextInput(
            label="Toggle Role (optional, blank = same role)",
            placeholder="@Announcements",
            default=default_toggle_role,
            required=False,
            max_length=100,
        )
        self.button_label_input = TextInput(
            label="Button Label Template (optional)",
            placeholder="Toggle {role}",
            default=default_button_label,
            required=False,
            max_length=200,
        )
        self.message_text_input = TextInput(
            label="Message Text Template (blank allowed)",
            style=discord.TextStyle.paragraph,
            default=default_message_text,
            required=False,
            max_length=2000,
        )
        self.add_item(self.ping_role_input)
        self.add_item(self.toggle_role_input)
        self.add_item(self.button_label_input)
        self.add_item(self.message_text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ping_role = self.cog.parse_role_input(self.guild, self.ping_role_input.value)
        if not ping_role:
            await interaction.response.send_message(
                embed=self.cog.make_embed("Could not find the ping role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        toggle_raw = self.toggle_role_input.value.strip()
        toggle_role = self.cog.parse_role_input(self.guild, toggle_raw) if toggle_raw else ping_role
        if not toggle_role:
            await interaction.response.send_message(
                embed=self.cog.make_embed("Could not find the toggle role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        valid, error_msg = self.cog.can_manage_toggle_role(self.guild, toggle_role)
        if not valid:
            await interaction.response.send_message(
                embed=self.cog.make_embed(error_msg, color=discord.Color.red()),
                ephemeral=True,
            )
            return

        button_label_template = self.button_label_input.value
        message_template = self.message_text_input.value

        if button_label_template.strip():
            err = self.cog.validate_template(button_label_template)
            if err:
                await interaction.response.send_message(
                    embed=self.cog.make_embed(f"Invalid button label template: {err}", color=discord.Color.red()),
                    ephemeral=True,
                )
                return

        err = self.cog.validate_template(message_template)
        if err:
            await interaction.response.send_message(
                embed=self.cog.make_embed(f"Invalid message template: {err}", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        mappings = await self.cog.config.guild(self.guild).mappings()
        old_key = str(self.selected_ping_role_id)
        if old_key in mappings and self.selected_ping_role_id != ping_role.id:
            del mappings[old_key]

        mappings[str(ping_role.id)] = {
            "toggle_role_id": toggle_role.id,
            "message_template": message_template,
            "button_label_template": button_label_template if button_label_template.strip() else None,
        }
        await self.cog.config.guild(self.guild).mappings.set(mappings)
        self.cog._register_persistent_view(toggle_role.id)

        if self.refresh_view and self.refresh_view.message:
            try:
                await self.refresh_view.message.edit(
                    embed=await self.refresh_view.build_embed(),
                    view=self.refresh_view,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        await interaction.response.send_message(
            embed=self.cog.make_embed(
                f"Updated mapping: {ping_role.mention} -> {toggle_role.mention}\n"
                "Saved Button Label and Message Text overrides."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class RemoveMappingModal(Modal):
    def __init__(self, cog: "RoleToggle", guild: discord.Guild):
        super().__init__(title="Remove Mapping")
        self.cog = cog
        self.guild = guild
        self.ping_role_input = TextInput(
            label="Ping Role to remove",
            placeholder="@Announcements",
            required=True,
            max_length=100,
        )
        self.add_item(self.ping_role_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ping_role = self.cog.parse_role_input(self.guild, self.ping_role_input.value)
        if not ping_role:
            await interaction.response.send_message(
                embed=self.cog.make_embed("Could not find that ping role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        mappings = await self.cog.config.guild(self.guild).mappings()
        if str(ping_role.id) not in mappings:
            await interaction.response.send_message(
                embed=self.cog.make_embed(
                    f"{ping_role.mention} has no mapping configured.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        del mappings[str(ping_role.id)]
        await self.cog.config.guild(self.guild).mappings.set(mappings)
        await interaction.response.send_message(
            embed=self.cog.make_embed(f"Removed mapping for {ping_role.mention}."),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class GlobalMessageTemplateModal(Modal):
    def __init__(self, cog: "RoleToggle", current: str):
        super().__init__(title="Global Message Template")
        self.cog = cog
        self.template_input = TextInput(
            label="Template (blank = button-only)",
            style=discord.TextStyle.paragraph,
            default=current,
            required=False,
            max_length=2000,
        )
        self.add_item(self.template_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        template = self.template_input.value
        error = self.cog.validate_template(template)
        if error:
            await interaction.response.send_message(
                embed=self.cog.make_embed(f"Invalid template: {error}", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await self.cog.config.guild(interaction.guild).message_template.set(template)
        status = "button-only (blank message)" if not template.strip() else "saved"
        await interaction.response.send_message(
            embed=self.cog.make_embed(f"Global message template {status}."),
            ephemeral=True,
        )


class GlobalButtonLabelModal(Modal):
    def __init__(self, cog: "RoleToggle", current: str):
        super().__init__(title="Global Button Label Template")
        self.cog = cog
        self.template_input = TextInput(
            label="Button label template",
            default=current,
            required=True,
            max_length=200,
        )
        self.add_item(self.template_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        template = self.template_input.value
        error = self.cog.validate_template(template)
        if error:
            await interaction.response.send_message(
                embed=self.cog.make_embed(f"Invalid template: {error}", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if not template.strip():
            await interaction.response.send_message(
                embed=self.cog.make_embed("Button label template cannot be blank.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await self.cog.config.guild(interaction.guild).button_label_template.set(template)
        await interaction.response.send_message(
            embed=self.cog.make_embed("Global button label template saved."),
            ephemeral=True,
        )


class GlobalButtonEmojiModal(Modal):
    def __init__(self, cog: "RoleToggle", current: Optional[str]):
        super().__init__(title="Global Button Emoji")
        self.cog = cog
        self.emoji_input = TextInput(
            label="Emoji (blank to clear)",
            default=current or "",
            required=False,
            max_length=100,
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        emoji = self.emoji_input.value.strip()
        await self.cog.config.guild(interaction.guild).button_emoji.set(emoji or None)
        await interaction.response.send_message(
            embed=self.cog.make_embed(
                "Global button emoji saved." if emoji else "Global button emoji cleared."
            ),
            ephemeral=True,
        )


class SelectedMappingTemplateModal(Modal):
    def __init__(
        self,
        cog: "RoleToggle",
        guild: discord.Guild,
        ping_role_id: int,
        toggle_role_id: int,
        mode: str,
        current: Optional[str],
    ):
        title = "Edit Message Text" if mode == "message" else "Edit Button Label"
        super().__init__(title=title)
        self.cog = cog
        self.guild = guild
        self.mode = mode
        self.ping_role_id = ping_role_id
        self.toggle_role_id = toggle_role_id

        is_message = mode == "message"
        self.template_input = TextInput(
            label="Template (blank = button-only)" if is_message else "Button label template",
            style=discord.TextStyle.paragraph if is_message else discord.TextStyle.short,
            default=current or "",
            required=not is_message,
            max_length=2000 if is_message else 200,
        )
        self.add_item(self.template_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        mappings = await self.cog.config.guild(self.guild).mappings()
        mapping = mappings.get(str(self.ping_role_id))
        if not mapping:
            await interaction.response.send_message(
                embed=self.cog.make_embed("That mapping no longer exists.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        template = self.template_input.value
        error = self.cog.validate_template(template)
        if error:
            await interaction.response.send_message(
                embed=self.cog.make_embed(f"Invalid template: {error}", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if self.mode == "button" and not template.strip():
            await interaction.response.send_message(
                embed=self.cog.make_embed("Button label cannot be blank.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        key = "message_template" if self.mode == "message" else "button_label_template"
        mapping[key] = template
        mappings[str(self.ping_role_id)] = mapping
        await self.cog.config.guild(self.guild).mappings.set(mappings)

        ping_role = self.guild.get_role(self.ping_role_id)
        ping_text = ping_role.mention if ping_role else f"`Role ID {self.ping_role_id}`"
        await interaction.response.send_message(
            embed=self.cog.make_embed(
                f"Updated {'message text' if self.mode == 'message' else 'button label'} for {ping_text}."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class MappingEditActionsView(SetupBaseView):
    def __init__(
        self,
        cog: "RoleToggle",
        guild: discord.Guild,
        author_id: int,
        ping_role_id: int,
        page: int,
    ):
        super().__init__(cog, guild, author_id)
        self.ping_role_id = ping_role_id
        self.page = page

    async def _mapping(self) -> Optional[Dict[str, Any]]:
        mappings = await self.cog.config.guild(self.guild).mappings()
        return mappings.get(str(self.ping_role_id))

    async def build_embed(self) -> discord.Embed:
        mappings = await self.cog.config.guild(self.guild).mappings()
        mapping = mappings.get(str(self.ping_role_id))
        if not mapping:
            return self.cog.make_embed("Selected mapping no longer exists.", color=discord.Color.red())

        toggle_role_id = mapping.get("toggle_role_id")
        ping_role = self.guild.get_role(self.ping_role_id)
        toggle_role = self.guild.get_role(toggle_role_id) if isinstance(toggle_role_id, int) else None
        ping_text = ping_role.mention if ping_role else f"`Deleted Role ({self.ping_role_id})`"
        toggle_text = toggle_role.mention if toggle_role else f"`Deleted Role ({toggle_role_id})`"
        msg_status = "custom" if isinstance(mapping.get("message_template"), str) else "default"
        btn_status = "custom" if isinstance(mapping.get("button_label_template"), str) else "default"
        return self.cog.make_embed(
            f"**Selected Mapping**\n{ping_text} -> {toggle_text}\n\n"
            f"Message override: `{msg_status}`\n"
            f"Button label override: `{btn_status}`\n\n"
            "Choose an edit action from the dropdown below.",
            title="Edit Mapping",
        )

    @discord.ui.select(
        placeholder="Choose an edit action...",
        options=[
            discord.SelectOption(label="Edit Button Label", value="edit_button", emoji="🏷️"),
            discord.SelectOption(label="Edit Message Text", value="edit_message", emoji="💬"),
            discord.SelectOption(label="Remove Mapping", value="remove", emoji="🗑️"),
        ],
    )
    async def edit_actions(self, interaction: discord.Interaction, select: Select) -> None:
        mapping = await self._mapping()
        if not mapping:
            await interaction.response.send_message(
                embed=self.cog.make_embed("That mapping no longer exists.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        action = select.values[0]
        toggle_role_id = mapping.get("toggle_role_id")
        if not isinstance(toggle_role_id, int):
            await interaction.response.send_message(
                embed=self.cog.make_embed("Invalid mapping data for selected role.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if action == "edit_button":
            await interaction.response.send_modal(
                SelectedMappingTemplateModal(
                    self.cog,
                    self.guild,
                    self.ping_role_id,
                    toggle_role_id,
                    mode="button",
                    current=mapping.get("button_label_template"),
                )
            )
            return
        if action == "edit_message":
            await interaction.response.send_modal(
                SelectedMappingTemplateModal(
                    self.cog,
                    self.guild,
                    self.ping_role_id,
                    toggle_role_id,
                    mode="message",
                    current=mapping.get("message_template"),
                )
            )
            return

        mappings = await self.cog.config.guild(self.guild).mappings()
        mappings.pop(str(self.ping_role_id), None)
        await self.cog.config.guild(self.guild).mappings.set(mappings)
        await interaction.response.send_message(
            embed=self.cog.make_embed("Removed the selected mapping."),
            ephemeral=True,
        )

    @discord.ui.button(label="Back to Mapping List", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_list(self, interaction: discord.Interaction, _: Button) -> None:
        selector_view = MappingEditSelectorView(self.cog, self.guild, self.author_id, page=self.page)
        selector_view.message = self.message
        await interaction.response.edit_message(embed=await selector_view.build_embed(), view=selector_view)


class MappingEditSelectorView(SetupBaseView):
    PAGE_SIZE = 10

    def __init__(self, cog: "RoleToggle", guild: discord.Guild, author_id: int, page: int = 0):
        super().__init__(cog, guild, author_id)
        self.page = page
        self.entries = []
        self.total_pages = 1
        self._refresh_entries_sync()

    def _refresh_entries_sync(self) -> None:
        # Placeholder until async load populates; keeps initial component valid.
        self.entries = []
        self.total_pages = 1
        self.mapping_select.options = [discord.SelectOption(label="No mappings yet", value="none")]
        self.mapping_select.disabled = True
        self.prev_page.disabled = True
        self.next_page.disabled = True

    async def _load_entries(self) -> None:
        self.entries = await self.cog.get_numbered_mapping_entries(self.guild)
        total = len(self.entries)
        self.total_pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.page >= self.total_pages:
            self.page = max(0, self.total_pages - 1)

        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        page_entries = self.entries[start:end]

        if not page_entries:
            self.mapping_select.options = [discord.SelectOption(label="No mappings yet", value="none")]
            self.mapping_select.disabled = True
        else:
            options = []
            for entry in page_entries:
                label = f"{entry['number']}. {entry['ping_name']} -> {entry['toggle_name']}"
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=str(entry["ping_role_id"]),
                    )
                )
            self.mapping_select.options = options
            self.mapping_select.disabled = False

        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    async def build_embed(self) -> discord.Embed:
        await self._load_entries()
        total = len(self.entries)
        return self.cog.make_embed(
            f"Select a mapping to edit from the dropdown.\n\n"
            f"Showing page **{self.page + 1}/{self.total_pages}** "
            f"({min(total, self.page * self.PAGE_SIZE + 1) if total else 0}-"
            f"{min(total, (self.page + 1) * self.PAGE_SIZE)} of {total}).\n"
            "Edit actions available after selection:\n"
            "- Edit Button Label\n- Edit Message Text\n- Remove Mapping",
            title="Select Mapping to Edit",
        )

    @discord.ui.select(
        placeholder="Choose a numbered mapping...",
        options=[discord.SelectOption(label="Loading mappings...", value="loading")],
        row=0,
    )
    async def mapping_select(self, interaction: discord.Interaction, select: Select) -> None:
        value = select.values[0]
        if value in {"none", "loading"}:
            await interaction.response.send_message(
                embed=self.cog.make_embed("No mappings available to edit.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        ping_role_id = int(value)
        action_view = MappingEditActionsView(
            self.cog,
            self.guild,
            self.author_id,
            ping_role_id=ping_role_id,
            page=self.page,
        )
        action_view.message = self.message
        await interaction.response.edit_message(embed=await action_view.build_embed(), view=action_view)

    @discord.ui.button(label="Previous 10", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, _: Button) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next 10", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Back to Mapping Panel", style=discord.ButtonStyle.secondary, row=1)
    async def back_to_mapping_panel(self, interaction: discord.Interaction, _: Button) -> None:
        mapping_view = MappingSetupView(self.cog, self.guild, self.author_id)
        mapping_view.message = self.message
        await interaction.response.edit_message(embed=mapping_view.embed(), view=mapping_view)


class MappingSetupView(SetupBaseView):
    def __init__(self, cog: "RoleToggle", guild: discord.Guild, author_id: int):
        super().__init__(cog, guild, author_id)

    def embed(self) -> discord.Embed:
        return self.cog.make_embed(
            "Manage role mappings here.\n\n"
            "- **Add / Update Mapping** uses form inputs for ping role and toggle role.\n"
            "- **Edit Existing Mapping** opens a numbered dropdown (10 per page) and then lets you:\n"
            "  - Edit Button Label\n"
            "  - Edit Message Text\n"
            "  - Remove Mapping\n\n"
            f"Template variables: `{TEMPLATE_HELP}`",
            title="RoleToggle Setup - Mappings",
        )

    @discord.ui.select(
        placeholder="Choose a mapping action...",
        row=0,
        options=[
            discord.SelectOption(label="Add / Update Mapping", value="add", emoji="➕"),
            discord.SelectOption(label="Edit Existing Mapping", value="edit_existing", emoji="🛠️"),
            discord.SelectOption(label="List Mappings", value="list", emoji="📋"),
            discord.SelectOption(label="Back to Main Panel", value="back", emoji="↩️"),
        ],
    )
    async def mapping_actions(self, interaction: discord.Interaction, select: Select) -> None:
        action = select.values[0]
        if action == "add":
            await interaction.response.send_modal(AddOrUpdateMappingModal(self.cog, self.guild))
            return
        if action == "edit_existing":
            selector_view = MappingEditSelectorView(self.cog, self.guild, self.author_id, page=0)
            selector_view.message = self.message
            await interaction.response.edit_message(
                embed=await selector_view.build_embed(),
                view=selector_view,
            )
            return
        if action == "list":
            summary = await self.cog.build_mapping_summary(self.guild)
            await interaction.response.send_message(
                embed=self.cog.make_embed(summary, title="RoleToggle Mappings"),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        main_view = MainSetupView(self.cog, self.guild, self.author_id)
        main_view.message = self.message
        await interaction.response.edit_message(embed=main_view.embed(), view=main_view)


class MainSetupView(SetupBaseView):
    def __init__(self, cog: "RoleToggle", guild: discord.Guild, author_id: int):
        super().__init__(cog, guild, author_id)

    def embed(self) -> discord.Embed:
        return self.cog.make_embed(
            "Configure everything from dropdowns below.\n"
            "- Mappings: ping role -> toggle role + per-role message/button overrides\n"
            "- Global defaults: message, button label, style, emoji\n\n"
            f"Template variables: `{TEMPLATE_HELP}`",
            title="RoleToggle Setup",
        )

    @discord.ui.select(
        placeholder="Choose a setup action...",
        row=0,
        options=[
            discord.SelectOption(label="Manage Mappings", value="mappings", emoji="🧩"),
            discord.SelectOption(label="Global Message Template", value="global_msg", emoji="💬"),
            discord.SelectOption(label="Global Button Label", value="global_btn", emoji="🏷️"),
            discord.SelectOption(label="Global Button Emoji", value="global_emoji", emoji="😀"),
            discord.SelectOption(label="Show Current Config", value="show", emoji="📋"),
            discord.SelectOption(label="Reset Global Defaults", value="reset", emoji="♻️"),
            discord.SelectOption(label="Close Panel", value="close", emoji="✅"),
        ],
    )
    async def main_actions(self, interaction: discord.Interaction, select: Select) -> None:
        action = select.values[0]
        if action == "mappings":
            mapping_view = MappingSetupView(self.cog, self.guild, self.author_id)
            mapping_view.message = self.message
            await interaction.response.edit_message(embed=mapping_view.embed(), view=mapping_view)
            return
        if action == "global_msg":
            current = await self.cog.config.guild(self.guild).message_template()
            await interaction.response.send_modal(GlobalMessageTemplateModal(self.cog, current))
            return
        if action == "global_btn":
            current = await self.cog.config.guild(self.guild).button_label_template()
            await interaction.response.send_modal(GlobalButtonLabelModal(self.cog, current))
            return
        if action == "global_emoji":
            current = await self.cog.config.guild(self.guild).button_emoji()
            await interaction.response.send_modal(GlobalButtonEmojiModal(self.cog, current))
            return
        if action == "show":
            summary = await self.cog.build_full_summary(self.guild)
            await interaction.response.send_message(
                embed=self.cog.make_embed(summary, title="RoleToggle Configuration"),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if action == "reset":
            guild_conf = self.cog.config.guild(self.guild)
            await guild_conf.message_template.set(DEFAULT_MESSAGE_TEMPLATE)
            await guild_conf.button_label_template.set(DEFAULT_BUTTON_LABEL_TEMPLATE)
            await guild_conf.button_style.set(DEFAULT_BUTTON_STYLE)
            await guild_conf.button_emoji.set(None)
            await interaction.response.send_message(
                embed=self.cog.make_embed("Global defaults reset."),
                ephemeral=True,
            )
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.cog.make_embed("RoleToggle setup panel closed.", title="RoleToggle Setup"),
            view=self,
        )

    @discord.ui.select(
        placeholder="Set global button style...",
        row=1,
        options=[
            discord.SelectOption(label="Primary", value="primary"),
            discord.SelectOption(label="Secondary", value="secondary"),
            discord.SelectOption(label="Success", value="success"),
            discord.SelectOption(label="Danger", value="danger"),
        ],
    )
    async def style_select(self, interaction: discord.Interaction, select: Select) -> None:
        style = select.values[0]
        await self.cog.config.guild(self.guild).button_style.set(style)
        await interaction.response.send_message(
            embed=self.cog.make_embed(f"Global button style set to `{style}`."),
            ephemeral=True,
        )


class RoleToggleMenuView(SetupBaseView):
    PAGE_SIZE = 10

    def __init__(self, cog: "RoleToggle", guild: discord.Guild, author_id: int, page: int = 0):
        super().__init__(cog, guild, author_id)
        self.page = page
        self.entries: list[Dict[str, Any]] = []
        self.total_pages = 1
        self._apply_empty_state()

    def _apply_empty_state(self) -> None:
        empty_option = [discord.SelectOption(label="No role toggles configured", value="none")]
        self.edit_role_toggle.options = empty_option
        self.delete_role_toggle.options = empty_option
        self.edit_role_toggle.disabled = True
        self.delete_role_toggle.disabled = True
        self.prev_page.disabled = True
        self.next_page.disabled = True

    async def _load_entries(self) -> None:
        self.entries = await self.cog.get_numbered_mapping_entries(self.guild)
        total = len(self.entries)
        self.total_pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        if self.page >= self.total_pages:
            self.page = max(0, self.total_pages - 1)

        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        page_entries = self.entries[start:end]

        if not page_entries:
            self._apply_empty_state()
            return

        options = []
        for entry in page_entries:
            label = f"{entry['number']}. {entry['ping_name']} -> {entry['toggle_name']}"
            options.append(discord.SelectOption(label=label[:100], value=str(entry["ping_role_id"])))

        self.edit_role_toggle.options = options
        self.delete_role_toggle.options = options
        self.edit_role_toggle.disabled = False
        self.delete_role_toggle.disabled = False
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    async def build_embed(self) -> discord.Embed:
        await self._load_entries()
        total = len(self.entries)
        start_num = self.page * self.PAGE_SIZE + 1 if total else 0
        end_num = min(total, (self.page + 1) * self.PAGE_SIZE)
        return self.cog.make_embed(
            "Use the components below to manage role toggles.\n"
            "- **Add Role Toggle** opens a form for ping/toggle role.\n"
            "- **Edit Role Toggle** lets you pick an existing mapping to edit.\n"
            "- **Delete Role Toggle** deletes the selected mapping.\n"
            "- **List Mappings** shows all mappings in an embed.\n\n"
            f"Page: **{self.page + 1}/{self.total_pages}** ({start_num}-{end_num} of {total})",
            title="RoleToggle Settings",
        )

    @discord.ui.button(label="Add Role Toggle", style=discord.ButtonStyle.success, row=0)
    async def add_role_toggle(self, interaction: discord.Interaction, _: Button) -> None:
        await interaction.response.send_modal(
            AddOrUpdateMappingModal(
                self.cog,
                self.guild,
                refresh_view=self,
                title="Add Role Toggle",
            )
        )

    @discord.ui.button(label="List Mappings", style=discord.ButtonStyle.secondary, row=0)
    async def list_mappings(self, interaction: discord.Interaction, _: Button) -> None:
        summary = await self.cog.build_mapping_summary(self.guild)
        await interaction.response.send_message(
            embed=self.cog.make_embed(summary, title="RoleToggle Mappings"),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.select(
        placeholder="Edit Role Toggle...",
        options=[discord.SelectOption(label="No role toggles configured", value="none")],
        row=1,
    )
    async def edit_role_toggle(self, interaction: discord.Interaction, select: Select) -> None:
        selected = select.values[0]
        if selected == "none":
            await interaction.response.send_message(
                embed=self.cog.make_embed("No role toggles to edit.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        ping_role_id = int(selected)
        mapping = (await self.cog.config.guild(self.guild).mappings()).get(str(ping_role_id))
        if not mapping:
            await interaction.response.send_message(
                embed=self.cog.make_embed("That mapping no longer exists.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        ping_role = self.guild.get_role(ping_role_id)
        toggle_role_id = mapping.get("toggle_role_id")
        toggle_role = self.guild.get_role(toggle_role_id) if isinstance(toggle_role_id, int) else None

        await interaction.response.send_modal(
            EditRoleToggleModal(
                self.cog,
                self.guild,
                selected_ping_role_id=ping_role_id,
                refresh_view=self,
                default_ping_role=ping_role.mention if ping_role else str(ping_role_id),
                default_toggle_role=toggle_role.mention if toggle_role else "",
                default_button_label=(mapping.get("button_label_template") or ""),
                default_message_text=(
                    mapping.get("message_template")
                    if isinstance(mapping.get("message_template"), str)
                    else ""
                ),
            )
        )

    @discord.ui.select(
        placeholder="Delete Role Toggle...",
        options=[discord.SelectOption(label="No role toggles configured", value="none")],
        row=2,
    )
    async def delete_role_toggle(self, interaction: discord.Interaction, select: Select) -> None:
        selected = select.values[0]
        if selected == "none":
            await interaction.response.send_message(
                embed=self.cog.make_embed("No role toggles to delete.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        ping_role_id = int(selected)
        mappings = await self.cog.config.guild(self.guild).mappings()
        removed = mappings.pop(str(ping_role_id), None)
        if removed is None:
            await interaction.response.send_message(
                embed=self.cog.make_embed("That mapping no longer exists.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        await self.cog.config.guild(self.guild).mappings.set(mappings)
        if self.message:
            try:
                await self.message.edit(embed=await self.build_embed(), view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

        ping_role = self.guild.get_role(ping_role_id)
        ping_text = ping_role.mention if ping_role else f"`Role ID {ping_role_id}`"
        await interaction.response.send_message(
            embed=self.cog.make_embed(f"Deleted mapping for {ping_text}."),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Previous 10", style=discord.ButtonStyle.secondary, row=3)
    async def prev_page(self, interaction: discord.Interaction, _: Button) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next 10", style=discord.ButtonStyle.secondary, row=3)
    async def next_page(self, interaction: discord.Interaction, _: Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


class RoleToggle(commands.Cog):
    """Post role-toggle prompts when configured roles are pinged."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB706A11, force_registration=True)

        default_guild = {
            "mappings": {},  # {ping_role_id(str): {"toggle_role_id": int, "message_template": str|None, "button_label_template": str|None}}
            "message_template": DEFAULT_MESSAGE_TEMPLATE,
            "button_label_template": DEFAULT_BUTTON_LABEL_TEMPLATE,
            "button_style": DEFAULT_BUTTON_STYLE,
            "button_emoji": None,
        }
        self.config.register_guild(**default_guild)
        self._registered_toggle_roles: Set[int] = set()

    async def cog_load(self) -> None:
        await self._register_persistent_views_from_config()

    async def red_delete_data_for_user(self, **kwargs: Any) -> None:
        """No user data is stored by this cog."""
        return

    async def _register_persistent_views_from_config(self) -> None:
        all_guilds = await self.config.all_guilds()
        toggle_role_ids: Set[int] = set()

        for guild_data in all_guilds.values():
            mappings = guild_data.get("mappings", {})
            for mapping in mappings.values():
                role_id = mapping.get("toggle_role_id")
                if isinstance(role_id, int):
                    toggle_role_ids.add(role_id)

        for role_id in toggle_role_ids:
            self._register_persistent_view(role_id)

    def _register_persistent_view(self, role_id: int) -> None:
        if role_id in self._registered_toggle_roles:
            return
        self.bot.add_view(RoleToggleView(self, role_id))
        self._registered_toggle_roles.add(role_id)

    @staticmethod
    def _role_mention(role: discord.Role) -> str:
        return f"<@&{role.id}>"

    @staticmethod
    def make_embed(
        description: str,
        *,
        title: str = "RoleToggle",
        color: Optional[discord.Color] = None,
    ) -> discord.Embed:
        return discord.Embed(
            title=title,
            description=description,
            color=color if color is not None else discord.Color.blurple(),
        )

    @staticmethod
    def _button_style_from_name(style_name: str) -> discord.ButtonStyle:
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
        }
        return style_map.get(style_name, discord.ButtonStyle.secondary)

    def _template_context(self, ping_role: discord.Role, toggle_role: discord.Role) -> Dict[str, Any]:
        return {
            "ping_role": self._role_mention(ping_role),
            "role": self._role_mention(toggle_role),
            "toggle_role": self._role_mention(toggle_role),
            "toggle_role_ping": self._role_mention(toggle_role),
            "ping_role_name": ping_role.name,
            "toggle_role_name": toggle_role.name,
            "ping_role_id": ping_role.id,
            "toggle_role_id": toggle_role.id,
        }

    def validate_template(self, template: str) -> Optional[str]:
        try:
            template.format(
                ping_role="@PingRole",
                role="@Role",
                toggle_role="@ToggleRole",
                toggle_role_ping="@ToggleRole",
                ping_role_name="PingRole",
                toggle_role_name="ToggleRole",
                ping_role_id=123,
                toggle_role_id=456,
            )
            return None
        except (KeyError, ValueError) as exc:
            return str(exc)

    def parse_role_input(self, guild: discord.Guild, raw: str) -> Optional[discord.Role]:
        value = raw.strip()
        if not value:
            return None

        match = re.match(r"<@&(\d+)>", value)
        if match:
            role = guild.get_role(int(match.group(1)))
            if role:
                return role

        if value.isdigit():
            role = guild.get_role(int(value))
            if role:
                return role

        role = discord.utils.get(guild.roles, name=value)
        if role:
            return role

        lowered = value.lower()
        return discord.utils.find(lambda r: r.name.lower() == lowered, guild.roles)

    def can_manage_toggle_role(self, guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
        me = guild.me
        if role.is_default() or role.managed:
            return False, "That role cannot be self-assigned."
        if me and me.top_role <= role:
            return False, "I can't manage that role. Move my highest role above it and try again."
        return True, ""

    @staticmethod
    def _resolve_mapping_template(mapping: Dict[str, Any], key: str, default_template: str) -> str:
        if key in mapping and isinstance(mapping.get(key), str):
            return mapping.get(key)
        return default_template

    def _build_toggle_message(
        self,
        template: str,
        ping_role: discord.Role,
        toggle_role: discord.Role,
    ) -> Optional[str]:
        if not template.strip():
            return None
        try:
            rendered = template.format(**self._template_context(ping_role, toggle_role)).strip()
            return rendered if rendered else None
        except (KeyError, ValueError):
            return DEFAULT_MESSAGE_TEMPLATE.format(toggle_role=self._role_mention(toggle_role))

    def _build_button_label(
        self,
        template: str,
        ping_role: discord.Role,
        toggle_role: discord.Role,
    ) -> str:
        template = template or DEFAULT_BUTTON_LABEL_TEMPLATE
        button_context = self._template_context(ping_role, toggle_role)
        # Button labels do not render role mentions well, so use readable names.
        button_context["role"] = toggle_role.name
        button_context["toggle_role"] = toggle_role.name
        button_context["toggle_role_ping"] = toggle_role.name
        button_context["ping_role"] = ping_role.name
        try:
            rendered = template.format(**button_context).strip()
        except (KeyError, ValueError):
            rendered = DEFAULT_BUTTON_LABEL_TEMPLATE.format(toggle_role_name=toggle_role.name)
        if not rendered:
            rendered = DEFAULT_BUTTON_LABEL_TEMPLATE.format(toggle_role_name=toggle_role.name)
        return rendered[:80]

    async def get_numbered_mapping_entries(self, guild: discord.Guild) -> list[Dict[str, Any]]:
        mappings = await self.config.guild(guild).mappings()
        sortable_entries = []
        for ping_role_id, mapping in mappings.items():
            ping_role = guild.get_role(int(ping_role_id))
            toggle_role_id = mapping.get("toggle_role_id")
            toggle_role = guild.get_role(toggle_role_id) if isinstance(toggle_role_id, int) else None
            ping_name = ping_role.name if ping_role else f"Deleted ({ping_role_id})"
            toggle_name = (
                toggle_role.name if toggle_role else f"Deleted ({toggle_role_id})"
            )
            sortable_entries.append(
                {
                    "ping_role_id": int(ping_role_id),
                    "ping_name": ping_name,
                    "toggle_name": toggle_name,
                    "mapping": mapping,
                }
            )

        sortable_entries.sort(key=lambda e: e["ping_name"].lower())
        for idx, entry in enumerate(sortable_entries, start=1):
            entry["number"] = idx
        return sortable_entries

    async def build_mapping_summary(self, guild: discord.Guild) -> str:
        entries = await self.get_numbered_mapping_entries(guild)
        if not entries:
            return "No mappings configured."

        lines = []
        for entry in entries:
            ping_role_id = entry["ping_role_id"]
            mapping = entry["mapping"]
            ping_role = guild.get_role(ping_role_id)
            toggle_role_id = mapping.get("toggle_role_id")
            toggle_role = guild.get_role(toggle_role_id) if isinstance(toggle_role_id, int) else None
            ping_text = ping_role.mention if ping_role else f"`Deleted Role ({ping_role_id})`"
            toggle_text = (
                toggle_role.mention
                if toggle_role
                else f"`Deleted Role ({toggle_role_id})`"
            )
            role_msg = "custom" if isinstance(mapping.get("message_template"), str) else "default"
            role_btn = "custom" if isinstance(mapping.get("button_label_template"), str) else "default"
            lines.append(
                f"{entry['number']}. {ping_text} -> {toggle_text} | msg: {role_msg}, button: {role_btn}"
            )
        return "**RoleToggle mappings:**\n" + "\n".join(lines)

    async def build_full_summary(self, guild: discord.Guild) -> str:
        guild_conf = self.config.guild(guild)
        message_template = await guild_conf.message_template()
        button_label_template = await guild_conf.button_label_template()
        button_style = await guild_conf.button_style()
        button_emoji = await guild_conf.button_emoji()
        mapping_summary = await self.build_mapping_summary(guild)

        shown_message = message_template if message_template.strip() else "(blank - button only)"
        return (
            "**Global Settings**\n"
            f"- Message template: {shown_message}\n"
            f"- Button label template: {button_label_template}\n"
            f"- Button style: {button_style}\n"
            f"- Button emoji: {button_emoji or '(none)'}\n\n"
            f"{mapping_summary}"
        )

    async def handle_role_toggle(self, interaction: discord.Interaction, role_id: int) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None

        if guild is None or member is None:
            await interaction.response.send_message(
                embed=self.make_embed("This button can only be used inside a server.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        role = guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                embed=self.make_embed("That role no longer exists.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        bot_member = guild.me
        if bot_member is None:
            await interaction.response.send_message(
                embed=self.make_embed(
                    "I couldn't verify my permissions. Please try again.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if role.is_default() or role.managed:
            await interaction.response.send_message(
                embed=self.make_embed("That role cannot be self-assigned.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if bot_member.top_role <= role:
            await interaction.response.send_message(
                embed=self.make_embed(
                    "I can't manage that role because it is above my highest role.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="RoleToggle self-unassign")
                await interaction.response.send_message(
                    embed=self.make_embed(f"Removed {role.mention}."),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await member.add_roles(role, reason="RoleToggle self-assign")
                await interaction.response.send_message(
                    embed=self.make_embed(f"Added {role.mention}."),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=self.make_embed("I don't have permission to manage that role.", color=discord.Color.red()),
                ephemeral=True,
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=self.make_embed(
                    "I couldn't update your role due to a Discord API error. Try again in a moment.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

    async def _send_toggle_prompt(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        ping_role: discord.Role,
        toggle_role: discord.Role,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> None:
        guild_conf = self.config.guild(guild)
        default_message_template = await guild_conf.message_template()
        default_button_label_template = await guild_conf.button_label_template()
        button_style_name = await guild_conf.button_style()
        button_emoji = await guild_conf.button_emoji()
        mapping = mapping or {}

        message_template = self._resolve_mapping_template(mapping, "message_template", default_message_template)
        button_label_template = self._resolve_mapping_template(
            mapping, "button_label_template", default_button_label_template
        )

        content = self._build_toggle_message(message_template, ping_role, toggle_role)
        view = RoleToggleView(
            self,
            toggle_role.id,
            label=self._build_button_label(button_label_template, ping_role, toggle_role),
            style=self._button_style_from_name(button_style_name),
            emoji=button_emoji,
        )

        try:
            embed = self.make_embed(content, title="Role Toggle") if content else None
            await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            log.warning(
                "Missing permissions to send role toggle prompt in %s (guild: %s).",
                channel,
                guild.id,
            )
        except discord.HTTPException as exc:
            log.error(
                "Failed to send role toggle prompt in %s (guild: %s): %s",
                channel,
                guild.id,
                exc,
            )

    @commands.group(name="roletoggle", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def roletoggle(self, ctx: commands.Context) -> None:
        """Open the interactive RoleToggle settings menu."""
        if ctx.invoked_subcommand is not None:
            return
        view = RoleToggleMenuView(self, ctx.guild, ctx.author.id)
        message = await ctx.send(embed=await view.build_embed(), view=view)
        view.message = message

    @roletoggle.command(name="setup")
    async def roletoggle_setup(self, ctx: commands.Context) -> None:
        """Launch the same interactive settings menu."""
        view = RoleToggleMenuView(self, ctx.guild, ctx.author.id)
        message = await ctx.send(embed=await view.build_embed(), view=view)
        view.message = message

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """When configured roles are pinged, post a role-toggle prompt in the same channel."""
        if message.author.bot or not message.guild:
            return
        if not message.role_mentions:
            return

        prefixes = await self.bot.get_valid_prefixes(message.guild)
        if any(message.content.startswith(prefix) for prefix in prefixes):
            return

        mappings = await self.config.guild(message.guild).mappings()
        if not mappings:
            return

        handled_ping_role_ids: Set[int] = set()
        for mentioned_role in message.role_mentions:
            if mentioned_role.id in handled_ping_role_ids:
                continue

            mapping = mappings.get(str(mentioned_role.id))
            if not mapping:
                continue

            toggle_role_id = mapping.get("toggle_role_id")
            if not isinstance(toggle_role_id, int):
                continue

            toggle_role = message.guild.get_role(toggle_role_id)
            if not toggle_role:
                continue

            self._register_persistent_view(toggle_role.id)
            await self._send_toggle_prompt(
                message.guild,
                message.channel,
                mentioned_role,
                toggle_role,
                mapping=mapping,
            )
            handled_ping_role_ids.add(mentioned_role.id)


async def setup(bot: Red) -> None:
    """Load the RoleToggle cog."""
    await bot.add_cog(RoleToggle(bot))
