import re
import json
from typing import Dict, Optional, Pattern, Tuple, List

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box


class TextReplace(commands.Cog):
    """Replace text using regex patterns."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543210, force_registration=True
        )
        
        default_guild = {
            "text_replacements": {},  # {rule_id: {"pattern": regex_pattern, "replacement": replacement_text}}
            "enabled": True,
            "next_rule_id": 1  # Counter for rule IDs
        }
        
        self.config.register_guild(**default_guild)
        self.pattern_cache: Dict[int, Dict[Pattern, Tuple[str, str]]] = {}  # guild_id -> {pattern: (replacement, rule_id)}
        
    async def initialize(self):
        """Initialize the cog by building pattern cache for all guilds."""
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            if guild_data["enabled"] and guild_data["text_replacements"]:
                self.pattern_cache[guild_id] = {}
                
                # Load the pattern cache
                replacements = guild_data["text_replacements"]
                for rule_id, rule_data in replacements.items():
                    pattern_str = rule_data["pattern"]
                    replacement = rule_data["replacement"]
                    try:
                        pattern = re.compile(pattern_str, re.IGNORECASE)
                        self.pattern_cache[guild_id][pattern] = (replacement, rule_id)
                    except re.error:
                        # Skip invalid patterns
                        continue

    @staticmethod
    def _extract_from_codeblock(text: str) -> str:
        """Extract content from a Discord codeblock if present."""
        # Match triple backtick codeblocks with or without language specification
        codeblock_match = re.match(r"```(?:\w+\n)?([\s\S]+)```", text.strip())
        if codeblock_match:
            return codeblock_match.group(1).strip()
        
        # Match single backtick inline code
        inline_match = re.match(r"`([\s\S]+)`", text.strip())
        if inline_match:
            return inline_match.group(1).strip()
            
        # Return original if no codeblock found
        return text

    @commands.group(name="textreplace")
    @checks.admin_or_permissions(manage_guild=True)
    async def _textreplace(self, ctx: commands.Context):
        """Text Replace settings."""
        pass
        
    async def _pattern_exists(self, guild_id: int, pattern_str: str) -> Optional[str]:
        """Check if a pattern already exists in the guild's replacements.
        
        Returns the rule ID if found, None otherwise.
        """
        replacements = await self.config.guild_from_id(guild_id).text_replacements()
        
        for rule_id, rule_data in replacements.items():
            if rule_data["pattern"] == pattern_str:
                return rule_id
        
        return None

    @_textreplace.command(name="add")
    async def _add_replacement(self, ctx: commands.Context, pattern: str, replacement: str):
        """Add a new text replacement rule using regex.
        
        You can optionally enclose your pattern or replacement in codeblocks (``` ```) 
        to preserve backslashes and special characters.
        
        Examples:
        [p]textreplace add "hello(\\s+)world" "goodbye$1world"
        
        [p]textreplace add ```hello(\s+)world``` ```goodbye$1world```
        
        Use regex groups with parentheses and reference them in the replacement with $1, $2, etc.
        """
        guild = ctx.guild
        
        # Extract pattern and replacement from codeblocks if present
        pattern = self._extract_from_codeblock(pattern)
        replacement = self._extract_from_codeblock(replacement)
        
        if not pattern or not replacement:
            await ctx.send("Both pattern and replacement text are required.")
            return
        
        # Check if pattern already exists
        existing_rule_id = await self._pattern_exists(guild.id, pattern)
        if existing_rule_id:
            await ctx.send(f"A rule with this exact pattern already exists (Rule ID: {existing_rule_id}).")
            return
            
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            await ctx.send(f"Invalid regex pattern: {e}")
            return
        
        # Get the next available rule ID
        next_id = await self.config.guild(guild).next_rule_id()
        rule_id = str(next_id)
        
        # Store the new rule
        async with self.config.guild(guild).text_replacements() as replacements:
            replacements[rule_id] = {
                "pattern": pattern,
                "replacement": replacement
            }
        
        # Update pattern cache
        if guild.id not in self.pattern_cache:
            self.pattern_cache[guild.id] = {}
        self.pattern_cache[guild.id][compiled_pattern] = (replacement, rule_id)
        
        # Increment the rule ID counter
        await self.config.guild(guild).next_rule_id.set(next_id + 1)
        
        await ctx.send(f"Text replacement rule added with ID: {rule_id}")

    @_textreplace.command(name="remove")
    async def _remove_replacement(self, ctx: commands.Context, *rule_ids: str):
        """Remove one or more text replacement rules by ID.
        
        You can specify multiple IDs separated by spaces.
        
        Example:
        [p]textreplace remove 1 2 3
        
        Use [p]textreplace list to see all rule IDs.
        """
        if not rule_ids:
            await ctx.send("Please specify at least one rule ID to remove.")
            return
            
        guild = ctx.guild
        successful_removals = []
        failed_removals = []
        
        async with self.config.guild(guild).text_replacements() as replacements:
            for rule_id in rule_ids:
                if rule_id not in replacements:
                    failed_removals.append(rule_id)
                    continue
                    
                # Remove the rule
                del replacements[rule_id]
                successful_removals.append(rule_id)
        
        # Update pattern cache
        if guild.id in self.pattern_cache and successful_removals:
            # Find the patterns with these rule_ids and remove them
            patterns_to_remove = []
            for pattern, (_, stored_rule_id) in self.pattern_cache[guild.id].items():
                if stored_rule_id in successful_removals:
                    patterns_to_remove.append(pattern)
            
            for pattern in patterns_to_remove:
                del self.pattern_cache[guild.id][pattern]
        
        # Send result message
        if successful_removals and failed_removals:
            await ctx.send(f"Removed rules: {', '.join(successful_removals)}\nNot found: {', '.join(failed_removals)}")
        elif successful_removals:
            await ctx.send(f"Successfully removed {len(successful_removals)} rule{'s' if len(successful_removals) > 1 else ''}: {', '.join(successful_removals)}")
        else:
            await ctx.send("No rules were found with the provided IDs.")

    @_textreplace.command(name="list")
    async def _list_replacements(self, ctx: commands.Context):
        """List all text replacement rules."""
        guild = ctx.guild
        replacements = await self.config.guild(guild).text_replacements()
        
        if not replacements:
            await ctx.send("No text replacement rules configured.")
            return
            
        # Create an embed for better formatting
        embed = discord.Embed(
            title="Text Replacement Rules",
            color=await ctx.embed_color()
        )
        
        for rule_id, rule_data in replacements.items():
            pattern = rule_data["pattern"]
            replacement = rule_data["replacement"]
            embed.add_field(
                name=f"Rule ID: {rule_id}",
                value=f"**Pattern:** `{pattern}`\n**Replacement:** `{replacement}`",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @_textreplace.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: bool = None):
        """Toggle text replacement on or off."""
        guild = ctx.guild
        current = await self.config.guild(guild).enabled()
        
        if on_off is None:
            on_off = not current
            
        await self.config.guild(guild).enabled.set(on_off)
        
        if on_off:
            status = "enabled"
            # Rebuild pattern cache if enabled
            await self.initialize()
        else:
            status = "disabled"
            # Clear pattern cache if disabled
            if guild.id in self.pattern_cache:
                del self.pattern_cache[guild.id]
                
        await ctx.send(f"Text replacement {status}.")

    @_textreplace.command(name="test")
    async def _test_replacement(self, ctx: commands.Context, *, test_text: str):
        """Test how the configured replacements will transform text."""
        guild = ctx.guild
        
        if not await self.config.guild(guild).enabled():
            await ctx.send("Text replacement is currently disabled.")
            return
            
        if guild.id not in self.pattern_cache or not self.pattern_cache[guild.id]:
            await ctx.send("No text replacement rules configured.")
            return
            
        # Apply replacements
        result = test_text
        for pattern, (replacement, _) in self.pattern_cache[guild.id].items():
            result = pattern.sub(replacement, result)
            
        embed = discord.Embed(
            title="Text Replacement Test",
            color=await ctx.embed_color()
        )
        embed.add_field(name="Original", value=test_text, inline=False)
        embed.add_field(name="Result", value=result, inline=False)
        
        await ctx.send(embed=embed)

    @_textreplace.command(name="export")
    async def _export_replacements(self, ctx: commands.Context):
        """Export all text replacement rules as a JSON codeblock for backup or sharing."""
        guild = ctx.guild
        replacements = await self.config.guild(guild).text_replacements()
        
        if not replacements:
            await ctx.send("No text replacement rules to export.")
            return
        
        # Convert to exportable format
        export_data = {}
        for rule_id, rule_data in replacements.items():
            export_data[rule_id] = {
                "pattern": rule_data["pattern"],
                "replacement": rule_data["replacement"]
            }
        
        # Convert to JSON and send as codeblock
        export_json = json.dumps(export_data, indent=2)
        if len(export_json) > 1990:  # Discord message limit safety
            # Split into multiple messages if too long
            chunks = [export_json[i:i+1990] for i in range(0, len(export_json), 1990)]
            for i, chunk in enumerate(chunks):
                await ctx.send(box(chunk, lang="json"))
                if i == 0:
                    await ctx.send("⚠️ Export is too large for a single message, splitting into parts...")
        else:
            await ctx.send(box(export_json, lang="json"))
        
    @_textreplace.command(name="import")
    async def _import_replacements(self, ctx: commands.Context, *, import_data: str):
        """Import text replacement rules from a JSON codeblock.
        
        The JSON must be enclosed in a codeblock (\\`\\`\\` \\`\\`\\`) to prevent Discord from
        altering backslashes and special characters.
        
        The JSON should be formatted as: 
        \\`\\`\\`json
        {
          "1": {"pattern": "regex_pattern", "replacement": "replacement_text"},
          "2": {"pattern": "another_pattern", "replacement": "another_replacement"}
        }
        \\`\\`\\`
        
        You can get this format by using the export command from another server.
        Duplicate patterns will be skipped.
        """
        guild = ctx.guild
        
        # Check if the data is in a codeblock
        extracted_data = self._extract_from_codeblock(import_data)
        if extracted_data == import_data:
            await ctx.send("JSON data must be enclosed in a codeblock (\\`\\`\\` \\`\\`\\`) to ensure backslashes and special characters are preserved. Please try again.")
            return
            
        import_data = extracted_data
        
        try:
            # Parse JSON data
            replacement_data = json.loads(import_data)
            
            if not isinstance(replacement_data, dict):
                await ctx.send("Invalid format. Import data must be a JSON object.")
                return
            
            # Validate structure
            invalid_entries = []
            duplicate_entries = []
            valid_entries = {}
            
            for rule_id, rule_data in replacement_data.items():
                if (not isinstance(rule_data, dict) or
                    "pattern" not in rule_data or
                    "replacement" not in rule_data):
                    invalid_entries.append(rule_id)
                    continue
                
                # Check if pattern already exists
                existing_rule_id = await self._pattern_exists(guild.id, rule_data["pattern"])
                if existing_rule_id:
                    duplicate_entries.append((rule_id, existing_rule_id))
                    continue
                    
                # Try compiling the pattern to make sure it's valid
                try:
                    re.compile(rule_data["pattern"], re.IGNORECASE)
                    valid_entries[rule_id] = rule_data
                except re.error:
                    invalid_entries.append(rule_id)
            
            if not valid_entries and not duplicate_entries:
                await ctx.send("No valid replacement rules found in the import data.")
                return
            
            # Get next available rule ID
            next_id = await self.config.guild(guild).next_rule_id()
            
            # Store the valid rules
            async with self.config.guild(guild).text_replacements() as replacements:
                for rule_data in valid_entries.values():
                    replacements[str(next_id)] = {
                        "pattern": rule_data["pattern"],
                        "replacement": rule_data["replacement"]
                    }
                    next_id += 1
            
            # Update next rule ID
            await self.config.guild(guild).next_rule_id.set(next_id)
            
            # Update pattern cache
            await self.initialize()
            
            # Report results
            result_messages = []
            
            if valid_entries:
                result_messages.append(f"Successfully imported {len(valid_entries)} replacement rule{'s' if len(valid_entries) > 1 else ''}.")
                
            if duplicate_entries:
                dupes = ", ".join([f"{import_id}→{existing_id}" for import_id, existing_id in duplicate_entries])
                result_messages.append(f"Skipped {len(duplicate_entries)} duplicate rule{'s' if len(duplicate_entries) > 1 else ''} (import ID→existing ID): {dupes}")
                
            if invalid_entries:
                result_messages.append(f"Skipped {len(invalid_entries)} invalid rule{'s' if len(invalid_entries) > 1 else ''}: {', '.join(invalid_entries)}")
            
            await ctx.send("\n".join(result_messages))
            
        except json.JSONDecodeError:
            await ctx.send("Invalid JSON format. Please check your input and try again.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages, DMs
        if message.author.bot or not message.guild:
            return
            
        guild_id = message.guild.id
        
        # Check if replacements are enabled for this guild and if there are any patterns
        if (guild_id not in self.pattern_cache or 
            not self.pattern_cache[guild_id] or 
            not await self.config.guild_from_id(guild_id).enabled()):
            return
        
        # Get the prefix for this guild
        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]
            
        # Ignore command messages to prevent recursion
        content = message.content
        for prefix in prefixes:
            if content.startswith(prefix):
                # Check if this is a textreplace command
                cmd_content = content[len(prefix):].strip()
                if cmd_content.startswith("textreplace "):
                    return
        
        original_content = content
        modified = False
        
        # Apply all configured replacements
        for pattern, (replacement, _) in self.pattern_cache[guild_id].items():
            new_content = pattern.sub(replacement, content)
            if new_content != content:
                content = new_content
                modified = True
                
        if modified:
            # Create a webhook to mimic the user
            webhooks = await message.channel.webhooks()
            webhook = discord.utils.get(webhooks, name="TextReplacer")
            
            if webhook is None:
                # Create a webhook if it doesn't exist
                try:
                    webhook = await message.channel.create_webhook(name="TextReplacer")
                except discord.Forbidden:
                    return  # No permission to create webhooks
                    
            # Try to get member's display avatar
            avatar_url = message.author.display_avatar.url
            
            # Send the modified message via webhook
            try:
                await webhook.send(
                    content=content,
                    username=message.author.display_name,
                    avatar_url=avatar_url,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=False
                    )
                )
                # Delete the original message
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass  # Can't delete message
            except discord.HTTPException:
                pass  # Failed to send webhook message

async def setup(bot: Red) -> None:
    """Set up the TextReplace cog."""
    cog = TextReplace(bot)
    await cog.initialize()
    await bot.add_cog(cog) 