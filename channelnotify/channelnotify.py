import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.wzyss-cogs.channelnotify")


class ChannelNotify(commands.Cog):
    """Automatically ping roles when messages are sent in configured channels."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654322, force_registration=True
        )
        
        default_guild = {
            "channels": {},  # {channel_id: {"roles": [role_id, ...], "cooldown": seconds}}
            "default_cooldown": 300  # 5 minutes in seconds
        }
        
        self.config.register_guild(**default_guild)
        self.last_ping: Dict[int, Dict[int, datetime]] = {}  # {guild_id: {channel_id: datetime}}
    
    @commands.command(name="channelnotify", aliases=["chnotify"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _channelnotify(self, ctx: commands.Context, channel: discord.TextChannel, *roles: discord.Role):
        """Configure channel notifications for role pings.
        
        Usage: [p]channelnotify <channel> <role1> [role2] [role3] ...
        
        Example: [p]channelnotify #social-media @Creators @Announcements
        
        This adds or updates the channel configuration. Use [p]channelnotifyset for other operations.
        """
        await self._add_channel(ctx, channel, *roles)
    
    @commands.group(name="channelnotifyset", aliases=["chnotifyset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _channelnotifyset(self, ctx: commands.Context):
        """Manage channel notification settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()
    
    @_channelnotifyset.command(name="add")
    async def _add_channel_set(self, ctx: commands.Context, channel: discord.TextChannel, *roles: discord.Role):
        """Add or update a channel with roles to ping (alternative to main command).
        
        Usage: [p]channelnotifyset add <channel> <role1> [role2] [role3] ...
        
        Example: [p]channelnotifyset add #social-media @Creators @Announcements
        """
        await self._add_channel(ctx, channel, *roles)
    
    async def _add_channel(self, ctx: commands.Context, channel: discord.TextChannel, *roles: discord.Role):
        """Add or update a channel with roles to ping.
        
        Usage: [p]channelnotify add <channel> <role1> [role2] [role3] ...
        
        Example: [p]channelnotify add #social-media @Creators @Announcements
        """
        if not roles:
            await ctx.send("You must specify at least one role to ping.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        # Get current channel config or create new
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            channels[str(channel_id)] = {
                "roles": [],
                "cooldown": await self.config.guild(guild).default_cooldown()
            }
        
        # Update roles list (replace existing)
        role_ids = [role.id for role in roles]
        channels[str(channel_id)]["roles"] = role_ids
        
        # Save config
        await self.config.guild(guild).channels.set(channels)
        
        role_mentions = ", ".join([role.mention for role in roles])
        await ctx.send(
            f"Channel {channel.mention} is now configured to ping: {role_mentions}\n"
            f"Cooldown: {channels[str(channel_id)]['cooldown']} seconds"
        )
    
    @_channelnotifyset.command(name="remove", aliases=["delete", "del"])
    async def _remove_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from notifications."""
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for notifications.")
            return
        
        del channels[str(channel_id)]
        await self.config.guild(guild).channels.set(channels)
        
        # Clear last ping cache for this channel
        if guild.id in self.last_ping and channel_id in self.last_ping[guild.id]:
            del self.last_ping[guild.id][channel_id]
        
        await ctx.send(f"Removed {channel.mention} from notifications.")
    
    @_channelnotifyset.command(name="list")
    async def _list_channels(self, ctx: commands.Context):
        """List all configured channels and their roles."""
        guild = ctx.guild
        channels = await self.config.guild(guild).channels()
        
        if not channels:
            await ctx.send("No channels are configured for notifications.")
            return
        
        message = "**Configured Channel Notifications:**\n\n"
        for channel_id_str, config in channels.items():
            try:
                channel_id = int(channel_id_str)
                channel = guild.get_channel(channel_id)
                if not channel:
                    message += f"❌ Channel ID `{channel_id}` (channel not found)\n"
                    continue
                
                role_ids = config.get("roles", [])
                roles = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
                if not roles:
                    message += f"{channel.mention}: No valid roles configured\n"
                else:
                    role_mentions = ", ".join([role.mention for role in roles])
                    cooldown = config.get("cooldown", await self.config.guild(guild).default_cooldown())
                    cooldown_min = cooldown // 60
                    cooldown_sec = cooldown % 60
                    cooldown_str = f"{cooldown_min}m {cooldown_sec}s" if cooldown_sec > 0 else f"{cooldown_min}m"
                    message += f"{channel.mention}: {role_mentions} (cooldown: {cooldown_str})\n"
            except (ValueError, KeyError) as e:
                log.error(f"Error processing channel config: {e}")
                continue
        
        await ctx.send(message)
    
    @_channelnotifyset.command(name="cooldown")
    async def _set_cooldown(self, ctx: commands.Context, channel: discord.TextChannel, cooldown_minutes: int):
        """Set the cooldown for a channel in minutes.
        
        Usage: [p]channelnotifyset cooldown <channel> <minutes>
        
        Example: [p]channelnotifyset cooldown #social-media 10
        """
        if cooldown_minutes < 0:
            await ctx.send("Cooldown cannot be negative.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        cooldown_seconds = cooldown_minutes * 60
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured. Use `{ctx.prefix}channelnotify` to configure it first."
            )
            return
        
        channels[str(channel_id)]["cooldown"] = cooldown_seconds
        await self.config.guild(guild).channels.set(channels)
        
        await ctx.send(
            f"Cooldown for {channel.mention} set to {cooldown_minutes} minute(s) ({cooldown_seconds} seconds)."
        )
    
    @_channelnotifyset.command(name="defaultcooldown")
    async def _set_default_cooldown(self, ctx: commands.Context, cooldown_minutes: int):
        """Set the default cooldown for new channels in minutes.
        
        Usage: [p]channelnotifyset defaultcooldown <minutes>
        
        Example: [p]channelnotifyset defaultcooldown 5
        """
        if cooldown_minutes < 0:
            await ctx.send("Cooldown cannot be negative.")
            return
        
        guild = ctx.guild
        cooldown_seconds = cooldown_minutes * 60
        
        await self.config.guild(guild).default_cooldown.set(cooldown_seconds)
        
        await ctx.send(
            f"Default cooldown set to {cooldown_minutes} minute(s) ({cooldown_seconds} seconds). "
            f"This will apply to newly configured channels."
        )
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle messages and ping roles if configured."""
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        
        guild = message.guild
        channel = message.channel
        channel_id = channel.id
        
        # Check if this channel is configured
        channels = await self.config.guild(guild).channels()
        channel_config = channels.get(str(channel_id))
        
        if not channel_config:
            return
        
        # Get roles to ping
        role_ids = channel_config.get("roles", [])
        if not role_ids:
            return
        
        # Get valid roles
        roles = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
        if not roles:
            return
        
        # Check cooldown
        cooldown_seconds = channel_config.get("cooldown", await self.config.guild(guild).default_cooldown())
        
        # Initialize guild dict if needed
        if guild.id not in self.last_ping:
            self.last_ping[guild.id] = {}
        
        # Check if we're on cooldown
        if channel_id in self.last_ping[guild.id]:
            last_ping_time = self.last_ping[guild.id][channel_id]
            time_since_ping = (datetime.utcnow() - last_ping_time).total_seconds()
            
            if time_since_ping < cooldown_seconds:
                # Still on cooldown, skip
                return
        
        # Update last ping time
        self.last_ping[guild.id][channel_id] = datetime.utcnow()
        
        # Ping the roles
        role_mentions = " ".join([role.mention for role in roles])
        
        try:
            await channel.send(role_mentions, allowed_mentions=discord.AllowedMentions(roles=True))
        except discord.Forbidden:
            log.warning(f"Missing permissions to send messages in {channel} (guild: {guild.id})")
        except discord.HTTPException as e:
            log.error(f"Error sending role ping in {channel} (guild: {guild.id}): {e}")


async def setup(bot: Red):
    """Load the ChannelNotify cog."""
    cog = ChannelNotify(bot)
    await bot.add_cog(cog)
