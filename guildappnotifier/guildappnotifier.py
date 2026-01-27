import logging
import discord
from typing import Optional
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.guildappnotifier")


class GuildAppNotifier(commands.Cog):
    """Notify when new guild applications are received."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543211, force_registration=True
        )
        
        default_guild = {
            "notification_channel": None,  # Channel ID for notifications
            "notification_users": [],  # List of user IDs to notify
            "enabled": True,
            "notify_on_join": True,  # Notify when member joins (after approval)
            "notify_on_verification": True,  # Notify on verification gate pass
            "embed_color": None,  # Custom embed color (optional)
        }
        
        self.config.register_guild(**default_guild)
        log.info("GuildAppNotifier cog initialized")
    
    async def send_notification(
        self, 
        guild: discord.Guild, 
        member: discord.Member, 
        event_type: str,
        additional_info: Optional[str] = None
    ):
        """Send a notification about a guild application event."""
        log.info(
            f"Processing notification request: Guild={guild.name} (ID: {guild.id}), "
            f"Member={member.display_name} (ID: {member.id}), Event={event_type}"
        )
        
        if not await self.config.guild(guild).enabled():
            log.debug(f"Notifications disabled for guild {guild.name} (ID: {guild.id}), skipping")
            return
        
        # Get notification channel
        channel_id = await self.config.guild(guild).notification_channel()
        channel = None
        if channel_id:
            channel = guild.get_channel(channel_id)
            if not channel:
                log.warning(
                    f"Configured notification channel {channel_id} not found in guild {guild.name} (ID: {guild.id}), "
                    "clearing from config"
                )
                # Channel was deleted, clear the config
                await self.config.guild(guild).notification_channel.set(None)
            else:
                log.debug(f"Found notification channel: {channel.name} (ID: {channel.id})")
        
        # Get users to notify
        user_ids = await self.config.guild(guild).notification_users()
        log.debug(f"Configured user IDs for notifications: {user_ids}")
        users_to_notify = [guild.get_member(uid) for uid in user_ids if guild.get_member(uid)]
        invalid_user_ids = [uid for uid in user_ids if not guild.get_member(uid)]
        if invalid_user_ids:
            log.warning(
                f"Some configured user IDs not found in guild {guild.name}: {invalid_user_ids}"
            )
        log.debug(f"Valid users to notify: {[u.display_name for u in users_to_notify]}")
        
        # Get embed color
        embed_color = await self.config.guild(guild).embed_color()
        if embed_color is None:
            embed_color = await self.bot.get_embed_color(guild)
        else:
            embed_color = discord.Color(embed_color)
        
        # Create embed
        embed = discord.Embed(
            title="New Guild Application Event",
            color=embed_color,
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="Event Type",
            value=event_type,
            inline=False
        )
        
        embed.add_field(
            name="User",
            value=f"{member.mention} ({member.display_name})",
            inline=True
        )
        
        embed.add_field(
            name="User ID",
            value=str(member.id),
            inline=True
        )
        
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(member.created_at.timestamp())}:R>",
            inline=True
        )
        
        if additional_info:
            embed.add_field(
                name="Additional Info",
                value=additional_info,
                inline=False
            )
        
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Guild: {guild.name} (ID: {guild.id})")
        
        # Send to channel if configured
        if channel:
            try:
                await channel.send(embed=embed)
                log.info(
                    f"Successfully sent notification to channel {channel.name} (ID: {channel.id}) "
                    f"in guild {guild.name} for event: {event_type}"
                )
            except discord.Forbidden:
                log.error(
                    f"Permission denied: Cannot send messages to channel {channel.name} (ID: {channel.id}) "
                    f"in guild {guild.name}"
                )
            except discord.HTTPException as e:
                log.error(
                    f"HTTP error sending notification to channel {channel.name} (ID: {channel.id}): {e}"
                )
        else:
            log.debug("No notification channel configured, skipping channel notification")
        
        # Send to users if configured
        for user in users_to_notify:
            if user:
                try:
                    await user.send(embed=embed)
                    log.info(
                        f"Successfully sent DM notification to user {user.display_name} (ID: {user.id}) "
                        f"for event: {event_type}"
                    )
                except discord.Forbidden:
                    log.warning(
                        f"Cannot send DM to user {user.display_name} (ID: {user.id}): "
                        "User has DMs disabled or bot blocked"
                    )
                except discord.HTTPException as e:
                    log.error(
                        f"HTTP error sending DM to user {user.display_name} (ID: {user.id}): {e}"
                    )
        
        if not channel and not users_to_notify:
            log.warning(
                f"No notification targets configured for guild {guild.name} (ID: {guild.id}). "
                "Notification was not sent."
            )
    
    @commands.group(name="guildappnotifier")
    @commands.admin_or_permissions(manage_guild=True)
    async def _guildappnotifier(self, ctx: commands.Context):
        """Guild Application Notifier settings."""
        pass
    
    @_guildappnotifier.command(name="channel")
    async def _set_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the channel for application notifications.
        
        If no channel is provided, clears the current setting.
        """
        if channel is None:
            await self.config.guild(ctx.guild).notification_channel.set(None)
            log.info(
                f"Notification channel cleared for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
                f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
            )
            await ctx.send("Notification channel cleared.")
        else:
            await self.config.guild(ctx.guild).notification_channel.set(channel.id)
            log.info(
                f"Notification channel set to {channel.name} (ID: {channel.id}) "
                f"for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
                f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
            )
            await ctx.send(f"Notification channel set to {channel.mention}")
    
    @_guildappnotifier.command(name="adduser")
    async def _add_user(self, ctx: commands.Context, user: discord.Member):
        """Add a user to receive DM notifications about applications."""
        async with self.config.guild(ctx.guild).notification_users() as users:
            if user.id not in users:
                users.append(user.id)
                log.info(
                    f"Added user {user.display_name} (ID: {user.id}) to notification list "
                    f"for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
                    f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
                )
                await ctx.send(f"{user.mention} will now receive DM notifications about applications.")
            else:
                log.debug(
                    f"User {user.display_name} (ID: {user.id}) already in notification list "
                    f"for guild {ctx.guild.name}"
                )
                await ctx.send(f"{user.mention} is already set to receive notifications.")
    
    @_guildappnotifier.command(name="removeuser")
    async def _remove_user(self, ctx: commands.Context, user: discord.Member):
        """Remove a user from receiving DM notifications."""
        async with self.config.guild(ctx.guild).notification_users() as users:
            if user.id in users:
                users.remove(user.id)
                log.info(
                    f"Removed user {user.display_name} (ID: {user.id}) from notification list "
                    f"for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
                    f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
                )
                await ctx.send(f"{user.mention} will no longer receive DM notifications.")
            else:
                log.debug(
                    f"User {user.display_name} (ID: {user.id}) not in notification list "
                    f"for guild {ctx.guild.name}"
                )
                await ctx.send(f"{user.mention} is not set to receive notifications.")
    
    @_guildappnotifier.command(name="listusers")
    async def _list_users(self, ctx: commands.Context):
        """List all users set to receive DM notifications."""
        user_ids = await self.config.guild(ctx.guild).notification_users()
        if not user_ids:
            await ctx.send("No users are configured to receive notifications.")
            return
        
        users = [ctx.guild.get_member(uid) for uid in user_ids if ctx.guild.get_member(uid)]
        if not users:
            await ctx.send("No valid users found in the notification list.")
            return
        
        user_list = "\n".join([f"- {user.mention} ({user.display_name})" for user in users])
        await ctx.send(f"**Users receiving notifications:**\n{user_list}")
    
    @_guildappnotifier.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: Optional[bool] = None):
        """Toggle application notifications on or off."""
        if on_off is None:
            current = await self.config.guild(ctx.guild).enabled()
            await self.config.guild(ctx.guild).enabled.set(not current)
            state = "enabled" if not current else "disabled"
        else:
            await self.config.guild(ctx.guild).enabled.set(on_off)
            state = "enabled" if on_off else "disabled"
        
        log.info(
            f"Notifications {state} for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
            f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
        )
        await ctx.send(f"Application notifications are now {state}.")
    
    @_guildappnotifier.command(name="settings")
    async def _show_settings(self, ctx: commands.Context):
        """Show current notification settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        channel_id = settings.get("notification_channel")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        user_ids = settings.get("notification_users", [])
        users = [ctx.guild.get_member(uid) for uid in user_ids if ctx.guild.get_member(uid)]
        
        embed = discord.Embed(
            title="Guild Application Notifier Settings",
            color=await ctx.embed_color()
        )
        
        embed.add_field(
            name="Enabled",
            value="Yes" if settings.get("enabled") else "No",
            inline=True
        )
        
        embed.add_field(
            name="Notification Channel",
            value=channel.mention if channel else "Not set",
            inline=True
        )
        
        embed.add_field(
            name="Notify on Member Join",
            value="Yes" if settings.get("notify_on_join") else "No",
            inline=True
        )
        
        embed.add_field(
            name="Notify on Verification",
            value="Yes" if settings.get("notify_on_verification") else "No",
            inline=True
        )
        
        if users:
            user_list = "\n".join([f"- {u.mention}" for u in users[:10]])
            if len(users) > 10:
                user_list += f"\n... and {len(users) - 10} more"
            embed.add_field(
                name="Users Receiving DMs",
                value=user_list or "None",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @_guildappnotifier.command(name="notifyonjoin")
    async def _notify_on_join(self, ctx: commands.Context, on_off: bool):
        """Toggle notifications when members join (after application approval)."""
        await self.config.guild(ctx.guild).notify_on_join.set(on_off)
        state = "enabled" if on_off else "disabled"
        log.info(
            f"Member join notifications {state} for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
            f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
        )
        await ctx.send(f"Notifications on member join are now {state}.")
    
    @_guildappnotifier.command(name="notifyonverification")
    async def _notify_on_verification(self, ctx: commands.Context, on_off: bool):
        """Toggle notifications when members pass verification gate."""
        await self.config.guild(ctx.guild).notify_on_verification.set(on_off)
        state = "enabled" if on_off else "disabled"
        log.info(
            f"Verification notifications {state} for guild {ctx.guild.name} (ID: {ctx.guild.id}) "
            f"by user {ctx.author.display_name} (ID: {ctx.author.id})"
        )
        await ctx.send(f"Notifications on verification are now {state}.")
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Notify when a member joins the guild (after application approval)."""
        log.info(
            f"on_member_join event triggered: Member={member.display_name} (ID: {member.id}), "
            f"Guild={member.guild.name} (ID: {member.guild.id}), Pending={member.pending}"
        )
        
        if not await self.config.guild(member.guild).notify_on_join():
            log.debug(
                f"Member join notifications disabled for guild {member.guild.name}, skipping notification"
            )
            return
        
        # Check if member is pending (still in verification)
        if member.pending:
            log.info(
                f"Member {member.display_name} (ID: {member.id}) joined but is pending verification"
            )
            # They joined but haven't passed verification yet
            await self.send_notification(
                member.guild,
                member,
                "Member Joined (Pending Verification)",
                "This member has joined but is still pending verification."
            )
        else:
            log.info(
                f"Member {member.display_name} (ID: {member.id}) fully joined (not pending)"
            )
            # They've fully joined
            await self.send_notification(
                member.guild,
                member,
                "Member Joined (Application Approved)",
                "This member has successfully joined the guild."
            )
    
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Notify when a member's pending status changes (passes verification)."""
        # Only log if pending status actually changed
        if before.pending != after.pending:
            log.info(
                f"on_member_update event: Member={after.display_name} (ID: {after.id}), "
                f"Guild={after.guild.name} (ID: {after.guild.id}), "
                f"Pending status changed: {before.pending} -> {after.pending}"
            )
        
        if not await self.config.guild(after.guild).notify_on_verification():
            if before.pending != after.pending:
                log.debug(
                    f"Verification notifications disabled for guild {after.guild.name}, skipping notification"
                )
            return
        
        # Check if member went from pending to verified
        if before.pending and not after.pending:
            log.info(
                f"Member {after.display_name} (ID: {after.id}) passed verification gate "
                f"in guild {after.guild.name}"
            )
            await self.send_notification(
                after.guild,
                after,
                "Member Passed Verification",
                "This member has passed the verification gate and can now interact with the server."
            )


async def setup(bot: Red):
    """Load the GuildAppNotifier cog."""
    cog = GuildAppNotifier(bot)
    await bot.add_cog(cog)
    log.info("GuildAppNotifier cog loaded successfully")
