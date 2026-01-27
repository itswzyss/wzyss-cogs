import logging
from typing import Dict, List, Optional
from datetime import datetime

import discord
from discord.ui import Button, Modal, TextInput, View
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
            else:  # text (short)
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
        for field_name, text_input in self.inputs.items():
            responses[field_name] = text_input.value

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
            "bypass_roles": [],  # List of role IDs that skip application
            "manager_roles": [],  # List of role IDs that can manage applications
            "category_id": None,  # Category ID for application channels
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
        log.info("Applications cog initialized")

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

        # Notify in channel
        await channel.send(
            f"📋 **Application Submitted**\n\n"
            f"Your application has been received and is pending review. "
            f"An admin will review it shortly.",
            embed=embed,
            view=view,
        )

        log.info(f"Application submitted by {member.display_name} in {member.guild.name}")

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

        # Assign restricted role
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
    ):
        """Add a form field.

        Types: text (short), paragraph (long), number
        """
        if field_type.lower() not in ["text", "paragraph", "number"]:
            await ctx.send("Invalid field type. Use `text`, `paragraph`, or `number`.")
            return

        async with self.config.guild(ctx.guild).form_fields() as fields:
            # Check if field name already exists
            if any(f.get("name") == name for f in fields):
                await ctx.send(f"A field with name `{name}` already exists.")
                return

            fields.append(
                {
                    "name": name,
                    "label": label,
                    "type": field_type.lower(),
                    "required": required,
                    "placeholder": "",
                }
            )

        await ctx.send(f"Added field `{name}` to the application form.")

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
            embed.add_field(
                name=f"{i}. {field.get('label', field.get('name'))}",
                value=f"**Name:** `{field.get('name')}`\n**Type:** {field_type}\n**Required:** {required}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @_applications.command(name="settings")
    async def _settings(self, ctx: commands.Context):
        """Show current application system settings."""
        settings = await self.config.guild(ctx.guild).all()

        restricted_role_id = settings.get("restricted_role")
        restricted_role = (
            ctx.guild.get_role(restricted_role_id) if restricted_role_id else None
        )

        bypass_role_ids = settings.get("bypass_roles", [])
        bypass_roles = [
            ctx.guild.get_role(rid) for rid in bypass_role_ids if ctx.guild.get_role(rid)
        ]

        category_id = settings.get("category_id")
        category = ctx.guild.get_channel(category_id) if category_id else None

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

        embed.add_field(
            name="Restricted Role",
            value=restricted_role.mention if restricted_role else "Not set",
            inline=True,
        )

        embed.add_field(
            name="Category",
            value=category.mention if category else "Not set",
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

    @_applications.command(name="approve")
    async def _approve(self, ctx: commands.Context, member: discord.Member):
        """Approve an application."""
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

        # Remove restricted role
        restricted_role_id = await self.config.guild(ctx.guild).restricted_role()
        if restricted_role_id:
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

        # Update status
        app_data["status"] = "approved"
        app_data["approved_by"] = ctx.author.id
        app_data["approved_at"] = datetime.utcnow().isoformat()
        applications[str(member.id)] = app_data
        await self.config.guild(ctx.guild).applications.set(applications)

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

    @_applications.command(name="deny")
    async def _deny(
        self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None
    ):
        """Deny an application."""
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
        applications[str(member.id)] = app_data
        await self.config.guild(ctx.guild).applications.set(applications)

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

        # Remove restricted role
        restricted_role_id = await self.config.guild(interaction.guild).restricted_role()
        if restricted_role_id:
            restricted_role = interaction.guild.get_role(restricted_role_id)
            if restricted_role and restricted_role in member.roles:
                try:
                    await member.remove_roles(restricted_role, reason="Application approved")
                    log.info(f"Removed restricted role from {member.display_name}")
                except discord.Forbidden:
                    log.error(f"Permission denied removing restricted role from {member.display_name}")
                except discord.HTTPException as e:
                    log.error(f"Error removing restricted role: {e}")

        # Update status
        app_data["status"] = "approved"
        app_data["approved_by"] = interaction.user.id
        app_data["approved_at"] = datetime.utcnow().isoformat()
        applications[str(member.id)] = app_data
        await self.config.guild(interaction.guild).applications.set(applications)

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
        applications[str(member.id)] = app_data
        await self.config.guild(interaction.guild).applications.set(applications)

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
    async def _close(self, ctx: commands.Context, member: discord.Member):
        """Close/delete an application channel."""
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


async def setup(bot: Red):
    """Load the Applications cog."""
    cog = Applications(bot)
    await bot.add_cog(cog)
    log.info("Applications cog loaded successfully")
