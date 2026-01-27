import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.wzyss-cogs.applications")


class ApplicationModal(Modal):
    """Dynamic modal for application forms."""

    def __init__(self, cog: "Applications", form_fields: List[Dict]):
        super().__init__(title="Server Application")
        self.cog = cog
        self.form_fields = form_fields
        self.inputs = {}

        # Create text inputs for each field
        for field in form_fields:
            field_type = field.get("type", "text")
            label = field.get("label", field.get("name", "Field"))
            placeholder = field.get("placeholder", "")
            required = field.get("required", True)
            default = field.get("default", "")

            # For select fields, build placeholder from options
            if field_type == "select":
                options = field.get("options", [])
                if options and not placeholder:
                    placeholder = f"Options: {', '.join(options)}"
                elif not placeholder:
                    placeholder = "Enter one of the options"
            
            # For confirm fields, build placeholder from required text
            if field_type == "confirm":
                confirm_text = field.get("confirm_text", "")
                if confirm_text and not placeholder:
                    placeholder = f"Type exactly: {confirm_text}"
                elif not placeholder:
                    placeholder = "Type the required confirmation text"

            # Discord text input limits
            if field_type == "paragraph":
                text_input = TextInput(
                    label=label[:45],  # Max 45 chars
                    placeholder=placeholder[:100] if placeholder else None,
                    default=default[:4000] if default else None,
                    required=required,
                    style=discord.TextStyle.paragraph,
                    max_length=4000,
                )
            elif field_type == "number":
                text_input = TextInput(
                    label=label[:45],
                    placeholder=placeholder[:100] if placeholder else None,
                    default=default[:4000] if default else None,
                    required=required,
                    style=discord.TextStyle.short,
                    max_length=20,
                )
            else:  # text (short) or select
                text_input = TextInput(
                    label=label[:45],
                    placeholder=placeholder[:100] if placeholder else None,
                    default=default[:4000] if default else None,
                    required=required,
                    style=discord.TextStyle.short,
                    max_length=4000,
                )

            self.inputs[field["name"]] = text_input
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle form submission."""
        responses = {}
        validation_errors = []

        for field in self.form_fields:
            field_name = field.get("name")
            field_type = field.get("type", "text")
            field_label = field.get("label", field_name)
            text_input = self.inputs.get(field_name)

            if not text_input:
                continue

            value = text_input.value.strip() if text_input.value else ""

            # Validate select fields
            if field_type == "select":
                options = field.get("options", [])
                if options:
                    # Case-insensitive comparison
                    value_lower = value.lower()
                    matched_option = None
                    for option in options:
                        if option.lower() == value_lower:
                            matched_option = option
                            break

                    if not matched_option:
                        validation_errors.append(
                            f"**{field_label}**: Must be one of: {', '.join(options)}"
                        )
                        continue
                    else:
                        # Use the original option value (preserve case)
                        value = matched_option

            # Validate confirm fields
            if field_type == "confirm":
                confirm_text = field.get("confirm_text", "")
                if confirm_text:
                    # Case-insensitive comparison for confirmation
                    if value.lower() != confirm_text.lower():
                        validation_errors.append(
                            f"**{field_label}**: Must type exactly: `{confirm_text}`"
                        )
                        continue
                    else:
                        # Use the original confirmation text (preserve case)
                        value = confirm_text

            responses[field_name] = value

        # If validation errors, show them and don't submit
        if validation_errors:
            error_msg = "❌ **Validation Errors:**\n\n" + "\n".join(validation_errors)
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        # Store responses
        await self.cog.submit_application(interaction.user, interaction.channel, responses)

        await interaction.response.send_message(
            "✅ Your application has been submitted! An admin will review it shortly.",
            ephemeral=True,
        )


class ApplicationButton(Button):
    """Button to open the application form."""

    def __init__(self, cog: "Applications"):
        super().__init__(label="Start Application", style=discord.ButtonStyle.primary, emoji="📝")
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        """Open the application modal."""
        form_fields = await self.cog.config.guild(interaction.guild).form_fields()
        if not form_fields:
            await interaction.response.send_message(
                "❌ No application form has been configured. Please contact an administrator.",
                ephemeral=True,
            )
            return

        modal = ApplicationModal(self.cog, form_fields)
        await interaction.response.send_modal(modal)


class DenyModal(Modal):
    """Modal for admins to provide a denial reason."""

    def __init__(self, cog: "Applications", member: discord.Member):
        super().__init__(title="Deny Application")
        self.cog = cog
        self.member = member

        self.reason_input = TextInput(
            label="Reason for Denial",
            placeholder="Provide a reason for denying this application...",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle denial submission."""
        reason = self.reason_input.value
        await self.cog.deny_application(interaction, self.member, reason)


