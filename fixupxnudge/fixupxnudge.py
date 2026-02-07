import re
import logging
from typing import Dict, List, Optional
from datetime import datetime

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.fixupxnudge")


class FixupXNudge(commands.Cog):
    """Gently nudge users to use fixupx.com for X/Twitter post links."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654323, force_registration=True
        )
        
        default_guild = {
            "enabled": True,
            "cooldown_seconds": 300,  # 5 minutes default
            "channels": {}  # {channel_id: {"enabled": bool}}
        }
        
        self.config.register_guild(**default_guild)
        self.last_nudge: Dict[int, Dict[int, datetime]] = {}  # {guild_id: {channel_id: datetime}}
        
        # Regex pattern to match X/Twitter post links (containing /status/)
        # Matches: x.com/username/status/123456789 or twitter.com/username/status/123456789
        # With optional www. and http/https protocols
        self.post_link_pattern = re.compile(
            r'https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/\s]+/status/\d+[^\s]*',
            re.IGNORECASE
        )
    
    def _convert_to_fixupx(self, url: str) -> str:
        """Convert an X/Twitter URL to fixupx.com format."""
        # Replace x.com or twitter.com with fixupx.com
        # Preserve the entire path and query parameters
        converted = re.sub(
            r'https?://(?:www\.)?(x\.com|twitter\.com)',
            r'https://fixupx.com',
            url,
            flags=re.IGNORECASE
        )
        return converted
    
    def _extract_post_links(self, content: str) -> List[str]:
        """Extract X/Twitter post links from message content."""
        # Find all matches
        matches = self.post_link_pattern.findall(content)
        # Filter out any that are already fixupx.com links
        post_links = [url for url in matches if 'fixupx.com' not in url.lower()]
        return post_links
    
    @commands.group(name="fixupxnudge")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _fixupxnudge(self, ctx: commands.Context):
        """FixupX Nudge settings."""
        pass
    
    @_fixupxnudge.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: bool = None):
        """Toggle FixupX nudge on or off.
        
        Usage: [p]fixupxnudge toggle [True|False]
        
        Without arguments, toggles the current state.
        """
        guild = ctx.guild
        
        if on_off is None:
            # Toggle current state
            current = await self.config.guild(guild).enabled()
            await self.config.guild(guild).enabled.set(not current)
            state = "enabled" if not current else "disabled"
        else:
            # Set to specified state
            await self.config.guild(guild).enabled.set(on_off)
            state = "enabled" if on_off else "disabled"
        
        await ctx.send(f"FixupX nudge is now {state}.")
    
    @_fixupxnudge.command(name="cooldown")
    async def _set_cooldown(self, ctx: commands.Context, seconds: int):
        """Set the cooldown between nudges in seconds.
        
        Usage: [p]fixupxnudge cooldown <seconds>
        
        Example: [p]fixupxnudge cooldown 300
        """
        if seconds < 0:
            await ctx.send("Cooldown must be 0 or greater.")
            return
        
        guild = ctx.guild
        await self.config.guild(guild).cooldown_seconds.set(seconds)
        await ctx.send(f"Cooldown set to {seconds} seconds.")
    
    @_fixupxnudge.command(name="channel")
    async def _channel_setting(self, ctx: commands.Context, channel: discord.TextChannel, enable: bool = None):
        """Configure per-channel settings.
        
        Usage: [p]fixupxnudge channel <channel> [enable|disable]
        
        Without enable/disable, shows current setting.
        """
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        
        if enable is None:
            # Show current setting
            channel_config = channels.get(str(channel_id), {})
            channel_enabled = channel_config.get("enabled", True)  # Default to enabled
            await ctx.send(
                f"Channel {channel.mention} is currently "
                f"{'enabled' if channel_enabled else 'disabled'} for FixupX nudges."
            )
        else:
            # Set channel setting
            if str(channel_id) not in channels:
                channels[str(channel_id)] = {}
            channels[str(channel_id)]["enabled"] = enable
            await self.config.guild(guild).channels.set(channels)
            
            state = "enabled" if enable else "disabled"
            await ctx.send(f"Channel {channel.mention} is now {state} for FixupX nudges.")
    
    @_fixupxnudge.command(name="status")
    async def _status(self, ctx: commands.Context):
        """Show current FixupX nudge settings."""
        guild = ctx.guild
        
        enabled = await self.config.guild(guild).enabled()
        cooldown = await self.config.guild(guild).cooldown_seconds()
        channels = await self.config.guild(guild).channels()
        
        status_msg = (
            f"**FixupX Nudge Settings for {guild.name}:**\n"
            f"Enabled: {enabled}\n"
            f"Cooldown: {cooldown} seconds\n"
        )
        
        if channels:
            status_msg += "\n**Channel Settings:**\n"
            for ch_id, ch_config in channels.items():
                channel = guild.get_channel(int(ch_id))
                if channel:
                    ch_enabled = ch_config.get("enabled", True)
                    status_msg += f"{channel.mention}: {'enabled' if ch_enabled else 'disabled'}\n"
        
        await ctx.send(status_msg)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle messages and nudge users about fixupx for X/Twitter post links."""
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        
        guild = message.guild
        channel = message.channel
        channel_id = channel.id
        
        # Check if enabled for this guild
        if not await self.config.guild(guild).enabled():
            return
        
        # Check per-channel settings
        channels = await self.config.guild(guild).channels()
        channel_config = channels.get(str(channel_id), {})
        channel_enabled = channel_config.get("enabled", True)  # Default to enabled
        
        if not channel_enabled:
            return
        
        # Extract post links from message
        post_links = self._extract_post_links(message.content)
        
        if not post_links:
            return
        
        # Check cooldown
        cooldown_seconds = await self.config.guild(guild).cooldown_seconds()
        
        # Initialize guild dict if needed
        if guild.id not in self.last_nudge:
            self.last_nudge[guild.id] = {}
        
        # Check if we're on cooldown
        if channel_id in self.last_nudge[guild.id]:
            last_nudge_time = self.last_nudge[guild.id][channel_id]
            time_since_nudge = (datetime.utcnow() - last_nudge_time).total_seconds()
            
            if time_since_nudge < cooldown_seconds:
                # Still on cooldown, skip
                return
        
        # Update last nudge time
        self.last_nudge[guild.id][channel_id] = datetime.utcnow()
        
        # Convert all post links to fixupx format
        fixupx_links = [self._convert_to_fixupx(link) for link in post_links]
        
        # Build response message
        if len(fixupx_links) == 1:
            nudge_msg = f"💡 For better embed support, consider using: {fixupx_links[0]}"
        else:
            links_text = "\n".join([f"- {link}" for link in fixupx_links])
            nudge_msg = f"💡 For better embed support, consider using:\n{links_text}"
        
        # Send reply
        try:
            await message.reply(nudge_msg, mention_author=False)
        except discord.Forbidden:
            # No permission to reply, try regular message
            try:
                await channel.send(nudge_msg)
            except discord.Forbidden:
                log.warning(f"Missing permissions to send nudge in {channel} (guild: {guild.id})")
        except Exception as e:
            log.error(f"Error sending fixupx nudge in {channel} (guild: {guild.id}): {e}", exc_info=True)


async def setup(bot: Red):
    """Load the FixupXNudge cog."""
    cog = FixupXNudge(bot)
    await bot.add_cog(cog)
    log.info("FixupXNudge cog loaded")
