import re
from typing import Dict, Optional, Pattern, Tuple, List

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
            "link_replacements": {},  # Now stores {rule_id: {"source": url, "target": url}}
            "enabled": True,
            "next_rule_id": 1  # Counter for rule IDs
        }
        
        self.config.register_guild(**default_guild)
        self.pattern_cache: Dict[int, Dict[Pattern, Tuple[str, str]]] = {}  # Now includes rule_id
        
    async def initialize(self):
        """Initialize the cog by building pattern cache for all guilds."""
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            if guild_data["enabled"] and guild_data["link_replacements"]:
                self.pattern_cache[guild_id] = {}
                
                # Handle old format data migration
                needs_migration = False
                
                # First pass - check if any old format data exists
                for rule_id, rule_data in guild_data["link_replacements"].items():
                    if isinstance(rule_data, str):
                        needs_migration = True
                        break
                
                # Migrate if needed
                if needs_migration:
                    async with self.config.guild_from_id(guild_id).link_replacements() as replacements:
                        # Create a new dict with numeric keys
                        new_replacements = {}
                        next_id = 1
                        
                        # Process old string format
                        for old_key, old_value in list(replacements.items()):
                            if isinstance(old_value, str):
                                # Old format: {source_url: target_url}
                                new_replacements[str(next_id)] = {
                                    "source": old_key,
                                    "target": old_value
                                }
                                next_id += 1
                                # Remove old format entry
                                del replacements[old_key]
                            else:
                                # Already new format, just ensure numeric ID
                                try:
                                    rule_num = int(old_key)
                                except ValueError:
                                    # Non-numeric ID, assign new one
                                    new_replacements[str(next_id)] = old_value
                                    next_id += 1
                                    # Remove old uuid entry
                                    del replacements[old_key]
                                else:
                                    # Already numeric, keep it
                                    new_replacements[old_key] = old_value
                                    if rule_num >= next_id:
                                        next_id = rule_num + 1
                        
                        # Replace all replacements with new format
                        replacements.clear()
                        replacements.update(new_replacements)
                        
                        # Update next_rule_id
                        await self.config.guild_from_id(guild_id).next_rule_id.set(next_id)
                
                # Now load the pattern cache from the updated/existing data
                replacements = await self.config.guild_from_id(guild_id).link_replacements()
                for rule_id, rule_data in replacements.items():
                    source = rule_data["source"]
                    target = rule_data["target"]
                    pattern = self._url_to_pattern(source)
                    if pattern:
                        self.pattern_cache[guild_id][pattern] = (target, rule_id)
    
    @staticmethod
    def _url_to_pattern(url: str) -> Optional[Pattern]:
        """Convert a URL with wildcards to a regex pattern.
        
        This method creates patterns that match domain boundaries properly to avoid
        matching substrings of larger domains.
        """
        if not url:
            return None
        
        # First, handle domain boundaries more carefully
        # Check if this is a domain-only pattern or a URL with path
        parts = url.split('/', 3)
        
        # Ensure domain boundaries are properly respected
        if len(parts) >= 3 and parts[0] in ('http:', 'https:'):
            # This is a URL with protocol
            domain_part = parts[2]
            prefix = f"{parts[0]}//{parts[1]}/"
            
            # Get path if it exists
            path_part = parts[3] if len(parts) > 3 else ""
            
            # Escape the domain part but handle wildcards carefully
            escaped_domain = ""
            in_wildcard = False
            for i, char in enumerate(domain_part):
                if char == '*':
                    if in_wildcard:
                        # Continue wildcard
                        continue
                    in_wildcard = True
                    # Ensure we don't match parts of larger domains
                    if i > 0 and i < len(domain_part) - 1:
                        # Middle of domain: match chars that aren't domain separators
                        escaped_domain += "([^./]*)"
                    else:
                        # Start or end: match any character sequence
                        escaped_domain += "(.*)"
                else:
                    in_wildcard = False
                    escaped_domain += re.escape(char)
            
            # Escape the path part but handle wildcards
            escaped_path = ""
            if path_part:
                in_wildcard = False
                for i, char in enumerate(path_part):
                    if char == '*':
                        if in_wildcard:
                            continue
                        in_wildcard = True
                        escaped_path += "(.*)"
                    else:
                        in_wildcard = False
                        escaped_path += re.escape(char)
                
                full_pattern = f"^{re.escape(prefix)}{escaped_domain}/{escaped_path}$"
            else:
                full_pattern = f"^{re.escape(prefix)}{escaped_domain}$"
        else:
            # This is probably just a domain or something else
            # Use a simpler approach
            escaped = ""
            in_wildcard = False
            for i, char in enumerate(url):
                if char == '*':
                    if in_wildcard:
                        continue
                    in_wildcard = True
                    escaped += "(.*)"
                else:
                    in_wildcard = False
                    escaped += re.escape(char)
            
            full_pattern = f"^{escaped}$"
        
        try:
            return re.compile(full_pattern, re.IGNORECASE)
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
        
        # Get the next available rule ID
        next_id = await self.config.guild(guild).next_rule_id()
        rule_id = str(next_id)
        
        # Increment the next ID
        await self.config.guild(guild).next_rule_id.set(next_id + 1)
            
        async with self.config.guild(guild).link_replacements() as replacements:
            replacements[rule_id] = {"source": source_url, "target": target_url}
            
        # Update pattern cache
        if guild.id not in self.pattern_cache:
            self.pattern_cache[guild.id] = {}
        self.pattern_cache[guild.id][pattern] = (target_url, rule_id)
            
        await ctx.send(f"Link replacement rule added with ID `{rule_id}`: {source_url} → {target_url}")
        
    @_linkreplacer.command(name="remove")
    async def _remove_replacement(self, ctx: commands.Context, rule_identifier: str):
        """Remove a link replacement rule by ID or source URL."""
        guild = ctx.guild
        
        async with self.config.guild(guild).link_replacements() as replacements:
            # Check if the identifier is a rule ID
            if rule_identifier in replacements:
                source_url = replacements[rule_identifier]["source"]
                del replacements[rule_identifier]
                await ctx.send(f"Removed replacement rule `{rule_identifier}` for {source_url}")
                
                # Update pattern cache
                if guild.id in self.pattern_cache:
                    # Find and remove the pattern
                    pattern_to_remove = None
                    for pattern, (_, rule_id) in self.pattern_cache[guild.id].items():
                        if rule_id == rule_identifier:
                            pattern_to_remove = pattern
                            break
                    
                    if pattern_to_remove:
                        del self.pattern_cache[guild.id][pattern_to_remove]
            else:
                # Try to convert to integer (for number-based removal)
                try:
                    rule_num = int(rule_identifier)
                    # Get rules sorted by ID to find the one at this position
                    sorted_rules = sorted(replacements.items(), key=lambda x: int(x[0]))
                    if 1 <= rule_num <= len(sorted_rules):
                        # Get the actual rule ID at this position
                        actual_rule_id = sorted_rules[rule_num - 1][0]
                        source_url = sorted_rules[rule_num - 1][1]["source"]
                        del replacements[actual_rule_id]
                        
                        await ctx.send(f"Removed replacement rule #{rule_num} for {source_url}")
                        
                        # Update pattern cache
                        if guild.id in self.pattern_cache:
                            pattern_to_remove = None
                            for pattern, (_, rule_id) in self.pattern_cache[guild.id].items():
                                if rule_id == actual_rule_id:
                                    pattern_to_remove = pattern
                                    break
                                    
                            if pattern_to_remove:
                                del self.pattern_cache[guild.id][pattern_to_remove]
                    else:
                        await ctx.send(f"No replacement rule found with number {rule_num}.")
                except ValueError:
                    # Check if the identifier is a source URL
                    found = False
                    rule_id_to_remove = None
                    source_to_remove = None
                    
                    for rule_id, rule_data in list(replacements.items()):
                        if rule_data["source"] == rule_identifier:
                            rule_id_to_remove = rule_id
                            source_to_remove = rule_identifier
                            found = True
                            break
                    
                    if found and rule_id_to_remove:
                        del replacements[rule_id_to_remove]
                        await ctx.send(f"Removed replacement rule for {source_to_remove}")
                        
                        # Update pattern cache
                        if guild.id in self.pattern_cache:
                            pattern_to_remove = None
                            for pattern, (_, rule_id) in self.pattern_cache[guild.id].items():
                                if rule_id == rule_id_to_remove:
                                    pattern_to_remove = pattern
                                    break
                                    
                            if pattern_to_remove:
                                del self.pattern_cache[guild.id][pattern_to_remove]
                    else:
                        await ctx.send(f"No replacement rule found with ID or source URL: {rule_identifier}")
                
    @_linkreplacer.command(name="list")
    async def _list_replacements(self, ctx: commands.Context):
        """List all link replacement rules."""
        guild = ctx.guild
        replacements = await self.config.guild(guild).link_replacements()
        
        if not replacements:
            await ctx.send("No link replacement rules configured.")
            return
            
        # Sort by rule ID numerically
        sorted_rules = sorted(replacements.items(), key=lambda x: int(x[0]))
        
        message = "**Link Replacement Rules:**\n"
        for i, (rule_id, rule_data) in enumerate(sorted_rules, 1):
            message += f"#{i} `{rule_id}`: {rule_data['source']} → {rule_data['target']}\n"
            
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
                for rule_id, rule_data in replacements.items():
                    source = rule_data["source"]
                    target = rule_data["target"]
                    pattern = self._url_to_pattern(source)
                    if pattern:
                        self.pattern_cache[guild.id][pattern] = (target, rule_id)
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
            for pattern, (replacement_template, _) in self.pattern_cache[guild.id].items():
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