class ApproveButton(Button):
    """Button to approve an application."""

    def __init__(self, cog: "Applications", member: discord.Member):
        super().__init__(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
        self.cog = cog
        self.member = member

    async def callback(self, interaction: discord.Interaction):
        """Approve the application."""
        # Check permissions
        if not await self.cog.can_manage_applications(interaction.user, interaction.guild):
            await interaction.response.send_message(
                "❌ You don't have permission to manage applications.", ephemeral=True
            )
            return

        # Check if already processed
        applications = await self.cog.config.guild(interaction.guild).applications()
        if str(self.member.id) not in applications:
            await interaction.response.send_message(
                f"❌ {self.member.mention} does not have an active application.", ephemeral=True
            )
            return

        app_data = applications[str(self.member.id)]
        if app_data.get("status") != "pending":
            await interaction.response.send_message(
                f"❌ This application is already {app_data.get('status')}.", ephemeral=True
            )
            return

        # Approve the application
        await self.cog.approve_application_interaction(interaction, self.member)


class DenyButton(Button):
    """Button to deny an application."""

    def __init__(self, cog: "Applications", member: discord.Member):
        super().__init__(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
        self.cog = cog
        self.member = member

    async def callback(self, interaction: discord.Interaction):
        """Open denial modal."""
        # Check permissions
        if not await self.cog.can_manage_applications(interaction.user, interaction.guild):
            await interaction.response.send_message(
                "❌ You don't have permission to manage applications.", ephemeral=True
            )
            return

        # Check if already processed
        applications = await self.cog.config.guild(interaction.guild).applications()
        if str(self.member.id) not in applications:
            await interaction.response.send_message(
                f"❌ {self.member.mention} does not have an active application.", ephemeral=True
            )
            return

        app_data = applications[str(self.member.id)]
        if app_data.get("status") != "pending":
            await interaction.response.send_message(
                f"❌ This application is already {app_data.get('status')}.", ephemeral=True
            )
            return

        # Open denial modal
        modal = DenyModal(self.cog, self.member)
        await interaction.response.send_modal(modal)


class ApplicationReviewView(View):
    """View with approve/deny buttons for application review."""

    def __init__(self, cog: "Applications", member: discord.Member):
        super().__init__(timeout=None)
        self.cog = cog
        self.member = member
        self.add_item(ApproveButton(cog, member))
        self.add_item(DenyButton(cog, member))


class FieldAddModal(Modal):
    """Modal for adding a new form field."""

    def __init__(self, cog: "Applications"):
        super().__init__(title="Add Form Field")
        self.cog = cog

        self.name_input = TextInput(
            label="Field Name",
            placeholder="e.g., age, experience, agreement",
            required=True,
            max_length=50,
        )
        self.label_input = TextInput(
            label="Field Label",
            placeholder="e.g., What is your age?",
            required=True,
            max_length=100,
        )
        self.type_input = TextInput(
            label="Field Type",
            placeholder="text, paragraph, number, select, or confirm",
            required=True,
            max_length=20,
        )
        self.required_input = TextInput(
            label="Required (true/false)",
            placeholder="true",
            required=True,
            max_length=5,
            default="true",
        )
        self.extra_input = TextInput(
            label="Options (select) or Confirm Text (confirm)",
            placeholder="For select: Option1,Option2,Option3 | For confirm: I agree",
            required=False,
            max_length=500,
        )

        self.add_item(self.name_input)
        self.add_item(self.label_input)
        self.add_item(self.type_input)
        self.add_item(self.required_input)
        self.add_item(self.extra_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle field creation."""
        name = self.name_input.value.strip().lower()
        label = self.label_input.value.strip()
        field_type = self.type_input.value.strip().lower()
        required_str = self.required_input.value.strip().lower()
        extra = self.extra_input.value.strip() if self.extra_input.value else None

        # Validate field type
        if field_type not in ["text", "paragraph", "number", "select", "confirm"]:
            await interaction.response.send_message(
                "❌ Invalid field type. Use `text`, `paragraph`, `number`, `select`, or `confirm`.",
                ephemeral=True,
            )
            return

        # Validate required
        required = required_str in ["true", "yes", "1", "required"]
        if required_str not in ["true", "yes", "1", "required", "false", "no", "0", "optional"]:
            await interaction.response.send_message(
                "❌ Invalid required value. Use `true` or `false`.",
                ephemeral=True,
            )
            return

        # Validate select fields
        if field_type == "select":
            if not extra:
                await interaction.response.send_message(
                    "❌ Select fields require options. Provide them separated by commas.",
                    ephemeral=True,
                )
                return
            options_list = [opt.strip() for opt in extra.split(",") if opt.strip()]
            if len(options_list) < 2:
                await interaction.response.send_message(
                    "❌ Select fields require at least 2 options.",
                    ephemeral=True,
                )
                return

        # Validate confirm fields
        if field_type == "confirm":
            if not extra:
                await interaction.response.send_message(
                    "❌ Confirm fields require the exact text users must type.",
                    ephemeral=True,
                )
                return

        # Add field
        async with self.cog.config.guild(interaction.guild).form_fields() as fields:
            # Check if field name already exists
            if any(f.get("name") == name for f in fields):
                await interaction.response.send_message(
                    f"❌ A field with name `{name}` already exists.",
                    ephemeral=True,
                )
                return

            field_data = {
                "name": name,
                "label": label,
                "type": field_type,
                "required": required,
                "placeholder": "",
            }

            if field_type == "select":
                field_data["options"] = [opt.strip() for opt in extra.split(",") if opt.strip()]
            elif field_type == "confirm":
                field_data["confirm_text"] = extra.strip()

            fields.append(field_data)

        options_text = ""
        if field_type == "select":
            options_text = f" with options: {extra}"
        elif field_type == "confirm":
            options_text = f" requiring confirmation: `{extra}`"

        await interaction.response.send_message(
            f"✅ Added field `{name}` ({field_type}){options_text} to the application form.",
            ephemeral=True,
        )

        # Refresh the field manager view
        if hasattr(self, "manager_view") and self.manager_view.message:
            await self.manager_view.refresh()


class FieldEditModal(Modal):
    """Modal for editing an existing form field."""

    def __init__(self, cog: "Applications", field: Dict):
        super().__init__(title="Edit Form Field")
        self.cog = cog
        self.field = field
        self.original_name = field.get("name")

        self.name_input = TextInput(
            label="Field Name",
            placeholder="e.g., age, experience, agreement",
            required=True,
            max_length=50,
            default=field.get("name", ""),
        )
        self.label_input = TextInput(
            label="Field Label",
            placeholder="e.g., What is your age?",
            required=True,
            max_length=100,
            default=field.get("label", ""),
        )
        self.type_input = TextInput(
            label="Field Type",
            placeholder="text, paragraph, number, select, or confirm",
            required=True,
            max_length=20,
            default=field.get("type", "text"),
        )
        self.required_input = TextInput(
            label="Required (true/false)",
            placeholder="true",
            required=True,
            max_length=5,
            default="true" if field.get("required", True) else "false",
        )
        
        # Pre-fill extra input based on field type
        extra_default = ""
        if field.get("type") == "select":
            extra_default = ", ".join(field.get("options", []))
        elif field.get("type") == "confirm":
            extra_default = field.get("confirm_text", "")
        
        self.extra_input = TextInput(
            label="Options (select) or Confirm Text (confirm)",
            placeholder="For select: Option1,Option2,Option3 | For confirm: I agree",
            required=False,
            max_length=500,
            default=extra_default,
        )

        self.add_item(self.name_input)
        self.add_item(self.label_input)
        self.add_item(self.type_input)
        self.add_item(self.required_input)
        self.add_item(self.extra_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle field update."""
        name = self.name_input.value.strip().lower()
        label = self.label_input.value.strip()
        field_type = self.type_input.value.strip().lower()
        required_str = self.required_input.value.strip().lower()
        extra = self.extra_input.value.strip() if self.extra_input.value else None

        # Validate field type
        if field_type not in ["text", "paragraph", "number", "select", "confirm"]:
            await interaction.response.send_message(
                "❌ Invalid field type. Use `text`, `paragraph`, `number`, `select`, or `confirm`.",
                ephemeral=True,
            )
            return

        # Validate required
        required = required_str in ["true", "yes", "1", "required"]
        if required_str not in ["true", "yes", "1", "required", "false", "no", "0", "optional"]:
            await interaction.response.send_message(
                "❌ Invalid required value. Use `true` or `false`.",
                ephemeral=True,
            )
            return

        # Validate select fields
        if field_type == "select":
            if not extra:
                await interaction.response.send_message(
                    "❌ Select fields require options. Provide them separated by commas.",
                    ephemeral=True,
                )
                return
            options_list = [opt.strip() for opt in extra.split(",") if opt.strip()]
            if len(options_list) < 2:
                await interaction.response.send_message(
                    "❌ Select fields require at least 2 options.",
                    ephemeral=True,
                )
                return

        # Validate confirm fields
        if field_type == "confirm":
            if not extra:
                await interaction.response.send_message(
                    "❌ Confirm fields require the exact text users must type.",
                    ephemeral=True,
                )
                return

        # Update field
        async with self.cog.config.guild(interaction.guild).form_fields() as fields:
            # Find the field to update
            field_index = None
            for i, f in enumerate(fields):
                if f.get("name") == self.original_name:
                    field_index = i
                    break

            if field_index is None:
                await interaction.response.send_message(
                    f"❌ Field `{self.original_name}` not found.",
                    ephemeral=True,
                )
                return

            # Check if renaming to an existing name (but not the same field)
            if name != self.original_name and any(f.get("name") == name for f in fields):
                await interaction.response.send_message(
                    f"❌ A field with name `{name}` already exists.",
                    ephemeral=True,
                )
                return

            # Update field data
            field_data = {
                "name": name,
                "label": label,
                "type": field_type,
                "required": required,
                "placeholder": fields[field_index].get("placeholder", ""),
            }

            if field_type == "select":
                field_data["options"] = [opt.strip() for opt in extra.split(",") if opt.strip()]
            elif field_type == "confirm":
                field_data["confirm_text"] = extra.strip()

            fields[field_index] = field_data

        options_text = ""
        if field_type == "select":
            options_text = f" with options: {extra}"
        elif field_type == "confirm":
            options_text = f" requiring confirmation: `{extra}`"

        await interaction.response.send_message(
            f"✅ Updated field `{name}` ({field_type}){options_text}.",
            ephemeral=True,
        )

        # Refresh the field manager view
        if hasattr(self, "manager_view") and self.manager_view.message:
            await self.manager_view.refresh()


class FieldSelectMenu(Select):
    """Select menu for choosing a field to edit, delete, or move."""

    def __init__(self, cog: "Applications", fields: List[Dict], action: str, manager_view: "FieldManagerView"):
        self.cog = cog
        self.action = action  # "edit", "delete", "move_up", or "move_down"
        self.manager_view = manager_view
        
        options = []
        for i, field in enumerate(fields):
            field_type = field.get("type", "text")
            label = field.get("label", field.get("name", "Unknown"))
            
            # Disable options that can't be moved
            disabled = False
            if action == "move_up" and i == 0:
                disabled = True
            elif action == "move_down" and i == len(fields) - 1:
                disabled = True
            
            options.append(
                discord.SelectOption(
                    label=f"{i+1}. {label[:80]}",
                    description=f"Type: {field_type} | Name: {field.get('name')}",
                    value=str(i),
                    default=False,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No fields available",
                    description="Add a field first",
                    value="-1",
                )
            )

        placeholder_text = f"Select a field to {action.replace('_', ' ')}..."
        super().__init__(
            placeholder=placeholder_text,
            options=options[:25],  # Discord limit
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle field selection."""
        if self.values[0] == "-1":
            await interaction.response.send_message("❌ No fields available.", ephemeral=True)
            return

        field_index = int(self.values[0])
        fields = await self.cog.config.guild(interaction.guild).form_fields()
        
        if field_index >= len(fields):
            await interaction.response.send_message("❌ Field not found.", ephemeral=True)
            return

        field = fields[field_index]

        if self.action == "edit":
            modal = FieldEditModal(self.cog, field)
            modal.manager_view = self.manager_view
            await interaction.response.send_modal(modal)
        elif self.action == "delete":
            # Confirm deletion
            embed = discord.Embed(
                title="⚠️ Confirm Deletion",
                description=f"Are you sure you want to delete field **{field.get('label', field.get('name'))}**?",
                color=discord.Color.red(),
            )
            embed.add_field(name="Field Name", value=f"`{field.get('name')}`", inline=False)
            embed.add_field(name="Field Type", value=field.get("type", "text"), inline=True)
            embed.add_field(name="Required", value="Yes" if field.get("required", True) else "No", inline=True)

            view = View(timeout=60)
            confirm_button = Button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
            cancel_button = Button(label="Cancel", style=discord.ButtonStyle.secondary)

            async def confirm_callback(interaction: discord.Interaction):
                async with self.cog.config.guild(interaction.guild).form_fields() as fields_list:
                    fields_list[:] = [f for f in fields_list if f.get("name") != field.get("name")]

                await interaction.response.send_message(
                    f"✅ Deleted field `{field.get('name')}`.",
                    ephemeral=True,
                )
                if self.manager_view.message:
                    await self.manager_view.refresh()

            async def cancel_callback(interaction: discord.Interaction):
                await interaction.response.send_message("❌ Deletion cancelled.", ephemeral=True)

            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            view.add_item(confirm_button)
            view.add_item(cancel_button)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        elif self.action == "move_up":
            if field_index == 0:
                await interaction.response.send_message("❌ Field is already at the top.", ephemeral=True)
                return

            async with self.cog.config.guild(interaction.guild).form_fields() as fields_list:
                fields_list[field_index], fields_list[field_index - 1] = (
                    fields_list[field_index - 1],
                    fields_list[field_index],
                )

            await interaction.response.send_message(
                f"✅ Moved field `{field.get('name')}` up.",
                ephemeral=True,
            )
            if self.manager_view.message:
                await self.manager_view.refresh()
        elif self.action == "move_down":
            if field_index == len(fields) - 1:
                await interaction.response.send_message("❌ Field is already at the bottom.", ephemeral=True)
                return

            async with self.cog.config.guild(interaction.guild).form_fields() as fields_list:
                fields_list[field_index], fields_list[field_index + 1] = (
                    fields_list[field_index + 1],
                    fields_list[field_index],
                )

            await interaction.response.send_message(
                f"✅ Moved field `{field.get('name')}` down.",
                ephemeral=True,
            )
            if self.manager_view.message:
                await self.manager_view.refresh()


class FieldManagerView(View):
    """Main view for managing application form fields."""

    def __init__(self, cog: "Applications"):
        super().__init__(timeout=None)
        self.cog = cog
        self.message: Optional[discord.Message] = None

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        """Refresh the field list display."""
        guild = None
        if interaction:
            guild = interaction.guild
        elif self.message:
            guild = self.message.guild
        
        if not guild:
            return

        fields = await self.cog.config.guild(guild).form_fields()

        embed = discord.Embed(
            title="📋 Application Form Fields Manager",
            description="Use the buttons below to manage your application form fields.",
            color=await self.cog.bot.get_embed_color(guild),
        )

        if not fields:
            embed.add_field(
                name="No Fields",
                value="Add your first field using the **Add Field** button below.",
                inline=False,
            )
        else:
            for i, field in enumerate(fields, 1):
                field_type = field.get("type", "text")
                required = field.get("required", True)
                options = field.get("options", [])
                confirm_text = field.get("confirm_text", "")

                value_text = f"**Type:** {field_type}\n**Required:** {'Yes' if required else 'No'}"
                if options:
                    value_text += f"\n**Options:** {', '.join(options)}"
                if confirm_text:
                    value_text += f"\n**Must type:** `{confirm_text}`"

                embed.add_field(
                    name=f"{i}. {field.get('label', field.get('name'))}",
                    value=value_text,
                    inline=False,
                )

        if interaction:
            if interaction.response.is_done():
                # Interaction already responded (e.g., from modal), edit the original message
                if self.message:
                    try:
                        await self.message.edit(embed=embed, view=self)
                    except discord.NotFound:
                        pass
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        elif self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.NotFound:
                # Message was deleted, can't refresh
                pass

    @discord.ui.button(label="Add Field", style=discord.ButtonStyle.success, emoji="➕")
    async def add_field(self, interaction: discord.Interaction, button: Button):
        """Open modal to add a new field."""
        modal = FieldAddModal(self.cog)
        modal.manager_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Field", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_field(self, interaction: discord.Interaction, button: Button):
        """Open select menu to choose a field to edit."""
        fields = await self.cog.config.guild(interaction.guild).form_fields()
        if not fields:
            await interaction.response.send_message("❌ No fields to edit. Add a field first.", ephemeral=True)
            return

        view = View(timeout=60)
        select_menu = FieldSelectMenu(self.cog, fields, "edit", self)
        view.add_item(select_menu)
        await interaction.response.send_message("Select a field to edit:", view=view, ephemeral=True)

    @discord.ui.button(label="Delete Field", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_field(self, interaction: discord.Interaction, button: Button):
        """Open select menu to choose a field to delete."""
        fields = await self.cog.config.guild(interaction.guild).form_fields()
        if not fields:
            await interaction.response.send_message("❌ No fields to delete. Add a field first.", ephemeral=True)
            return

        view = View(timeout=60)
        select_menu = FieldSelectMenu(self.cog, fields, "delete", self)
        view.add_item(select_menu)
        await interaction.response.send_message("Select a field to delete:", view=view, ephemeral=True)

    @discord.ui.button(label="Move Up", style=discord.ButtonStyle.secondary, emoji="⬆️")
    async def move_up(self, interaction: discord.Interaction, button: Button):
        """Move selected field up in order."""
        fields = await self.cog.config.guild(interaction.guild).form_fields()
        if not fields:
            await interaction.response.send_message("❌ No fields to reorder.", ephemeral=True)
            return

        if len(fields) < 2:
            await interaction.response.send_message("❌ Need at least 2 fields to reorder.", ephemeral=True)
            return

        view = View(timeout=60)
        select_menu = FieldSelectMenu(self.cog, fields, "move_up", self)
        view.add_item(select_menu)
        await interaction.response.send_message("Select a field to move up:", view=view, ephemeral=True)

    @discord.ui.button(label="Move Down", style=discord.ButtonStyle.secondary, emoji="⬇️")
    async def move_down(self, interaction: discord.Interaction, button: Button):
        """Move selected field down in order."""
        fields = await self.cog.config.guild(interaction.guild).form_fields()
        if not fields:
            await interaction.response.send_message("❌ No fields to reorder.", ephemeral=True)
            return

        if len(fields) < 2:
            await interaction.response.send_message("❌ Need at least 2 fields to reorder.", ephemeral=True)
            return

        view = View(timeout=60)
        select_menu = FieldSelectMenu(self.cog, fields, "move_down", self)
        view.add_item(select_menu)
        await interaction.response.send_message("Select a field to move down:", view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_button(self, interaction: discord.Interaction, button: Button):
        """Refresh the field list."""
        await self.refresh(interaction)


class Applications(commands.Cog):
    """Server application system for member screening."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543213, force_registration=True
        )

        default_guild = {
            "enabled": False,
            "restricted_role": None,  # Role ID that blocks channel access
            "access_roles": [],  # List of role IDs that grant access on approval (alternative to restricted_role)
            "bypass_roles": [],  # List of role IDs that skip application
            "manager_roles": [],  # List of role IDs that can manage applications
            "category_id": None,  # Category ID for application channels
            "log_channel": None,  # Channel ID for application logging
            "notification_role": None,  # Role ID to ping on new submissions
            "cleanup_delay": 24,  # Hours before deleting channels after approval/denial
            "form_fields": [
                {
                    "name": "name",
                    "label": "What is your name?",
                    "type": "text",
                    "required": True,
                    "placeholder": "Enter your name",
                },
                {
                    "name": "reason",
                    "label": "Why do you want to join this server?",
                    "type": "paragraph",
                    "required": True,
                    "placeholder": "Tell us why you're interested in joining...",
                },
            ],
            "applications": {},  # {user_id: {channel_id, status, submitted_at, responses}}
        }

        self.config.register_guild(**default_guild)
        self.cleanup_task: Optional[asyncio.Task] = None
        log.info("Applications cog initialized")

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_loop())

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self.cleanup_task:
            self.cleanup_task.cancel()

    async def has_bypass_role(self, member: discord.Member) -> bool:
        """Check if member has any bypass roles."""
        bypass_roles = await self.config.guild(member.guild).bypass_roles()
        if not bypass_roles:
            return False

        member_role_ids = [role.id for role in member.roles]
        return any(role_id in member_role_ids for role_id in bypass_roles)

    async def can_manage_applications(self, user: discord.Member, guild: discord.Guild) -> bool:
        """Check if user can manage applications (admin or manager role)."""
        # Check if user has manage_guild permission (admin)
        if user.guild_permissions.manage_guild:
            return True

        # Check manager roles
        manager_roles = await self.config.guild(guild).manager_roles()
        if not manager_roles:
            return False

        user_role_ids = [role.id for role in user.roles]
        return any(role_id in user_role_ids for role_id in manager_roles)

    async def create_application_channel(
        self, guild: discord.Guild, member: discord.Member
    ) -> Optional[discord.TextChannel]:
        """Create a private channel for the application."""
        category_id = await self.config.guild(guild).category_id()
        if not category_id:
            log.warning(f"No category configured for guild {guild.name}")
            return None

        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            log.warning(f"Category {category_id} not found in guild {guild.name}")
            # Clear invalid category from config
            await self.config.guild(guild).category_id.set(None)
            return None

        # Generate channel name
        username = member.display_name.replace(" ", "-").lower()[:20]
        channel_name = f"application-{username}"

        # Check if channel already exists
        existing = discord.utils.get(category.text_channels, name=channel_name)
        if existing:
            log.warning(f"Channel {channel_name} already exists for {member.display_name}")
            return existing

        # Get admin roles (roles with manage_guild permission)
        admin_roles = [
            role
            for role in guild.roles
            if role.permissions.manage_guild or role.permissions.administrator
        ]

        # Get manager roles
        manager_role_ids = await self.config.guild(guild).manager_roles()
        manager_roles = [
            guild.get_role(rid) for rid in manager_role_ids if guild.get_role(rid)
        ]

        # Create channel with permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }

        # Add admin roles
        for role in admin_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        # Add manager roles
        for role in manager_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        try:
            channel = await category.create_text_channel(
                name=channel_name, overwrites=overwrites, reason=f"Application channel for {member.display_name}"
            )
            log.info(f"Created application channel {channel.name} for {member.display_name}")
            return channel
        except discord.Forbidden:
            log.error(f"Permission denied creating channel in {guild.name}")
            return None
        except discord.HTTPException as e:
            log.error(f"HTTP error creating channel in {guild.name}: {e}")
            return None

    async def send_welcome_message(self, channel: discord.TextChannel, member: discord.Member):
        """Send welcome message with application form button."""
        embed = discord.Embed(
            title="Welcome! Please Complete Your Application",
            description=(
                "Thank you for joining! Before you can access the full server, "
                "you need to complete an application. Click the button below to get started."
            ),
            color=await self.bot.get_embed_color(channel.guild),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_footer(text="Your application will be reviewed by our staff team.")

        view = View(timeout=None)
        view.add_item(ApplicationButton(self))

        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.error(f"Error sending welcome message: {e}")

    async def submit_application(
        self, member: discord.Member, channel: discord.TextChannel, responses: Dict[str, str]
    ):
        """Store application and notify admins."""
        applications = await self.config.guild(member.guild).applications()
        if str(member.id) not in applications:
            applications[str(member.id)] = {}

        applications[str(member.id)].update(
            {
                "channel_id": channel.id,
                "status": "pending",
                "submitted_at": datetime.utcnow().isoformat(),
                "responses": responses,
            }
        )

        await self.config.guild(member.guild).applications.set(applications)

        # Create review embed
        embed = await self.create_review_embed(member, responses, "pending")

        # Create review view with approve/deny buttons
        view = ApplicationReviewView(self, member)

        # Get notification role to ping in the application channel
        notification_role = None
        notification_role_id = await self.config.guild(member.guild).notification_role()
        if notification_role_id:
            notification_role = member.guild.get_role(notification_role_id)
            if not notification_role:
                # Role was deleted, clear from config
                await self.config.guild(member.guild).notification_role.set(None)
                log.warning(f"Notification role {notification_role_id} not found in {member.guild.name}")

        # Build message content with notification role ping if configured
        content = f"📋 **Application Submitted**\n\n"
        if notification_role:
            content = f"{notification_role.mention}\n\n{content}"
        content += (
            f"Your application has been received and is pending review. "
            f"An admin will review it shortly."
        )

        # Notify in channel
        # Use allowed_mentions to ensure role mention is properly highlighted
        allowed_mentions = None
        if notification_role:
            allowed_mentions = discord.AllowedMentions(roles=[notification_role])
        
        await channel.send(
            content,
            embed=embed,
            view=view,
            allowed_mentions=allowed_mentions,
        )

        log.info(f"Application submitted by {member.display_name} in {member.guild.name}")

        # Log to log channel (without notification role ping)
        await self.log_application_event(member.guild, member, "submitted", responses=responses)

    async def log_application_event(
        self,
        guild: discord.Guild,
        member: discord.Member,
        event_type: str,
        decision_maker: Optional[discord.Member] = None,
        reason: Optional[str] = None,
        responses: Optional[Dict[str, str]] = None,
    ):
        """Log an application event to the log channel."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel or not isinstance(log_channel, discord.TextChannel):
            # Channel was deleted, clear from config
            await self.config.guild(guild).log_channel.set(None)
            log.warning(f"Log channel {log_channel_id} not found in {guild.name}")
            return

        # Create embed based on event type
        if event_type == "submitted":
            embed = await self.create_log_embed(member, "submitted", responses=responses)
            content = ""
        elif event_type == "approved":
            embed = await self.create_log_embed(member, "approved", decision_maker=decision_maker)
            content = f"✅ Application approved by {decision_maker.mention if decision_maker else 'Unknown'}"
        elif event_type == "denied":
            embed = await self.create_log_embed(
                member, "denied", decision_maker=decision_maker, reason=reason
            )
            content = f"❌ Application denied by {decision_maker.mention if decision_maker else 'Unknown'}"
        else:
            return

        try:
            await log_channel.send(content=content if content else None, embed=embed)
        except discord.Forbidden:
            log.error(f"Permission denied sending to log channel {log_channel.name} in {guild.name}")
        except discord.HTTPException as e:
            log.error(f"Error sending to log channel: {e}")

    async def create_log_embed(
        self,
        member: discord.Member,
        event_type: str,
        decision_maker: Optional[discord.Member] = None,
        reason: Optional[str] = None,
        responses: Optional[Dict[str, str]] = None,
    ) -> discord.Embed:
        """Create an embed for logging application events."""
        status_colors = {
            "submitted": discord.Color.orange(),
            "approved": discord.Color.green(),
            "denied": discord.Color.red(),
        }

        titles = {
            "submitted": "Application Submitted",
            "approved": "Application Approved",
            "denied": "Application Denied",
        }

        embed = discord.Embed(
            title=titles.get(event_type, "Application Event"),
            color=status_colors.get(event_type, discord.Color.blue()),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        # Add user info
        embed.add_field(
            name="User",
            value=f"{member.mention} ({member.display_name})\nID: {member.id}",
            inline=True,
        )

        embed.add_field(
            name="Account Created",
            value=f"<t:{int(member.created_at.timestamp())}:R>",
            inline=True,
        )

        embed.add_field(
            name="Joined Server",
            value=f"<t:{int(member.joined_at.timestamp())}:R>",
            inline=True,
        )

        # Add form responses for submissions
        if event_type == "submitted" and responses:
            form_fields = await self.config.guild(member.guild).form_fields()
            responses_text = ""
            for field in form_fields:
                field_name = field.get("name")
                field_label = field.get("label", field_name)
                response = responses.get(field_name, "Not provided")
                # Truncate long responses
                if len(str(response)) > 500:
                    response = str(response)[:497] + "..."
                responses_text += f"**{field_label}:** {box(str(response), lang='')}\n"

            if responses_text:
                embed.add_field(name="Application Responses", value=responses_text, inline=False)

        # Add decision maker for approvals/denials
        if event_type in ["approved", "denied"] and decision_maker:
            embed.add_field(
                name="Decision By",
                value=f"{decision_maker.mention} ({decision_maker.display_name})",
                inline=True,
            )

        # Add denial reason
        if event_type == "denied" and reason:
            embed.add_field(name="Denial Reason", value=reason, inline=False)

        embed.set_footer(text=f"Guild: {member.guild.name}")

        return embed

    async def create_review_embed(
        self, member: discord.Member, responses: Dict[str, str], status: str
    ) -> discord.Embed:
        """Create an embed showing application details."""
        status_colors = {
            "pending": discord.Color.orange(),
            "approved": discord.Color.green(),
            "denied": discord.Color.red(),
        }

        embed = discord.Embed(
            title=f"Application - {member.display_name}",
            color=status_colors.get(status, discord.Color.blue()),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        # Add form responses
        form_fields = await self.config.guild(member.guild).form_fields()
        for field in form_fields:
            field_name = field.get("name")
            field_label = field.get("label", field_name)
            response = responses.get(field_name, "Not provided")

            # Truncate long responses
            if len(str(response)) > 1024:
                response = str(response)[:1021] + "..."

            embed.add_field(name=field_label, value=box(str(response)), inline=False)

        # Add user info
        embed.add_field(
            name="User Information",
            value=(
                f"**User:** {member.mention} ({member.display_name})\n"
                f"**ID:** {member.id}\n"
                f"**Account Created:** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Joined Server:** <t:{int(member.joined_at.timestamp())}:R>"
            ),
            inline=False,
        )

        embed.add_field(
            name="Status",
            value=status.upper(),
            inline=True,
        )

        embed.set_footer(text=f"Guild: {member.guild.name}")

        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member join."""
        if member.bot:
            return

        if not await self.config.guild(member.guild).enabled():
            return

        # Check for bypass roles
        if await self.has_bypass_role(member):
            log.info(f"Member {member.display_name} has bypass role, skipping application")
            return

        # Check if user already has an active application
        applications = await self.config.guild(member.guild).applications()
        if str(member.id) in applications:
            app_data = applications[str(member.id)]
            if app_data.get("status") == "pending":
                log.info(f"Member {member.display_name} already has pending application")
                # Check if channel still exists, recreate if needed
                channel_id = app_data.get("channel_id")
                if channel_id:
                    channel = member.guild.get_channel(channel_id)
                    if not channel:
                        # Channel was deleted, recreate it
                        log.info(f"Recreating deleted application channel for {member.display_name}")
                        channel = await self.create_application_channel(member.guild, member)
                        if channel:
                            app_data["channel_id"] = channel.id
                            applications[str(member.id)] = app_data
                            await self.config.guild(member.guild).applications.set(applications)
                            await self.send_welcome_message(channel, member)
                return

        # Assign restricted role (if configured)
        # Note: Either restricted_role OR access_roles should be used, not both
        restricted_role_id = await self.config.guild(member.guild).restricted_role()
        if restricted_role_id:
            restricted_role = member.guild.get_role(restricted_role_id)
            if restricted_role:
                try:
                    await member.add_roles(restricted_role, reason="Application required")
                    log.info(f"Assigned restricted role to {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied assigning restricted role in {member.guild.name}")
                except discord.HTTPException as e:
                    log.error(f"Error assigning restricted role: {e}")
            else:
                log.warning(f"Restricted role {restricted_role_id} not found in {member.guild.name}")
        # If access roles are configured (and no restricted role), no role assignment needed on join

        # Create application channel
        channel = await self.create_application_channel(member.guild, member)
        if not channel:
            log.error(f"Failed to create application channel for {member.display_name}")
            return

        # Store application record
        applications[str(member.id)] = {
            "channel_id": channel.id,
            "status": "pending",
            "submitted_at": None,
            "responses": {},
        }
        await self.config.guild(member.guild).applications.set(applications)

        # Send welcome message
        await self.send_welcome_message(channel, member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Clean up when member leaves."""
        applications = await self.config.guild(member.guild).applications()
        if str(member.id) not in applications:
            return

        app_data = applications[str(member.id)]
        channel_id = app_data.get("channel_id")

        if channel_id:
            channel = member.guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.delete(reason="Member left server")
                    log.info(f"Deleted application channel for {member.display_name}")
                except discord.Forbidden:
                    log.warning(f"Permission denied deleting channel {channel_id}")
                except discord.HTTPException as e:
                    log.error(f"Error deleting channel: {e}")

        # Remove from applications
        del applications[str(member.id)]
        await self.config.guild(member.guild).applications.set(applications)

    @commands.group(name="applications", aliases=["app"])
    @commands.admin_or_permissions(manage_guild=True)
    async def _applications(self, ctx: commands.Context):
        """Application system management."""
        pass

    @_applications.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: Optional[bool] = None):
        """Enable or disable the application system."""
        if on_off is None:
            current = await self.config.guild(ctx.guild).enabled()
            await self.config.guild(ctx.guild).enabled.set(not current)
            state = "enabled" if not current else "disabled"
        else:
            await self.config.guild(ctx.guild).enabled.set(on_off)
            state = "enabled" if on_off else "disabled"

        await ctx.send(f"Application system is now {state}.")

    @_applications.command(name="restrictedrole")
    async def _set_restricted_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the role that restricts channel access.

        This role should be configured in Discord to deny view permissions
        for all channels except the application category.
        """
        if role is None:
            await self.config.guild(ctx.guild).restricted_role.set(None)
            await ctx.send("Restricted role cleared.")
        else:
            await self.config.guild(ctx.guild).restricted_role.set(role.id)
            await ctx.send(
                f"Restricted role set to {role.mention}. "
                f"Make sure this role denies view permissions for all channels except the application category."
            )

    @_applications.command(name="bypassrole")
    async def _bypass_role(
        self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None
    ):
        """Add or remove a bypass role.

        Members with bypass roles will skip the application process.
        """
        if action.lower() not in ["add", "remove"]:
            await ctx.send("Invalid action. Use `add` or `remove`.")
            return

        if role is None:
            await ctx.send("Please specify a role.")
            return

        async with self.config.guild(ctx.guild).bypass_roles() as bypass_roles:
            if action.lower() == "add":
                if role.id not in bypass_roles:
                    bypass_roles.append(role.id)
                    await ctx.send(f"Added {role.mention} as a bypass role.")
                else:
                    await ctx.send(f"{role.mention} is already a bypass role.")
            else:  # remove
                if role.id in bypass_roles:
                    bypass_roles.remove(role.id)
                    await ctx.send(f"Removed {role.mention} from bypass roles.")
                else:
                    await ctx.send(f"{role.mention} is not a bypass role.")

    @_applications.command(name="accessrole")
    async def _access_role(
        self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None
    ):
        """Add or remove an access role.

        Access roles are granted to members when their application is approved.
        This is an alternative to using a restricted role system.
        Note: Use either restricted_role OR access_roles, not both.
        """
        if action.lower() not in ["add", "remove"]:
            await ctx.send("Invalid action. Use `add` or `remove`.")
            return

        if role is None:
            await ctx.send("Please specify a role.")
            return

        async with self.config.guild(ctx.guild).access_roles() as access_roles:
            if action.lower() == "add":
                if role.id not in access_roles:
                    access_roles.append(role.id)
                    await ctx.send(f"Added {role.mention} as an access role.")
                else:
                    await ctx.send(f"{role.mention} is already an access role.")
            else:  # remove
                if role.id in access_roles:
                    access_roles.remove(role.id)
                    await ctx.send(f"Removed {role.mention} from access roles.")
                else:
                    await ctx.send(f"{role.mention} is not an access role.")

    @_applications.command(name="managerrole")
    async def _manager_role(
        self, ctx: commands.Context, action: str, role: Optional[discord.Role] = None
    ):
        """Add or remove a manager role.

        Members with manager roles can approve/deny applications.
        """
        if action.lower() not in ["add", "remove"]:
            await ctx.send("Invalid action. Use `add` or `remove`.")
            return

        if role is None:
            await ctx.send("Please specify a role.")
            return

        async with self.config.guild(ctx.guild).manager_roles() as manager_roles:
            if action.lower() == "add":
                if role.id not in manager_roles:
                    manager_roles.append(role.id)
                    await ctx.send(f"Added {role.mention} as a manager role.")
                else:
                    await ctx.send(f"{role.mention} is already a manager role.")
            else:  # remove
                if role.id in manager_roles:
                    manager_roles.remove(role.id)
                    await ctx.send(f"Removed {role.mention} from manager roles.")
                else:
                    await ctx.send(f"{role.mention} is not a manager role.")

    @_applications.command(name="category")
    async def _set_category(
        self, ctx: commands.Context, category: Optional[discord.CategoryChannel] = None
    ):
        """Set the category where application channels will be created."""
        if category is None:
            await self.config.guild(ctx.guild).category_id.set(None)
            await ctx.send("Category cleared.")
        else:
            await self.config.guild(ctx.guild).category_id.set(category.id)
            await ctx.send(f"Application channels will be created in {category.mention}.")

    @_applications.command(name="logchannel")
    async def _set_log_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Set the channel for logging application events."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Log channel cleared.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Application events will be logged to {channel.mention}.")

    @_applications.command(name="notificationrole")
    async def _set_notification_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the role to ping when new applications are submitted."""
        if role is None:
            await self.config.guild(ctx.guild).notification_role.set(None)
            await ctx.send("Notification role cleared.")
        else:
            await self.config.guild(ctx.guild).notification_role.set(role.id)
            await ctx.send(
                f"Notification role set to {role.mention}. "
                f"This role will be pinged when new applications are submitted."
            )

    @_applications.command(name="cleanupdelay")
    async def _set_cleanup_delay(self, ctx: commands.Context, hours: int):
        """Set the delay in hours before deleting channels after approval/denial."""
        if hours < 0:
            await ctx.send("Delay must be a positive number.")
            return

        await self.config.guild(ctx.guild).cleanup_delay.set(hours)
        await ctx.send(
            f"Cleanup delay set to {hours} hour(s). "
            f"Application channels will be deleted {hours} hour(s) after approval or denial."
        )

    @_applications.command(name="cleanup")
    async def _cleanup(self, ctx: commands.Context):
        """Manually trigger cleanup of expired application channels."""
        await ctx.send("Cleaning up expired application channels...")
        cleaned = await self.cleanup_channels(ctx.guild)
        await ctx.send(f"✅ Cleaned up {cleaned} expired application channel(s).")

    @_applications.command(name="resend", aliases=["refresh"])
    async def _resend_welcome(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Re-send the welcome message to a user's application channel.
        
        Useful for troubleshooting when buttons stop working after cog updates.
        If no member is specified and run in an application channel, uses the channel owner.
        """
        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send("The application system is not enabled.")
            return

        # If no member specified, try to find from current channel
        if member is None:
            applications = await self.config.guild(ctx.guild).applications()
            current_channel_id = ctx.channel.id
            
            # Find application by channel ID
            found_member = None
            for user_id, app_data in applications.items():
                if app_data.get("channel_id") == current_channel_id:
                    found_member = ctx.guild.get_member(int(user_id))
                    if found_member:
                        member = found_member
                        break
            
            if not member:
                await ctx.send(
                    "❌ No member specified and this doesn't appear to be an application channel. "
                    "Please specify a member: `[p]applications resend @user`"
                )
                return

        # Get the member's application data
        applications = await self.config.guild(ctx.guild).applications()
        if str(member.id) not in applications:
            await ctx.send(f"❌ {member.mention} does not have an active application.")
            return

        app_data = applications[str(member.id)]
        channel_id = app_data.get("channel_id")
        
        if not channel_id:
            await ctx.send(f"❌ No application channel found for {member.mention}.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            await ctx.send(f"❌ Application channel not found or invalid.")
            return

        # Check if application is still pending
        if app_data.get("status") != "pending":
            await ctx.send(
                f"⚠️ {member.mention}'s application is already {app_data.get('status')}. "
                f"Re-sending welcome message anyway for troubleshooting."
            )

        # Delete old welcome messages (messages from the bot in this channel)
        try:
            async for message in channel.history(limit=50):
                if message.author == ctx.guild.me:
                    # Check if it's a welcome message (contains "Welcome" in embed title or content)
                    is_welcome = False
                    if message.embeds:
                        for embed in message.embeds:
                            if embed.title and "Welcome" in embed.title:
                                is_welcome = True
                                break
                    if not is_welcome and message.content and "Welcome" in message.content:
                        is_welcome = True
                    
                    if is_welcome:
                        try:
                            await message.delete()
                        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                            pass  # Ignore errors deleting old messages
        except discord.Forbidden:
            await ctx.send("⚠️ Permission denied reading channel history. Re-sending welcome message anyway.")
        except discord.HTTPException:
            pass  # Ignore errors

        # Send fresh welcome message
        try:
            await self.send_welcome_message(channel, member)
            await ctx.send(f"✅ Re-sent welcome message to {member.mention}'s application channel.")
        except discord.Forbidden:
            await ctx.send(f"❌ Permission denied sending message to {channel.mention}.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Error sending welcome message: {e}")

    @_applications.group(name="field")
    async def _field(self, ctx: commands.Context):
        """Manage application form fields."""
        pass

    @_field.command(name="add")
    async def _field_add(
        self,
        ctx: commands.Context,
        name: str,
        label: str,
        field_type: str,
        required: bool = True,
        confirm_or_options: Optional[str] = None,
    ):
        """Add a form field.

        Types: text (short), paragraph (long), number, select (multiple choice), confirm (text confirmation)
        
        For select fields, provide options separated by commas.
        Example: [p]applications field add experience "Experience Level" select True "Beginner,Intermediate,Advanced"
        
        For confirm fields, provide the exact text users must type.
        Example: [p]applications field add agreement "I agree to the rules" confirm True "I agree"
        """
        field_type_lower = field_type.lower()
        if field_type_lower not in ["text", "paragraph", "number", "select", "confirm"]:
            await ctx.send("Invalid field type. Use `text`, `paragraph`, `number`, `select`, or `confirm`.")
            return

        # Parse options for select fields
        options_list = []
        if field_type_lower == "select":
            if not confirm_or_options:
                await ctx.send(
                    "Select fields require options. Provide them separated by commas.\n"
                    "Example: `[p]applications field add experience \"Experience Level\" select True \"Beginner,Intermediate,Advanced\"`"
                )
                return
            
            # Split by comma
            options_list = [opt.strip() for opt in confirm_or_options.split(",") if opt.strip()]
            
            if not options_list:
                await ctx.send("At least one option is required for select fields.")
                return
            if len(options_list) < 2:
                await ctx.send("Select fields require at least 2 options.")
                return

        # Parse confirmation text for confirm fields
        confirm_text = ""
        if field_type_lower == "confirm":
            if not confirm_or_options:
                await ctx.send(
                    "Confirm fields require the exact text users must type.\n"
                    "Example: `[p]applications field add agreement \"I agree to the rules\" confirm True \"I agree\"`"
                )
                return
            
            confirm_text = confirm_or_options.strip()
            if not confirm_text:
                await ctx.send("Confirmation text cannot be empty.")
                return

        async with self.config.guild(ctx.guild).form_fields() as fields:
            # Check if field name already exists
            if any(f.get("name") == name for f in fields):
                await ctx.send(f"A field with name `{name}` already exists.")
                return

            field_data = {
                "name": name,
                "label": label,
                "type": field_type_lower,
                "required": required,
                "placeholder": "",
            }

            if field_type_lower == "select":
                field_data["options"] = options_list
            elif field_type_lower == "confirm":
                field_data["confirm_text"] = confirm_text

            fields.append(field_data)

        if options_list:
            options_text = f" with options: {', '.join(options_list)}"
        elif confirm_text:
            options_text = f" requiring confirmation: `{confirm_text}`"
        else:
            options_text = ""
        await ctx.send(f"Added field `{name}` ({field_type_lower}){options_text} to the application form.")

    @_field.command(name="remove")
    async def _field_remove(self, ctx: commands.Context, name: str):
        """Remove a form field."""
        async with self.config.guild(ctx.guild).form_fields() as fields:
            field_names = [f.get("name") for f in fields]
            if name not in field_names:
                await ctx.send(f"Field `{name}` not found.")
                return

            fields[:] = [f for f in fields if f.get("name") != name]

        await ctx.send(f"Removed field `{name}` from the application form.")

    @_field.command(name="list")
    async def _field_list(self, ctx: commands.Context):
        """List all form fields."""
        fields = await self.config.guild(ctx.guild).form_fields()
        if not fields:
            await ctx.send("No form fields configured.")
            return

        embed = discord.Embed(
            title="Application Form Fields",
            color=await ctx.embed_color(),
        )

        for i, field in enumerate(fields, 1):
            field_type = field.get("type", "text")
            required = field.get("required", True)
            options = field.get("options", [])
            confirm_text = field.get("confirm_text", "")
            
            value_text = f"**Name:** `{field.get('name')}`\n**Type:** {field_type}\n**Required:** {required}"
            if options:
                value_text += f"\n**Options:** {', '.join(options)}"
            if confirm_text:
                value_text += f"\n**Must type:** `{confirm_text}`"
            
            embed.add_field(
                name=f"{i}. {field.get('label', field.get('name'))}",
                value=value_text,
                inline=False,
            )

        await ctx.send(embed=embed)

    @_field.command(name="options")
    async def _field_options(self, ctx: commands.Context, name: str, *, options: str):
        """Set options for a select field.
        
        Options should be separated by commas.
        Example: [p]applications field options experience "Beginner,Intermediate,Advanced"
        """
        async with self.config.guild(ctx.guild).form_fields() as fields:
            field = None
            for f in fields:
                if f.get("name") == name:
                    field = f
                    break

            if not field:
                await ctx.send(f"Field `{name}` not found.")
                return

            if field.get("type") != "select":
                await ctx.send(f"Field `{name}` is not a select field. Only select fields can have options.")
                return

            options_list = [opt.strip() for opt in options.split(",") if opt.strip()]
            if not options_list:
                await ctx.send("At least one option is required.")
                return
            if len(options_list) < 2:
                await ctx.send("Select fields require at least 2 options.")
                return

            field["options"] = options_list
            await ctx.send(f"Updated options for field `{name}`: {', '.join(options_list)}")

    @_field.command(name="confirmtext")
    async def _field_confirm_text(self, ctx: commands.Context, name: str, *, confirm_text: str):
        """Set the confirmation text for a confirm field.
        
        Example: [p]applications field confirmtext agreement "I agree"
        """
        async with self.config.guild(ctx.guild).form_fields() as fields:
            field = None
            for f in fields:
                if f.get("name") == name:
                    field = f
                    break

            if not field:
                await ctx.send(f"Field `{name}` not found.")
                return

            if field.get("type") != "confirm":
                await ctx.send(f"Field `{name}` is not a confirm field. Only confirm fields can have confirmation text.")
                return

            confirm_text = confirm_text.strip()
            if not confirm_text:
                await ctx.send("Confirmation text cannot be empty.")
                return

            field["confirm_text"] = confirm_text
            await ctx.send(f"Updated confirmation text for field `{name}`: `{confirm_text}`")

    @_field.command(name="manager", aliases=["ui"])
    async def _field_manager(self, ctx: commands.Context):
        """Open the interactive field manager UI."""
        view = FieldManagerView(self)
        
        # Create initial embed
        fields = await self.config.guild(ctx.guild).form_fields()
        embed = discord.Embed(
            title="📋 Application Form Fields Manager",
            description="Use the buttons below to manage your application form fields.",
            color=await ctx.embed_color(),
        )

        if not fields:
            embed.add_field(
                name="No Fields",
                value="Add your first field using the **Add Field** button below.",
                inline=False,
            )
        else:
            for i, field in enumerate(fields, 1):
                field_type = field.get("type", "text")
                required = field.get("required", True)
                options = field.get("options", [])
                confirm_text = field.get("confirm_text", "")

                value_text = f"**Type:** {field_type}\n**Required:** {'Yes' if required else 'No'}"
                if options:
                    value_text += f"\n**Options:** {', '.join(options)}"
                if confirm_text:
                    value_text += f"\n**Must type:** `{confirm_text}`"

                embed.add_field(
                    name=f"{i}. {field.get('label', field.get('name'))}",
                    value=value_text,
                    inline=False,
                )

        view.message = await ctx.send(embed=embed, view=view)

    @_applications.command(name="settings")
    async def _settings(self, ctx: commands.Context):
        """Show current application system settings."""
        settings = await self.config.guild(ctx.guild).all()

        restricted_role_id = settings.get("restricted_role")
        restricted_role = (
            ctx.guild.get_role(restricted_role_id) if restricted_role_id else None
        )

        access_role_ids = settings.get("access_roles", [])
        access_roles = [
            ctx.guild.get_role(rid) for rid in access_role_ids if ctx.guild.get_role(rid)
        ]

        bypass_role_ids = settings.get("bypass_roles", [])
        bypass_roles = [
            ctx.guild.get_role(rid) for rid in bypass_role_ids if ctx.guild.get_role(rid)
        ]

        category_id = settings.get("category_id")
        category = ctx.guild.get_channel(category_id) if category_id else None

        log_channel_id = settings.get("log_channel")
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None

        notification_role_id = settings.get("notification_role")
        notification_role = (
            ctx.guild.get_role(notification_role_id) if notification_role_id else None
        )

        cleanup_delay = settings.get("cleanup_delay", 24)

        form_fields = settings.get("form_fields", [])
        applications = settings.get("applications", {})
        pending_count = sum(
            1 for app in applications.values() if app.get("status") == "pending"
        )

        embed = discord.Embed(
            title="Application System Settings",
            color=await ctx.embed_color(),
        )

        embed.add_field(
            name="Enabled",
            value="Yes" if settings.get("enabled") else "No",
            inline=True,
        )

        # Show which role system is active
        role_system_info = ""
        if restricted_role:
            role_system_info = f"**Restricted Role:** {restricted_role.mention}"
        elif access_roles:
            access_list = ", ".join([r.mention for r in access_roles])
            role_system_info = f"**Access Roles:** {access_list if len(access_list) <= 1024 else f'{len(access_roles)} roles'}"
        else:
            role_system_info = "**Role System:** Not configured"
        
        embed.add_field(
            name="Role System",
            value=role_system_info,
            inline=False,
        )

        embed.add_field(
            name="Category",
            value=category.mention if category else "Not set",
            inline=True,
        )

        embed.add_field(
            name="Log Channel",
            value=log_channel.mention if log_channel else "Not set",
            inline=True,
        )

        embed.add_field(
            name="Notification Role",
            value=notification_role.mention if notification_role else "Not set",
            inline=True,
        )

        embed.add_field(
            name="Cleanup Delay",
            value=f"{cleanup_delay} hour(s)",
            inline=True,
        )

        if bypass_roles:
            bypass_list = ", ".join([r.mention for r in bypass_roles])
            embed.add_field(
                name="Bypass Roles",
                value=bypass_list if len(bypass_list) <= 1024 else f"{len(bypass_roles)} roles",
                inline=False,
            )

        manager_role_ids = settings.get("manager_roles", [])
        manager_roles = [
            ctx.guild.get_role(rid) for rid in manager_role_ids if ctx.guild.get_role(rid)
        ]
        if manager_roles:
            manager_list = ", ".join([r.mention for r in manager_roles])
            embed.add_field(
                name="Manager Roles",
                value=manager_list if len(manager_list) <= 1024 else f"{len(manager_roles)} roles",
                inline=False,
            )

        embed.add_field(
            name="Form Fields",
            value=f"{len(form_fields)} field(s)",
            inline=True,
        )

        embed.add_field(
            name="Pending Applications",
            value=str(pending_count),
            inline=True,
        )

        await ctx.send(embed=embed)

    @_applications.command(name="approve", aliases=["a"])
    async def _approve(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Approve an application.
        
        If run in an application channel without specifying a member, 
        it will approve the application for the channel owner.
        """
        # If no member specified, try to find from current channel
        if member is None:
            applications = await self.config.guild(ctx.guild).applications()
            current_channel_id = ctx.channel.id
            
            # Find application by channel ID
            found_member = None
            for user_id, app_data in applications.items():
                if app_data.get("channel_id") == current_channel_id:
                    found_member = ctx.guild.get_member(int(user_id))
                    if found_member:
                        member = found_member
                        break
            
            if not member:
                await ctx.send(
                    "❌ No member specified and this doesn't appear to be an application channel. "
                    "Please specify a member: `[p]applications approve @user`"
                )
                return

        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send("The application system is not enabled.")
            return

        applications = await self.config.guild(ctx.guild).applications()
        if str(member.id) not in applications:
            await ctx.send(f"{member.mention} does not have an active application.")
            return

        app_data = applications[str(member.id)]
        if app_data.get("status") != "pending":
            await ctx.send(
                f"{member.mention}'s application is already {app_data.get('status')}."
            )
            return

        # Handle role changes on approval
        # Either remove restricted role OR add access roles (not both)
        restricted_role_id = await self.config.guild(ctx.guild).restricted_role()
        access_role_ids = await self.config.guild(ctx.guild).access_roles()
        
        if restricted_role_id:
            # Remove restricted role (existing behavior)
            restricted_role = ctx.guild.get_role(restricted_role_id)
            if restricted_role and restricted_role in member.roles:
                try:
                    await member.remove_roles(restricted_role, reason="Application approved")
                    log.info(f"Removed restricted role from {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied removing restricted role from {member.display_name}")
                    await ctx.send(
                        f"⚠️ Approved the application but couldn't remove the restricted role. "
                        f"Please remove it manually from {member.mention}."
                    )
                except discord.HTTPException as e:
                    log.error(f"Error removing restricted role: {e}")
                    await ctx.send(
                        f"⚠️ Approved the application but encountered an error removing the restricted role: {e}"
                    )
        elif access_role_ids:
            # Add access roles (new behavior)
            access_roles = [ctx.guild.get_role(rid) for rid in access_role_ids if ctx.guild.get_role(rid)]
            if access_roles:
                try:
                    await member.add_roles(*access_roles, reason="Application approved")
                    log.info(f"Added access roles to {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied adding access roles to {member.display_name}")
                    await ctx.send(
                        f"⚠️ Approved the application but couldn't add access roles. "
                        f"Please add them manually to {member.mention}."
                    )
                except discord.HTTPException as e:
                    log.error(f"Error adding access roles: {e}")
                    await ctx.send(
                        f"⚠️ Approved the application but encountered an error adding access roles: {e}"
                    )

        # Update status
        app_data["status"] = "approved"
        app_data["approved_by"] = ctx.author.id
        app_data["approved_at"] = datetime.utcnow().isoformat()
        
        # Schedule cleanup
        cleanup_delay = await self.config.guild(ctx.guild).cleanup_delay()
        cleanup_time = datetime.utcnow() + timedelta(hours=cleanup_delay)
        app_data["cleanup_scheduled_at"] = cleanup_time.isoformat()
        
        applications[str(member.id)] = app_data
        await self.config.guild(ctx.guild).applications.set(applications)

        # Log to log channel
        decision_maker = ctx.guild.get_member(ctx.author.id)
        await self.log_application_event(
            ctx.guild, member, "approved", decision_maker=decision_maker
        )

        # Notify in channel
        channel_id = app_data.get("channel_id")
        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                embed = await self.create_review_embed(
                    member, app_data.get("responses", {}), "approved"
                )
                try:
                    await channel.send(
                        f"✅ **Application Approved!**\n\n"
                        f"Congratulations {member.mention}! Your application has been approved. "
                        f"You now have full access to the server.",
                        embed=embed,
                    )
                except discord.HTTPException:
                    pass

        await ctx.send(f"✅ Approved {member.mention}'s application.")

    @_applications.command(name="deny", aliases=["d"])
    async def _deny(
        self, ctx: commands.Context, member: Optional[discord.Member] = None, *, reason: Optional[str] = None
    ):
        """Deny an application.
        
        If run in an application channel without specifying a member, 
        it will deny the application for the channel owner.
        """
        # If no member specified, try to find from current channel
        if member is None:
            applications = await self.config.guild(ctx.guild).applications()
            current_channel_id = ctx.channel.id
            
            # Find application by channel ID
            found_member = None
            for user_id, app_data in applications.items():
                if app_data.get("channel_id") == current_channel_id:
                    found_member = ctx.guild.get_member(int(user_id))
                    if found_member:
                        member = found_member
                        break
            
            if not member:
                await ctx.send(
                    "❌ No member specified and this doesn't appear to be an application channel. "
                    "Please specify a member: `[p]applications deny @user [reason]`"
                )
                return

        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send("The application system is not enabled.")
            return

        applications = await self.config.guild(ctx.guild).applications()
        if str(member.id) not in applications:
            await ctx.send(f"{member.mention} does not have an active application.")
            return

        app_data = applications[str(member.id)]
        if app_data.get("status") != "pending":
            await ctx.send(
                f"{member.mention}'s application is already {app_data.get('status')}."
            )
            return

        # Update status
        app_data["status"] = "denied"
        app_data["denied_by"] = ctx.author.id
        app_data["denied_at"] = datetime.utcnow().isoformat()
        if reason:
            app_data["denial_reason"] = reason
        
        # Schedule cleanup
        cleanup_delay = await self.config.guild(ctx.guild).cleanup_delay()
        cleanup_time = datetime.utcnow() + timedelta(hours=cleanup_delay)
        app_data["cleanup_scheduled_at"] = cleanup_time.isoformat()
        
        applications[str(member.id)] = app_data
        await self.config.guild(ctx.guild).applications.set(applications)

        # Log to log channel
        decision_maker = ctx.guild.get_member(ctx.author.id)
        await self.log_application_event(
            ctx.guild, member, "denied", decision_maker=decision_maker, reason=reason
        )

        # Notify in channel
        channel_id = app_data.get("channel_id")
        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                embed = await self.create_review_embed(
                    member, app_data.get("responses", {}), "denied"
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                try:
                    await channel.send(
                        f"❌ **Application Denied**\n\n"
                        f"Sorry {member.mention}, your application has been denied. "
                        f"You can appeal this decision by messaging an admin in this channel.",
                        embed=embed,
                    )
                except discord.HTTPException:
                    pass

        await ctx.send(f"❌ Denied {member.mention}'s application.")

    async def approve_application_interaction(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        """Approve application from button interaction."""
        applications = await self.config.guild(interaction.guild).applications()
        if str(member.id) not in applications:
            await interaction.response.send_message(
                f"❌ {member.mention} does not have an active application.", ephemeral=True
            )
            return

        app_data = applications[str(member.id)]
        if app_data.get("status") != "pending":
            await interaction.response.send_message(
                f"❌ This application is already {app_data.get('status')}.", ephemeral=True
            )
            return

        # Handle role changes on approval
        # Either remove restricted role OR add access roles (not both)
        restricted_role_id = await self.config.guild(interaction.guild).restricted_role()
        access_role_ids = await self.config.guild(interaction.guild).access_roles()
        
        if restricted_role_id:
            # Remove restricted role (existing behavior)
            restricted_role = interaction.guild.get_role(restricted_role_id)
            if restricted_role and restricted_role in member.roles:
                try:
                    await member.remove_roles(restricted_role, reason="Application approved")
                    log.info(f"Removed restricted role from {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied removing restricted role from {member.display_name}")
                except discord.HTTPException as e:
                    log.error(f"Error removing restricted role: {e}")
        elif access_role_ids:
            # Add access roles (new behavior)
            access_roles = [interaction.guild.get_role(rid) for rid in access_role_ids if interaction.guild.get_role(rid)]
            if access_roles:
                try:
                    await member.add_roles(*access_roles, reason="Application approved")
                    log.info(f"Added access roles to {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied adding access roles to {member.display_name}")
                except discord.HTTPException as e:
                    log.error(f"Error adding access roles: {e}")

        # Update status
        app_data["status"] = "approved"
        app_data["approved_by"] = interaction.user.id
        app_data["approved_at"] = datetime.utcnow().isoformat()
        
        # Schedule cleanup
        cleanup_delay = await self.config.guild(interaction.guild).cleanup_delay()
        cleanup_time = datetime.utcnow() + timedelta(hours=cleanup_delay)
        app_data["cleanup_scheduled_at"] = cleanup_time.isoformat()
        
        applications[str(member.id)] = app_data
        await self.config.guild(interaction.guild).applications.set(applications)

        # Log to log channel
        decision_maker = interaction.guild.get_member(interaction.user.id)
        await self.log_application_event(
            interaction.guild, member, "approved", decision_maker=decision_maker
        )

        # Update the message to remove buttons
        embed = await self.create_review_embed(
            member, app_data.get("responses", {}), "approved"
        )
        try:
            if interaction.response.is_done():
                # If already responded, try to edit via followup
                await interaction.followup.edit_message(
                    interaction.message.id,
                    content=(
                        f"✅ **Application Approved by {interaction.user.mention}**\n\n"
                        f"Congratulations {member.mention}! Your application has been approved. "
                        f"You now have full access to the server."
                    ),
                    embed=embed,
                    view=None,
                )
            else:
                await interaction.response.edit_message(
                    content=(
                        f"✅ **Application Approved by {interaction.user.mention}**\n\n"
                        f"Congratulations {member.mention}! Your application has been approved. "
                        f"You now have full access to the server."
                    ),
                    embed=embed,
                    view=None,
                )
        except discord.HTTPException:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"✅ Approved {member.mention}'s application.", ephemeral=True
                )

        # Notify in channel
        channel_id = app_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
            if channel and channel != interaction.channel:
                embed = await self.create_review_embed(
                    member, app_data.get("responses", {}), "approved"
                )
                try:
                    await channel.send(
                        f"✅ **Application Approved!**\n\n"
                        f"Congratulations {member.mention}! Your application has been approved. "
                        f"You now have full access to the server.",
                        embed=embed,
                    )
                except discord.HTTPException:
                    pass

        log.info(f"Application approved for {member.display_name} by {interaction.user.display_name}")

    async def deny_application(
        self, interaction: discord.Interaction, member: discord.Member, reason: str
    ):
        """Deny application from modal interaction."""
        applications = await self.config.guild(interaction.guild).applications()
        if str(member.id) not in applications:
            await interaction.response.send_message(
                f"❌ {member.mention} does not have an active application.", ephemeral=True
            )
            return

        app_data = applications[str(member.id)]
        if app_data.get("status") != "pending":
            await interaction.response.send_message(
                f"❌ This application is already {app_data.get('status')}.", ephemeral=True
            )
            return

        # Update status
        app_data["status"] = "denied"
        app_data["denied_by"] = interaction.user.id
        app_data["denied_at"] = datetime.utcnow().isoformat()
        app_data["denial_reason"] = reason
        
        # Schedule cleanup
        cleanup_delay = await self.config.guild(interaction.guild).cleanup_delay()
        cleanup_time = datetime.utcnow() + timedelta(hours=cleanup_delay)
        app_data["cleanup_scheduled_at"] = cleanup_time.isoformat()
        
        applications[str(member.id)] = app_data
        await self.config.guild(interaction.guild).applications.set(applications)

        # Log to log channel
        decision_maker = interaction.guild.get_member(interaction.user.id)
        await self.log_application_event(
            interaction.guild, member, "denied", decision_maker=decision_maker, reason=reason
        )

        # Respond to modal
        await interaction.response.send_message(
            f"✅ Application denied. {member.mention} has been notified.", ephemeral=True
        )

        # Try to find and update the original message with buttons
        channel_id = app_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                # Try to find the submission message and update it
                try:
                    async for message in channel.history(limit=50):
                        if message.author == interaction.guild.me and message.embeds:
                            embed = message.embeds[0]
                            if embed.title and member.display_name in embed.title and "pending" in embed.title.lower():
                                # Found the submission message, update it
                                new_embed = await self.create_review_embed(
                                    member, app_data.get("responses", {}), "denied"
                                )
                                new_embed.add_field(name="Reason", value=reason, inline=False)
                                await message.edit(
                                    content=(
                                        f"❌ **Application Denied by {interaction.user.mention}**\n\n"
                                        f"Sorry {member.mention}, your application has been denied. "
                                        f"You can appeal this decision by messaging an admin in this channel."
                                    ),
                                    embed=new_embed,
                                    view=None,
                                )
                                break
                except discord.HTTPException:
                    pass

                # Also send a notification message
                embed = await self.create_review_embed(
                    member, app_data.get("responses", {}), "denied"
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                try:
                    await channel.send(
                        f"❌ **Application Denied**\n\n"
                        f"Sorry {member.mention}, your application has been denied. "
                        f"You can appeal this decision by messaging an admin in this channel.",
                        embed=embed,
                    )
                except discord.HTTPException:
                    pass

        log.info(f"Application denied for {member.display_name} by {interaction.user.display_name}")

    @_applications.command(name="view")
    async def _view(self, ctx: commands.Context, member: discord.Member):
        """View an application."""
        applications = await self.config.guild(ctx.guild).applications()
        if str(member.id) not in applications:
            await ctx.send(f"{member.mention} does not have an active application.")
            return

        app_data = applications[str(member.id)]
        responses = app_data.get("responses", {})
        
        # Handle case where application hasn't been submitted yet
        if not responses:
            await ctx.send(
                f"{member.mention} has an application channel but hasn't submitted the form yet."
            )
            return

        embed = await self.create_review_embed(
            member, responses, app_data.get("status", "pending")
        )

        if app_data.get("denial_reason"):
            embed.add_field(name="Denial Reason", value=app_data["denial_reason"], inline=False)

        await ctx.send(embed=embed)

    @_applications.command(name="list")
    async def _list(self, ctx: commands.Context):
        """List all pending applications."""
        applications = await self.config.guild(ctx.guild).applications()
        pending = {
            uid: app
            for uid, app in applications.items()
            if app.get("status") == "pending"
        }

        if not pending:
            await ctx.send("No pending applications.")
            return

        embed = discord.Embed(
            title="Pending Applications",
            color=await ctx.embed_color(),
        )

        for user_id, app_data in list(pending.items())[:10]:  # Limit to 10
            member = ctx.guild.get_member(int(user_id))
            if member:
                channel_id = app_data.get("channel_id")
                channel = ctx.guild.get_channel(channel_id) if channel_id else None
                embed.add_field(
                    name=member.display_name,
                    value=f"**Channel:** {channel.mention if channel else 'N/A'}\n**User:** {member.mention}",
                    inline=True,
                )

        if len(pending) > 10:
            embed.set_footer(text=f"Showing 10 of {len(pending)} pending applications")

        await ctx.send(embed=embed)

    @_applications.command(name="close")
    async def _close(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Close/delete an application channel.
        
        If run in an application channel without specifying a member, 
        it will close the current channel.
        """
        # If no member specified, try to find from current channel
        if member is None:
            applications = await self.config.guild(ctx.guild).applications()
            current_channel_id = ctx.channel.id
            
            # Find application by channel ID
            found_member = None
            for user_id, app_data in applications.items():
                if app_data.get("channel_id") == current_channel_id:
                    found_member = ctx.guild.get_member(int(user_id))
                    if found_member:
                        member = found_member
                        break
            
            if not member:
                await ctx.send(
                    "❌ No member specified and this doesn't appear to be an application channel. "
                    "Please specify a member: `[p]applications close @user`"
                )
                return

        applications = await self.config.guild(ctx.guild).applications()
        if str(member.id) not in applications:
            await ctx.send(f"{member.mention} does not have an active application.")
            return

        app_data = applications[str(member.id)]
        channel_id = app_data.get("channel_id")

        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.delete(reason=f"Application closed by {ctx.author.display_name}")
                    if channel.id != ctx.channel.id:
                        await ctx.send(f"✅ Closed application channel for {member.mention}.")
                except discord.Forbidden:
                    await ctx.send("❌ Permission denied deleting channel.")
                except discord.HTTPException as e:
                    await ctx.send(f"❌ Error deleting channel: {e}")
            else:
                await ctx.send("Channel not found.")
        else:
            await ctx.send("No channel associated with this application.")

        # Remove from applications
        del applications[str(member.id)]
        await self.config.guild(ctx.guild).applications.set(applications)

    async def cleanup_loop(self):
        """Background task to periodically clean up expired application channels."""
        await self.bot.wait_until_ready()

        while True:
            try:
                # Wait 1 hour before checking again
                await asyncio.sleep(3600)

                log.debug("Running application channel cleanup check")

                for guild in self.bot.guilds:
                    try:
                        if not await self.config.guild(guild).enabled():
                            continue

                        applications = await self.config.guild(guild).applications()
                        current_time = datetime.utcnow()

                        to_remove = []
                        for user_id, app_data in applications.items():
                            cleanup_time_str = app_data.get("cleanup_scheduled_at")
                            if not cleanup_time_str:
                                continue

                            try:
                                cleanup_time = datetime.fromisoformat(cleanup_time_str)
                                if current_time >= cleanup_time:
                                    # Time to clean up
                                    channel_id = app_data.get("channel_id")
                                    if channel_id:
                                        channel = guild.get_channel(channel_id)
                                        if channel:
                                            try:
                                                await channel.delete(
                                                    reason="Application channel cleanup (approved/denied)"
                                                )
                                                log.info(
                                                    f"Cleaned up application channel {channel.name} for user {user_id} in {guild.name}"
                                                )
                                            except discord.Forbidden:
                                                log.warning(
                                                    f"Permission denied deleting channel {channel_id} in {guild.name}"
                                                )
                                            except discord.HTTPException as e:
                                                log.error(f"Error deleting channel {channel_id}: {e}")
                                        else:
                                            # Channel already deleted
                                            log.debug(f"Channel {channel_id} already deleted, removing from tracking")
                                    to_remove.append(user_id)
                            except (ValueError, TypeError) as e:
                                log.warning(f"Invalid cleanup_scheduled_at for user {user_id}: {e}")
                                continue

                        # Remove cleaned up applications
                        if to_remove:
                            async with self.config.guild(guild).applications() as apps:
                                for user_id in to_remove:
                                    if user_id in apps:
                                        del apps[user_id]
                            log.info(f"Removed {len(to_remove)} cleaned up applications from {guild.name}")

                    except Exception as e:
                        log.error(f"Error in cleanup loop for guild {guild.name}: {e}", exc_info=True)

            except asyncio.CancelledError:
                log.info("Cleanup loop cancelled")
                break
            except Exception as e:
                log.error(f"Error in cleanup loop: {e}", exc_info=True)
                # Wait a bit before retrying
                await asyncio.sleep(300)  # 5 minutes

    async def cleanup_channels(self, guild: discord.Guild) -> int:
        """Manually trigger cleanup of expired channels. Returns number of channels cleaned."""
        if not await self.config.guild(guild).enabled():
            return 0

        applications = await self.config.guild(guild).applications()
        current_time = datetime.utcnow()
        cleaned = 0

        to_remove = []
        for user_id, app_data in applications.items():
            cleanup_time_str = app_data.get("cleanup_scheduled_at")
            if not cleanup_time_str:
                continue

            try:
                cleanup_time = datetime.fromisoformat(cleanup_time_str)
                if current_time >= cleanup_time:
                    channel_id = app_data.get("channel_id")
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            try:
                                await channel.delete(
                                    reason="Manual application channel cleanup"
                                )
                                cleaned += 1
                                log.info(
                                    f"Manually cleaned up application channel {channel.name} for user {user_id} in {guild.name}"
                                )
                            except discord.Forbidden:
                                log.warning(
                                    f"Permission denied deleting channel {channel_id} in {guild.name}"
                                )
                            except discord.HTTPException as e:
                                log.error(f"Error deleting channel {channel_id}: {e}")
                        else:
                            # Channel already deleted
                            cleaned += 1
                    to_remove.append(user_id)
            except (ValueError, TypeError) as e:
                log.warning(f"Invalid cleanup_scheduled_at for user {user_id}: {e}")
                continue

        # Remove cleaned up applications
        if to_remove:
            async with self.config.guild(guild).applications() as apps:
                for user_id in to_remove:
                    if user_id in apps:
                        del apps[user_id]

        return cleaned


async def setup(bot: Red):
    """Load the Applications cog."""
    cog = Applications(bot)
    await bot.add_cog(cog)
    log.info("Applications cog loaded successfully")
