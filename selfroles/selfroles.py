import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.wzyss-cogs.selfroles")


class RoleButton(Button):
    """Button for role assignment."""

    CUSTOM_ID_PREFIX = "selfrole:"

    def __init__(
        self,
        cog: "SelfRoles",
        role_id: int,
        label: str,
        emoji: Optional[str] = None,
        style: discord.ButtonStyle = discord.ButtonStyle.primary,
    ):
        # Deterministic custom_id so interactions work after bot restart without re-registering views
        custom_id = f"{self.CUSTOM_ID_PREFIX}{role_id}"
        super().__init__(label=label, emoji=emoji, style=style, custom_id=custom_id)
        self.cog = cog
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        """Handle button click for role assignment."""
        await self.cog.handle_role_button_click(interaction, self.role_id)


class RoleAssignmentView(View):
    """View containing role assignment buttons."""

    def __init__(self, cog: "SelfRoles", message_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.message_id = message_id

    async def refresh_buttons(self, interaction: Optional[discord.Interaction] = None):
        """Refresh button states based on user's current roles."""
        if not interaction:
            return

        user = interaction.user
        if not isinstance(user, discord.Member):
            return

        message_config = await self.cog._get_role_assignment_config(
            user.guild, self.message_id
        )
        if not message_config:
            return

        assignments = message_config.get("assignments", [])
        user_role_ids = [role.id for role in user.roles]

        # Update button styles based on user's roles
        for item in self.children:
            if isinstance(item, RoleButton):
                if item.role_id in user_role_ids:
                    item.style = discord.ButtonStyle.success
                else:
                    item.style = discord.ButtonStyle.primary

        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(view=self)
            else:
                await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass


class EmbedConfigModal(Modal):
    """Modal for configuring embed settings."""

    def __init__(self, cog: "SelfRoles", existing_data: Optional[Dict] = None):
        title = "Edit Embed" if existing_data else "Configure Embed"
        super().__init__(title=title)
        self.cog = cog
        self.existing_data = existing_data or {}

        self.title_input = TextInput(
            label="Title",
            placeholder="Role Selection",
            default=self.existing_data.get("title", ""),
            required=True,
            max_length=256,
        )
        self.add_item(self.title_input)

        self.description_input = TextInput(
            label="Description",
            placeholder="Click the buttons below to assign roles...",
            default=self.existing_data.get("description", ""),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.description_input)

        self.color_input = TextInput(
            label="Color (hex code, optional)",
            placeholder="#FF0000 or leave empty for default",
            default=self.existing_data.get("color_hex", ""),
            required=False,
            max_length=7,
        )
        self.add_item(self.color_input)

        self.footer_input = TextInput(
            label="Footer (optional)",
            placeholder="Footer text",
            default=self.existing_data.get("footer", ""),
            required=False,
            max_length=2048,
        )
        self.add_item(self.footer_input)

        self.thumbnail_input = TextInput(
            label="Thumbnail Image URL (optional)",
            placeholder="https://example.com/image.png",
            default=self.existing_data.get("thumbnail_url", ""),
            required=False,
            max_length=2000,
        )
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        color_hex = self.color_input.value.strip() if self.color_input.value else None
        color = None

        if color_hex:
            color_hex = color_hex.lstrip("#")
            if len(color_hex) == 6:
                try:
                    color = int(color_hex, 16)
                except ValueError:
                    color = None
                    color_hex = None

        embed_data = {
            "title": self.title_input.value,
            "description": self.description_input.value or None,
            "color": color,
            "color_hex": color_hex if color_hex else None,
            "footer": self.footer_input.value or None,
            "thumbnail_url": self.thumbnail_input.value.strip() if self.thumbnail_input.value else None,
        }

        # Store in builder state
        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            self.cog._builder_states[user_id] = {
                "embed_data": {},
                "assignments": [],
                "groups": {},
            }

        self.cog._builder_states[user_id]["embed_data"] = embed_data

        await interaction.response.send_message(
            "✅ Embed configuration saved!", ephemeral=True
        )

        # Refresh builder view
        if hasattr(self, "builder_view") and self.builder_view.message:
            await self.builder_view.refresh(interaction)


class AddRoleModal(Modal):
    """Modal for adding a role to the assignment message."""

    def __init__(self, cog: "SelfRoles"):
        super().__init__(title="Add Role")
        self.cog = cog

        self.role_input = TextInput(
            label="Role (mention or ID)",
            placeholder="@Role or 123456789012345678",
            required=True,
            max_length=100,
        )
        self.add_item(self.role_input)

        self.method_input = TextInput(
            label="Method (button/reaction/both/command)",
            placeholder="button",
            required=True,
            max_length=20,
            default="button",
        )
        self.add_item(self.method_input)

        self.label_input = TextInput(
            label="Button Label (optional, for button/both)",
            placeholder="Leave empty to use role name",
            required=False,
            max_length=80,
        )
        self.add_item(self.label_input)

        self.emoji_input = TextInput(
            label="Emoji (optional)",
            placeholder="😀 or <:name:123456789>",
            required=False,
            max_length=100,
        )
        self.add_item(self.emoji_input)

        self.group_input = TextInput(
            label="Group ID (optional, for exclusive groups)",
            placeholder="colors",
            required=False,
            max_length=50,
        )
        self.add_item(self.group_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle role addition."""
        role_str = self.role_input.value.strip()
        method = self.method_input.value.strip().lower()
        label = self.label_input.value.strip() if self.label_input.value else None
        emoji = self.emoji_input.value.strip() if self.emoji_input.value else None
        group_id = self.group_input.value.strip() if self.group_input.value else None

        # Validate method
        if method not in ["button", "reaction", "both", "command"]:
            await interaction.response.send_message(
                "❌ Invalid method. Use: button, reaction, both, or command",
                ephemeral=True,
            )
            return

        # Parse role - try multiple methods
        role = None
        
        # Method 1: Try to parse as mention <@&123456789>
        role_match = re.match(r"<@&(\d+)>", role_str)
        if role_match:
            role_id = int(role_match.group(1))
            role = interaction.guild.get_role(role_id)
        
        # Method 2: Try to parse as raw ID (numeric string)
        if not role:
            try:
                role_id = int(role_str)
                role = interaction.guild.get_role(role_id)
            except ValueError:
                pass
        
        # Method 3: Try exact name match (case-sensitive)
        if not role:
            role = discord.utils.get(interaction.guild.roles, name=role_str)
        
        # Method 4: Try case-insensitive name match
        if not role:
            role_str_lower = role_str.lower()
            for r in interaction.guild.roles:
                if r.name.lower() == role_str_lower:
                    role = r
                    break
        
        # Method 5: Try partial name match (case-insensitive)
        if not role:
            role_str_lower = role_str.lower()
            for r in interaction.guild.roles:
                if role_str_lower in r.name.lower() or r.name.lower() in role_str_lower:
                    role = r
                    break

        if not role:
            await interaction.response.send_message(
                "❌ Role not found. Use a role mention, ID, or exact name.",
                ephemeral=True,
            )
            return

        # Check bot permissions
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ I need the 'Manage Roles' permission to assign roles.",
                ephemeral=True,
            )
            return

        if interaction.guild.me.top_role <= role:
            await interaction.response.send_message(
                "❌ I cannot assign this role because it's higher than or equal to my highest role.",
                ephemeral=True,
            )
            return

        # Get builder state
        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            self.cog._builder_states[user_id] = {
                "embed_data": {},
                "assignments": [],
                "groups": {},
            }

        state = self.cog._builder_states[user_id]
        assignments = state.get("assignments", [])

        # Check if role already added
        if any(a.get("role_id") == role.id for a in assignments):
            await interaction.response.send_message(
                f"❌ Role {role.mention} is already added.",
                ephemeral=True,
            )
            return

        assignment = {
            "role_id": role.id,
            "method": method,
        }

        if method in ["button", "both"]:
            button_label = label or role.name
            if len(button_label) > 80:
                button_label = button_label[:77] + "..."
            assignment["button_label"] = button_label
            assignment["button_emoji"] = self.cog._parse_emoji(emoji) if emoji else None

        if method in ["reaction", "both"]:
            assignment["reaction_emoji"] = self.cog._parse_emoji(emoji) if emoji else None

        if group_id:
            assignment["group_id"] = group_id
            groups = state.get("groups", {})
            if group_id not in groups:
                groups[group_id] = []
            if role.id not in groups[group_id]:
                groups[group_id].append(role.id)
            state["groups"] = groups

        assignments.append(assignment)
        state["assignments"] = assignments

        await interaction.response.send_message(
            f"✅ Added role {role.mention} with method '{method}'.",
            ephemeral=True,
        )

        # Refresh builder view
        if hasattr(self, "builder_view") and self.builder_view.message:
            await self.builder_view.refresh(interaction)


class RoleSelectMenu(Select):
    """Select menu for choosing a role to edit or delete."""

    def __init__(
        self,
        cog: "SelfRoles",
        assignments: List[Dict],
        action: str,
        builder_view: "RoleBuilderView",
    ):
        self.cog = cog
        self.assignments = assignments
        self.action = action
        self.builder_view = builder_view

        options = []
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = cog.bot.get_guild(builder_view.guild_id).get_role(role_id)
            if role:
                method = assignment.get("method", "button")
                label = role.name[:100] if len(role.name) <= 100 else role.name[:97] + "..."
                description = f"Method: {method}"[:100]
                options.append(
                    discord.SelectOption(
                        label=label,
                        description=description,
                        value=str(role_id),
                    )
                )

        super().__init__(
            placeholder=f"Select a role to {action}...",
            options=options[:25],  # Discord limit
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle role selection."""
        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message(
                "❌ Role not found.", ephemeral=True
            )
            return

        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            await interaction.response.send_message(
                "❌ No builder session found.", ephemeral=True
            )
            return

        state = self.cog._builder_states[user_id]
        assignments = state.get("assignments", [])

        assignment = next(
            (a for a in assignments if a.get("role_id") == role_id), None
        )
        if not assignment:
            await interaction.response.send_message(
                "❌ Assignment not found.", ephemeral=True
            )
            return

        if self.action == "delete":
            # Remove assignment
            assignments.remove(assignment)
            state["assignments"] = assignments

            # Remove from groups
            if assignment.get("group_id"):
                group_id = assignment["group_id"]
                groups = state.get("groups", {})
                if group_id in groups and role_id in groups[group_id]:
                    groups[group_id].remove(role_id)
                    if not groups[group_id]:
                        del groups[group_id]
                state["groups"] = groups

            await interaction.response.send_message(
                f"✅ Removed role {role.mention}.", ephemeral=True
            )
            await self.builder_view.refresh(interaction)
        else:
            await interaction.response.send_message(
                "❌ Edit action not yet implemented. Delete and re-add the role.",
                ephemeral=True,
            )


class RoleBuilderView(View):
    """Main view for building role assignment messages."""

    def __init__(self, cog: "SelfRoles", guild_id: int, edit_message_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message: Optional[discord.Message] = None
        self.edit_message_id = edit_message_id  # If set, we're editing an existing message

    async def refresh(self, interaction: Optional[discord.Interaction] = None):
        """Refresh the builder display."""
        guild = None
        if interaction:
            guild = interaction.guild
        elif self.message:
            guild = self.message.guild

        if not guild:
            return

        user_id = interaction.user.id if interaction else (
            self.message.author.id if self.message else None
        )
        if not user_id:
            return

        state = self.cog._builder_states.get(user_id, {
            "embed_data": {},
            "assignments": [],
            "groups": {},
        })

        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])
        groups = state.get("groups", {})

        title_text = "✏️ Self-Roles Builder (Edit Mode)" if self.edit_message_id else "🔧 Self-Roles Builder"
        description_text = (
            f"Editing message {self.edit_message_id}. Use the buttons below to modify the configuration."
            if self.edit_message_id
            else "Use the buttons below to configure your role assignment message."
        )
        
        embed = discord.Embed(
            title=title_text,
            description=description_text,
            color=await self.cog.bot.get_embed_color(guild),
        )

        # Embed configuration
        title = embed_data.get("title", "Not set")
        description = embed_data.get("description") or "Not set"
        color = embed_data.get("color_hex") or "Default"
        footer = embed_data.get("footer") or "Not set"
        thumbnail = embed_data.get("thumbnail_url") or "Not set"

        embed.add_field(
            name="📝 Embed Configuration",
            value=(
                f"**Title:** {title}\n"
                f"**Description:** {description}\n"
                f"**Color:** {color}\n"
                f"**Footer:** {footer}\n"
                f"**Thumbnail:** {thumbnail}"
            ),
            inline=False,
        )

        # Roles list
        if not assignments:
            embed.add_field(
                name="No Roles",
                value="Add roles using the **Add Role** button below.",
                inline=False,
            )
        else:
            role_list = []
            for i, assignment in enumerate(assignments, 1):
                role_id = assignment.get("role_id")
                role = guild.get_role(role_id)
                if role:
                    method = assignment.get("method", "button")
                    group_id = assignment.get("group_id")
                    group_text = f" (Group: {group_id})" if group_id else ""
                    role_list.append(f"{i}. {role.mention} - {method}{group_text}")

            if role_list:
                embed.add_field(
                    name=f"Roles ({len(assignments)})",
                    value="\n".join(role_list) if len("\n".join(role_list)) < 1024 else "Too many roles to list",
                    inline=False,
                )

        # Groups
        if groups:
            group_list = []
            for group_id, role_ids in groups.items():
                role_names = [
                    guild.get_role(rid).name
                    for rid in role_ids
                    if guild.get_role(rid)
                ]
                if role_names:
                    group_list.append(f"**{group_id}:** {', '.join(role_names)}")

            if group_list:
                embed.add_field(
                    name="Exclusive Groups",
                    value="\n".join(group_list),
                    inline=False,
                )

        if interaction:
            if interaction.response.is_done():
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
                pass

    @discord.ui.button(label="Configure Embed", style=discord.ButtonStyle.primary, emoji="📝")
    async def configure_embed(self, interaction: discord.Interaction, button: Button):
        """Open modal to configure embed."""
        user_id = interaction.user.id
        existing_data = {}
        if user_id in self.cog._builder_states:
            existing_data = self.cog._builder_states[user_id].get("embed_data", {})

        modal = EmbedConfigModal(self.cog, existing_data)
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Role", style=discord.ButtonStyle.success, emoji="➕")
    async def add_role(self, interaction: discord.Interaction, button: Button):
        """Open modal to add a role."""
        modal = AddRoleModal(self.cog)
        modal.builder_view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove Role", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_role(self, interaction: discord.Interaction, button: Button):
        """Open select menu to remove a role."""
        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            await interaction.response.send_message(
                "❌ No builder session found. Add roles first.", ephemeral=True
            )
            return

        state = self.cog._builder_states[user_id]
        assignments = state.get("assignments", [])
        if not assignments:
            await interaction.response.send_message(
                "❌ No roles to remove. Add roles first.", ephemeral=True
            )
            return

        view = View(timeout=60)
        select_menu = RoleSelectMenu(self.cog, assignments, "delete", self)
        view.add_item(select_menu)
        await interaction.response.send_message(
            "Select a role to remove:", view=view, ephemeral=True
        )

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👁️")
    async def preview(self, interaction: discord.Interaction, button: Button):
        """Preview the role assignment message."""
        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            await interaction.response.send_message(
                "❌ No builder session found.", ephemeral=True
            )
            return

        state = self.cog._builder_states[user_id]
        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])

        if not embed_data.get("title"):
            await interaction.response.send_message(
                "❌ Embed title is required. Configure the embed first.",
                ephemeral=True,
            )
            return

        if not assignments:
            await interaction.response.send_message(
                "❌ No roles added yet. Add roles first.", ephemeral=True
            )
            return

        embed = await self.cog._create_embed(embed_data, interaction.guild)

        # Add role list to embed
        role_list = []
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = interaction.guild.get_role(role_id)
            if role:
                method = assignment.get("method", "button")
                group_id = assignment.get("group_id")
                group_text = f" (Group: {group_id})" if group_id else ""
                role_list.append(f"{role.mention} - {method}{group_text}")

        if role_list:
            embed.add_field(
                name="Available Roles",
                value="\n".join(role_list) if len("\n".join(role_list)) < 1024 else "Too many roles to list",
                inline=False,
            )

        # Create view with buttons (disabled for preview)
        view = View(timeout=None)
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = interaction.guild.get_role(role_id)
            if not role:
                continue

            method = assignment.get("method", "button")
            if method in ["button", "both"]:
                button_label = assignment.get("button_label", role.name)
                button_emoji = assignment.get("button_emoji")
                button = Button(
                    label=button_label,
                    emoji=self.cog._parse_emoji(button_emoji) if button_emoji else None,
                    style=discord.ButtonStyle.primary,
                    disabled=True,  # Disabled in preview
                )
                view.add_item(button)

        await interaction.response.send_message(
            embed=embed, view=view if view.children else None, ephemeral=True
        )

    @discord.ui.button(label="Update Message", style=discord.ButtonStyle.success, emoji="📤")
    async def send_message(self, interaction: discord.Interaction, button: Button):
        """Open channel select to send the message, or update if editing."""
        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            await interaction.response.send_message(
                "❌ No builder session found.", ephemeral=True
            )
            return

        state = self.cog._builder_states[user_id]
        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])

        if not embed_data.get("title"):
            await interaction.response.send_message(
                "❌ Embed title is required. Configure the embed first.",
                ephemeral=True,
            )
            return

        if not assignments:
            await interaction.response.send_message(
                "❌ No roles added yet. Add roles first.", ephemeral=True
            )
            return

        # If editing, update the existing message directly
        if self.edit_message_id:
            await self._update_existing_message(interaction)
            return

        # Otherwise, show channel select for new message
        # Create channel select menu
        channels = [
            ch
            for ch in interaction.guild.text_channels
            if ch.permissions_for(interaction.guild.me).send_messages
            and ch.permissions_for(interaction.guild.me).embed_links
        ]

        if not channels:
            await interaction.response.send_message(
                "❌ No channels available where I can send messages.",
                ephemeral=True,
            )
            return

        options = []
        for channel in channels[:25]:  # Discord limit
            options.append(
                discord.SelectOption(
                    label=channel.name,
                    description=f"#{channel.name}",
                    value=str(channel.id),
                )
            )

        view = View(timeout=60)
        select = ChannelSelectMenu(self.cog, options, self)
        view.add_item(select)

        await interaction.response.send_message(
            "Select a channel to send the role assignment message:",
            view=view,
            ephemeral=True,
        )

    async def _update_existing_message(self, interaction: discord.Interaction):
        """Update an existing role assignment message."""
        user_id = interaction.user.id
        state = self.cog._builder_states[user_id]
        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])
        groups = state.get("groups", {})

        # Get the existing message
        messages = await self.cog.config.guild(interaction.guild).messages()
        message_config = messages.get(str(self.edit_message_id))
        if not message_config:
            await interaction.response.send_message(
                "❌ Original message configuration not found.", ephemeral=True
            )
            return

        channel_id = message_config.get("channel_id")
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message(
                "❌ Original message channel not found.", ephemeral=True
            )
            return

        try:
            old_message = await channel.fetch_message(self.edit_message_id)
        except (discord.NotFound, discord.Forbidden):
            await interaction.response.send_message(
                "❌ Original message not found. It may have been deleted.",
                ephemeral=True,
            )
            return

        # Create new embed
        embed = await self.cog._create_embed(embed_data, interaction.guild)

        # Create view with buttons
        view = RoleAssignmentView(self.cog, self.edit_message_id)
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = interaction.guild.get_role(role_id)
            if not role:
                continue

            method = assignment.get("method", "button")
            if method in ["button", "both"]:
                button_label = assignment.get("button_label", role.name)
                button_emoji = assignment.get("button_emoji")
                button = RoleButton(
                    self.cog,
                    role_id,
                    button_label,
                    emoji=self.cog._parse_emoji(button_emoji) if button_emoji else None,
                )
                view.add_item(button)

        # Update the message
        try:
            await old_message.edit(embed=embed, view=view if view.children else None)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Error updating message: {e}", ephemeral=True
            )
            return

        # Clear old reactions and add new ones
        try:
            await old_message.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Add reactions if needed
        for assignment in assignments:
            method = assignment.get("method", "button")
            if method in ["reaction", "both"]:
                reaction_emoji = assignment.get("reaction_emoji")
                if reaction_emoji:
                    try:
                        await old_message.add_reaction(reaction_emoji)
                    except (discord.HTTPException, discord.InvalidArgument):
                        log.warning(f"Could not add reaction {reaction_emoji}")

        # Update configuration
        message_config = {
            "channel_id": channel.id,
            "embed_data": embed_data,
            "assignments": assignments,
            "groups": groups,
        }

        messages[str(self.edit_message_id)] = message_config
        await self.cog.config.guild(interaction.guild).messages.set(messages)

        # Clear builder state
        del self.cog._builder_states[user_id]

        await interaction.response.send_message(
            f"✅ Role assignment message updated!", ephemeral=True
        )

        # Delete builder message
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        """Cancel the builder and clear state."""
        user_id = interaction.user.id
        if user_id in self.cog._builder_states:
            del self.cog._builder_states[user_id]

        await interaction.response.send_message(
            "✅ Builder session cancelled.", ephemeral=True
        )

        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass


class ChannelSelectMenu(Select):
    """Select menu for choosing a channel to send the message."""

    def __init__(
        self,
        cog: "SelfRoles",
        options: List[discord.SelectOption],
        builder_view: "RoleBuilderView",
    ):
        placeholder = "Select a channel..."
        if builder_view.edit_message_id:
            placeholder = "Select a channel (or message will be updated in place)..."
        super().__init__(placeholder=placeholder, options=options)
        self.cog = cog
        self.builder_view = builder_view

    async def callback(self, interaction: discord.Interaction):
        """Handle channel selection and send message."""
        channel_id = int(self.values[0])
        channel = interaction.guild.get_channel(channel_id)

        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Channel not found.", ephemeral=True
            )
            return

        user_id = interaction.user.id
        if user_id not in self.cog._builder_states:
            await interaction.response.send_message(
                "❌ No builder session found.", ephemeral=True
            )
            return

        state = self.cog._builder_states[user_id]
        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])
        groups = state.get("groups", {})

        # Create embed
        embed = await self.cog._create_embed(embed_data, interaction.guild)

        # Create view with buttons
        view = RoleAssignmentView(self.cog, 0)  # Will be updated after message is sent
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = interaction.guild.get_role(role_id)
            if not role:
                continue

            method = assignment.get("method", "button")
            if method in ["button", "both"]:
                button_label = assignment.get("button_label", role.name)
                button_emoji = assignment.get("button_emoji")
                button = RoleButton(
                    self.cog,
                    role_id,
                    button_label,
                    emoji=self.cog._parse_emoji(button_emoji) if button_emoji else None,
                )
                view.add_item(button)

        # Send message
        try:
            message = await channel.send(embed=embed, view=view if view.children else None)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Error sending message: {e}", ephemeral=True
            )
            return

        # Add reactions if needed
        for assignment in assignments:
            method = assignment.get("method", "button")
            if method in ["reaction", "both"]:
                reaction_emoji = assignment.get("reaction_emoji")
                if reaction_emoji:
                    try:
                        await message.add_reaction(reaction_emoji)
                    except (discord.HTTPException, discord.InvalidArgument):
                        log.warning(f"Could not add reaction {reaction_emoji}")

        # Store configuration
        message_config = {
            "channel_id": channel.id,
            "embed_data": embed_data,
            "assignments": assignments,
            "groups": groups,
        }

        messages = await self.cog.config.guild(interaction.guild).messages()
        messages[str(message.id)] = message_config
        await self.cog.config.guild(interaction.guild).messages.set(messages)

        # Update view with correct message_id
        view.message_id = message.id

        # Clear builder state
        del self.cog._builder_states[user_id]

        await interaction.response.send_message(
            f"✅ Role assignment message sent to {channel.mention}!", ephemeral=True
        )

        # Delete builder message
        if self.builder_view.message:
            try:
                await self.builder_view.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass


