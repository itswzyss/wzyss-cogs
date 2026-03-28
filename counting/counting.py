import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord.ui import View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.wzyss-cogs.counting")

LEADERBOARD_PAGE_SIZE = 20
LEADERBOARD_MULTI_TOP = 10
LEADERBOARD_MAX_FIELDS_PER_EMBED = 25

# #region agent log
_AGENT_DEBUG_LOG = Path(__file__).resolve().parent.parent / "debug-bfac2e.log"


def _agent_counting_dbg(payload: dict) -> None:
    try:
        row = {
            "sessionId": "bfac2e",
            "timestamp": int(time.time() * 1000),
            **payload,
        }
        with _AGENT_DEBUG_LOG.open("a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# #endregion


def _int_from_config(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clear_goal_announcement_state(target: dict) -> None:
    target["last_announced_interval_milestone"] = None
    target["last_announced_cap_goal"] = None
    target["last_announced_goal"] = None


def _sync_goal_announcement_to_current(ch: dict) -> None:
    """After admin changes goal or goal_interval, align markers with current count (no stale re-announces)."""
    ch["last_announced_goal"] = None
    current = _int_from_config(ch.get("current"))
    if current is None:
        current = 0
    gi = _int_from_config(ch.get("goal_interval"))
    if gi and gi > 0:
        ch["last_announced_interval_milestone"] = (current // gi) * gi
    else:
        ch["last_announced_interval_milestone"] = None
    g = _int_from_config(ch.get("goal"))
    if g is not None and current >= g:
        ch["last_announced_cap_goal"] = g
    else:
        ch["last_announced_cap_goal"] = None


def _last_interval_from_config(channel_config: dict, goal_interval) -> Optional[int]:
    v = _int_from_config(channel_config.get("last_announced_interval_milestone"))
    if v is not None:
        return v
    legacy = _int_from_config(channel_config.get("last_announced_goal"))
    gi = _int_from_config(goal_interval)
    if legacy is not None and gi and gi > 0 and legacy > 0 and legacy % gi == 0:
        return legacy
    return None


def _last_cap_from_config(channel_config: dict, goal) -> Optional[int]:
    v = _int_from_config(channel_config.get("last_announced_cap_goal"))
    if v is not None:
        return v
    legacy = _int_from_config(channel_config.get("last_announced_goal"))
    g = _int_from_config(goal)
    if legacy is not None and g is not None and legacy == g:
        return g
    return None


class CountingLeaderboardView(View):
    """Previous / next buttons for paginated counting leaderboard embeds."""

    def __init__(self, pages: List[discord.Embed], *, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.page_index = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= len(self.pages) - 1

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.Forbidden, discord.NotFound):
                pass

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="\N{BLACK LEFT-POINTING TRIANGLE}", row=0)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page_index <= 0:
            await interaction.response.defer()
            return
        self.page_index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="\N{BLACK RIGHT-POINTING TRIANGLE}", row=0)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page_index >= len(self.pages) - 1:
            await interaction.response.defer()
            return
        self.page_index += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)


