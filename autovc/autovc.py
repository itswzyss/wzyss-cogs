import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.wzyss-cogs.autovc")


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

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_loop())

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

    @commands.group(name="autovc", aliases=["avc"])
    @commands.guild_only()
    async def _autovc(self, ctx: commands.Context):
        """AutoVC management commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @_autovc.command(name="add")
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
        - [p]autovc add #Create Public public
        - [p]autovc add #Create Personal personal
        - [p]autovc add #Create Private private #Private-VCs
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

    @_autovc.command(name="remove", aliases=["delete", "del"])
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

    @_autovc.command(name="list")
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

    @_autovc.command(name="settings")
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

    @_autovc.command(name="memberrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def _set_member_role(
        self, ctx: commands.Context, role: Optional[discord.Role] = None
    ):
        """Set the @Member role for permission handling.
        
        Use without a role to clear and use @everyone instead.
        Example: [p]autovc memberrole @Member
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
                await ctx.send(
                    "You must be in a voice channel or specify one to claim."
                )
                return

        # Check if VC is tracked
        created_vcs = await self.config.guild(guild).created_vcs()
        vc_data = created_vcs.get(str(vc.id))

        if not vc_data:
            await ctx.send("This VC is not managed by AutoVC.")
            return

        # Check if VC is claimable
        claimable_vcs = await self.config.guild(guild).claimable_vcs()
        claim_data = claimable_vcs.get(str(vc.id))

        if not claim_data:
            owner_id = vc_data.get("owner_id")
            if owner_id:
                owner = guild.get_member(owner_id)
                if owner and owner.voice and owner.voice.channel == vc:
                    await ctx.send(
                        "This VC already has an owner who is still in the channel."
                    )
                    return
            await ctx.send(
                "This VC is not available for claiming. The owner may still be present, "
                "or the 5-minute waiting period hasn't passed yet."
            )
            return

        # Check if 5 minutes have passed
        owner_left_at = datetime.fromisoformat(claim_data["owner_left_at"])
        now = datetime.utcnow()
        time_passed = (now - owner_left_at).total_seconds()

        if time_passed < 300:  # 5 minutes
            remaining = int(300 - time_passed)
            await ctx.send(
                f"You must wait {remaining} more seconds before claiming this VC."
            )
            return

        # Transfer ownership
        await self._transfer_ownership(guild, vc, member, vc_data)

        # Remove from claimable list
        del claimable_vcs[str(vc.id)]
        await self.config.guild(guild).claimable_vcs.set(claimable_vcs)

        await ctx.send(
            f"You have successfully claimed ownership of {vc.mention}!"
        )

    async def _transfer_ownership(
        self,
        guild: discord.Guild,
        vc: discord.VoiceChannel,
        new_owner: discord.Member,
        vc_data: dict,
    ):
        """Transfer ownership of a VC to a new owner."""
        old_role_id = vc_data.get("role_id")
        vc_type = vc_data.get("type")

        # Delete old owner role if it exists
        if old_role_id:
            old_role = guild.get_role(old_role_id)
            if old_role:
                try:
                    await old_role.delete(reason="VC ownership transferred")
                except discord.HTTPException:
                    log.warning(f"Failed to delete old owner role {old_role_id}")

        # Create new owner role
        if vc_type in ["personal", "private"]:
            role = await self._create_owner_role(guild, new_owner, vc)
            if role:
                vc_data["role_id"] = role.id
                vc_data["owner_id"] = new_owner.id

                # Update config
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

        # Generate VC name
        username = member.display_name.replace(" ", "-").lower()[:20]
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

            # Create owner role for personal/private VCs
            owner_role = None
            if vc_type in ["personal", "private"]:
                owner_role = await self._create_owner_role(guild, member, vc)

            # Track the VC
            created_vcs = await self.config.guild(guild).created_vcs()
            created_vcs[str(vc.id)] = {
                "source_vc_id": source_vc.id,
                "owner_id": member.id if vc_type in ["personal", "private"] else None,
                "role_id": owner_role.id if owner_role else None,
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
