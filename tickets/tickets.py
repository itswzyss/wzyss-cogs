"""
Tickets cog: button-based ticket creation, dedicated channels, manager controls,
auto-assign, inactivity auto-close, transcript logging. No user-facing commands.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import parse_timedelta
from redbot.core.utils.chat_formatting import box, humanize_timedelta

log = logging.getLogger("red.wzyss-cogs.tickets")

TICKETS_PREFIX = "tickets:"
CREATE_CUSTOM_ID = "tickets:create"
CLAIM_PREFIX = "tickets:claim:"
CLOSE_PREFIX = "tickets:close:"

# Discord channel name: alphanumeric, hyphen, underscore; 1-100 chars
def _sanitize_channel_name(name: str) -> str:
    s = re.sub(r"[^\w\-]", "", name.replace(" ", "-"))[:100]
    return s or "ticket"


# --- Embed builder modal (panel + welcome) ---


class TicketEmbedConfigModal(Modal):
    """Modal for configuring embed (panel or welcome)."""

    def __init__(
        self,
        cog: "Tickets",
        embed_type: str,
        existing_data: Optional[Dict] = None,
        title_required: bool = True,
    ):
        title = "Edit Embed" if existing_data else "Configure Embed"
        super().__init__(title=title)
        self.cog = cog
        self.embed_type = embed_type
        self.existing_data = existing_data or {}
        self.title_required = title_required

        self.title_input = TextInput(
            label="Title",
            placeholder="Support Tickets" if embed_type == "panel" else "How can we help?",
            default=self.existing_data.get("title", ""),
            required=title_required,
            max_length=256,
        )
        self.add_item(self.title_input)

        self.description_input = TextInput(
            label="Description",
            placeholder="Click the button below to open a ticket...",
            default=self.existing_data.get("description", ""),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.description_input)

        self.color_input = TextInput(
            label="Color (hex, optional)",
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
            label="Thumbnail URL (optional)",
            placeholder="https://example.com/image.png",
            default=self.existing_data.get("thumbnail_url", ""),
            required=False,
            max_length=2000,
        )
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction):
        color_hex = (self.color_input.value or "").strip().lstrip("#") or None
        color = None
        if color_hex and len(color_hex) == 6:
            try:
                color = int(color_hex, 16)
            except ValueError:
                color_hex = None

        embed_data = {
            "title": (self.title_input.value or "").strip() or None,
            "description": (self.description_input.value or "").strip() or None,
            "color": color,
            "color_hex": color_hex,
            "footer": (self.footer_input.value or "").strip() or None,
            "thumbnail_url": (self.thumbnail_input.value or "").strip() or None,
        }

        key = (interaction.user.id, self.embed_type)
        if key not in self.cog._builder_states:
            self.cog._builder_states[key] = {}
        self.cog._builder_states[key]["embed_data"] = embed_data

        await interaction.response.send_message(
            "Embed configuration saved. Use Preview or Save.",
            ephemeral=True,
        )


# --- Embed builder view (panel / welcome) ---


class TicketEmbedBuilderView(View):
    """Interactive embed builder: Configure Embed, Preview, Save, Cancel."""

    def __init__(self, cog: "Tickets", guild_id: int, embed_type: str, title_required: bool = True):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.embed_type = embed_type
        self.title_required = title_required
        self.message: Optional[discord.Message] = None

    def _get_state(self, user_id: int) -> Dict:
        key = (user_id, self.embed_type)
        if key not in self.cog._builder_states:
            self.cog._builder_states[key] = {"embed_data": {}}
        return self.cog._builder_states[key]

    async def _refresh_embed(self, interaction: Optional[discord.Interaction] = None):
        user_id = interaction.user.id if interaction else (self.message.author.id if self.message else None)
        if not user_id:
            return
        guild = interaction.guild if interaction else (self.message.guild if self.message else None)
        if not guild:
            return

        state = self._get_state(user_id)
        embed_data = state.get("embed_data", {})

        label = "Panel embed" if self.embed_type == "panel" else "Welcome embed"
        embed = discord.Embed(
            title=f"Embed Builder: {label}",
            description="Use the buttons below to configure, preview, or save.",
            color=await self.cog.bot.get_embed_color(guild),
        )
        title = embed_data.get("title") or "Not set"
        desc = embed_data.get("description") or "Not set"
        embed.add_field(name="Title", value=title[:1024], inline=False)
        embed.add_field(name="Description", value=desc[:1024], inline=False)
        embed.add_field(
            name="Color",
            value=embed_data.get("color_hex") or "Default",
            inline=True,
        )
        embed.add_field(
            name="Footer",
            value=(embed_data.get("footer") or "Not set")[:256],
            inline=True,
        )

        try:
            if interaction:
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)
            elif self.message:
                await self.message.edit(embed=embed, view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Configure Embed", style=discord.ButtonStyle.primary, emoji="\u270d\ufe0f", row=0)
    async def configure_embed(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        existing = state.get("embed_data", {})
        modal = TicketEmbedConfigModal(
            self.cog, self.embed_type, existing, title_required=self.title_required
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="\U0001f441\ufe0f", row=0)
    async def preview(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        embed_data = state.get("embed_data", {})
        if self.title_required and not embed_data.get("title"):
            await interaction.response.send_message(
                "Set a title first (Configure Embed).",
                ephemeral=True,
            )
            return
        embed = self.cog._embed_from_data(embed_data, interaction.guild)
        if not embed:
            await interaction.response.send_message(
                "Configure at least a title for the embed.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, emoji="\u2705", row=0)
    async def save(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        embed_data = state.get("embed_data", {})
        if self.title_required and not embed_data.get("title"):
            await interaction.response.send_message(
                "Panel embed requires a title. Configure Embed first.",
                ephemeral=True,
            )
            return
        if self.embed_type == "panel":
            await self.cog.config.guild(interaction.guild).panel_embed.set(embed_data)
        else:
            await self.cog.config.guild(interaction.guild).welcome_embed.set(
                embed_data if embed_data.get("title") else None
            )
        key = (interaction.user.id, self.embed_type)
        self.cog._builder_states.pop(key, None)
        await interaction.response.send_message(
            f"Saved. Use `ticketset panel` to send or update the panel." if self.embed_type == "panel" else "Welcome embed saved.",
            ephemeral=True,
        )
        try:
            if self.message:
                await self.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="\u2716", row=0)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        key = (interaction.user.id, self.embed_type)
        self.cog._builder_states.pop(key, None)
        await interaction.response.send_message("Builder cancelled.", ephemeral=True)
        try:
            if self.message:
                await self.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass


# --- Create-ticket button view (persistent; handled via on_interaction) ---


class CreateTicketView(View):
    """View with single Create ticket button. Handled by on_interaction after restart."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(
            Button(
                label="Create ticket",
                style=discord.ButtonStyle.primary,
                emoji="\U0001f4cb",
                custom_id=CREATE_CUSTOM_ID,
            )
        )


