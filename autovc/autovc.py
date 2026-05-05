import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.autovc")

PANEL_CUSTOM_ID_PREFIX = "autovc:panel:"
ACCESS_CUSTOM_ID_PREFIX = "autovc:access:"


class SetLimitModal(Modal, title="Set user limit"):
    """Modal for setting VC user limit from the panel."""

    limit_input = TextInput(
        label="User limit",
        placeholder="0 = no limit (1-99)",
        required=True,
        min_length=1,
        max_length=2,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.limit_input.value.strip()
        try:
            limit = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a number between 0 and 99.",
                ephemeral=True,
            )
            return
        if limit < 0 or limit > 99:
            await interaction.response.send_message(
                "Limit must be between 0 and 99 (0 = no limit).",
                ephemeral=True,
            )
            return
        await self.cog._panel_set_limit(interaction, limit)


class RenameVCModal(Modal, title="Rename VC"):
    """Modal for renaming the current owned VC from the panel."""

    name_input = TextInput(
        label="New VC name",
        placeholder="Leave blank to reset to default (e.g. Chill VC)",
        required=False,
        min_length=0,
        max_length=100,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        await self.cog._panel_rename_vc(interaction, name[:100] if name else None)


class VCPanelView(View):
    """Panel view with buttons for VC control. Persistent (timeout=None)."""

    def __init__(self, cog: "AutoVC"):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def _dispatch(self, interaction: discord.Interaction, action: str):
        if action == "limit":
            await interaction.response.send_modal(SetLimitModal(self.cog))
            return
        if action == "rename":
            await interaction.response.send_modal(RenameVCModal(self.cog))
            return
        await self.cog._panel_action(interaction, action)

    @discord.ui.button(
        label="Lock VC",
        style=discord.ButtonStyle.primary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}lock",
        row=0,
    )
    async def lock_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "lock")

    @discord.ui.button(
        label="Unlock VC",
        style=discord.ButtonStyle.primary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}unlock",
        row=0,
    )
    async def unlock_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "unlock")

    @discord.ui.button(
        label="Hide VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}hide",
        row=0,
    )
    async def hide_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "hide")

    @discord.ui.button(
        label="Show VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}show",
        row=0,
    )
    async def show_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "show")

    @discord.ui.button(
        label="Set user limit",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}limit",
        row=1,
    )
    async def limit_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "limit")

    @discord.ui.button(
        label="Rename VC",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{PANEL_CUSTOM_ID_PREFIX}rename",
        row=1,
    )
    async def rename_btn(self, interaction: discord.Interaction, button: Button):
        await self._dispatch(interaction, "rename")


