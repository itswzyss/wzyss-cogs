import re
from typing import Dict, Optional, Pattern

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red


class LinkReplacer(commands.Cog):
    """Replace links with configured alternatives."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654321, force_registration=True
        )
        
        default_guild = {
            "link_replacements": {},
            "enabled": True
        }
        
        self.config.register_guild(**default_guild)
        self.pattern_cache: Dict[int, Dict[Pattern, str]] = {}
        
    async def initialize(self):
        """Initialize the cog by building pattern cache for all guilds."""
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            if guild_data["enabled"] and guild_data["link_replacements"]:
                self.pattern_cache[guild_id] = {}
                for source, target in guild_data["link_replacements"].items():
                    pattern = self._url_to_pattern(source)
                    if pattern:
                        self.pattern_cache[guild_id][pattern] = target
    
    @staticmethod
    def _url_to_pattern(url: str) -> Optional[Pattern]:
        """Convert a URL with wildcards to a regex pattern."""
        if not url:
            return None
            
        # Escape regex special characters except for *
        escaped = re.escape(url).replace("\\*", "(.*)")
        
        try:
            return re.compile(f"^{escaped}$", re.IGNORECASE)
        except re.error:
            return None

    @commands.group(name="linkreplacer")
    @checks.admin_or_permissions(manage_guild=True)
    async def _linkreplacer(self, ctx: commands.Context):
        """Link Replacer settings."""
        pass
        
    @_linkreplacer.command(name="add")
    async def _add_replacement(self, ctx: commands.Context, source_url: str, target_url: str):
        """Add a new link replacement rule.
        
        Use * as a wildcard. Example:
        [p]linkreplacer add https://x.com/* https://fixupx.com/*
        """
        guild = ctx.guild
        
        if not source_url or not target_url:
            await ctx.send("Both source and target URLs are required.")
            return
            
        pattern = self._url_to_pattern(source_url)
        if not pattern:
            await ctx.send("Invalid source URL pattern.")
            return
            
        async with self.config.guild(guild).link_replacements() as replacements:
            replacements[source_url] = target_url
            
        # Update pattern cache
        if guild.id not in self.pattern_cache:
            self.pattern_cache[guild.id] = {}
        self.pattern_cache[guild.id][pattern] = target_url
            
        await ctx.send(f"Link replacement rule added: {source_url} → {target_url}")
        
    @_linkreplacer.command(name="remove")
    async def _remove_replacement(self, ctx: commands.Context, source_url: str):
        """Remove a link replacement rule."""
        guild = ctx.guild
        
        async with self.config.guild(guild).link_replacements() as replacements:
            if source_url in replacements:
                del replacements[source_url]
                await ctx.send(f"Removed replacement rule for {source_url}")
                
                # Update pattern cache
                if guild.id in self.pattern_cache:
                    # Find and remove the pattern
                    pattern_to_remove = None
                    for pattern in self.pattern_cache[guild.id]:
                        if pattern.pattern == self._url_to_pattern(source_url).pattern:
                            pattern_to_remove = pattern
                            break
                    
                    if pattern_to_remove:
                        del self.pattern_cache[guild.id][pattern_to_remove]
            else:
                await ctx.send(f"No replacement rule found for {source_url}")
                
    @_linkreplacer.command(name="list")
    async def _list_replacements(self, ctx: commands.Context):
        """List all link replacement rules."""
        guild = ctx.guild
        replacements = await self.config.guild(guild).link_replacements()
        
        if not replacements:
            await ctx.send("No link replacement rules configured.")
            return
            
        message = "**Link Replacement Rules:**\n"
        for source, target in replacements.items():
            message += f"{source} → {target}\n"
            
        await ctx.send(message)
        
    @_linkreplacer.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: bool = None):
        """Toggle link replacement on or off."""
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
            
        if not on_off:
            # Clear pattern cache for this guild
            if guild.id in self.pattern_cache:
                del self.pattern_cache[guild.id]
                
        await ctx.send(f"Link replacement is now {state}.")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Process messages to replace links."""
        if message.author.bot or not message.guild:
            return
            
        guild = message.guild
        
        # Check if enabled for this guild
        if not await self.config.guild(guild).enabled():
            return
            
        # Make sure we have patterns for this guild
        if guild.id not in self.pattern_cache:
            replacements = await self.config.guild(guild).link_replacements()
            if replacements:
                self.pattern_cache[guild.id] = {}
                for source, target in replacements.items():
                    pattern = self._url_to_pattern(source)
                    if pattern:
                        self.pattern_cache[guild.id][pattern] = target
            else:
                return
                
        if not self.pattern_cache.get(guild.id):
            return
            
        # Check if the message contains any URLs we should replace
        content = message.content
        urls_to_replace = {}
        
        # Extract all URLs from the message
        words = content.split()
        for word in words:
            for pattern, replacement_template in self.pattern_cache[guild.id].items():
                match = pattern.match(word)
                if match:
                    # Get the capture groups (wildcard matches)
                    captured_parts = match.groups()
                    
                    # If the replacement has wildcards, we need to handle them
                    if "*" in replacement_template and captured_parts:
                        # Replace each * in the template with the corresponding captured part
                        new_url = replacement_template
                        for part in captured_parts:
                            new_url = new_url.replace("*", part, 1)
                        urls_to_replace[word] = new_url
                    else:
                        # Simple replacement or no wildcards found
                        urls_to_replace[word] = replacement_template
        
        if not urls_to_replace:
            return
            
        # Replace all matched URLs
        new_content = content
        for original_url, new_url in urls_to_replace.items():
            new_content = new_content.replace(original_url, new_url)
            
        # If the content changed, delete the original and post the new one
        if new_content != content:
            # Create a webhook to mimic the original author
            permissions = message.channel.permissions_for(guild.me)
            
            if permissions.manage_webhooks:
                try:
                    webhooks = await message.channel.webhooks()
                    webhook = discord.utils.get(webhooks, name="LinkReplacer")
                    
                    if webhook is None:
                        webhook = await message.channel.create_webhook(name="LinkReplacer")
                        
                    # Delete original message
                    await message.delete()
                    
                    # Send replacement via webhook with original author's avatar and name
                    await webhook.send(
                        content=new_content,
                        username=message.author.display_name,
                        avatar_url=message.author.display_avatar.url,
                        allowed_mentions=discord.AllowedMentions.none()
                    )
                except discord.Forbidden:
                    # Fallback if webhook fails
                    await message.delete()
                    await message.channel.send(
                        f"**{message.author.display_name}**: {new_content}",
                        allowed_mentions=discord.AllowedMentions.none()
                    )
            else:
                # No webhook permission, use regular message
                await message.delete()
                await message.channel.send(
                    f"**{message.author.display_name}**: {new_content}",
                    allowed_mentions=discord.AllowedMentions.none()
                )

async def setup(bot: Red):
    """Load the LinkReplacer cog."""
    cog = LinkReplacer(bot)
    await cog.initialize()
    await bot.add_cog(cog) 