# --- Ticket management buttons (claim, close) - custom_ids include channel_id ---


def _ticket_management_view(cog: "Tickets", channel_id: int) -> View:
    v = View(timeout=None)
    v.add_item(
        Button(
            label="Claim",
            style=discord.ButtonStyle.primary,
            emoji="\U0001f4dd",
            custom_id=f"{CLAIM_PREFIX}{channel_id}",
        )
    )
    v.add_item(
        Button(
            label="Close ticket",
            style=discord.ButtonStyle.danger,
            emoji="\U0001f512",
            custom_id=f"{CLOSE_PREFIX}{channel_id}",
        )
    )
    return v


# --- Cog ---


class Tickets(commands.Cog):
    """Button-based support tickets with management controls and transcript logging."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x7B17E7C, force_registration=True)
        default_guild = {
            "panel_channel_id": None,
            "panel_message_id": None,
            "category_id": None,
            "manager_role_ids": [],
            "auto_assign_delay_seconds": 0,
            "auto_assign_role_ids": [],
            "inactivity_close_seconds": 0,
            "log_channel_id": None,
            "panel_embed": {},
            "welcome_embed": None,
            "open_tickets": {},
            "next_case_id": 1,
        }
        self.config.register_guild(**default_guild)
        self._builder_states: Dict[Tuple[int, str], Dict] = {}
        self._auto_assign_tasks: Dict[int, asyncio.Task] = {}
        self._inactivity_tasks: Dict[int, asyncio.Task] = {}
        self._inactivity_loops: Dict[int, asyncio.Task] = {}
        log.info("Tickets cog initialized")

    def _embed_from_data(
        self, data: Optional[Dict], guild: Optional[discord.Guild]
    ) -> Optional[discord.Embed]:
        if not data or not data.get("title"):
            return None
        color = data.get("color")
        if not color and guild:
            color = guild.me.color.value if guild.me.color else None
        embed = discord.Embed(
            title=data["title"],
            description=data.get("description") or "",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if data.get("footer"):
            embed.set_footer(text=data["footer"])
        if data.get("thumbnail_url"):
            embed.set_thumbnail(url=data["thumbnail_url"])
        return embed

    async def can_manage_tickets(self, user: discord.Member) -> bool:
        if user.guild_permissions.manage_guild or user.guild_permissions.administrator:
            return True
        role_ids = await self.config.guild(user.guild).manager_role_ids()
        return any(rid in [r.id for r in user.roles] for rid in role_ids)

    async def cog_load(self):
        """Re-schedule tasks for open tickets."""
        for guild in self.bot.guilds:
            await self._restart_tasks_for_guild(guild)

    async def cog_unload(self):
        for task in list(self._auto_assign_tasks.values()):
            if not task.done():
                task.cancel()
        self._auto_assign_tasks.clear()
        for task in list(self._inactivity_loops.values()):
            if not task.done():
                task.cancel()
        self._inactivity_loops.clear()
        for task in list(self._inactivity_tasks.values()):
            if not task.done():
                task.cancel()
        self._inactivity_tasks.clear()
        log.info("Tickets cog unloaded")

    async def _restart_tasks_for_guild(self, guild: discord.Guild):
        open_tickets = await self.config.guild(guild).open_tickets()
        if not open_tickets:
            return
        now = time.time()
        delay_sec = await self.config.guild(guild).auto_assign_delay_seconds()
        inact_sec = await self.config.guild(guild).inactivity_close_seconds()

        for cid_str, data in list(open_tickets.items()):
            try:
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue
            created_at = data.get("created_at") or 0
            last_ts = data.get("last_message_ts") or created_at
            if delay_sec and created_at:
                remaining = max(0, delay_sec - (now - created_at))
                if remaining > 0:
                    self._schedule_auto_assign(guild.id, cid, remaining)
            if inact_sec and inact_sec > 0:
                self._ensure_inactivity_loop(guild.id)
        return

    def _schedule_auto_assign(self, guild_id: int, channel_id: int, delay_seconds: float):
        async def run():
            try:
                await asyncio.sleep(delay_seconds)
                await self._run_auto_assign(guild_id, channel_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._auto_assign_tasks.pop(channel_id, None)

        old = self._auto_assign_tasks.get(channel_id)
        if old and not old.done():
            old.cancel()
        self._auto_assign_tasks[channel_id] = self.bot.loop.create_task(run())

    async def _run_auto_assign(self, guild_id: int, channel_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        open_tickets = await self.config.guild(guild).open_tickets()
        if str(channel_id) not in open_tickets:
            return
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            async with self.config.guild(guild).open_tickets() as ot:
                ot.pop(str(channel_id), None)
            return
        role_ids = await self.config.guild(guild).auto_assign_role_ids()
        if not role_ids:
            return
        overwrites = dict(channel.overwrites)
        for rid in role_ids:
            role = guild.get_role(rid)
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        try:
            await channel.edit(overwrites=overwrites, reason="Tickets: auto-assign")
            roles_mentions = " ".join(guild.get_role(rid).mention for rid in role_ids if guild.get_role(rid))
            if roles_mentions:
                await channel.send(f"This ticket has been assigned to: {roles_mentions}")
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Auto-assign failed for channel %s: %s", channel_id, e)

    def _ensure_inactivity_loop(self, guild_id: int):
        if guild_id in self._inactivity_loops and not self._inactivity_loops[guild_id].done():
            return
        async def loop():
            while True:
                try:
                    await asyncio.sleep(60)
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        break
                    inact = await self.config.guild(guild).inactivity_close_seconds()
                    if inact <= 0:
                        continue
                    open_tickets = await self.config.guild(guild).open_tickets()
                    now = time.time()
                    for cid_str, data in list(open_tickets.items()):
                        try:
                            cid = int(cid_str)
                        except (ValueError, TypeError):
                            continue
                        last = data.get("last_message_ts") or data.get("created_at") or 0
                        if (now - last) >= inact:
                            await self._close_ticket_by_id(guild, cid, "Inactivity auto-close")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.exception("Tickets inactivity loop error: %s", e)
        self._inactivity_loops[guild_id] = self.bot.loop.create_task(loop())

    async def _close_ticket_by_id(
        self, guild: discord.Guild, channel_id: int, reason: str = "Closed"
    ):
        """Generate transcript, delete channel, cleanup config and tasks."""
        open_tickets = await self.config.guild(guild).open_tickets()
        ticket_data = open_tickets.get(str(channel_id)) or {}
        creator_id = ticket_data.get("creator_id")
        case_id = ticket_data.get("case_id")
        creator_name = "Unknown"
        if creator_id:
            member = guild.get_member(creator_id)
            creator_name = member.display_name if member else str(creator_id)

        channel = guild.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            transcript = await self._build_transcript(channel)
            log_channel_id = await self.config.guild(guild).log_channel_id()
            if log_channel_id:
                log_ch = guild.get_channel(log_channel_id)
                if log_ch and isinstance(log_ch, discord.TextChannel):
                    plain_line = f"Ticket #{case_id or '?'} | {creator_name}"
                    if transcript:
                        try:
                            await log_ch.send(content=plain_line, embed=transcript[0])
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                        for emb in transcript[1:]:
                            try:
                                await log_ch.send(embed=emb)
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                    else:
                        try:
                            await log_ch.send(plain_line)
                        except (discord.Forbidden, discord.HTTPException):
                            pass
            try:
                await channel.delete(reason=reason)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
        self._auto_assign_tasks.pop(channel_id, None)
        self._inactivity_tasks.pop(channel_id, None)
        async with self.config.guild(guild).open_tickets() as ot:
            ot.pop(str(channel_id), None)

    async def _build_transcript(
        self, channel: discord.TextChannel, limit: int = 500
    ) -> List[discord.Embed]:
        """Build transcript embeds from channel history."""
        lines: List[str] = []
        try:
            async for msg in channel.history(limit=limit, oldest_first=True):
                if msg.author.bot and msg.content.startswith("This ticket has been assigned"):
                    continue
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M UTC")
                name = msg.author.display_name
                content = (msg.content or "").strip() or "[no text]"
                if msg.attachments:
                    content += " " + " ".join(a.url for a in msg.attachments)
                line = f"[{ts}] {name}: {content[:500]}"
                if len(content) > 500:
                    line += "..."
                lines.append(line)
        except (discord.Forbidden, discord.HTTPException):
            return []

        if not lines:
            emb = discord.Embed(
                title=f"Transcript: #{channel.name}",
                description="No messages.",
                color=discord.Color.dark_gray(),
            )
            return [emb]

        embeds: List[discord.Embed] = []
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 4000:
                emb = discord.Embed(
                    title=f"Transcript: #{channel.name}",
                    description=chunk,
                    color=discord.Color.dark_gray(),
                )
                embeds.append(emb)
                chunk = line
            else:
                chunk = chunk + line + "\n" if chunk else line + "\n"
        if chunk:
            emb = discord.Embed(
                title=f"Transcript: #{channel.name}",
                description=chunk,
                color=discord.Color.dark_gray(),
            )
            embeds.append(emb)
        return embeds

    async def _create_ticket_channel(
        self, guild: discord.Guild, member: discord.Member
    ) -> Optional[Tuple[discord.TextChannel, int]]:
        """Create ticket channel. Returns (channel, case_id) or None."""
        category_id = await self.config.guild(guild).category_id()
        if not category_id:
            return None
        category = guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            return None

        case_id = await self.config.guild(guild).next_case_id()
        await self.config.guild(guild).next_case_id.set(case_id + 1)
        name_base = _sanitize_channel_name(member.display_name)
        channel_name = f"ticket-{name_base}-{case_id}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        manager_role_ids = await self.config.guild(guild).manager_role_ids()
        for rid in manager_role_ids:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        try:
            channel = await category.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                reason=f"Ticket for {member.display_name}",
            )
            return (channel, case_id)
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Failed to create ticket channel: %s", e)
            return None

    async def _send_ticket_welcome(
        self, channel: discord.TextChannel, creator: discord.Member
    ):
        welcome_embed_data = await self.config.guild(channel.guild).welcome_embed()
        welcome_embed = self._embed_from_data(welcome_embed_data, channel.guild)
        parts = [creator.mention]
        if welcome_embed:
            await channel.send(
                content=" ".join(parts),
                embed=welcome_embed,
                allowed_mentions=discord.AllowedMentions(users=[creator]),
            )
        else:
            await channel.send(
                content=" ".join(parts) + "\nA staff member will be with you shortly.",
                allowed_mentions=discord.AllowedMentions(users=[creator]),
            )
        view = _ticket_management_view(self, channel.id)
        await channel.send("Use the buttons below to manage this ticket.", view=view)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        open_tickets = await self.config.guild(message.guild).open_tickets()
        if str(message.channel.id) not in open_tickets:
            return
        async with self.config.guild(message.guild).open_tickets() as ot:
            data = ot.get(str(message.channel.id))
            if data is not None:
                data["last_message_ts"] = time.time()
        return

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if getattr(interaction.type, "value", interaction.type) != 3:
            return
        custom_id = (interaction.data or {}).get("custom_id") or ""
        if not custom_id.startswith(TICKETS_PREFIX):
            return
        if interaction.response.is_done():
            return

        if custom_id == CREATE_CUSTOM_ID:
            await self._handle_create_ticket(interaction)
            return
        if custom_id.startswith(CLAIM_PREFIX):
            try:
                cid = int(custom_id[len(CLAIM_PREFIX):].strip())
            except ValueError:
                return
            await self._handle_claim(interaction, cid)
            return
        if custom_id.startswith(CLOSE_PREFIX):
            try:
                cid = int(custom_id[len(CLOSE_PREFIX):].strip())
            except ValueError:
                return
            await self._handle_close(interaction, cid)
            return

    async def _handle_create_ticket(self, interaction: discord.Interaction):
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message(
                "This can only be used in a server.",
                ephemeral=True,
            )
            return
        guild = interaction.guild
        user = interaction.user
        if not isinstance(user, discord.Member):
            await interaction.response.send_message(
                "Could not resolve your membership.",
                ephemeral=True,
            )
            return

        panel_channel_id = await self.config.guild(guild).panel_channel_id()
        if interaction.channel_id != panel_channel_id:
            await interaction.response.send_message(
                "Please use the ticket panel in the correct channel.",
                ephemeral=True,
            )
            return

        open_tickets = await self.config.guild(guild).open_tickets()
        for cid_str, data in open_tickets.items():
            if data.get("creator_id") == user.id:
                await interaction.response.send_message(
                    "You already have an open ticket. Please use that channel.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        result = await self._create_ticket_channel(guild, user)
        if not result:
            await interaction.followup.send(
                "Failed to create the ticket channel. Check category and bot permissions.",
                ephemeral=True,
            )
            return
        channel, case_id = result

        now = time.time()
        async with self.config.guild(guild).open_tickets() as ot:
            ot[str(channel.id)] = {
                "creator_id": user.id,
                "case_id": case_id,
                "created_at": now,
                "last_message_ts": now,
                "assigned_to": None,
            }

        await self._send_ticket_welcome(channel, user)

        delay = await self.config.guild(guild).auto_assign_delay_seconds()
        if delay > 0:
            self._schedule_auto_assign(guild.id, channel.id, float(delay))
        inact = await self.config.guild(guild).inactivity_close_seconds()
        if inact > 0:
            self._ensure_inactivity_loop(guild.id)

        await interaction.followup.send(
            f"Ticket created: {channel.mention}",
            ephemeral=True,
        )

    async def _handle_claim(self, interaction: discord.Interaction, channel_id: int):
        if not interaction.guild:
            return
        if not await self.can_manage_tickets(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to manage tickets.",
                ephemeral=True,
            )
            return
        open_tickets = await self.config.guild(interaction.guild).open_tickets()
        if str(channel_id) not in open_tickets:
            await interaction.response.send_message(
                "This ticket is no longer open.",
                ephemeral=True,
            )
            return
        async with self.config.guild(interaction.guild).open_tickets() as ot:
            ot[str(channel_id)]["assigned_to"] = interaction.user.id
        await interaction.response.send_message(
            f"Ticket claimed by {interaction.user.mention}.",
            ephemeral=False,
        )

    async def _handle_close(self, interaction: discord.Interaction, channel_id: int):
        if not interaction.guild:
            return
        if not await self.can_manage_tickets(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to manage tickets.",
                ephemeral=True,
            )
            return
        open_tickets = await self.config.guild(interaction.guild).open_tickets()
        if str(channel_id) not in open_tickets:
            await interaction.response.send_message(
                "This ticket is no longer open.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Closing ticket and saving transcript...",
            ephemeral=False,
        )
        await self._close_ticket_by_id(
            interaction.guild, channel_id, reason=f"Closed by {interaction.user}"
        )

    # --- ticketset commands ---

    @commands.group(name="ticketset", aliases=["tset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def ticketset(self, ctx: commands.Context):
        """Configure the ticket system. No user-facing commands; users create tickets via the panel button."""
        pass

    @ticketset.command(name="channel")
    async def ticketset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where the create-ticket panel is sent."""
        await self.config.guild(ctx.guild).panel_channel_id.set(channel.id)
        await ctx.send(f"Panel channel set to {channel.mention}. Use `ticketset panel` to send the panel.")

    @ticketset.command(name="category")
    async def ticketset_category(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Set the category where ticket channels are created."""
        await self.config.guild(ctx.guild).category_id.set(category.id)
        await ctx.send(f"Ticket category set to {category.name}.")

    @ticketset.command(name="managerroles")
    async def ticketset_managerroles(
        self, ctx: commands.Context, *roles: discord.Role
    ):
        """Set the roles that can claim and close tickets."""
        await self.config.guild(ctx.guild).manager_role_ids.set([r.id for r in roles])
        if roles:
            await ctx.send(f"Manager roles set to: {', '.join(r.mention for r in roles)}.")
        else:
            await ctx.send("Manager roles cleared. Only server admins can manage tickets.")

    @ticketset.command(name="autoassign")
    async def ticketset_autoassign(
        self,
        ctx: commands.Context,
        delay: str,
        *roles: discord.Role,
    ):
        """Set delay and roles for auto-assign. Delay: e.g. 5m, 1h. Roles to assign after delay."""
        try:
            delta = parse_timedelta(
                delay,
                minimum=timedelta(seconds=0),
                maximum=timedelta(days=7),
                allowed_units=["days", "hours", "minutes", "seconds"],
            )
        except Exception:
            await ctx.send("Invalid delay. Use e.g. 5m, 1h, 30s.")
            return
        sec = int(delta.total_seconds())
        await self.config.guild(ctx.guild).auto_assign_delay_seconds.set(sec)
        await self.config.guild(ctx.guild).auto_assign_role_ids.set([r.id for r in roles])
        await ctx.send(
            f"Auto-assign set: after {humanize_timedelta(seconds=sec)}, assign to {', '.join(r.mention for r in roles) or 'no roles'}."
        )

    @ticketset.command(name="inactivity")
    async def ticketset_inactivity(self, ctx: commands.Context, delay: str):
        """Set inactivity auto-close delay. Use 0 or 'off' to disable."""
        if delay.lower() in ("0", "off", "none", "disable"):
            await self.config.guild(ctx.guild).inactivity_close_seconds.set(0)
            await ctx.send("Inactivity auto-close disabled.")
            return
        try:
            delta = parse_timedelta(
                delay,
                minimum=timedelta(minutes=1),
                maximum=timedelta(days=30),
                allowed_units=["days", "hours", "minutes", "seconds"],
            )
        except Exception:
            await ctx.send("Invalid delay. Use e.g. 1h, 24h, 7d, or 0 to disable.")
            return
        sec = int(delta.total_seconds())
        await self.config.guild(ctx.guild).inactivity_close_seconds.set(sec)
        await ctx.send(f"Tickets will auto-close after {humanize_timedelta(seconds=sec)} of no messages.")

    @ticketset.command(name="logchannel")
    async def ticketset_logchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the channel for ticket transcripts. Omit to clear."""
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id if channel else None)
        if channel:
            await ctx.send(f"Transcripts will be sent to {channel.mention}.")
        else:
            await ctx.send("Transcript log channel cleared.")

    @ticketset.command(name="panelembed")
    async def ticketset_panelembed(self, ctx: commands.Context):
        """Open the interactive embed builder for the panel message."""
        key = (ctx.author.id, "panel")
        if key not in self._builder_states:
            self._builder_states[key] = {"embed_data": await self.config.guild(ctx.guild).panel_embed() or {}}
        view = TicketEmbedBuilderView(self, ctx.guild.id, "panel", title_required=True)
        embed = discord.Embed(
            title="Panel Embed Builder",
            description="Configure the embed shown on the create-ticket panel.",
            color=await ctx.embed_color(),
        )
        view.message = await ctx.send(embed=embed, view=view)

    @ticketset.command(name="welcomeembed")
    async def ticketset_welcomeembed(self, ctx: commands.Context):
        """Open the interactive embed builder for the welcome message in new tickets. Save with empty title to disable welcome embed."""
        key = (ctx.author.id, "welcome")
        if key not in self._builder_states:
            self._builder_states[key] = {"embed_data": await self.config.guild(ctx.guild).welcome_embed() or {}}
        view = TicketEmbedBuilderView(self, ctx.guild.id, "welcome", title_required=False)
        embed = discord.Embed(
            title="Welcome Embed Builder",
            description="Configure the embed shown when a ticket is opened. Save with empty title to disable.",
            color=await ctx.embed_color(),
        )
        view.message = await ctx.send(embed=embed, view=view)

    @ticketset.command(name="panel")
    async def ticketset_panel(self, ctx: commands.Context):
        """Send or update the create-ticket panel in the configured channel."""
        channel_id = await self.config.guild(ctx.guild).panel_channel_id()
        if not channel_id:
            await ctx.send("Set the panel channel first with `ticketset channel #channel`.")
            return
        channel = ctx.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            await ctx.send("Panel channel not found. Set it again with `ticketset channel`.")
            return
        panel_embed_data = await self.config.guild(ctx.guild).panel_embed()
        panel_embed = self._embed_from_data(panel_embed_data, ctx.guild)
        if not panel_embed:
            await ctx.send("Configure the panel embed first with `ticketset panelembed`.")
            return
        view = CreateTicketView(self)
        try:
            msg_id = await self.config.guild(ctx.guild).panel_message_id()
            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=panel_embed, view=view)
                    await ctx.send("Panel updated.")
                    return
                except (discord.NotFound, discord.HTTPException):
                    pass
            msg = await channel.send(embed=panel_embed, view=view)
            await self.config.guild(ctx.guild).panel_message_id.set(msg.id)
            await self.config.guild(ctx.guild).panel_channel_id.set(channel.id)
            await ctx.send("Panel sent.")
        except (discord.Forbidden, discord.HTTPException) as e:
            await ctx.send(f"Could not send or edit the panel: {e}")


async def setup(bot: Red):
    await bot.add_cog(Tickets(bot))