class UserSearchModal(Modal, title="Grant VC Access"):
    search_input = TextInput(
        label="Search for a user",
        placeholder="Enter a username or display name",
        required=True,
        min_length=1,
        max_length=100,
    )

    def __init__(self, cog: "AutoVC"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            await interaction.response.send_message("Could not determine the voice channel.", ephemeral=True)
            return

        query = self.search_input.value.strip().lower()

        def rank(m: discord.Member) -> int:
            dn = m.display_name.lower()
            un = m.name.lower()
            if dn == query or un == query:
                return 0
            if dn.startswith(query) or un.startswith(query):
                return 1
            return 2

        candidates = [
            m for m in interaction.guild.members
            if not m.bot
            and m.id != interaction.user.id
            and (query in m.display_name.lower() or query in m.name.lower())
        ]
        candidates.sort(key=rank)
        truncated = len(candidates) > 25
        results = candidates[:25]

        if not results:
            await interaction.response.send_message(
                f"No members found matching **{discord.utils.escape_markdown(self.search_input.value.strip())}**.",
                ephemeral=True,
            )
            return

        note = "\n*Showing first 25 results — refine your search to narrow down.*" if truncated else ""
        view = UserSearchResultView(self.cog, results, vc)
        await interaction.response.send_message(
            f"Select a user to grant access:{note}",
            view=view,
            ephemeral=True,
        )


class UserSearchResultView(View):
    def __init__(self, cog: "AutoVC", members: List[discord.Member], vc: discord.VoiceChannel):
        super().__init__(timeout=60)
        self.cog = cog
        self.vc = vc

        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=(f"@{m.name}" if m.display_name != m.name else None),
            )
            for m in members
        ]
        self._select = discord.ui.Select(
            placeholder="Choose a user...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return
        member = interaction.guild.get_member(int(self._select.values[0]))
        if not member:
            await interaction.followup.send("User not found — they may have left the server.", ephemeral=True)
            return
        try:
            overwrite = self.vc.overwrites_for(member)
            overwrite.view_channel = True
            overwrite.connect = True
            await self.vc.set_permissions(member, overwrite=overwrite)
            await interaction.followup.send(
                f"Granted {member.mention} access to {self.vc.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning(f"Failed to grant VC access: {e}")
            await interaction.followup.send("Failed to grant access. Check bot permissions.", ephemeral=True)


class RevokeSelectView(View):
    def __init__(self, cog: "AutoVC", members: List[discord.Member], vc: discord.VoiceChannel):
        super().__init__(timeout=60)
        self.cog = cog
        self.vc = vc

        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                value=str(m.id),
                description=(f"@{m.name}" if m.display_name != m.name else None),
            )
            for m in members
        ]
        self._select = discord.ui.Select(
            placeholder="Choose a user to remove...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            return
        member = interaction.guild.get_member(int(self._select.values[0]))
        if not member:
            await interaction.followup.send("User not found — they may have left the server.", ephemeral=True)
            return
        try:
            await self.vc.set_permissions(member, overwrite=None)
            await interaction.followup.send(
                f"Revoked {member.mention}'s access to {self.vc.mention}.", ephemeral=True
            )
        except discord.HTTPException as e:
            log.warning(f"Failed to revoke VC access: {e}")
            await interaction.followup.send("Failed to revoke access. Check bot permissions.", ephemeral=True)


class AccessManagementView(View):
    """Persistent view sent in VC chat on creation. Lets the owner grant/revoke user access."""

    def __init__(self, cog: "AutoVC"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Grant Access",
        style=discord.ButtonStyle.success,
        custom_id=f"{ACCESS_CUSTOM_ID_PREFIX}grant",
    )
    async def grant_btn(self, interaction: discord.Interaction, button: Button):
        err = await self.cog._check_access_owner(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.send_modal(UserSearchModal(self.cog))

    @discord.ui.button(
        label="Revoke Access",
        style=discord.ButtonStyle.danger,
        custom_id=f"{ACCESS_CUSTOM_ID_PREFIX}revoke",
    )
    async def revoke_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        err = await self.cog._check_access_owner(interaction)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            await interaction.followup.send("Could not determine the voice channel.", ephemeral=True)
            return

        created_vcs = await self.cog.config.guild(interaction.guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        owner_id = vc_data.get("owner_id") if vc_data else 0

        whitelisted = self.cog._get_whitelisted_members(vc, owner_id or 0)
        if not whitelisted:
            await interaction.followup.send(
                "No users have been granted access to this VC.", ephemeral=True
            )
            return

        view = RevokeSelectView(self.cog, whitelisted, vc)
        await interaction.followup.send("Select a user to revoke access from:", view=view, ephemeral=True)


class AutoVC(commands.Cog):
    """Automatically create voice channels when members join source VCs."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=987654323, force_registration=True
        )

        default_guild = {
            "source_vcs": {},  # {source_vc_id: {"type": "public|personal|private", "category_id": int}}
            "created_vcs": {},  # {vc_id: {"source_vc_id": int, "owner_id": int|None, "role_id": int|None, "type": str, "created_at": timestamp}}
            "claimable_vcs": {},  # {vc_id: {"owner_left_at": timestamp, "original_owner": int}}
            "member_role_id": None,  # Optional: role that grants member access (for @Member scenarios)
            "panel_enabled": False,
            "panel_channel_ids": [],
            "panel_message_ids": {},  # {channel_id: message_id}
        }

        self.config.register_guild(**default_guild)

        # In-memory rate limiting: {user_id: [timestamp1, timestamp2, ...]}
        self.rate_limit: Dict[int, List[datetime]] = {}

        # Track members currently being processed to prevent duplicate VC creation
        # {user_id: timestamp} - tracks when we started processing a move
        self.processing_members: Dict[int, datetime] = {}
        
        # Lock to prevent concurrent processing of the same member
        self.processing_lock = asyncio.Lock()

        # Background tasks
        self.cleanup_task: Optional[asyncio.Task] = None

        log.info("AutoVC cog initialized")

    async def _ctx_send(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        ephemeral: bool = False,
        **kwargs,
    ):
        """Send a message; ephemeral only when invoked as a slash command."""
        if getattr(ctx, "interaction", None) and ephemeral:
            return await ctx.send(content, embed=embed, ephemeral=True, **kwargs)
        return await ctx.send(content, embed=embed, **kwargs)

    async def _get_panel_embed(self, guild: discord.Guild) -> discord.Embed:
        """Build the VC panel embed."""
        embed = discord.Embed(
            title="VC controls",
            description=(
                "Use the buttons below to control your AutoVC voice channel. "
                "You must be in your owned personal or private VC for the buttons to work."
            ),
            color=await self.bot.get_embed_color(guild),
        )
        return embed

    async def _send_panel_to_channel(
        self, guild: discord.Guild, channel: discord.TextChannel
    ) -> Optional[discord.Message]:
        """Send panel embed + view to channel. Store message_id. Return the message or None."""
        try:
            view = VCPanelView(self)
            embed = await self._get_panel_embed(guild)
            msg = await channel.send(embed=embed, view=view)
            self.bot.add_view(view, message_id=msg.id)
            ids = await self.config.guild(guild).panel_message_ids()
            ids[str(channel.id)] = msg.id
            await self.config.guild(guild).panel_message_ids.set(ids)
            return msg
        except discord.HTTPException as e:
            log.warning(f"Failed to send panel to {channel.id}: {e}")
            return None

    async def _remove_panel_from_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> None:
        """Delete panel message from channel and clear stored message_id."""
        ids = await self.config.guild(guild).panel_message_ids()
        mid = ids.pop(str(channel_id), None)
        await self.config.guild(guild).panel_message_ids.set(ids)
        if mid and isinstance(mid, int):
            ch = guild.get_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(mid)
                    await msg.delete()
                except (discord.HTTPException, discord.NotFound):
                    pass

    async def _check_access_owner(self, interaction: discord.Interaction) -> Optional[str]:
        """Return an error string if the user is not the AutoVC owner of the interaction's VC."""
        if not interaction.guild:
            return "This can only be used in a server."
        vc = interaction.channel
        if not isinstance(vc, discord.VoiceChannel):
            vc = interaction.guild.get_channel(interaction.channel_id)
        if not isinstance(vc, discord.VoiceChannel):
            return "This can only be used in a voice channel's chat."
        created_vcs = await self.config.guild(interaction.guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        if not vc_data:
            return "This VC is not managed by AutoVC."
        if vc_data.get("owner_id") != interaction.user.id:
            return "You are not the owner of this VC."
        return None

    def _get_whitelisted_members(
        self, vc: discord.VoiceChannel, owner_id: int
    ) -> List[discord.Member]:
        """Return members with an explicit connect=True overwrite, excluding the owner."""
        return [
            target
            for target, overwrite in vc.overwrites.items()
            if isinstance(target, discord.Member)
            and target.id != owner_id
            and overwrite.connect is True
        ]

    async def _send_vc_welcome(self, vc: discord.VoiceChannel) -> None:
        """Send the access management embed and buttons to the VC's text chat."""
        embed = discord.Embed(
            title="Manage VC Access",
            description=(
                "Use **Grant Access** to let a specific user join this voice channel, "
                "or **Revoke Access** to remove someone you previously invited."
            ),
            color=await self.bot.get_embed_color(vc.guild),
        )
        view = AccessManagementView(self)
        try:
            msg = await vc.send(embed=embed, view=view)
            self.bot.add_view(view, message_id=msg.id)
        except discord.HTTPException as e:
            log.warning(f"Failed to send welcome message to VC {vc.id}: {e}")

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_loop())
        self.bot.add_view(VCPanelView(self))
        self.bot.add_view(AccessManagementView(self))

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        log.info("AutoVC cog unloaded")

    def check_rate_limit(self, user_id: int) -> bool:
        """Check if user has exceeded rate limit (3 creations per 30 seconds).
        
        Returns True if rate limit exceeded, False otherwise.
        Does NOT add timestamp - that should be done after successful VC creation.
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=30)

        # Filter out timestamps older than 30 seconds
        if user_id in self.rate_limit:
            self.rate_limit[user_id] = [
                ts for ts in self.rate_limit[user_id] if ts > cutoff
            ]
        else:
            self.rate_limit[user_id] = []

        # Check if user has 3+ creations in last 30 seconds
        return len(self.rate_limit[user_id]) >= 3

    def record_vc_creation(self, user_id: int):
        """Record a successful VC creation for rate limiting."""
        now = datetime.utcnow()
        if user_id not in self.rate_limit:
            self.rate_limit[user_id] = []
        self.rate_limit[user_id].append(now)

    @commands.hybrid_group(name="autovcset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _autovcset(self, ctx: commands.Context):
        """AutoVC admin: source VCs, settings, member role, panel."""
        pass

    @_autovcset.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def _add_source_vc(
        self,
        ctx: commands.Context,
        source_vc: discord.VoiceChannel,
        vc_type: str,
        category: Optional[discord.CategoryChannel] = None,
    ):
        """Add a source VC with specified type.
        
        Types: public, personal, private
        
        Examples:
        - [p]autovcset add #Create Public public
        - [p]autovcset add #Create Personal personal
        - [p]autovcset add #Create Private private #Private-VCs
        """
        vc_type = vc_type.lower()
        if vc_type not in ["public", "personal", "private"]:
            await ctx.send(
                "Invalid type. Must be one of: `public`, `personal`, or `private`"
            )
            return

        guild = ctx.guild
        source_vc_id = source_vc.id

        # Determine category
        if category is None:
            category = source_vc.category
            if category is None:
                await ctx.send(
                    "Source VC must be in a category, or you must specify a category."
                )
                return
            category_id = category.id
        else:
            category_id = category.id

        # Get current source VCs
        source_vcs = await self.config.guild(guild).source_vcs()
        source_vcs[str(source_vc_id)] = {
            "type": vc_type,
            "category_id": category_id,
        }
        await self.config.guild(guild).source_vcs.set(source_vcs)

        await ctx.send(
            f"Source VC {source_vc.mention} configured as **{vc_type}** type.\n"
            f"Created VCs will be placed in {category.mention if category else 'the same category'}."
        )

    @_autovcset.command(name="remove", aliases=["delete", "del"])
    @commands.admin_or_permissions(manage_guild=True)
    async def _remove_source_vc(
        self, ctx: commands.Context, source_vc: discord.VoiceChannel
    ):
        """Remove a source VC configuration."""
        guild = ctx.guild
        source_vc_id = source_vc.id

        source_vcs = await self.config.guild(guild).source_vcs()
        if str(source_vc_id) not in source_vcs:
            await ctx.send(f"{source_vc.mention} is not configured as a source VC.")
            return

        del source_vcs[str(source_vc_id)]
        await self.config.guild(guild).source_vcs.set(source_vcs)

        await ctx.send(f"Removed {source_vc.mention} from source VCs.")

    @_autovcset.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def _list_source_vcs(self, ctx: commands.Context):
        """List all configured source VCs and their types."""
        guild = ctx.guild
        source_vcs = await self.config.guild(guild).source_vcs()

        if not source_vcs:
            await ctx.send("No source VCs are configured.")
            return

        message = "**Configured Source VCs:**\n\n"
        for source_vc_id_str, config in source_vcs.items():
            try:
                source_vc_id = int(source_vc_id_str)
                source_vc = guild.get_channel(source_vc_id)
                if not source_vc:
                    message += f"❌ VC ID `{source_vc_id}` (channel not found)\n"
                    continue

                vc_type = config.get("type", "unknown")
                category_id = config.get("category_id")
                category = guild.get_channel(category_id) if category_id else None

                category_str = category.mention if category else "Unknown category"
                message += (
                    f"{source_vc.mention}: **{vc_type}** type → {category_str}\n"
                )
            except (ValueError, KeyError) as e:
                log.error(f"Error processing source VC config: {e}")
                continue

        await ctx.send(message)

    @_autovcset.command(name="settings")
    @commands.admin_or_permissions(manage_guild=True)
    async def _settings(self, ctx: commands.Context):
        """Show current AutoVC configuration."""
        guild = ctx.guild
        source_vcs = await self.config.guild(guild).source_vcs()
        member_role_id = await self.config.guild(guild).member_role_id()
        created_vcs = await self.config.guild(guild).created_vcs()

        embed = discord.Embed(
            title="AutoVC Settings",
            color=await self.bot.get_embed_color(ctx.guild),
        )

        # Source VCs count
        embed.add_field(
            name="Source VCs",
            value=f"{len(source_vcs)} configured",
            inline=True,
        )

        # Created VCs count
        embed.add_field(
            name="Active VCs",
            value=f"{len(created_vcs)} created",
            inline=True,
        )

        # Member role
        if member_role_id:
            member_role = guild.get_role(member_role_id)
            if member_role:
                embed.add_field(
                    name="Member Role",
                    value=member_role.mention,
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Member Role",
                    value=f"Role ID {member_role_id} (not found)",
                    inline=True,
                )
        else:
            embed.add_field(
                name="Member Role",
                value="Using @everyone",
                inline=True,
            )

        await ctx.send(embed=embed)

    @_autovcset.group(name="panel")
    @commands.admin_or_permissions(manage_guild=True)
    async def _autovcset_panel(self, ctx: commands.Context):
        """VC panel: embed with buttons for owners to control their VC."""
        pass

    @_autovcset_panel.command(name="toggle")
    @commands.admin_or_permissions(manage_guild=True)
    async def _panel_toggle(self, ctx: commands.Context):
        """Enable or disable the VC panel in designated channels."""
        guild = ctx.guild
        enabled = await self.config.guild(guild).panel_enabled()
        new_enabled = not enabled
        await self.config.guild(guild).panel_enabled.set(new_enabled)

        if new_enabled:
            channel_ids = await self.config.guild(guild).panel_channel_ids()
            ids = await self.config.guild(guild).panel_message_ids()
            for cid in channel_ids or []:
                ch = guild.get_channel(cid)
                if not isinstance(ch, discord.TextChannel):
                    continue
                # Send only if no message or message was deleted; otherwise refresh to latest view
                mid = ids.get(str(cid)) if ids else None
                if mid:
                    try:
                        msg = await ch.fetch_message(int(mid))
                        view = VCPanelView(self)
                        embed = await self._get_panel_embed(guild)
                        await msg.edit(embed=embed, view=view)
                        self.bot.add_view(view, message_id=msg.id)
                        continue
                    except (discord.NotFound, ValueError, TypeError):
                        pass
                await self._send_panel_to_channel(guild, ch)
            await ctx.send(
                "VC panel is now **enabled**. Panel message(s) have been sent to the designated channel(s)."
            )
        else:
            ids = await self.config.guild(guild).panel_message_ids()
            for cid_str in list(ids.keys()):
                await self._remove_panel_from_channel(guild, int(cid_str))
            await ctx.send(
                "VC panel is now **disabled**. Panel message(s) have been removed."
            )

    @_autovcset_panel.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def _panel_channel_add(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Add a channel for the VC panel."""
        guild = ctx.guild
        channel_ids = await self.config.guild(guild).panel_channel_ids()
        if channel_ids is None:
            channel_ids = []
        if channel.id in channel_ids:
            await ctx.send(f"{channel.mention} is already a panel channel.")
            return
        channel_ids.append(channel.id)
        await self.config.guild(guild).panel_channel_ids.set(channel_ids)
        if await self.config.guild(guild).panel_enabled():
            msg = await self._send_panel_to_channel(guild, channel)
            if msg:
                await ctx.send(
                    f"Added {channel.mention} as a panel channel and sent the panel."
                )
            else:
                await ctx.send(
                    f"Added {channel.mention} as a panel channel but failed to send the panel. Check bot permissions."
                )
        else:
            await ctx.send(
                f"Added {channel.mention} as a panel channel. Use `{ctx.clean_prefix}autovcset panel toggle` to enable the panel."
            )

    @_autovcset_panel.command(name="remove")
    @commands.admin_or_permissions(manage_guild=True)
    async def _panel_channel_remove(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Remove a channel from the VC panel."""
        guild = ctx.guild
        channel_ids = await self.config.guild(guild).panel_channel_ids()
        if channel_ids is None or channel.id not in channel_ids:
            await ctx.send(f"{channel.mention} is not a panel channel.")
            return
        channel_ids = [x for x in channel_ids if x != channel.id]
        await self.config.guild(guild).panel_channel_ids.set(channel_ids)
        await self._remove_panel_from_channel(guild, channel.id)
        await ctx.send(f"Removed {channel.mention} from panel channels.")

    @_autovcset_panel.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def _panel_channel_list(self, ctx: commands.Context):
        """List channels designated for the VC panel."""
        guild = ctx.guild
        channel_ids = await self.config.guild(guild).panel_channel_ids()
        enabled = await self.config.guild(guild).panel_enabled()
        if not channel_ids:
            await ctx.send(
                "No panel channels are set. Use `autovcset panel add <channel>` to add one."
            )
            return
        lines = [
            f"Panel is **{'enabled' if enabled else 'disabled'}**.",
            "",
            "**Panel channels:**",
        ]
        for cid in channel_ids:
            ch = guild.get_channel(cid)
            if ch:
                lines.append(f"- {ch.mention}")
            else:
                lines.append(f"- Channel ID `{cid}` (not found)")
        await ctx.send("\n".join(lines))

    @_autovcset.command(name="memberrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def _set_member_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the @Member role for permission handling.
        
        Use without a role to clear and use @everyone instead.
        Example: [p]autovcset memberrole @Member
        """
        guild = ctx.guild

        if role is None:
            await self.config.guild(guild).member_role_id.set(None)
            await ctx.send(
                "Member role cleared. Using @everyone for permission handling."
            )
        else:
            await self.config.guild(guild).member_role_id.set(role.id)
            await ctx.send(
                f"Member role set to {role.mention}. This role will be used for base permissions."
            )

    @commands.hybrid_group(name="autovc", aliases=["avc"])
    @commands.guild_only()
    async def _autovc(self, ctx: commands.Context):
        """AutoVC user commands: lock, unlock, hide, show, limit, name, claim."""
        pass

    async def _vc_lock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Lock your VC so others cannot connect."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        base_role = (
            ctx.guild.get_role(member_role_id)
            if member_role_id
            else ctx.guild.default_role
        )
        overwrite = vc.overwrites_for(base_role)
        overwrite.connect = False
        await vc.set_permissions(base_role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now locked.", ephemeral=True)

    async def _vc_unlock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Unlock your VC so others can connect."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        base_role = (
            ctx.guild.get_role(member_role_id)
            if member_role_id
            else ctx.guild.default_role
        )
        overwrite = vc.overwrites_for(base_role)
        overwrite.connect = True
        await vc.set_permissions(base_role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now unlocked.", ephemeral=True)

    async def _vc_hide(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Hide your VC from the channel list."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        roles_to_update = [ctx.guild.default_role]
        if member_role_id:
            r = ctx.guild.get_role(member_role_id)
            if r:
                roles_to_update.append(r)
        for role in roles_to_update:
            overwrite = vc.overwrites_for(role)
            overwrite.view_channel = False
            await vc.set_permissions(role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now hidden.", ephemeral=True)

    async def _vc_show(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Show your VC in the channel list."""
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        roles_to_update = [ctx.guild.default_role]
        if member_role_id:
            r = ctx.guild.get_role(member_role_id)
            if r:
                roles_to_update.append(r)
        for role in roles_to_update:
            overwrite = vc.overwrites_for(role)
            overwrite.view_channel = True
            await vc.set_permissions(role, overwrite=overwrite)
        await self._ctx_send(ctx, f"{vc.mention} is now visible.", ephemeral=True)

    async def _vc_limit(
        self,
        ctx: commands.Context,
        limit: int,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Set the user limit for your VC (0 = no limit)."""
        if limit < 0 or limit > 99:
            await self._ctx_send(
                ctx, "Limit must be between 0 and 99 (0 = no limit).", ephemeral=True
            )
            return
        vc, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        await vc.edit(user_limit=limit)
        msg = "User limit removed." if limit == 0 else f"User limit set to {limit}."
        await self._ctx_send(ctx, msg, ephemeral=True)

    @_autovc.command(name="lock")
    @commands.guild_only()
    async def _autovc_lock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Lock your VC so others cannot connect."""
        await self._vc_lock(ctx, vc)

    @_autovc.command(name="unlock")
    @commands.guild_only()
    async def _autovc_unlock(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Unlock your VC so others can connect."""
        await self._vc_unlock(ctx, vc)

    @_autovc.command(name="hide")
    @commands.guild_only()
    async def _autovc_hide(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Hide your VC from the channel list."""
        await self._vc_hide(ctx, vc)

    @_autovc.command(name="show")
    @commands.guild_only()
    async def _autovc_show(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Show your VC in the channel list."""
        await self._vc_show(ctx, vc)

    @_autovc.command(name="limit")
    @commands.guild_only()
    async def _autovc_limit(
        self,
        ctx: commands.Context,
        limit: int,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Set the user limit for your VC (0 = no limit)."""
        await self._vc_limit(ctx, limit, vc)

    @_autovc.command(name="name")
    @commands.guild_only()
    async def _autovc_name(
        self,
        ctx: commands.Context,
        new_name: Optional[str] = None,
        vc: Optional[discord.VoiceChannel] = None,
    ):
        """Rename your VC. Leave name blank to reset to default (e.g. YourName's VC)."""
        vc_resolved, vc_data, err = await self._get_owned_vc_for_member(
            ctx.guild, ctx.author, vc
        )
        if err:
            await self._ctx_send(ctx, err, ephemeral=True)
            return
        assert vc_resolved is not None
        if not new_name or not new_name.strip():
            username = ctx.author.display_name[:20]
            name_to_set = f"{username}'s VC"
        else:
            name_to_set = new_name.strip()[:100]
        try:
            await vc_resolved.edit(name=name_to_set)
            await self._ctx_send(
                ctx,
                f"VC renamed to **{discord.utils.escape_markdown(name_to_set)}**.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            log.warning(f"autovc name failed: {e}")
            await self._ctx_send(
                ctx,
                "Failed to rename the VC. Check bot permissions.",
                ephemeral=True,
            )

    @_autovc.command(name="claim")
    @commands.guild_only()
    async def _claim_vc(
        self, ctx: commands.Context, vc: Optional[discord.VoiceChannel] = None
    ):
        """Claim ownership of a VC whose owner left.
        
        If VC not specified, claims the VC you are currently in.
        """
        guild = ctx.guild
        member = ctx.author

        # Determine which VC to claim
        if vc is None:
            if member.voice and member.voice.channel:
                vc = member.voice.channel
            else:
                await self._ctx_send(
                    ctx,
                    "You must be in a voice channel or specify one to claim.",
                    ephemeral=True,
                )
                return

        # Check if VC is tracked
        created_vcs = await self.config.guild(guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))

        if not vc_data:
            await self._ctx_send(ctx, "This VC is not managed by AutoVC.", ephemeral=True)
            return

        # Check if VC is claimable
        claimable_vcs = await self.config.guild(guild).claimable_vcs()
        claim_data = claimable_vcs.get(str(vc.id))

        if not claim_data:
            owner_id = vc_data.get("owner_id")
            if owner_id:
                owner = guild.get_member(owner_id)
                if owner and owner.voice and owner.voice.channel == vc:
                    await self._ctx_send(
                        ctx,
                        "This VC already has an owner who is still in the channel.",
                        ephemeral=True,
                    )
                    return
            await self._ctx_send(
                ctx,
                "This VC is not available for claiming. The owner may still be present, "
                "or the 5-minute waiting period hasn't passed yet.",
                ephemeral=True,
            )
            return

        # Check if 5 minutes have passed
        owner_left_at = datetime.fromisoformat(claim_data["owner_left_at"])
        now = datetime.utcnow()
        time_passed = (now - owner_left_at).total_seconds()

        if time_passed < 300:  # 5 minutes
            remaining = int(300 - time_passed)
            await self._ctx_send(
                ctx,
                f"You must wait {remaining} more seconds before claiming this VC.",
                ephemeral=True,
            )
            return

        # Transfer ownership
        await self._transfer_ownership(guild, vc, member, vc_data)

        # Remove from claimable list
        del claimable_vcs[str(vc.id)]
        await self.config.guild(guild).claimable_vcs.set(claimable_vcs)

        await self._ctx_send(
            ctx,
            f"You have successfully claimed ownership of {vc.mention}!",
            ephemeral=True,
        )

    async def _get_owned_vc_for_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        vc: Optional[discord.VoiceChannel] = None,
    ) -> Tuple[Optional[discord.VoiceChannel], Optional[dict], Optional[str]]:
        """Resolve VC from optional channel or member's current channel. Check ownership.
        Returns (vc, vc_data, error_message). If error_message is set, vc and vc_data are None.
        """
        if vc is None:
            vc = member.voice.channel if member.voice else None
        if not vc:
            return (None, None, "You must be in a voice channel or specify one.")
        created_vcs = await self.config.guild(guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))
        if not vc_data:
            return (None, None, "This VC is not managed by AutoVC.")
        if vc_data.get("owner_id") != member.id:
            return (None, None, "You are not the owner of this VC.")
        if vc_data.get("type") not in ("personal", "private"):
            return (None, None, "This VC has no owner.")
        return (vc, vc_data, None)

    async def _panel_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        """Handle panel button click: lock, unlock, hide, show."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None and vc_data is not None
        member_role_id = await self.config.guild(interaction.guild).member_role_id()
        base_role = (
            interaction.guild.get_role(member_role_id)
            if member_role_id
            else interaction.guild.default_role
        )
        try:
            if action == "lock":
                overwrite = vc.overwrites_for(base_role)
                overwrite.connect = False
                await vc.set_permissions(base_role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now locked.", ephemeral=True
                )
            elif action == "unlock":
                overwrite = vc.overwrites_for(base_role)
                overwrite.connect = True
                await vc.set_permissions(base_role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now unlocked.", ephemeral=True
                )
            elif action == "hide":
                roles_to_update = [interaction.guild.default_role]
                if member_role_id:
                    r = interaction.guild.get_role(member_role_id)
                    if r:
                        roles_to_update.append(r)
                for role in roles_to_update:
                    overwrite = vc.overwrites_for(role)
                    overwrite.view_channel = False
                    await vc.set_permissions(role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now hidden.", ephemeral=True
                )
            elif action == "show":
                roles_to_update = [interaction.guild.default_role]
                if member_role_id:
                    r = interaction.guild.get_role(member_role_id)
                    if r:
                        roles_to_update.append(r)
                for role in roles_to_update:
                    overwrite = vc.overwrites_for(role)
                    overwrite.view_channel = True
                    await vc.set_permissions(role, overwrite=overwrite)
                await interaction.followup.send(
                    f"{vc.mention} is now visible.", ephemeral=True
                )
        except discord.HTTPException as e:
            log.warning(f"Panel action {action} failed: {e}")
            await interaction.followup.send(
                "Failed to update the channel. Check bot permissions.",
                ephemeral=True,
            )

    async def _panel_set_limit(
        self, interaction: discord.Interaction, limit: int
    ) -> None:
        """Handle panel modal submit for user limit."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None
        try:
            await vc.edit(user_limit=limit)
            msg = (
                "User limit removed."
                if limit == 0
                else f"User limit set to {limit}."
            )
            await interaction.followup.send(msg, ephemeral=True)
        except discord.HTTPException as e:
            log.warning(f"Panel set limit failed: {e}")
            await interaction.followup.send(
                "Failed to set user limit. Check bot permissions.",
                ephemeral=True,
            )

    async def _panel_rename_vc(
        self, interaction: discord.Interaction, name: Optional[str]
    ) -> None:
        """Handle panel modal submit for VC rename."""
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Could not resolve your membership.", ephemeral=True
            )
            return
        vc = member.voice.channel if member.voice else None
        vc, vc_data, err = await self._get_owned_vc_for_member(
            interaction.guild, member, vc
        )
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        assert vc is not None
        if name is None:
            # Match initial VC naming behavior
            username = member.display_name[:20]
            new_name = f"{username}'s VC"
        else:
            new_name = name.strip()[:100]
            if not new_name:
                username = member.display_name[:20]
                new_name = f"{username}'s VC"
        try:
            await vc.edit(name=new_name)
            await interaction.followup.send(
                f"VC renamed to **{discord.utils.escape_markdown(new_name)}**.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            log.warning(f"Panel rename VC failed: {e}")
            await interaction.followup.send(
                "Failed to rename the VC. Check bot permissions.",
                ephemeral=True,
            )

    async def _transfer_ownership(
        self,
        guild: discord.Guild,
        vc: discord.VoiceChannel,
        new_owner: discord.Member,
        vc_data: dict,
    ):
        """Transfer ownership of a VC to a new owner. No new role is created."""
        old_role_id = vc_data.get("role_id")

        # Delete old owner role if it exists (migrate existing VCs off roles)
        if old_role_id:
            old_role = guild.get_role(old_role_id)
            if old_role:
                try:
                    await old_role.delete(reason="VC ownership transferred")
                except discord.HTTPException:
                    log.warning(f"Failed to delete old owner role {old_role_id}")

        vc_data["owner_id"] = new_owner.id
        vc_data["role_id"] = None

        created_vcs = await self.config.guild(guild).created_vcs()
        created_vcs[str(vc.id)] = vc_data
        await self.config.guild(guild).created_vcs.set(created_vcs)

    async def _create_owner_role(
        self, guild: discord.Guild, owner: discord.Member, vc: discord.VoiceChannel
    ) -> Optional[discord.Role]:
        """Create a temporary role for VC owner with manage_channels permission."""
        try:
            # Create role with unique name
            role_name = f"VC-{vc.id}-Owner"
            role = await guild.create_role(
                name=role_name,
                reason=f"Temporary role for VC owner {owner.display_name}",
                mentionable=False,
            )

            # Grant manage_channels and manage_permissions on the VC
            overwrites = vc.overwrites.copy()
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                manage_channels=True,
                manage_permissions=True,
            )
            await vc.edit(overwrites=overwrites)

            # Assign role to owner
            await owner.add_roles(role, reason="VC owner role assignment")

            log.info(f"Created owner role {role.id} for VC {vc.id}")
            return role

        except discord.Forbidden:
            log.error(f"Permission denied creating owner role in {guild.name}")
            return None
        except discord.HTTPException as e:
            log.error(f"HTTP error creating owner role in {guild.name}: {e}")
            return None

    async def _create_vc(
        self,
        guild: discord.Guild,
        source_vc: discord.VoiceChannel,
        source_config: dict,
        member: discord.Member,
    ) -> Optional[discord.VoiceChannel]:
        """Create a new VC based on source VC configuration."""
        vc_type = source_config.get("type", "public")
        category_id = source_config.get("category_id")

        # Get category
        category = guild.get_channel(category_id) if category_id else None
        if not category or not isinstance(category, discord.CategoryChannel):
            category = source_vc.category
            if not category:
                log.warning(f"No category found for source VC {source_vc.id}")
                return None

        # Generate VC name (preserve spaces and special characters from display name)
        username = member.display_name[:20]
        vc_name = f"{username}'s VC"

        # Get member role for permissions
        member_role_id = await self.config.guild(guild).member_role_id()
        member_role = guild.get_role(member_role_id) if member_role_id else None

        # Set up permission overwrites
        overwrites = {}

        # Base role (@everyone or @Member)
        base_role = member_role if member_role else guild.default_role

        if vc_type == "public":
            # Public: everyone can view/connect
            overwrites[base_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

        elif vc_type == "personal":
            # Personal: everyone can view/connect by default, owner can change
            overwrites[base_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )

        elif vc_type == "private":
            # Private: hidden from everyone by default
            overwrites[guild.default_role] = discord.PermissionOverwrite(
                view_channel=False
            )
            if member_role:
                overwrites[member_role] = discord.PermissionOverwrite(
                    view_channel=False
                )

        # Create the VC
        try:
            vc = await category.create_voice_channel(
                name=vc_name,
                overwrites=overwrites,
                reason=f"AutoVC: {vc_type} VC for {member.display_name}",
            )

            # No owner role: control is via bot commands and panel only (avoids 2FA-for-mods)

            # Track the VC
            created_vcs = await self.config.guild(guild).created_vcs()
            created_vcs[str(vc.id)] = {
                "source_vc_id": source_vc.id,
                "owner_id": member.id if vc_type in ["personal", "private"] else None,
                "role_id": None,
                "type": vc_type,
                "created_at": datetime.utcnow().isoformat(),
            }
            await self.config.guild(guild).created_vcs.set(created_vcs)

            log.info(f"Created {vc_type} VC {vc.id} for {member.display_name}")
            return vc

        except discord.Forbidden:
            log.error(f"Permission denied creating VC in {guild.name}")
            return None
        except discord.HTTPException as e:
            log.error(f"HTTP error creating VC in {guild.name}: {e}")
            return None

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Handle voice state updates (join/leave events)."""
        if member.bot:
            return

        guild = member.guild

        # Handle joining a source VC
        if after.channel:
            source_vcs = await self.config.guild(guild).source_vcs()
            source_vc_id = str(after.channel.id)

            if source_vc_id in source_vcs:
                # Use lock to prevent concurrent processing of the same member
                # This ensures only one VC creation process runs at a time per member
                async with self.processing_lock:
                    # Check if we're currently processing this member (prevent race conditions)
                    # This is the primary protection against duplicate VC creation
                    now = datetime.utcnow()
                    if member.id in self.processing_members:
                        processing_start = self.processing_members[member.id]
                        time_since_start = (now - processing_start).total_seconds()
                        if time_since_start < 5.0:  # Still processing within last 5 seconds
                            log.debug(
                                f"Member {member.display_name} is already being processed (started {time_since_start:.2f}s ago), skipping"
                            )
                            return
                        else:
                            # Clean up old entry
                            del self.processing_members[member.id]

                    # Mark member as being processed
                    self.processing_members[member.id] = now

                    # Check rate limit
                    if self.check_rate_limit(member.id):
                        del self.processing_members[member.id]
                        log.warning(
                            f"Rate limit exceeded for {member.display_name} in {guild.name}"
                        )
                        try:
                            await member.send(
                                "You're creating VCs too quickly! Please wait a moment before creating another one."
                            )
                        except discord.HTTPException:
                            pass
                        return

                    # Create new VC
                    source_config = source_vcs[source_vc_id]
                    new_vc = await self._create_vc(
                        guild, after.channel, source_config, member
                    )

                    if new_vc:
                        # Record successful creation for rate limiting
                        self.record_vc_creation(member.id)
                        
                        # Move user to new VC
                        try:
                            await member.move_to(new_vc, reason="AutoVC: Created new VC")
                            if source_config.get("type") in ("personal", "private"):
                                await self._send_vc_welcome(new_vc)
                        except discord.HTTPException as e:
                            log.error(f"Failed to move user to new VC: {e}")
                        finally:
                            # Clean up processing flag after a short delay to allow move to complete
                            # Use a task to remove the flag after move completes
                            async def cleanup_processing():
                                await asyncio.sleep(2.0)  # Wait 2 seconds for move to complete
                                async with self.processing_lock:
                                    if member.id in self.processing_members:
                                        del self.processing_members[member.id]
                            
                            self.bot.loop.create_task(cleanup_processing())
                    else:
                        # VC creation failed, remove processing flag
                        del self.processing_members[member.id]

        # Handle leaving a created VC
        if before.channel:
            created_vcs = await self.config.guild(guild).created_vcs()
            vc_data = created_vcs.get(str(before.channel.id))

            if vc_data:
                # Don't delete if someone is being moved TO this VC
                # (prevents race condition where VC is deleted before user is moved in)
                if after.channel and after.channel.id == before.channel.id:
                    # User is staying in the same channel (e.g., mute/deafen change)
                    return
                
                # Additional safety: check if VC was just created (within last 2 seconds)
                # This prevents deletion of VCs that are in the process of having users moved to them
                created_at_str = vc_data.get("created_at")
                if created_at_str:
                    created_at = datetime.fromisoformat(created_at_str)
                    time_since_creation = (datetime.utcnow() - created_at).total_seconds()
                    if time_since_creation < 2.0:
                        # VC was just created, don't delete it yet (user is being moved)
                        log.debug(f"Skipping deletion of newly created VC {before.channel.id} (created {time_since_creation:.2f}s ago)")
                        return
                
                # Check if VC is empty - delete immediately
                try:
                    # Get current member count
                    current_members = len(before.channel.members)
                    
                    if current_members == 0:
                        await self._delete_vc_immediately(guild, before.channel, vc_data)
                    else:
                        # Check if owner left
                        owner_id = vc_data.get("owner_id")
                        if owner_id and owner_id == member.id:
                            # Owner left, start claim timer
                            claimable_vcs = await self.config.guild(guild).claimable_vcs()
                            claimable_vcs[str(before.channel.id)] = {
                                "owner_left_at": datetime.utcnow().isoformat(),
                                "original_owner": owner_id,
                            }
                            await self.config.guild(guild).claimable_vcs.set(claimable_vcs)
                except Exception as e:
                    log.error(f"Error checking VC {before.channel.id} for deletion: {e}")

    async def _delete_vc_immediately(
        self,
        guild: discord.Guild,
        vc: discord.VoiceChannel,
        vc_data: dict,
    ):
        """Immediately delete a VC and clean up its owner role."""
        vc_id = vc.id
        vc_id_str = str(vc_id)

        # Delete owner role if it exists
        role_id = vc_data.get("role_id")
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await role.delete(reason="VC deleted, cleaning up owner role")
                except discord.HTTPException:
                    pass

        # Delete VC
        try:
            await vc.delete(reason="AutoVC: VC is empty")
            log.info(f"Deleted empty VC {vc_id} immediately")
        except discord.Forbidden:
            log.warning(f"Permission denied deleting VC {vc_id}")
        except discord.HTTPException as e:
            log.error(f"Error deleting VC {vc_id}: {e}")

        # Remove from configs
        created_vcs = await self.config.guild(guild).created_vcs()
        claimable_vcs = await self.config.guild(guild).claimable_vcs()

        if vc_id_str in created_vcs:
            del created_vcs[vc_id_str]
        if vc_id_str in claimable_vcs:
            del claimable_vcs[vc_id_str]

        await self.config.guild(guild).created_vcs.set(created_vcs)
        await self.config.guild(guild).claimable_vcs.set(claimable_vcs)

    async def cleanup_loop(self):
        """Background task to clean up empty VCs and manage claim timers."""
        await self.bot.wait_until_ready()

        while True:
            try:
                await asyncio.sleep(30)  # Run every 30 seconds

                for guild in self.bot.guilds:
                    try:
                        await self._cleanup_guild_vcs(guild)
                    except Exception as e:
                        log.error(f"Error cleaning up VCs in {guild.name}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in cleanup loop: {e}")

    async def _cleanup_guild_vcs(self, guild: discord.Guild):
        """Clean up empty VCs for a specific guild."""
        created_vcs = await self.config.guild(guild).created_vcs()
        claimable_vcs = await self.config.guild(guild).claimable_vcs()

        vcs_to_delete = []

        for vc_id_str, vc_data in list(created_vcs.items()):
            try:
                vc_id = int(vc_id_str)
                vc = guild.get_channel(vc_id)

                if not vc:
                    # VC was deleted, clean up config
                    vcs_to_delete.append(vc_id_str)
                    continue

                # Check if VC is empty
                if len(vc.members) == 0:
                    vcs_to_delete.append(vc_id_str)

            except (ValueError, KeyError) as e:
                log.error(f"Error processing VC {vc_id_str}: {e}")
                vcs_to_delete.append(vc_id_str)

        # Delete empty VCs
        for vc_id_str in vcs_to_delete:
            vc_id = int(vc_id_str)
            vc = guild.get_channel(vc_id)
            vc_data = created_vcs.get(vc_id_str, {})

            # Delete owner role if it exists
            role_id = vc_data.get("role_id")
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await role.delete(reason="VC deleted, cleaning up owner role")
                    except discord.HTTPException:
                        pass

            # Delete VC if it still exists
            if vc:
                try:
                    await vc.delete(reason="AutoVC: VC is empty")
                    log.info(f"Deleted empty VC {vc_id}")
                except discord.Forbidden:
                    log.warning(f"Permission denied deleting VC {vc_id}")
                except discord.HTTPException as e:
                    log.error(f"Error deleting VC {vc_id}: {e}")

            # Remove from configs
            if vc_id_str in created_vcs:
                del created_vcs[vc_id_str]
            if vc_id_str in claimable_vcs:
                del claimable_vcs[vc_id_str]

        # Update configs
        if vcs_to_delete:
            await self.config.guild(guild).created_vcs.set(created_vcs)
            await self.config.guild(guild).claimable_vcs.set(claimable_vcs)


async def setup(bot: Red):
    """Load the AutoVC cog."""
    cog = AutoVC(bot)
    await bot.add_cog(cog)
    log.info("AutoVC cog loaded")