class SelfRoles(commands.Cog):
    """Interactive builder for self-assignable roles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543214, force_registration=True
        )

        default_guild = {
            "messages": {},  # {message_id: {
            #   "channel_id": int,
            #   "embed_data": {...},
            #   "assignments": [...],
            #   "groups": {...}
            # }}
        }

        self.config.register_guild(**default_guild)
        self._builder_states: Dict[int, Dict] = {}  # Temporary builder state per user
        log.info("SelfRoles cog initialized")

    def _parse_emoji(self, emoji_str: str) -> Optional[str]:
        """Parse emoji string (unicode or custom emoji ID)."""
        if not emoji_str:
            return None

        emoji_str = emoji_str.strip()

        # Try to parse as custom emoji <:name:id> or <a:name:id>
        custom_match = re.match(r"<a?:(\w+):(\d+)>", emoji_str)
        if custom_match:
            return emoji_str  # Return full format for Discord

        # Check if it's a unicode emoji
        if len(emoji_str) <= 2:  # Most unicode emojis are 1-2 characters
            return emoji_str

        # Try to get emoji by name or ID
        return emoji_str

    async def _create_embed(self, embed_data: Dict, guild: discord.Guild) -> discord.Embed:
        """Create a Discord embed from embed data."""
        color = embed_data.get("color")
        if not color:
            color = await self.bot.get_embed_color(guild)

        embed = discord.Embed(
            title=embed_data.get("title", "Role Selection"),
            description=embed_data.get("description"),
            color=color,
        )

        if embed_data.get("footer"):
            embed.set_footer(text=embed_data["footer"])

        if embed_data.get("image_url"):
            embed.set_image(url=embed_data["image_url"])

        if embed_data.get("thumbnail_url"):
            embed.set_thumbnail(url=embed_data["thumbnail_url"])

        return embed

    async def _get_role_assignment_config(
        self, guild: discord.Guild, message_id: int
    ) -> Optional[Dict]:
        """Get role assignment configuration for a message."""
        messages = await self.config.guild(guild).messages()
        return messages.get(str(message_id))

    async def handle_role_button_click(
        self, interaction: discord.Interaction, role_id: int
    ):
        """Handle role button click."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ This command can only be used in a server.", ephemeral=True
            )
            return

        user = interaction.user
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message(
                "❌ This role no longer exists.", ephemeral=True
            )
            return

        # Get message config
        message_config = await self._get_role_assignment_config(
            interaction.guild, interaction.message.id
        )
        if not message_config:
            await interaction.response.send_message(
                "❌ This role assignment message is no longer configured.", ephemeral=True
            )
            return

        # Check if user has the role
        has_role = role in user.roles

        try:
            if has_role:
                # Remove role
                await user.remove_roles(role, reason="Self-role removal via button")
                await interaction.response.send_message(
                    f"✅ Removed role {role.mention}.", ephemeral=True
                )
            else:
                # Add role - handle exclusive groups
                assignments = message_config.get("assignments", [])
                groups = message_config.get("groups", {})

                # Find assignment for this role
                assignment = next(
                    (a for a in assignments if a.get("role_id") == role_id), None
                )
                if assignment and assignment.get("group_id"):
                    # This role is in an exclusive group
                    group_id = assignment["group_id"]
                    group_role_ids = groups.get(str(group_id), [])
                    # Remove other roles in the same group
                    roles_to_remove = [
                        interaction.guild.get_role(rid)
                        for rid in group_role_ids
                        if rid != role_id
                    ]
                    roles_to_remove = [r for r in roles_to_remove if r and r in user.roles]
                    if roles_to_remove:
                        await user.remove_roles(*roles_to_remove, reason="Exclusive group role assignment")

                await user.add_roles(role, reason="Self-role assignment via button")
                await interaction.response.send_message(
                    f"✅ Added role {role.mention}.", ephemeral=True
                )

            # Refresh button state (optional - buttons will update on next interaction)
            # Note: We can't easily refresh the view here since we've already responded
            # The button style will update naturally on the next click

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to manage roles. Please contact an administrator.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            log.error(f"Error managing role: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while managing the role.", ephemeral=True
            )

    async def _populate_view_from_config(
        self, view: RoleAssignmentView, guild: discord.Guild, message_config: Dict
    ):
        """Populate a view with buttons from message config."""
        assignments = message_config.get("assignments", [])
        for assignment in assignments:
            role_id = assignment.get("role_id")
            if not role_id:
                continue

            role = guild.get_role(role_id)
            if not role:
                continue

            # Only add button if button_label is set
            button_label = assignment.get("button_label")
            if not button_label:
                continue

            button_emoji = assignment.get("button_emoji")
            button = RoleButton(
                self,
                role_id,
                button_label,
                emoji=self._parse_emoji(button_emoji) if button_emoji else None,
            )
            view.add_item(button)

    @commands.group(name="selfroles", aliases=["sr"])
    @commands.guild_only()
    async def _selfroles(self, ctx: commands.Context):
        """Manage self-assignable roles."""
        pass

    @_selfroles.command(name="build")
    @commands.admin_or_permissions(manage_guild=True)
    async def _build(self, ctx: commands.Context):
        """Start the interactive builder UI for creating a role assignment message."""
        # Initialize builder state
        if ctx.author.id not in self._builder_states:
            self._builder_states[ctx.author.id] = {
                "embed_data": {},
                "assignments": [],
                "groups": {},
            }

        # Create builder view
        view = RoleBuilderView(self, ctx.guild.id, edit_message_id=None)
        
        # Create initial embed
        state = self._builder_states[ctx.author.id]
        embed_data = state.get("embed_data", {})
        assignments = state.get("assignments", [])
        groups = state.get("groups", {})

        embed = discord.Embed(
            title="🔧 Self-Roles Builder",
            description="Use the buttons below to configure your role assignment message.",
            color=await ctx.embed_color(),
        )

        # Embed configuration
        title = embed_data.get("title", "Not set")
        description = embed_data.get("description") or "Not set"
        color = embed_data.get("color_hex") or "Default"
        footer = embed_data.get("footer") or "Not set"
        thumbnail = embed_data.get("thumbnail_url") or "Not set"

        embed.add_field(
            name="📝 Embed Configuration",
            value=(
                f"**Title:** {title}\n"
                f"**Description:** {description}\n"
                f"**Color:** {color}\n"
                f"**Footer:** {footer}\n"
                f"**Thumbnail:** {thumbnail}"
            ),
            inline=False,
        )

        # Roles list
        if not assignments:
            embed.add_field(
                name="No Roles",
                value="Add roles using the **Add Role** button below.",
                inline=False,
            )
        else:
            role_list = []
            for i, assignment in enumerate(assignments, 1):
                role_id = assignment.get("role_id")
                role = ctx.guild.get_role(role_id)
                if role:
                    method = assignment.get("method", "button")
                    group_id = assignment.get("group_id")
                    group_text = f" (Group: {group_id})" if group_id else ""
                    role_list.append(f"{i}. {role.mention} - {method}{group_text}")

            if role_list:
                embed.add_field(
                    name=f"Roles ({len(assignments)})",
                    value="\n".join(role_list) if len("\n".join(role_list)) < 1024 else "Too many roles to list",
                    inline=False,
                )

        # Groups
        if groups:
            group_list = []
            for group_id, role_ids in groups.items():
                role_names = [
                    ctx.guild.get_role(rid).name
                    for rid in role_ids
                    if ctx.guild.get_role(rid)
                ]
                if role_names:
                    group_list.append(f"**{group_id}:** {', '.join(role_names)}")

            if group_list:
                embed.add_field(
                    name="Exclusive Groups",
                    value="\n".join(group_list),
                    inline=False,
                )

        view.message = await ctx.send(embed=embed, view=view)

    @_selfroles.command(name="edit")
    @commands.admin_or_permissions(manage_guild=True)
    async def _edit(self, ctx: commands.Context, message_id: int):
        """Edit an existing role assignment message using the builder UI."""
        messages = await self.config.guild(ctx.guild).messages()
        message_id_str = str(message_id)
        
        if message_id_str not in messages:
            await ctx.send("❌ Message not found in configuration.")
            return

        message_config = messages[message_id_str]
        channel_id = message_config.get("channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        if not channel:
            await ctx.send("❌ Original message channel not found.")
            return

        # Verify message exists
        try:
            old_message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            await ctx.send("❌ Original message not found. It may have been deleted.")
            return

        # Load existing configuration into builder state
        embed_data = message_config.get("embed_data", {})
        assignments = message_config.get("assignments", [])
        groups = message_config.get("groups", {})

        self._builder_states[ctx.author.id] = {
            "embed_data": embed_data.copy(),
            "assignments": assignments.copy(),
            "groups": groups.copy(),
        }

        # Create builder view in edit mode
        view = RoleBuilderView(self, ctx.guild.id, edit_message_id=message_id)

        # Create initial embed showing current configuration
        embed = discord.Embed(
            title="✏️ Self-Roles Builder (Edit Mode)",
            description=f"Editing message {message_id}. Use the buttons below to modify the configuration.",
            color=await ctx.embed_color(),
        )

        # Embed configuration
        title = embed_data.get("title", "Not set")
        description = embed_data.get("description") or "Not set"
        color = embed_data.get("color_hex") or "Default"
        footer = embed_data.get("footer") or "Not set"
        thumbnail = embed_data.get("thumbnail_url") or "Not set"

        embed.add_field(
            name="📝 Embed Configuration",
            value=(
                f"**Title:** {title}\n"
                f"**Description:** {description}\n"
                f"**Color:** {color}\n"
                f"**Footer:** {footer}\n"
                f"**Thumbnail:** {thumbnail}"
            ),
            inline=False,
        )

        # Roles list
        if not assignments:
            embed.add_field(
                name="No Roles",
                value="Add roles using the **Add Role** button below.",
                inline=False,
            )
        else:
            role_list = []
            for i, assignment in enumerate(assignments, 1):
                role_id = assignment.get("role_id")
                role = ctx.guild.get_role(role_id)
                if role:
                    method = assignment.get("method", "button")
                    group_id = assignment.get("group_id")
                    group_text = f" (Group: {group_id})" if group_id else ""
                    role_list.append(f"{i}. {role.mention} - {method}{group_text}")

            if role_list:
                embed.add_field(
                    name=f"Roles ({len(assignments)})",
                    value="\n".join(role_list) if len("\n".join(role_list)) < 1024 else "Too many roles to list",
                    inline=False,
                )

        # Groups
        if groups:
            group_list = []
            for group_id, role_ids in groups.items():
                role_names = [
                    ctx.guild.get_role(rid).name
                    for rid in role_ids
                    if ctx.guild.get_role(rid)
                ]
                if role_names:
                    group_list.append(f"**{group_id}:** {', '.join(role_names)}")

            if group_list:
                embed.add_field(
                    name="Exclusive Groups",
                    value="\n".join(group_list),
                    inline=False,
                )

        view.message = await ctx.send(embed=embed, view=view)

    @_selfroles.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def _list(self, ctx: commands.Context):
        """List all configured role assignment messages."""
        messages = await self.config.guild(ctx.guild).messages()
        if not messages:
            await ctx.send("❌ No role assignment messages configured.")
            return

        embed = discord.Embed(
            title="Role Assignment Messages",
            color=await ctx.embed_color(),
        )

        for message_id_str, config in messages.items():
            try:
                message_id = int(message_id_str)
                channel_id = config.get("channel_id")
                channel = ctx.guild.get_channel(channel_id) if channel_id else None
                assignments = config.get("assignments", [])

                channel_text = channel.mention if channel else f"Channel {channel_id} (not found)"
                role_count = len(assignments)

                embed.add_field(
                    name=f"Message {message_id}",
                    value=f"Channel: {channel_text}\nRoles: {role_count}",
                    inline=True,
                )
            except (ValueError, KeyError):
                continue

        await ctx.send(embed=embed)

    @_selfroles.command(name="delete")
    @commands.admin_or_permissions(manage_guild=True)
    async def _delete(self, ctx: commands.Context, message_id: int):
        """Delete a role assignment message configuration."""
        messages = await self.config.guild(ctx.guild).messages()
        message_id_str = str(message_id)

        if message_id_str not in messages:
            await ctx.send("❌ Message not found in configuration.")
            return

        config = messages[message_id_str]
        channel_id = config.get("channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        if channel:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # Message might already be deleted

        del messages[message_id_str]
        await self.config.guild(ctx.guild).messages.set(messages)

        await ctx.send(f"✅ Deleted role assignment message {message_id}.")

    @_selfroles.command(name="refresh")
    @commands.admin_or_permissions(manage_guild=True)
    async def _refresh(self, ctx: commands.Context, message_id: int):
        """Refresh a role assignment message (re-send with current config)."""
        messages = await self.config.guild(ctx.guild).messages()
        message_id_str = str(message_id)

        if message_id_str not in messages:
            await ctx.send("❌ Message not found in configuration.")
            return

        config = messages[message_id_str]
        channel_id = config.get("channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        if not channel:
            await ctx.send("❌ Channel not found.")
            return

        # Delete old message
        try:
            old_message = await channel.fetch_message(message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass  # Message might already be deleted

        # Re-send message
        embed_data = config.get("embed_data", {})
        assignments = config.get("assignments", [])
        groups = config.get("groups", {})

        embed = await self._create_embed(embed_data, ctx.guild)

        view = RoleAssignmentView(self, 0)
        for assignment in assignments:
            role_id = assignment.get("role_id")
            role = ctx.guild.get_role(role_id)
            if not role:
                continue

            method = assignment.get("method", "button")
            if method in ["button", "both"]:
                button_label = assignment.get("button_label", role.name)
                button_emoji = assignment.get("button_emoji")
                button = RoleButton(
                    self,
                    role_id,
                    button_label,
                    emoji=self._parse_emoji(button_emoji) if button_emoji else None,
                )
                view.add_item(button)

        try:
            message = await channel.send(embed=embed, view=view if view.children else None)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Error sending message: {e}")
            return

        # Add reactions
        for assignment in assignments:
            method = assignment.get("method", "button")
            if method in ["reaction", "both"]:
                reaction_emoji = assignment.get("reaction_emoji")
                if reaction_emoji:
                    try:
                        await message.add_reaction(reaction_emoji)
                    except (discord.HTTPException, discord.InvalidArgument):
                        pass

        # Update message ID in config
        del messages[message_id_str]
        messages[str(message.id)] = config
        await self.config.guild(ctx.guild).messages.set(messages)

        view.message_id = message.id

        await ctx.send(f"✅ Refreshed role assignment message!")

    @commands.command(name="role")
    @commands.guild_only()
    async def _role(self, ctx: commands.Context, *, role_name: str):
        """Assign or remove a self-assignable role by name."""
        if not ctx.guild:
            return

        # Search for role assignment messages
        messages = await self.config.guild(ctx.guild).messages()
        matching_assignments = []

        for message_config in messages.values():
            assignments = message_config.get("assignments", [])
            for assignment in assignments:
                role_id = assignment.get("role_id")
                role = ctx.guild.get_role(role_id)
                if role and role_name.lower() in role.name.lower():
                    method = assignment.get("method", "button")
                    if method in ["command", "both"]:
                        matching_assignments.append((role, assignment, message_config))

        if not matching_assignments:
            await ctx.send(f"❌ No self-assignable role found matching '{role_name}'.")
            return

        if len(matching_assignments) > 1:
            # Multiple matches - list them
            role_list = "\n".join([f"• {r.mention}" for r, _, _ in matching_assignments])
            await ctx.send(
                f"Multiple roles found. Please be more specific:\n{role_list}"
            )
            return

        role, assignment, message_config = matching_assignments[0]
        user = ctx.author

        # Check if user has the role
        has_role = role in user.roles

        try:
            if has_role:
                # Remove role
                await user.remove_roles(role, reason="Self-role removal via command")
                await ctx.send(f"✅ Removed role {role.mention}.")
            else:
                # Add role - handle exclusive groups
                groups = message_config.get("groups", {})
                if assignment.get("group_id"):
                    group_id = assignment["group_id"]
                    group_role_ids = groups.get(str(group_id), [])
                    # Remove other roles in the same group
                    roles_to_remove = [
                        ctx.guild.get_role(rid)
                        for rid in group_role_ids
                        if rid != role.id
                    ]
                    roles_to_remove = [r for r in roles_to_remove if r and r in user.roles]
                    if roles_to_remove:
                        await user.remove_roles(*roles_to_remove, reason="Exclusive group role assignment")

                await user.add_roles(role, reason="Self-role assignment via command")
                await ctx.send(f"✅ Added role {role.mention}.")

        except discord.Forbidden:
            await ctx.send(
                "❌ I don't have permission to manage roles. Please contact an administrator."
            )
        except discord.HTTPException as e:
            log.error(f"Error managing role: {e}")
            await ctx.send("❌ An error occurred while managing the role.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle role assignment button interactions when no view is registered (e.g. after bot restart)."""
        if interaction.type != discord.InteractionType.message_component:
            return
        custom_id = (interaction.data or {}).get("custom_id") or ""
        if not custom_id.startswith(RoleButton.CUSTOM_ID_PREFIX):
            return
        if interaction.response.is_done():
            return
        try:
            role_id = int(custom_id[len(RoleButton.CUSTOM_ID_PREFIX) :].strip())
        except ValueError:
            return
        await self.handle_role_button_click(interaction, role_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle reaction-based role assignment."""
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        # Get message config
        messages = await self.config.guild(guild).messages()
        message_config = messages.get(str(payload.message_id))
        if not message_config:
            return

        assignments = message_config.get("assignments", [])
        reaction_emoji_str = str(payload.emoji)

        # Find assignment with matching reaction emoji
        assignment = None
        for a in assignments:
            method = a.get("method", "button")
            if method in ["reaction", "both"]:
                if str(a.get("reaction_emoji")) == reaction_emoji_str:
                    assignment = a
                    break

        if not assignment:
            return

        role_id = assignment.get("role_id")
        role = guild.get_role(role_id)
        if not role:
            # Role was deleted - clean up assignment
            log.warning(f"Role {role_id} not found for reaction assignment, cleaning up")
            await self._cleanup_invalid_role(guild, payload.message_id, role_id)
            return

        # Check bot permissions
        if not guild.me.guild_permissions.manage_roles:
            log.warning(f"Bot lacks manage_roles permission in guild {guild.id}")
            return

        # Check role hierarchy
        if guild.me.top_role <= role:
            log.warning(f"Bot cannot assign role {role_id} - hierarchy issue")
            return

        # Check if user already has role
        if role in user.roles:
            return  # Already has role, do nothing

        try:
            # Add role - handle exclusive groups
            groups = message_config.get("groups", {})
            if assignment.get("group_id"):
                group_id = assignment["group_id"]
                group_role_ids = groups.get(str(group_id), [])
                # Remove other roles in the same group
                roles_to_remove = [
                    guild.get_role(rid) for rid in group_role_ids if rid != role.id
                ]
                roles_to_remove = [r for r in roles_to_remove if r and r in user.roles]
                if roles_to_remove:
                    await user.remove_roles(*roles_to_remove, reason="Exclusive group role assignment")

            await user.add_roles(role, reason="Self-role assignment via reaction")

            # Send ephemeral confirmation (if possible)
            channel = guild.get_channel(payload.channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        f"✅ Added role {role.mention} to {user.mention}.",
                        delete_after=5,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        except discord.Forbidden:
            log.warning(f"Permission denied assigning role {role_id} to {user.id}")
        except discord.HTTPException as e:
            log.error(f"Error assigning role via reaction: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction removal for role removal."""
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return

        # Get message config
        messages = await self.config.guild(guild).messages()
        message_config = messages.get(str(payload.message_id))
        if not message_config:
            return

        assignments = message_config.get("assignments", [])
        reaction_emoji_str = str(payload.emoji)

        # Find assignment with matching reaction emoji
        assignment = None
        for a in assignments:
            method = a.get("method", "button")
            if method in ["reaction", "both"]:
                if str(a.get("reaction_emoji")) == str(reaction_emoji_str):
                    assignment = a
                    break

        if not assignment:
            return

        role_id = assignment.get("role_id")
        role = guild.get_role(role_id)
        if not role:
            # Role was deleted - clean up assignment
            await self._cleanup_invalid_role(guild, payload.message_id, role_id)
            return

        # Check bot permissions
        if not guild.me.guild_permissions.manage_roles:
            return

        # Check if user has role
        if role not in user.roles:
            return  # Doesn't have role, do nothing

        try:
            await user.remove_roles(role, reason="Self-role removal via reaction")

            # Send ephemeral confirmation (if possible)
            channel = guild.get_channel(payload.channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        f"✅ Removed role {role.mention} from {user.mention}.",
                        delete_after=5,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        except discord.Forbidden:
            log.warning(f"Permission denied removing role {role_id} from {user.id}")
        except discord.HTTPException as e:
            log.error(f"Error removing role via reaction: {e}")

    async def _cleanup_invalid_role(
        self, guild: discord.Guild, message_id: int, role_id: int
    ):
        """Remove invalid role from assignment configuration."""
        messages = await self.config.guild(guild).messages()
        message_id_str = str(message_id)
        message_config = messages.get(message_id_str)
        if message_config:
            assignments = message_config.get("assignments", [])
            assignments = [a for a in assignments if a.get("role_id") != role_id]
            message_config["assignments"] = assignments
            messages[message_id_str] = message_config
            await self.config.guild(guild).messages.set(messages)
            log.info(f"Cleaned up invalid role {role_id} from message {message_id}")


async def setup(bot: Red):
    """Load the SelfRoles cog."""
    cog = SelfRoles(bot)
    await bot.add_cog(cog)
    log.info("SelfRoles cog loaded")
