import logging
import time
from typing import Dict, List, Optional, Tuple

import discord
from discord.ui import Button, Modal, TextInput, View
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

log = logging.getLogger("red.wzyss-cogs.boostutils")


def _is_booster_role(role: discord.Role) -> bool:
    """Return True when role is the guild booster role."""
    if not role.tags:
        return False
    return getattr(role.tags, "is_premium_subscriber", lambda: False)()


class BoostAnnounceEmbedConfigModal(Modal):
    """Modal for configuring boost announcement embed."""

    def __init__(
        self,
        cog: "BoostUtils",
        existing_data: Optional[Dict] = None,
        builder_view: Optional["BoostAnnounceEmbedBuilderView"] = None,
    ):
        title = "Edit Boost Announcement Embed" if existing_data else "Configure Boost Announcement Embed"
        super().__init__(title=title)
        self.cog = cog
        self.existing_data = existing_data or {}
        self.builder_view = builder_view

        self.title_input = TextInput(
            label="Title",
            placeholder="Thanks for the boost!",
            default=self.existing_data.get("title", ""),
            required=True,
            max_length=256,
        )
        self.add_item(self.title_input)

        self.description_input = TextInput(
            label="Description",
            placeholder="{member} just boosted {guild}!",
            default=self.existing_data.get("description", ""),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.description_input)

        self.color_input = TextInput(
            label="Color (hex, optional)",
            placeholder="#FF73FA",
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

        key = interaction.user.id
        if key not in self.cog._announce_embed_builder_states:
            self.cog._announce_embed_builder_states[key] = {}
        self.cog._announce_embed_builder_states[key]["embed_data"] = embed_data

        await interaction.response.send_message(
            "Embed configuration saved. Use Preview or Save.",
            ephemeral=True,
        )
        if self.builder_view:
            await self.builder_view._refresh_embed(user_id=interaction.user.id)


class BoostAnnounceEmbedBuilderView(View):
    """Interactive embed builder for boost announcement embeds."""

    def __init__(self, cog: "BoostUtils", owner_user_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_user_id = owner_user_id
        self.message: Optional[discord.Message] = None

    def _get_state(self, user_id: int) -> Dict:
        if user_id not in self.cog._announce_embed_builder_states:
            self.cog._announce_embed_builder_states[user_id] = {"embed_data": {}}
        return self.cog._announce_embed_builder_states[user_id]

    async def _refresh_embed(
        self,
        interaction: Optional[discord.Interaction] = None,
        *,
        user_id: Optional[int] = None,
    ):
        user_id = user_id or (interaction.user.id if interaction else self.owner_user_id)
        if not user_id:
            return
        guild = interaction.guild if interaction else (self.message.guild if self.message else None)
        if not guild:
            return

        state = self._get_state(user_id)
        embed_data = state.get("embed_data", {})
        text = await self.cog.config.guild(guild).announce_text()

        embed = discord.Embed(
            title="Boost Announcement Embed Builder",
            description="Use the buttons below to configure, preview, or save.",
            color=await self.cog.bot.get_embed_color(guild),
        )
        embed.add_field(name="Title", value=(embed_data.get("title") or "Not set")[:1024], inline=False)
        embed.add_field(
            name="Description",
            value=(embed_data.get("description") or "Not set")[:1024],
            inline=False,
        )
        embed.add_field(name="Color", value=embed_data.get("color_hex") or "Default", inline=True)
        embed.add_field(name="Footer", value=(embed_data.get("footer") or "Not set")[:256], inline=True)
        embed.add_field(name="Current Text", value=(text or "None")[:1024], inline=False)

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

    @discord.ui.button(label="Configure Embed", style=discord.ButtonStyle.primary, emoji="✍️", row=0)
    async def configure_embed(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        existing = state.get("embed_data", {})
        modal = BoostAnnounceEmbedConfigModal(self.cog, existing, builder_view=self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👁️", row=0)
    async def preview(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        embed_data = state.get("embed_data", {})
        if not embed_data.get("title"):
            await interaction.response.send_message(
                "Set a title first (Configure Embed).",
                ephemeral=True,
            )
            return

        embed = await self.cog._embed_from_announce_data(
            interaction.guild, embed_data, interaction.user
        )
        if not embed:
            await interaction.response.send_message(
                "Configure at least a title for the embed.",
                ephemeral=True,
            )
            return

        text = await self.cog.config.guild(interaction.guild).announce_text()
        content = self.cog._render_announcement_text(text, interaction.user, interaction.guild)
        await interaction.response.send_message(content=content, embed=embed, ephemeral=True)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def save(self, interaction: discord.Interaction, button: Button):
        state = self._get_state(interaction.user.id)
        embed_data = state.get("embed_data", {})
        if not embed_data.get("title"):
            await interaction.response.send_message(
                "Boost announcement embed requires a title. Configure Embed first.",
                ephemeral=True,
            )
            return
        await self.cog.config.guild(interaction.guild).announce_embed.set(embed_data)
        self.cog._announce_embed_builder_states.pop(interaction.user.id, None)
        await interaction.response.send_message(
            "Boost announcement embed saved.",
            ephemeral=True,
        )
        try:
            if self.message:
                await self.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.cog._announce_embed_builder_states.pop(interaction.user.id, None)
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        try:
            if self.message:
                await self.message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass


class BoostUtils(commands.Cog):
    """Booster utilities: tracked roles, restoration, and notifications."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xB0057005, force_registration=True)

        default_guild = {
            "tracked_role_ids": [],
            "linked_role_ids": [],
            "removed_roles_by_user": {},
            "announce_enabled": False,
            "announce_channel_id": None,
            "announce_text": None,
            "announce_embed": None,
            "status_notify_enabled": False,
            "status_notify_channel_id": None,
            "status_notify_ping_role_id": None,
            "dm_notify_enabled": False,
            "dm_added_text": None,
            "dm_removed_text": None,
            "dm_cooldown_seconds": 300,
            "dm_last_sent": {},
        }
        self.config.register_guild(**default_guild)
        self._announce_embed_builder_states: Dict[int, Dict] = {}

    @staticmethod
    def _get_booster_role(guild: discord.Guild) -> Optional[discord.Role]:
        for role in guild.roles:
            if _is_booster_role(role):
                return role
        return None

    @staticmethod
    def _render_template(
        template: Optional[str], member: discord.Member, guild: discord.Guild
    ) -> Optional[str]:
        if not template:
            return None
        try:
            return template.format(
                member=member.mention,
                member_name=member.display_name,
                guild=guild.name,
            )
        except Exception:
            return template

    def _render_announcement_text(
        self, text: Optional[str], member: discord.Member, guild: discord.Guild
    ) -> Optional[str]:
        return self._render_template(text, member, guild)

    @staticmethod
    def _render_dm_text(
        template: Optional[str], member: discord.Member, role: discord.Role
    ) -> Optional[str]:
        if not template:
            return None
        return (
            template.replace("$guildname", f"**{member.guild.name}**")
            .replace("$customrole", f"**{role.name}**")
            .strip()
        )

    async def _send_role_change_dm(
        self, member: discord.Member, role: discord.Role, *, added: bool
    ) -> None:
        conf = self.config.guild(member.guild)
        if not await conf.dm_notify_enabled():
            return
        cooldown_seconds = await conf.dm_cooldown_seconds()
        action_key = "added" if added else "removed"

        now = int(time.time())
        async with conf.dm_last_sent() as dm_last_sent:
            entry = dm_last_sent.get(str(member.id), {})
            last_sent = int(entry.get(action_key, 0) or 0)
            if cooldown_seconds > 0 and (now - last_sent) < cooldown_seconds:
                return

        template = await (conf.dm_added_text() if added else conf.dm_removed_text())
        content = self._render_dm_text(template, member, role)
        if not content:
            return
        try:
            await member.send(content)
            async with conf.dm_last_sent() as dm_last_sent:
                entry = dm_last_sent.get(str(member.id), {})
                entry[action_key] = now
                dm_last_sent[str(member.id)] = entry
        except (discord.Forbidden, discord.HTTPException):
            log.debug(
                "BoostUtils: unable to DM user %s for role change in guild %s",
                member.id,
                member.guild.id,
            )

    async def _embed_from_announce_data(
        self, guild: discord.Guild, embed_data: Optional[Dict], member: discord.Member
    ) -> Optional[discord.Embed]:
        if not embed_data or not embed_data.get("title"):
            return None
        color = embed_data.get("color")
        if color is None:
            color = await self.bot.get_embed_color(guild)
        embed = discord.Embed(
            title=(embed_data.get("title") or "")[:256],
            description=(
                self._render_template(embed_data.get("description"), member, guild) or ""
            )[:4096],
            color=color,
        )
        if embed_data.get("footer"):
            embed.set_footer(
                text=(self._render_template(embed_data["footer"], member, guild) or "")[:2048]
            )
        if embed_data.get("thumbnail_url"):
            embed.set_thumbnail(url=embed_data["thumbnail_url"])
        return embed

    async def _get_valid_tracked_roles(self, guild: discord.Guild) -> List[discord.Role]:
        role_ids = await self.config.guild(guild).tracked_role_ids()
        roles: List[discord.Role] = []
        valid_ids: List[int] = []
        changed = False

        for rid in role_ids:
            role = guild.get_role(rid)
            if role is None:
                changed = True
                continue
            valid_ids.append(rid)
            roles.append(role)

        if changed:
            await self.config.guild(guild).tracked_role_ids.set(valid_ids)
        return roles

    async def _get_valid_linked_roles(self, guild: discord.Guild) -> List[discord.Role]:
        role_ids = await self.config.guild(guild).linked_role_ids()
        roles: List[discord.Role] = []
        valid_ids: List[int] = []
        changed = False

        for rid in role_ids:
            role = guild.get_role(rid)
            if role is None:
                changed = True
                continue
            valid_ids.append(rid)
            roles.append(role)

        if changed:
            await self.config.guild(guild).linked_role_ids.set(valid_ids)
        return roles

    async def _has_entitlement(
        self, member: discord.Member, booster_role: Optional[discord.Role]
    ) -> bool:
        if self._is_boosting(member, booster_role):
            return True
        linked_roles = await self._get_valid_linked_roles(member.guild)
        return any(role in member.roles for role in linked_roles)

    @staticmethod
    def _is_boosting(member: discord.Member, booster_role: Optional[discord.Role]) -> bool:
        # premium_since is the most direct signal for boost state and is reliable
        # even when the premium_subscriber role cannot be resolved transiently.
        if getattr(member, "premium_since", None) is not None:
            return True
        return bool(booster_role and booster_role in member.roles)

    async def _collect_compliance_snapshot(
        self, guild: discord.Guild
    ) -> Optional[Dict[str, object]]:
        tracked_roles = await self._get_valid_tracked_roles(guild)
        if not tracked_roles:
            return None

        linked_roles = await self._get_valid_linked_roles(guild)
        linked_ids = {r.id for r in linked_roles}
        booster_role = self._get_booster_role(guild)
        booster_id = booster_role.id if booster_role else None

        tracked_holders: Dict[int, List[discord.Role]] = {}
        for role in tracked_roles:
            for member in role.members:
                if member.bot:
                    continue
                tracked_holders.setdefault(member.id, [])
                tracked_holders[member.id].append(role)

        rows: List[Dict[str, object]] = []
        member_cache = {m.id: m for m in guild.members}
        for member_id, roles in tracked_holders.items():
            member = member_cache.get(member_id)
            if not member:
                continue
            role_ids = {r.id for r in member.roles}
            has_booster = booster_id in role_ids if booster_id else False
            has_linked = bool(role_ids.intersection(linked_ids))
            entitled = has_booster or has_linked

            if has_booster:
                reason = "booster"
            elif has_linked:
                reason = "linked role"
            else:
                reason = "missing entitlement"

            rows.append(
                {
                    "member": member,
                    "tracked_roles": sorted(roles, key=lambda x: x.position, reverse=True),
                    "entitled": entitled,
                    "reason": reason,
                }
            )

        return {
            "tracked_roles": tracked_roles,
            "linked_roles": linked_roles,
            "rows": rows,
        }

    async def _remove_tracked_roles(
        self,
        member: discord.Member,
        *,
        save_for_restore: bool,
        reason: str,
    ) -> Tuple[int, int]:
        tracked_roles = await self._get_valid_tracked_roles(member.guild)
        to_remove = [r for r in tracked_roles if r in member.roles]
        if not to_remove:
            return 0, 0

        removed_count = 0
        failed_count = 0
        removed_ids: List[int] = []

        for role in to_remove:
            try:
                await member.remove_roles(role, reason=reason)
                removed_count += 1
                removed_ids.append(role.id)
                await self._send_role_change_dm(member, role, added=False)
            except discord.Forbidden:
                failed_count += 1
                log.warning(
                    "BoostUtils: cannot remove tracked role %s from user %s in guild %s",
                    role.id,
                    member.id,
                    member.guild.id,
                )
            except discord.HTTPException:
                failed_count += 1
                log.warning(
                    "BoostUtils: HTTP error removing tracked role %s from user %s in guild %s",
                    role.id,
                    member.id,
                    member.guild.id,
                )

        if save_for_restore and removed_ids:
            async with self.config.guild(member.guild).removed_roles_by_user() as data:
                key = str(member.id)
                previous = data.get(key, [])
                merged = list(dict.fromkeys(previous + removed_ids))
                data[key] = merged

        return removed_count, failed_count

    async def _restore_removed_roles(self, member: discord.Member) -> Tuple[int, int]:
        tracked_roles = await self._get_valid_tracked_roles(member.guild)
        tracked_ids = {r.id for r in tracked_roles}
        role_lookup = {r.id: r for r in tracked_roles}

        async with self.config.guild(member.guild).removed_roles_by_user() as data:
            key = str(member.id)
            stored = data.get(key, [])
            if not stored:
                return 0, 0

            restored_count = 0
            failed_count = 0
            remaining: List[int] = []

            for rid in stored:
                if rid not in tracked_ids:
                    continue
                role = role_lookup.get(rid)
                if role is None:
                    continue
                if role in member.roles:
                    continue
                try:
                    await member.add_roles(role, reason="Server booster role regained")
                    restored_count += 1
                    await self._send_role_change_dm(member, role, added=True)
                except discord.Forbidden:
                    failed_count += 1
                    remaining.append(rid)
                    log.warning(
                        "BoostUtils: cannot restore tracked role %s to user %s in guild %s",
                        rid,
                        member.id,
                        member.guild.id,
                    )
                except discord.HTTPException:
                    failed_count += 1
                    remaining.append(rid)
                    log.warning(
                        "BoostUtils: HTTP error restoring tracked role %s to user %s in guild %s",
                        rid,
                        member.id,
                        member.guild.id,
                    )

            if remaining:
                data[key] = list(dict.fromkeys(remaining))
            else:
                data.pop(key, None)

            return restored_count, failed_count

    async def _send_boost_announcement(self, member: discord.Member):
        guild_conf = self.config.guild(member.guild)
        if not await guild_conf.announce_enabled():
            return

        channel_id = await guild_conf.announce_channel_id()
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        text = await guild_conf.announce_text()
        embed_data = await guild_conf.announce_embed()

        content = self._render_announcement_text(text, member, member.guild)
        embed = await self._embed_from_announce_data(member.guild, embed_data, member)

        if not content and embed is None:
            return

        try:
            await channel.send(content=content, embed=embed)
        except discord.HTTPException:
            log.warning(
                "BoostUtils: failed to send boost announcement in channel %s (guild %s)",
                channel_id,
                member.guild.id,
            )

    async def _send_status_notification(self, member: discord.Member, *, event: str):
        guild_conf = self.config.guild(member.guild)
        if not await guild_conf.status_notify_enabled():
            return

        channel_id = await guild_conf.status_notify_channel_id()
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        ping_role_id = await guild_conf.status_notify_ping_role_id()
        ping_mention = ""
        allowed_mentions = discord.AllowedMentions(users=True, roles=False)

        if ping_role_id:
            ping_role = member.guild.get_role(ping_role_id)
            if ping_role:
                ping_mention = ping_role.mention + " "
                allowed_mentions = discord.AllowedMentions(users=True, roles=[ping_role])

        if event == "booster_gained":
            text = f"{ping_mention}{member.mention} started boosting the server."
        elif event == "booster_lost":
            text = f"{ping_mention}{member.mention} is no longer boosting the server."
        elif event == "linked_gained":
            text = f"{ping_mention}{member.mention} gained linked-role entitlement."
        elif event == "linked_lost":
            text = f"{ping_mention}{member.mention} lost linked-role entitlement."
        else:
            return

        try:
            await channel.send(text, allowed_mentions=allowed_mentions)
        except discord.HTTPException:
            log.warning(
                "BoostUtils: failed to send status notification in channel %s (guild %s)",
                channel_id,
                member.guild.id,
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.guild is None or after.guild is None:
            return
        if before.bot or after.bot:
            return
        if before.roles == after.roles:
            return

        guild = after.guild
        booster_role = self._get_booster_role(guild)
        linked_roles = await self._get_valid_linked_roles(guild)
        linked_role_ids = {role.id for role in linked_roles}
        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}

        had_booster = self._is_boosting(before, booster_role)
        has_booster = self._is_boosting(after, booster_role)
        had_linked = bool(before_role_ids.intersection(linked_role_ids))
        has_linked = bool(after_role_ids.intersection(linked_role_ids))
        had_entitlement = had_booster or had_linked
        has_entitlement = has_booster or has_linked

        # Any entitlement loss (booster and linked roles no longer present).
        if had_entitlement and not has_entitlement:
            await self._remove_tracked_roles(
                after,
                save_for_restore=True,
                reason="Server booster/linked role entitlement removed",
            )

        # Any entitlement gain (server booster role or linked role granted).
        if not had_entitlement and has_entitlement:
            await self._restore_removed_roles(after)

        # Enforce tracked role requirement at all times.
        if not has_entitlement:
            await self._remove_tracked_roles(
                after,
                save_for_restore=False,
                reason="Tracked role requires booster or linked-role entitlement",
            )

        # Booster-only events still control boost announcement and status notifications.
        if had_booster and not has_booster:
            await self._send_status_notification(after, event="booster_lost")

        if not had_booster and has_booster:
            await self._send_boost_announcement(after)
            await self._send_status_notification(after, event="booster_gained")

        # Linked-role status notifications are separate from boost announcements.
        if had_linked and not has_linked:
            await self._send_status_notification(after, event="linked_lost")

        if not had_linked and has_linked:
            await self._send_status_notification(after, event="linked_gained")

    @commands.group(name="boostutils")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def _boostutils(self, ctx: commands.Context):
        """Manage booster-dependent roles and boost notifications."""

    @_boostutils.group(name="role")
    async def _role(self, ctx: commands.Context):
        """Manage tracked custom roles that require entitlement."""

    @_role.command(name="add")
    async def _role_add(self, ctx: commands.Context, role: discord.Role):
        role_ids = await self.config.guild(ctx.guild).tracked_role_ids()
        if role.id in role_ids:
            await ctx.send(f"{role.mention} is already a tracked role.")
            return
        role_ids.append(role.id)
        await self.config.guild(ctx.guild).tracked_role_ids.set(role_ids)
        await ctx.send(f"Added {role.mention} as a tracked custom role.")

    @_role.command(name="remove")
    async def _role_remove(self, ctx: commands.Context, role: discord.Role):
        role_ids = await self.config.guild(ctx.guild).tracked_role_ids()
        if role.id not in role_ids:
            await ctx.send(f"{role.mention} is not a tracked role.")
            return
        role_ids = [rid for rid in role_ids if rid != role.id]
        await self.config.guild(ctx.guild).tracked_role_ids.set(role_ids)
        await ctx.send(f"Removed {role.mention} from tracked custom roles.")

    @_role.command(name="list")
    async def _role_list(self, ctx: commands.Context):
        roles = await self._get_valid_tracked_roles(ctx.guild)
        if not roles:
            await ctx.send("No tracked custom roles configured.")
            return
        lines = []
        for role in sorted(roles, key=lambda r: r.position, reverse=True):
            member_count = len([m for m in role.members if not m.bot])
            lines.append(f"- {role.mention} (`{role.id}`) - {member_count} member(s)")
        await ctx.send("\n".join(lines))

    @_boostutils.group(name="linkedrole")
    async def _linkedrole(self, ctx: commands.Context):
        """Manage linked roles that count as booster-equivalent entitlement."""

    @_linkedrole.command(name="add")
    async def _linkedrole_add(self, ctx: commands.Context, role: discord.Role):
        role_ids = await self.config.guild(ctx.guild).linked_role_ids()
        if role.id in role_ids:
            await ctx.send(f"{role.mention} is already a linked role.")
            return
        role_ids.append(role.id)
        await self.config.guild(ctx.guild).linked_role_ids.set(role_ids)
        await ctx.send(f"Added {role.mention} as a linked entitlement role.")

    @_linkedrole.command(name="remove")
    async def _linkedrole_remove(self, ctx: commands.Context, role: discord.Role):
        role_ids = await self.config.guild(ctx.guild).linked_role_ids()
        if role.id not in role_ids:
            await ctx.send(f"{role.mention} is not a linked role.")
            return
        role_ids = [rid for rid in role_ids if rid != role.id]
        await self.config.guild(ctx.guild).linked_role_ids.set(role_ids)
        await ctx.send(f"Removed {role.mention} from linked entitlement roles.")

    @_linkedrole.command(name="list")
    async def _linkedrole_list(self, ctx: commands.Context):
        roles = await self._get_valid_linked_roles(ctx.guild)
        if not roles:
            await ctx.send("No linked entitlement roles configured.")
            return
        lines = []
        for role in sorted(roles, key=lambda r: r.position, reverse=True):
            member_count = len([m for m in role.members if not m.bot])
            lines.append(f"- {role.mention} (`{role.id}`) - {member_count} member(s)")
        await ctx.send("\n".join(lines))

    @_boostutils.command(name="list")
    async def _list_members(self, ctx: commands.Context):
        """List tracked custom roles and members currently holding them."""
        roles = await self._get_valid_tracked_roles(ctx.guild)
        if not roles:
            await ctx.send("No tracked custom roles configured.")
            return

        lines: List[str] = []
        for role in sorted(roles, key=lambda r: r.position, reverse=True):
            members = [m for m in role.members if not m.bot]
            if members:
                member_str = ", ".join(m.mention for m in members)
            else:
                member_str = "None"
            lines.append(f"{role.name} ({role.id})")
            lines.append(f"Members ({len(members)}): {member_str}")
            lines.append("")

        output = "\n".join(lines).strip()
        for page in pagify(output, page_length=1800):
            await ctx.send(page)

    @_boostutils.group(name="check", invoke_without_command=True)
    async def _check(self, ctx: commands.Context):
        """Run a live compliance check for tracked-role members."""
        snapshot = await self._collect_compliance_snapshot(ctx.guild)
        if snapshot is None:
            await ctx.send("No tracked custom roles configured.")
            return

        rows = snapshot["rows"]
        tracked_holders_count = len(rows)

        if tracked_holders_count == 0:
            await ctx.send("Live check complete: no members currently hold tracked custom roles.")
            return

        compliant: List[str] = []
        non_compliant: List[str] = []
        for row in rows:
            member = row["member"]
            roles = row["tracked_roles"]
            entitled = row["entitled"]
            reason = row["reason"]
            tracked_label = ", ".join(r.name for r in roles)
            if entitled:
                compliant.append(f"- {member.mention}: OK via {reason} | tracked: {tracked_label}")
            else:
                non_compliant.append(
                    f"- {member.mention}: missing entitlement | tracked: {tracked_label}"
                )

        summary = (
            f"Live check complete.\n"
            f"- Members with tracked roles: {tracked_holders_count}\n"
            f"- Compliant: {len(compliant)}\n"
            f"- Non-compliant: {len(non_compliant)}"
        )
        await ctx.send(summary)

        if non_compliant:
            report = "Non-compliant members:\n" + "\n".join(non_compliant)
            for page in pagify(report, page_length=1800):
                await ctx.send(page)
        else:
            await ctx.send("No non-compliant members found.")

    @_check.command(name="verbose")
    async def _check_verbose(self, ctx: commands.Context):
        """Run live check and print each tracked role with member compliance."""
        snapshot = await self._collect_compliance_snapshot(ctx.guild)
        if snapshot is None:
            await ctx.send("No tracked custom roles configured.")
            return

        rows = snapshot["rows"]
        tracked_roles = snapshot["tracked_roles"]
        if not rows:
            await ctx.send("Live check complete: no members currently hold tracked custom roles.")
            return

        row_lookup = {row["member"].id: row for row in rows}
        lines: List[str] = ["Live check (verbose):"]
        for role in sorted(tracked_roles, key=lambda r: r.position, reverse=True):
            members = [m for m in role.members if not m.bot]
            lines.append("")
            lines.append(f"{role.mention} ({role.id}) - {len(members)} member(s)")
            if not members:
                lines.append("- None")
                continue
            for member in sorted(members, key=lambda m: m.display_name.lower()):
                row = row_lookup.get(member.id)
                if not row:
                    lines.append(f"- {member.mention}: unknown")
                    continue
                status = "OK" if row["entitled"] else "NOT OK"
                reason = row["reason"]
                lines.append(f"- {member.mention}: {status} ({reason})")

        for page in pagify("\n".join(lines), page_length=1800):
            await ctx.send(
                page,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False),
            )

    @_check.command(name="run")
    async def _check_run(self, ctx: commands.Context):
        """Manually run reconciliation and update member roles."""
        snapshot = await self._collect_compliance_snapshot(ctx.guild)
        if snapshot is None:
            await ctx.send("No tracked custom roles configured.")
            return

        rows = snapshot["rows"]
        if not rows:
            await ctx.send("Run complete: no members currently hold tracked custom roles.")
            return

        removed_total = 0
        remove_failed_total = 0
        restored_total = 0
        restore_failed_total = 0

        for row in rows:
            member = row["member"]
            entitled = row["entitled"]
            if entitled:
                restored, restore_failed = await self._restore_removed_roles(member)
                restored_total += restored
                restore_failed_total += restore_failed
            else:
                removed, remove_failed = await self._remove_tracked_roles(
                    member,
                    save_for_restore=True,
                    reason="BoostUtils manual check run: missing entitlement",
                )
                removed_total += removed
                remove_failed_total += remove_failed

        summary = (
            "Manual run complete.\n"
            f"- Members checked: {len(rows)}\n"
            f"- Roles removed: {removed_total}\n"
            f"- Removal failures: {remove_failed_total}\n"
            f"- Roles restored: {restored_total}\n"
            f"- Restore failures: {restore_failed_total}"
        )
        await ctx.send(summary)

    @_boostutils.group(name="announce")
    async def _announce(self, ctx: commands.Context):
        """Configure boost gain announcement messages."""

    @_announce.command(name="toggle")
    async def _announce_toggle(self, ctx: commands.Context, enabled: bool):
        await self.config.guild(ctx.guild).announce_enabled.set(enabled)
        await ctx.send(f"Boost announcement is now set to `{enabled}`.")

    @_announce.command(name="channel")
    async def _announce_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        if channel is None:
            await self.config.guild(ctx.guild).announce_channel_id.set(None)
            await ctx.send("Boost announcement channel cleared.")
            return
        await self.config.guild(ctx.guild).announce_channel_id.set(channel.id)
        await ctx.send(f"Boost announcement channel set to {channel.mention}.")

    @_announce.command(name="text")
    async def _announce_text(self, ctx: commands.Context, *, text: Optional[str] = None):
        if text is None:
            await self.config.guild(ctx.guild).announce_text.set(None)
            await ctx.send("Boost announcement text cleared.")
            return
        await self.config.guild(ctx.guild).announce_text.set(text)
        await ctx.send(
            "Boost announcement text saved. Supported tokens: "
            "`{member}`, `{member_name}`, `{guild}`."
        )

    @_announce.command(name="embed")
    async def _announce_embed_builder(self, ctx: commands.Context):
        existing = await self.config.guild(ctx.guild).announce_embed()
        if existing is None:
            existing = {}
        self._announce_embed_builder_states[ctx.author.id] = {"embed_data": existing}
        view = BoostAnnounceEmbedBuilderView(self, owner_user_id=ctx.author.id)
        embed = discord.Embed(
            title="Boost Announcement Embed Builder",
            description="Use the buttons below to configure, preview, or save.",
            color=await ctx.embed_color(),
        )
        if existing and existing.get("title"):
            embed.add_field(
                name="Current title",
                value=existing.get("title", "Not set")[:1024],
                inline=False,
            )
        view.message = await ctx.send(embed=embed, view=view)
        await view._refresh_embed(user_id=ctx.author.id)

    @_announce.command(name="embedclear")
    async def _announce_embed_clear(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).announce_embed.set(None)
        await ctx.send("Boost announcement embed cleared.")

    @_boostutils.group(name="statusnotify")
    async def _statusnotify(self, ctx: commands.Context):
        """Configure booster status-change notifications."""

    @_statusnotify.command(name="toggle")
    async def _statusnotify_toggle(self, ctx: commands.Context, enabled: bool):
        await self.config.guild(ctx.guild).status_notify_enabled.set(enabled)
        await ctx.send(f"Booster status notifications are now set to `{enabled}`.")

    @_statusnotify.command(name="channel")
    async def _statusnotify_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        if channel is None:
            await self.config.guild(ctx.guild).status_notify_channel_id.set(None)
            await ctx.send("Booster status notification channel cleared.")
            return
        await self.config.guild(ctx.guild).status_notify_channel_id.set(channel.id)
        await ctx.send(f"Booster status notification channel set to {channel.mention}.")

    @_statusnotify.command(name="ping")
    async def _statusnotify_ping(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        if role is None:
            await self.config.guild(ctx.guild).status_notify_ping_role_id.set(None)
            await ctx.send("Booster status notification ping role cleared.")
            return
        await self.config.guild(ctx.guild).status_notify_ping_role_id.set(role.id)
        await ctx.send(f"Booster status notification ping role set to {role.mention}.")

    @_boostutils.group(name="dm")
    async def _dm(self, ctx: commands.Context):
        """Configure optional member DM notifications for role add/remove."""

    @_dm.command(name="toggle")
    async def _dm_toggle(self, ctx: commands.Context, enabled: bool):
        await self.config.guild(ctx.guild).dm_notify_enabled.set(enabled)
        await ctx.send(f"DM role-change notifications are now set to `{enabled}`.")

    @_dm.command(name="added")
    async def _dm_added(self, ctx: commands.Context, *, text: Optional[str] = None):
        if text is None:
            await self.config.guild(ctx.guild).dm_added_text.set(None)
            await ctx.send("DM added-role template cleared.")
            return
        await self.config.guild(ctx.guild).dm_added_text.set(text)
        await ctx.send("DM added-role template saved. Variables: `$guildname`, `$customrole`.")

    @_dm.command(name="removed")
    async def _dm_removed(self, ctx: commands.Context, *, text: Optional[str] = None):
        if text is None:
            await self.config.guild(ctx.guild).dm_removed_text.set(None)
            await ctx.send("DM removed-role template cleared.")
            return
        await self.config.guild(ctx.guild).dm_removed_text.set(text)
        await ctx.send("DM removed-role template saved. Variables: `$guildname`, `$customrole`.")

    @_dm.command(name="cooldown")
    async def _dm_cooldown(self, ctx: commands.Context, seconds: Optional[int] = None):
        """Set DM cooldown seconds per member per action (added/removed)."""
        conf = self.config.guild(ctx.guild)
        if seconds is None:
            current = await conf.dm_cooldown_seconds()
            await ctx.send(f"DM cooldown is currently `{current}` second(s).")
            return
        if seconds < 0:
            await ctx.send("Cooldown must be 0 or greater.")
            return
        await conf.dm_cooldown_seconds.set(seconds)
        await ctx.send(f"DM cooldown set to `{seconds}` second(s).")

    @_boostutils.command(name="show")
    async def _show(self, ctx: commands.Context):
        guild_conf = self.config.guild(ctx.guild)
        tracked_roles = await self._get_valid_tracked_roles(ctx.guild)
        linked_roles = await self._get_valid_linked_roles(ctx.guild)

        announce_enabled = await guild_conf.announce_enabled()
        announce_channel_id = await guild_conf.announce_channel_id()
        announce_text = await guild_conf.announce_text()
        announce_embed = await guild_conf.announce_embed()

        status_enabled = await guild_conf.status_notify_enabled()
        status_channel_id = await guild_conf.status_notify_channel_id()
        status_ping_role_id = await guild_conf.status_notify_ping_role_id()
        dm_enabled = await guild_conf.dm_notify_enabled()
        dm_added_text = await guild_conf.dm_added_text()
        dm_removed_text = await guild_conf.dm_removed_text()
        dm_cooldown_seconds = await guild_conf.dm_cooldown_seconds()

        announce_channel = ctx.guild.get_channel(announce_channel_id) if announce_channel_id else None
        status_channel = ctx.guild.get_channel(status_channel_id) if status_channel_id else None
        status_ping_role = ctx.guild.get_role(status_ping_role_id) if status_ping_role_id else None

        embed = discord.Embed(title="BoostUtils settings", color=await ctx.embed_color())
        embed.add_field(name="Tracked roles", value=str(len(tracked_roles)), inline=True)
        embed.add_field(name="Linked roles", value=str(len(linked_roles)), inline=True)
        embed.add_field(name="Announce enabled", value=str(announce_enabled), inline=True)
        embed.add_field(
            name="Announce channel",
            value=announce_channel.mention if announce_channel else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Announcement text",
            value=(announce_text[:1024] if announce_text else "None"),
            inline=False,
        )
        embed.add_field(
            name="Announcement embed",
            value="Configured" if announce_embed and announce_embed.get("title") else "None",
            inline=True,
        )
        embed.add_field(name="Status notify enabled", value=str(status_enabled), inline=True)
        embed.add_field(
            name="Status notify channel",
            value=status_channel.mention if status_channel else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Status notify ping role",
            value=status_ping_role.mention if status_ping_role else "None",
            inline=True,
        )
        embed.add_field(name="DM notify enabled", value=str(dm_enabled), inline=True)
        embed.add_field(
            name="DM added template",
            value=(dm_added_text[:1024] if dm_added_text else "None"),
            inline=False,
        )
        embed.add_field(
            name="DM removed template",
            value=(dm_removed_text[:1024] if dm_removed_text else "None"),
            inline=False,
        )
        embed.add_field(
            name="DM cooldown seconds",
            value=str(dm_cooldown_seconds),
            inline=True,
        )

        booster_role = self._get_booster_role(ctx.guild)
        embed.add_field(
            name="Detected booster role",
            value=booster_role.mention if booster_role else "Not found",
            inline=True,
        )
        tracked_roles_text = ", ".join(r.mention for r in tracked_roles) if tracked_roles else "None"
        linked_roles_text = ", ".join(r.mention for r in linked_roles) if linked_roles else "None"
        embed.add_field(name="Configured tracked roles", value=tracked_roles_text[:1024], inline=False)
        embed.add_field(name="Configured linked roles", value=linked_roles_text[:1024], inline=False)

        await ctx.send(embed=embed)


async def setup(bot: Red):
    cog = BoostUtils(bot)
    await bot.add_cog(cog)