class Counting(commands.Cog):
    """Count upwards in channels with optional math expressions."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654323, force_registration=True
        )
        
        default_guild = {
            "channels": {},  # channel_id -> config incl. segment_contributions, show_milestone_contributors, etc.
            "global_contributor_counts": {},  # str user_id -> lifetime valid counts (all counting channels in guild)
        }
        
        self.config.register_guild(**default_guild)
        
        # Reaction queue system for handling rate limits
        # Format: {channel_id: [(message, emoji, timestamp), ...]}
        self.reaction_queue: Dict[int, List[Tuple[discord.Message, str, float]]] = {}
        self.reaction_task: Optional[asyncio.Task] = None
        self._queue_lock = asyncio.Lock()  # Lock for thread-safe queue operations
        
        # Channel description update task
        self.description_update_task: Optional[asyncio.Task] = None

    def _contributor_rank_lines(
        self, guild: discord.Guild, contributions: Dict[str, int]
    ) -> List[str]:
        """Full ranked display lines; sort by (-count, user_id)."""
        if not contributions:
            return []
        ranked = sorted(
            contributions.items(),
            key=lambda x: (-x[1], int(x[0])),
        )
        lines: List[str] = []
        rank = 0
        for uid_str, count in ranked:
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            rank += 1
            member = guild.get_member(uid)
            who = member.mention if member else f"<@{uid}>"
            lines.append(f"{rank}. {who} — {count} count{'s' if count != 1 else ''}")
        return lines

    def _format_top_contributors(
        self, guild: discord.Guild, contributions: Dict[str, int], limit: int
    ) -> str:
        """Build ranked lines for contributor dict (first `limit` entries)."""
        lines = self._contributor_rank_lines(guild, contributions)
        return "\n".join(lines[:limit])

    def _enabled_counting_text_channels(
        self, guild: discord.Guild, channels_data: Dict
    ) -> List[Tuple[discord.TextChannel, dict]]:
        """Enabled counting channels that still exist as guild text channels."""
        out: List[Tuple[discord.TextChannel, dict]] = []
        for cid_str, cfg in channels_data.items():
            if not cfg.get("enabled", False):
                continue
            try:
                cid = int(cid_str)
            except (TypeError, ValueError):
                continue
            ch = guild.get_channel(cid)
            if ch and isinstance(ch, discord.TextChannel):
                out.append((ch, cfg))
        out.sort(key=lambda t: (t[0].position, t[0].id))
        return out

    def _leaderboard_channel_field_value(self, guild: discord.Guild, cfg: dict, top: int) -> str:
        """Record line + top contributors; capped for embed field limit (1024)."""
        record = cfg.get("highest_record", 0)
        counts = cfg.get("channel_contributor_counts") or {}
        lines = self._contributor_rank_lines(guild, counts)[:top]
        body = "\n".join(lines) if lines else "No counts yet."
        text = f"**Record:** {record}\n{body}"
        if len(text) > 1024:
            text = text[:1021] + "…"
        return text

    def _safe_eval_math(self, expression: str) -> Optional[float]:
        """Safely evaluate a math expression containing only numbers and basic operators.
        
        Returns the result as a float, or None if the expression is invalid.
        """
        # Remove all whitespace
        expression = expression.replace(" ", "").replace("\n", "").replace("\t", "")
        
        if not expression:
            return None
        
        # Only allow numbers, operators, parentheses, and decimal points
        allowed_chars = set("0123456789+-*/.()")
        if not all(c in allowed_chars for c in expression):
            return None
        
        # Check for balanced parentheses
        if expression.count("(") != expression.count(")"):
            return None
        
        # Prevent dangerous patterns
        dangerous_patterns = [
            "__",  # No double underscores (could be used for builtins)
            "import",
            "exec",
            "eval",
            "open",
            "file",
        ]
        expression_lower = expression.lower()
        for pattern in dangerous_patterns:
            if pattern in expression_lower:
                return None
        
        try:
            # Use eval with a restricted namespace containing only safe math functions
            safe_dict = {
                "__builtins__": {},
                "abs": abs,
                "round": round,
                "min": min,
                "max": max,
                "pow": pow,
            }
            result = eval(expression, safe_dict, {})
            
            # Ensure result is a number
            if not isinstance(result, (int, float)):
                return None
            
            return float(result)
        except (SyntaxError, NameError, TypeError, ZeroDivisionError, ValueError):
            return None
    
    def _parse_count(self, content: str) -> Optional[float]:
        """Parse a message content to extract a number or evaluate a math expression.
        
        Returns the numeric value, or None if invalid.
        """
        # Remove leading/trailing whitespace
        content = content.strip()
        
        # Try to parse as a direct number first
        try:
            return float(content)
        except ValueError:
            pass
        
        # Try to evaluate as a math expression
        return self._safe_eval_math(content)
    
    async def _add_reaction_with_retry(self, message: discord.Message, emoji: str, max_retries: int = 3) -> bool:
        """Add reaction with retry logic for rate limits.
        
        Returns True if reaction was added successfully, False otherwise.
        """
        for attempt in range(max_retries):
            try:
                await message.add_reaction(emoji)
                return True
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    log.debug(f"Rate limited adding reaction, waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                    await asyncio.sleep(wait_time)
                else:
                    log.warning(f"HTTP error adding reaction: {e.status} - {e}")
                    return False
            except (discord.Forbidden, discord.NotFound) as e:
                log.debug(f"Cannot add reaction: {type(e).__name__}")
                return False
            except Exception as e:
                log.error(f"Unexpected error adding reaction: {e}", exc_info=True)
                return False
        log.warning(f"Failed to add reaction after {max_retries} retries")
        return False
    
    async def _process_reaction_queue(self):
        """Process queued reactions respecting rate limits.
        
        This background task processes reactions one at a time per channel,
        handling rate limits gracefully with retry logic.
        """
        while True:
            try:
                await asyncio.sleep(1)  # Check queue every second
                
                async with self._queue_lock:
                    # Process one reaction per channel to respect rate limits
                    channels_to_process = list(self.reaction_queue.keys())
                    
                    for channel_id in channels_to_process:
                        queue = self.reaction_queue.get(channel_id, [])
                        if not queue:
                            # Remove empty queues
                            self.reaction_queue.pop(channel_id, None)
                            continue
                        
                        # Process the oldest reaction in this channel
                        message, emoji, timestamp = queue.pop(0)
                        
                        # Limit queue size to prevent memory issues
                        if len(queue) > 100:
                            log.warning(f"Reaction queue for channel {channel_id} exceeded 100 items, dropping oldest")
                            # Drop oldest items if queue is too large
                            self.reaction_queue[channel_id] = queue[-100:]
                        
                        # Try to add the reaction
                        success = await self._add_reaction_with_retry(message, emoji)
                        
                        if not success:
                            # If failed, put it back at the end of the queue for retry
                            # But only if queue isn't too large
                            if len(queue) < 100:
                                queue.append((message, emoji, timestamp))
                                self.reaction_queue[channel_id] = queue
                            else:
                                log.warning(f"Dropping failed reaction for channel {channel_id} due to queue size")
                        
                        # Only process one reaction per channel per cycle to respect rate limits
                        break
                        
            except asyncio.CancelledError:
                log.info("Reaction queue processor cancelled")
                break
            except Exception as e:
                log.error(f"Error in reaction queue processor: {e}", exc_info=True)
                await asyncio.sleep(5)  # Wait a bit before retrying on unexpected errors
    
    async def _update_channel_description(self, channel: discord.TextChannel, record: int):
        """Update a single channel's description with the record."""
        try:
            description = f"Record: {record}"
            await channel.edit(topic=description)
            log.debug(f"Updated channel description for {channel.id} to 'Record: {record}'")
        except discord.Forbidden:
            log.warning(f"Missing permission to edit channel {channel.id} (guild: {channel.guild.id})")
        except discord.HTTPException as e:
            log.warning(f"HTTP error updating channel description for {channel.id}: {e}")
        except Exception as e:
            log.error(f"Unexpected error updating channel description for {channel.id}: {e}", exc_info=True)
    
    async def _update_channel_descriptions(self):
        """Update channel descriptions with records every minute."""
        while True:
            try:
                await asyncio.sleep(60)  # 1 minute
                
                # Iterate through all guilds
                all_guilds = await self.config.all_guilds()
                for guild_id, guild_data in all_guilds.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                    
                    channels = guild_data.get("channels", {})
                    for channel_id_str, channel_config in channels.items():
                        try:
                            channel_id = int(channel_id_str)
                            channel = guild.get_channel(channel_id)
                            if not channel or not isinstance(channel, discord.TextChannel):
                                continue
                            
                            # Get the latest config (record may have been updated)
                            # Re-fetch to ensure we have the latest record
                            latest_channels = await self.config.guild(guild).channels()
                            latest_config = latest_channels.get(channel_id_str, {})
                            highest_record = latest_config.get("highest_record", 0)
                            
                            # Update description if we have a record
                            if highest_record > 0:
                                await self._update_channel_description(channel, highest_record)
                                
                        except (ValueError, KeyError) as e:
                            log.error(f"Error processing channel config: {e}")
                            continue
                        except Exception as e:
                            log.error(f"Unexpected error in description update loop: {e}", exc_info=True)
                            continue
                        
            except asyncio.CancelledError:
                log.info("Channel description update task cancelled")
                break
            except Exception as e:
                log.error(f"Error in channel description update task: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait a minute before retrying on unexpected errors
    
    @commands.group(name="countingset", aliases=["countset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _countingset(self, ctx: commands.Context):
        """Manage counting channel settings."""
        pass
    
    @_countingset.command(name="channel")
    async def _set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Enable counting in a channel.
        
        Usage: [p]countingset channel <channel>
        
        Example: [p]countingset channel #counting
        """
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            channels[str(channel_id)] = {
                "current": 0,  # Will start at 1 on first message
                "goal": None,  # None means infinite
                "enabled": True,
                "last_user": None,
                "max_consecutive": 1,  # Default: 1 = same user disabled, >1 = allows that many consecutive
                "ruin_enabled": False,  # Default: don't reset on wrong number
                "ruin_message": "💥 **The count was ruined!** The count has been reset to 0. Next count will be 1.",
                "consecutive_count": 0,  # Track current consecutive count
                "consecutive_user": None,  # Track which user is on a streak
                "goal_interval": None,  # None = no consecutive goals, int = goal every N (e.g., 100 = goals at 100, 200, 300...)
                "last_announced_interval_milestone": None,  # last interval milestone announced (goal_interval)
                "last_announced_cap_goal": None,  # singular goal value already announced
                "last_announced_goal": None,  # legacy; cleared on write
                "reactions_enabled": True,  # Default: enable reactions on valid counts
                "highest_record": 0,  # Track highest number reached
                "segment_contributions": {},  # user_id str -> valid counts this milestone segment
                "show_milestone_contributors": True,  # append top contributors to goal messages
                "channel_contributor_counts": {},  # user_id str -> lifetime valid counts in this channel
            }
        else:
            channels[str(channel_id)]["enabled"] = True
        
        await self.config.guild(guild).channels.set(channels)
        await ctx.send(f"Counting enabled in {channel.mention}. The count will start at 1.")
    
    @_countingset.command(name="disable")
    async def _disable_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Disable counting in a channel."""
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for counting.")
            return
        
        channels[str(channel_id)]["enabled"] = False
        await self.config.guild(guild).channels.set(channels)
        await ctx.send(f"Counting disabled in {channel.mention}.")
    
    @_countingset.command(name="goal")
    async def _set_goal(self, ctx: commands.Context, channel: discord.TextChannel, goal: Optional[int] = None):
        """Set a counting goal for a channel. Use 0 or omit to set infinite.
        
        Usage: [p]countingset goal <channel> [goal]
        
        Examples:
        [p]countingset goal #counting 100
        [p]countingset goal #counting 0  (sets infinite)
        """
        if goal is not None and goal < 0:
            await ctx.send("Goal cannot be negative.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        # 0 means infinite (None)
        goal_value = None if goal == 0 else goal
        ch = channels[str(channel_id)]
        ch["goal"] = goal_value
        ch["segment_contributions"] = {}
        _sync_goal_announcement_to_current(ch)
        await self.config.guild(guild).channels.set(channels)
        
        if goal_value is None:
            await ctx.send(f"Counting goal for {channel.mention} set to infinite.")
        else:
            await ctx.send(f"Counting goal for {channel.mention} set to {goal_value}.")
    
    @_countingset.command(name="goalinterval")
    async def _set_goal_interval(self, ctx: commands.Context, channel: discord.TextChannel, interval: Optional[int] = None):
        """Set consecutive goals for a channel (e.g., every 100 = goals at 100, 200, 300...). Use 0 or omit to disable.
        
        Usage: [p]countingset goalinterval <channel> [interval]
        
        Examples:
        [p]countingset goalinterval #counting 100  (goals at 100, 200, 300...)
        [p]countingset goalinterval #counting 0  (disables consecutive goals)
        """
        if interval is not None and interval < 0:
            await ctx.send("Goal interval cannot be negative.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        # 0 means disabled (None)
        interval_value = None if interval == 0 else interval
        ch = channels[str(channel_id)]
        ch["goal_interval"] = interval_value
        ch["segment_contributions"] = {}
        _sync_goal_announcement_to_current(ch)
        await self.config.guild(guild).channels.set(channels)
        
        if interval_value is None:
            await ctx.send(f"Consecutive goals disabled for {channel.mention}.")
        else:
            await ctx.send(f"Consecutive goals for {channel.mention} set to every {interval_value} (goals at {interval_value}, {interval_value * 2}, {interval_value * 3}...).")
    
    @_countingset.command(name="reset")
    async def _reset_count(self, ctx: commands.Context, channel: discord.TextChannel):
        """Reset the count in a channel back to 0 (next count will be 1)."""
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for counting.")
            return
        
        channels[str(channel_id)]["current"] = 0
        channels[str(channel_id)]["last_user"] = None
        channels[str(channel_id)]["consecutive_count"] = 0
        channels[str(channel_id)]["consecutive_user"] = None
        _clear_goal_announcement_state(channels[str(channel_id)])
        channels[str(channel_id)]["segment_contributions"] = {}
        await self.config.guild(guild).channels.set(channels)
        await ctx.send(f"Count reset in {channel.mention}. Next count will be 1.")
    
    @_countingset.command(name="setnext", aliases=["setcount", "nextnumber"])
    async def _set_next_number(self, ctx: commands.Context, channel: discord.TextChannel, next_number: int):
        """Manually set the next number that should be counted.
        
        Usage: [p]countingset setnext <channel> <number>
        
        Example: [p]countingset setnext #counting 50
        (Sets the current count to 49, so the next count will be 50)
        """
        if next_number < 1:
            await ctx.send("Next number must be at least 1.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for counting.")
            return
        
        # Set current to next_number - 1 so the next count will be next_number
        channels[str(channel_id)]["current"] = next_number - 1
        # Reset tracking to allow anyone to count next
        channels[str(channel_id)]["last_user"] = None
        channels[str(channel_id)]["consecutive_count"] = 0
        channels[str(channel_id)]["consecutive_user"] = None
        _clear_goal_announcement_state(channels[str(channel_id)])
        channels[str(channel_id)]["segment_contributions"] = {}
        await self.config.guild(guild).channels.set(channels)
        await ctx.send(f"Next number for {channel.mention} set to {next_number}. Current count is {next_number - 1}.")
    
    @_countingset.command(name="status")
    async def _status(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Show counting status for a channel. If no channel specified, shows all channels."""
        guild = ctx.guild
        
        if channel:
            # Show status for specific channel
            channel_id = channel.id
            channels = await self.config.guild(guild).channels()
            channel_config = channels.get(str(channel_id))
            
            if not channel_config:
                await ctx.send(f"{channel.mention} is not configured for counting.")
                return
            
            current = channel_config.get("current", 0)
            goal = channel_config.get("goal")
            goal_interval = channel_config.get("goal_interval")
            enabled = channel_config.get("enabled", False)
            max_consecutive = channel_config.get("max_consecutive", 1)
            ruin_enabled = channel_config.get("ruin_enabled", False)
            reactions_enabled = channel_config.get("reactions_enabled", True)
            next_count = current + 1
            
            status_msg = f"**Counting Status for {channel.mention}:**\n"
            status_msg += f"Current count: {current}\n"
            status_msg += f"Next count: {next_count}\n"
            status_msg += f"Goal: {goal if goal else 'Infinite'}\n"
            if goal_interval:
                status_msg += f"Consecutive goals: Every {goal_interval} (next at {((current // goal_interval) + 1) * goal_interval})\n"
            status_msg += f"Status: {'Enabled' if enabled else 'Disabled'}\n"
            if max_consecutive == 1:
                status_msg += f"Same user counting: Disabled\n"
            else:
                status_msg += f"Same user counting: Enabled (max {max_consecutive} consecutive)\n"
            status_msg += f"Ruin mode: {'Enabled' if ruin_enabled else 'Disabled'}\n"
            status_msg += f"Reactions: {'Enabled' if reactions_enabled else 'Disabled'}\n"
            show_mc = channel_config.get("show_milestone_contributors", True)
            status_msg += f"Milestone contributor list: {'On' if show_mc else 'Off'}"
            
            if goal and current >= goal:
                status_msg += f"\n✅ Goal reached!"
            
            await ctx.send(status_msg)
        else:
            # Show status for all channels
            channels = await self.config.guild(guild).channels()
            
            if not channels:
                await ctx.send("No channels are configured for counting.")
                return
            
            status_msg = "**Counting Status:**\n\n"
            for channel_id_str, channel_config in channels.items():
                try:
                    channel_id = int(channel_id_str)
                    channel_obj = guild.get_channel(channel_id)
                    if not channel_obj:
                        status_msg += f"❌ Channel ID `{channel_id}` (channel not found)\n"
                        continue
                    
                    current = channel_config.get("current", 0)
                    goal = channel_config.get("goal")
                    enabled = channel_config.get("enabled", False)
                    next_count = current + 1
                    
                    status_msg += f"{channel_obj.mention}:\n"
                    status_msg += f"  Current: {current} | Next: {next_count} | Goal: {goal if goal else 'Infinite'} | {'✅' if enabled else '❌'}\n"
                except (ValueError, KeyError) as e:
                    log.error(f"Error processing channel config: {e}")
                    continue
            
            await ctx.send(status_msg)
    
    @_countingset.command(name="consecutive")
    async def _set_consecutive(self, ctx: commands.Context, channel: discord.TextChannel, max_count: int):
        """Set the maximum number of consecutive counts the same user can make.
        
        Usage: [p]countingset consecutive <channel> <max_count>
        
        Examples:
        [p]countingset consecutive #counting 1  (disables same user counting)
        [p]countingset consecutive #counting 3  (allows a user to count up to 3 times in a row)
        """
        if max_count < 1:
            await ctx.send("Maximum consecutive count must be at least 1.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        channels[str(channel_id)]["max_consecutive"] = max_count
        await self.config.guild(guild).channels.set(channels)
        
        await ctx.send(f"Maximum consecutive counts for {channel.mention} set to {max_count}.")
    
    @_countingset.command(name="ruin")
    async def _set_ruin(self, ctx: commands.Context, channel: discord.TextChannel, enable: bool):
        """Enable or disable resetting the count when someone sends the wrong number.
        
        Usage: [p]countingset ruin <channel> <true/false>
        
        Examples:
        [p]countingset ruin #counting true
        [p]countingset ruin #counting false
        """
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        channels[str(channel_id)]["ruin_enabled"] = enable
        await self.config.guild(guild).channels.set(channels)
        
        status = "enabled" if enable else "disabled"
        await ctx.send(f"Ruin mode {status} for {channel.mention}.")
    
    @_countingset.command(name="ruinmessage")
    async def _set_ruin_message(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """Set the custom message sent when the count is ruined.
        
        Usage: [p]countingset ruinmessage <channel> <message>
        
        Example: [p]countingset ruinmessage #counting 💥 Oops! {user} ruined it! Back to 0!
        
        Available placeholders:
        - {user} - The user who ruined the count
        - {count} - The count that was reached before ruin
        """
        if len(message) > 2000:
            await ctx.send("Ruin message cannot exceed 2000 characters.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        channels[str(channel_id)]["ruin_message"] = message
        await self.config.guild(guild).channels.set(channels)
        
        await ctx.send(f"Ruin message for {channel.mention} updated.")
    
    @_countingset.command(name="reactions")
    async def _set_reactions(self, ctx: commands.Context, channel: discord.TextChannel, enable: bool):
        """Enable or disable reactions on valid counts.
        
        Usage: [p]countingset reactions <channel> <true/false>
        
        Examples:
        [p]countingset reactions #counting true
        [p]countingset reactions #counting false
        """
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return
        
        channels[str(channel_id)]["reactions_enabled"] = enable
        await self.config.guild(guild).channels.set(channels)
        
        status = "enabled" if enable else "disabled"
        await ctx.send(f"Reactions {status} for {channel.mention}.")

    @_countingset.command(name="milestonecontributors")
    async def _set_milestone_contributors(
        self, ctx: commands.Context, channel: discord.TextChannel, enable: bool
    ):
        """Toggle top contributor lines on milestone goal messages for a channel.

        When enabled, the bot lists the top 5 participants for the milestone segment
        (between goal announcements or count resets) on each goal message.

        Usage: [p]countingset milestonecontributors <channel> <true/false>
        """
        guild = ctx.guild
        channel_id = channel.id

        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(
                f"{channel.mention} is not configured for counting. Use `{ctx.prefix}countingset channel` first."
            )
            return

        channels[str(channel_id)]["show_milestone_contributors"] = enable
        await self.config.guild(guild).channels.set(channels)

        status = "enabled" if enable else "disabled"
        await ctx.send(f"Milestone contributor list {status} for {channel.mention}.")
    
    @_countingset.command(name="setrecord")
    async def _set_record(self, ctx: commands.Context, channel: discord.TextChannel, record: int):
        """Manually set the highest record for a channel.
        
        Usage: [p]countingset setrecord <channel> <number>
        
        Example: [p]countingset setrecord #counting 1000
        """
        if record < 0:
            await ctx.send("Record cannot be negative.")
            return
        
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for counting.")
            return
        
        channels[str(channel_id)]["highest_record"] = record
        await self.config.guild(guild).channels.set(channels)
        
        # Update channel description immediately
        await self._update_channel_description(channel, record)
        
        await ctx.send(f"Record for {channel.mention} set to {record}.")
    
    @_countingset.command(name="removerecord", aliases=["resetrecord"])
    async def _remove_record(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove/reset the highest record for a channel (sets it to 0).
        
        Usage: [p]countingset removerecord <channel>
        
        Example: [p]countingset removerecord #counting
        """
        guild = ctx.guild
        channel_id = channel.id
        
        channels = await self.config.guild(guild).channels()
        if str(channel_id) not in channels:
            await ctx.send(f"{channel.mention} is not configured for counting.")
            return
        
        channels[str(channel_id)]["highest_record"] = 0
        await self.config.guild(guild).channels.set(channels)
        
        # Clear channel description (set to empty or None)
        try:
            await channel.edit(topic=None)
        except (discord.Forbidden, discord.HTTPException):
            pass  # Ignore if we can't edit
        
        await ctx.send(f"Record for {channel.mention} has been removed.")

    @commands.command(name="countingleaderboard", aliases=["clb"])
    @commands.guild_only()
    async def counting_leaderboard(self, ctx: commands.Context):
        """Show counting contributors per enabled channel (one channel: ranks + record; several: one field each)."""
        guild = ctx.guild
        channels_data = await self.config.guild(guild).channels()
        enabled = self._enabled_counting_text_channels(guild, channels_data)
        if not enabled:
            await ctx.send("No enabled counting channels in this server.")
            return

        color = await ctx.embed_color()
        mention_policy = discord.AllowedMentions(users=True)

        if len(enabled) == 1:
            text_ch, cfg = enabled[0]
            record = cfg.get("highest_record", 0)
            counts = cfg.get("channel_contributor_counts") or {}
            lines = self._contributor_rank_lines(guild, counts)
            if not lines:
                emb = discord.Embed(
                    title="Counting leaderboard",
                    description=(
                        f"{text_ch.mention}\n\n"
                        "No contributor data yet. Count in this channel to appear on the board."
                    ),
                    color=color,
                )
                emb.set_footer(text=f"Record: {record}")
                await ctx.send(embed=emb, allowed_mentions=mention_policy)
                return

            total = len(lines)
            num_pages = (total + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE
            pages: List[discord.Embed] = []
            for p in range(num_pages):
                start = p * LEADERBOARD_PAGE_SIZE
                chunk = lines[start : start + LEADERBOARD_PAGE_SIZE]
                header = f"{text_ch.mention}\n**Record:** {record}\n\n"
                emb = discord.Embed(
                    title="Counting leaderboard",
                    description=header + "\n".join(chunk),
                    color=color,
                )
                emb.set_footer(
                    text=f"Page {p + 1}/{num_pages} · {total} contributors · #{text_ch.name}"
                )
                pages.append(emb)

            if num_pages == 1:
                await ctx.send(embed=pages[0], allowed_mentions=mention_policy)
                return

            view = CountingLeaderboardView(pages)
            message = await ctx.send(
                embed=pages[0],
                view=view,
                allowed_mentions=mention_policy,
            )
            view.message = message
            return

        batches: List[List[Tuple[discord.TextChannel, dict]]] = []
        batch: List[Tuple[discord.TextChannel, dict]] = []
        for pair in enabled:
            batch.append(pair)
            if len(batch) >= LEADERBOARD_MAX_FIELDS_PER_EMBED:
                batches.append(batch)
                batch = []
        if batch:
            batches.append(batch)

        pages_multi: List[discord.Embed] = []
        total_ch = len(enabled)
        for bi, bch in enumerate(batches):
            emb = discord.Embed(
                title="Counting leaderboards",
                description=f"{total_ch} counting channels · top {LEADERBOARD_MULTI_TOP} per channel",
                color=color,
            )
            for text_ch, cfg in bch:
                name = text_ch.mention[:256]
                value = self._leaderboard_channel_field_value(
                    guild, cfg, LEADERBOARD_MULTI_TOP
                )
                emb.add_field(name=name, value=value, inline=False)
            emb.set_footer(
                text=f"Page {bi + 1}/{len(batches)} \u00b7 {total_ch} channels"
            )
            pages_multi.append(emb)

        if len(pages_multi) == 1:
            await ctx.send(embed=pages_multi[0], allowed_mentions=mention_policy)
            return

        view = CountingLeaderboardView(pages_multi)
        message = await ctx.send(
            embed=pages_multi[0],
            view=view,
            allowed_mentions=mention_policy,
        )
        view.message = message

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle counting messages."""
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        
        guild = message.guild
        channel = message.channel
        channel_id = channel.id
        
        # Check if this channel is configured for counting
        channels = await self.config.guild(guild).channels()
        channel_config = channels.get(str(channel_id))
        
        if not channel_config or not channel_config.get("enabled", False):
            return
        
        # Get current state
        current = channel_config.get("current", 0)
        goal = channel_config.get("goal")
        goal_interval = channel_config.get("goal_interval")
        last_user = channel_config.get("last_user")
        max_consecutive = channel_config.get("max_consecutive", 1)
        ruin_enabled = channel_config.get("ruin_enabled", False)
        ruin_message = channel_config.get("ruin_message", "💥 **The count was ruined!** The count has been reset to 0. Next count will be 1.")
        reactions_enabled = channel_config.get("reactions_enabled", True)
        highest_record = channel_config.get("highest_record", 0)
        consecutive_count = channel_config.get("consecutive_count", 0)
        consecutive_user = channel_config.get("consecutive_user")
        
        # Parse the message content
        content = message.content.strip()
        parsed_value = self._parse_count(content)
        
        if parsed_value is None:
            # Not a valid number or expression
            # Always update record if current count exceeds it (before resetting)
            if current > highest_record:
                channels[str(channel_id)]["highest_record"] = current
                await self.config.guild(guild).channels.set(channels)
                # Update channel description immediately
                await self._update_channel_description(channel, current)
            
            if ruin_enabled:
                # Reset count and send ruin message
                channels[str(channel_id)]["current"] = 0
                channels[str(channel_id)]["last_user"] = None
                channels[str(channel_id)]["consecutive_count"] = 0
                channels[str(channel_id)]["consecutive_user"] = None
                _clear_goal_announcement_state(channels[str(channel_id)])
                channels[str(channel_id)]["segment_contributions"] = {}
                await self.config.guild(guild).channels.set(channels)
                
                # Format ruin message - tag the user who ruined it
                formatted_message = ruin_message.replace("{user}", message.author.mention).replace("{count}", str(current))
                try:
                    await channel.send(formatted_message, allowed_mentions=discord.AllowedMentions(users=True))
                except discord.Forbidden:
                    pass
            
            # Delete the invalid message
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            return
        
        # Check if it's the correct next number
        expected = current + 1
        
        # Allow small floating point differences (for division results, etc.)
        if abs(parsed_value - expected) > 0.0001:
            # Wrong number
            # Always update record if current count exceeds it (before resetting)
            if current > highest_record:
                channels[str(channel_id)]["highest_record"] = current
                await self.config.guild(guild).channels.set(channels)
                # Update channel description immediately
                await self._update_channel_description(channel, current)
            
            if ruin_enabled:
                # Reset count and send ruin message
                channels[str(channel_id)]["current"] = 0
                channels[str(channel_id)]["last_user"] = None
                channels[str(channel_id)]["consecutive_count"] = 0
                channels[str(channel_id)]["consecutive_user"] = None
                _clear_goal_announcement_state(channels[str(channel_id)])
                channels[str(channel_id)]["segment_contributions"] = {}
                await self.config.guild(guild).channels.set(channels)
                
                # Format ruin message - tag the user who ruined it
                formatted_message = ruin_message.replace("{user}", message.author.mention).replace("{count}", str(current))
                try:
                    await channel.send(formatted_message, allowed_mentions=discord.AllowedMentions(users=True))
                except discord.Forbidden:
                    pass
            
            # Delete the wrong message
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            return
        
        # Check same user / consecutive count logic
        # If max_consecutive is 1, same user counting is disabled
        # If max_consecutive > 1, same user counting is enabled with that limit
        if max_consecutive == 1:
            # Same user counting disabled - check if same user
            if last_user == message.author.id:
                # Same user tried to count again - ruin the count if ruin mode is enabled
                if ruin_enabled:
                    # Always update record if current count exceeds it (before resetting)
                    if current > highest_record:
                        channels[str(channel_id)]["highest_record"] = current
                        await self.config.guild(guild).channels.set(channels)
                        # Update channel description immediately
                        await self._update_channel_description(channel, current)
                    
                    # Reset count and send ruin message
                    channels[str(channel_id)]["current"] = 0
                    channels[str(channel_id)]["last_user"] = None
                    channels[str(channel_id)]["consecutive_count"] = 0
                    channels[str(channel_id)]["consecutive_user"] = None
                    _clear_goal_announcement_state(channels[str(channel_id)])
                    channels[str(channel_id)]["segment_contributions"] = {}
                    await self.config.guild(guild).channels.set(channels)
                    
                    # Format ruin message - tag the user who ruined it
                    formatted_message = ruin_message.replace("{user}", message.author.mention).replace("{count}", str(current))
                    try:
                        await channel.send(formatted_message, allowed_mentions=discord.AllowedMentions(users=True))
                    except discord.Forbidden:
                        pass
                
                # Delete the message
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
                return
        else:
            # Same user counting enabled - check consecutive limit
            if consecutive_user == message.author.id:
                # Same user continuing streak
                if consecutive_count >= max_consecutive:
                    # Exceeded max consecutive, delete the message
                    try:
                        await message.delete()
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    return
                # Valid consecutive count - will increment below
            else:
                # Different user or first count - reset consecutive tracking
                consecutive_count = 0
        
        # Valid count! Update the state
        new_current = int(parsed_value)
        channels[str(channel_id)]["current"] = new_current
        channels[str(channel_id)]["last_user"] = message.author.id

        uid_str = str(message.author.id)
        seg = dict(channel_config.get("segment_contributions") or {})
        seg[uid_str] = seg.get(uid_str, 0) + 1
        channels[str(channel_id)]["segment_contributions"] = seg
        ch_cc = dict(channel_config.get("channel_contributor_counts") or {})
        ch_cc[uid_str] = ch_cc.get(uid_str, 0) + 1
        channels[str(channel_id)]["channel_contributor_counts"] = ch_cc
        
        # Update highest record if we exceeded it
        if new_current > highest_record:
            channels[str(channel_id)]["highest_record"] = new_current
        
        # Update consecutive tracking
        if consecutive_user == message.author.id:
            consecutive_count += 1
        else:
            consecutive_count = 1
            consecutive_user = message.author.id
        
        channels[str(channel_id)]["consecutive_count"] = consecutive_count
        channels[str(channel_id)]["consecutive_user"] = consecutive_user
        await self.config.guild(guild).channels.set(channels)

        global_counts = await self.config.guild(guild).global_contributor_counts()
        gc = dict(global_counts)
        gc[uid_str] = gc.get(uid_str, 0) + 1
        await self.config.guild(guild).global_contributor_counts.set(gc)
        
        # Queue reaction for background processing (handles rate limits) if reactions are enabled
        if reactions_enabled:
            async with self._queue_lock:
                if channel_id not in self.reaction_queue:
                    self.reaction_queue[channel_id] = []
                self.reaction_queue[channel_id].append((message, "✅", time.time()))
        
        # Check if goal was reached (interval milestones vs cap goal use separate state)
        goal_reached = None
        goal_message = None
        interval_fired = False

        last_interval = _last_interval_from_config(channel_config, goal_interval)
        last_cap = _last_cap_from_config(channel_config, goal)

        if goal_interval and goal_interval > 0:
            milestone = (new_current // goal_interval) * goal_interval
            if milestone > 0 and milestone != last_interval:
                goal_reached = milestone
                goal_message = f"🎉 **Goal reached!** The count reached {milestone}!"
                interval_fired = True

        if goal and new_current >= goal and not interval_fired:
            g = _int_from_config(goal)
            if g is not None and last_cap != g:
                goal_reached = g
                goal_message = f"🎉 **Goal reached!** The count reached {g}!"

        # #region agent log
        _milestone_dbg = (
            (new_current // goal_interval) * goal_interval
            if goal_interval and goal_interval > 0
            else None
        )
        _agent_counting_dbg(
            {
                "hypothesisId": "H1-verify",
                "location": "counting.py:on_message:goal_check",
                "message": "goal announcement evaluation",
                "runId": "post-fix",
                "data": {
                    "guild_id": guild.id,
                    "channel_id": channel_id,
                    "new_current": new_current,
                    "goal": goal,
                    "goal_interval": goal_interval,
                    "last_interval": last_interval,
                    "last_cap": last_cap,
                    "interval_fired": interval_fired,
                    "milestone_computed": _milestone_dbg,
                    "will_send": goal_reached is not None and bool(goal_message),
                    "announced_value": goal_reached,
                    "goal_message_preview": (goal_message[:80] + "…")
                    if goal_message and len(goal_message) > 80
                    else goal_message,
                },
            }
        )
        # #endregion

        # Send goal message only if we reached a new goal
        if goal_reached is not None and goal_message:
            show_mc = channel_config.get("show_milestone_contributors", True)
            contributor_block = ""
            if show_mc and seg:
                block = self._format_top_contributors(guild, seg, 5)
                if block:
                    contributor_block = "\n\n**Top contributors this milestone:**\n" + block
            ch_update = channels[str(channel_id)]
            gi = _int_from_config(goal_interval)
            if gi and gi > 0:
                ms = (new_current // gi) * gi
                if ms > 0 and goal_reached == ms:
                    ch_update["last_announced_interval_milestone"] = ms
            gcap = _int_from_config(goal)
            if gcap is not None and goal_reached == gcap:
                ch_update["last_announced_cap_goal"] = gcap
            ch_update["last_announced_goal"] = None
            ch_update["segment_contributions"] = {}
            await self.config.guild(guild).channels.set(channels)
            try:
                color = await self.bot.get_embed_color(guild)
                desc = f"The count reached **{goal_reached}**!{contributor_block}"
                embed = discord.Embed(
                    title="🎉 Goal reached!",
                    description=desc,
                    color=color,
                )
                await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden:
                pass
    
    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        if self.reaction_task and not self.reaction_task.done():
            self.reaction_task.cancel()
        if self.description_update_task and not self.description_update_task.done():
            self.description_update_task.cancel()
        log.info("Counting cog unloaded, background tasks stopped")


async def setup(bot: Red):
    """Load the Counting cog."""
    cog = Counting(bot)
    await bot.add_cog(cog)
    # Start the background reaction queue processor
    cog.reaction_task = asyncio.create_task(cog._process_reaction_queue())
    # Start the background channel description update task
    cog.description_update_task = asyncio.create_task(cog._update_channel_descriptions())
    log.info("Counting cog loaded, background tasks started")