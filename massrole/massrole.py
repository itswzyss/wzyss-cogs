import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.wzyss-cogs.massrole")


class MassRole(commands.Cog):
    """Assign roles to all members of a role or everyone on the server."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543215, force_registration=True
        )

    async def _add_role_with_retry(
        self,
        member: discord.Member,
        role: discord.Role,
        reason: str = "Mass role assignment",
        max_retries: int = 5,
    ) -> Tuple[bool, Optional[str]]:
        """Add role to member with retry logic for rate limits.
        
        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        for attempt in range(max_retries):
            try:
                await member.add_roles(role, reason=reason)
                return True, None
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    # Try to get retry_after from response headers
                    retry_after = getattr(e, "retry_after", None)
                    if retry_after is None:
                        # Exponential backoff: 2s, 4s, 8s, 16s, 32s
                        wait_time = 2 ** (attempt + 1)
                    else:
                        # Use Discord's suggested wait time, add a small buffer
                        wait_time = retry_after + 1.0
                    
                    log.debug(
                        f"Rate limited adding role {role.name} to {member}, "
                        f"waiting {wait_time:.1f}s before retry {attempt + 1}/{max_retries}"
                    )
                    await asyncio.sleep(wait_time)
                elif e.status == 403:
                    return False, "Missing permissions"
                elif e.status == 404:
                    return False, "Member or role not found"
                else:
                    log.warning(f"HTTP error adding role: {e.status} - {e}")
                    return False, f"HTTP {e.status}"
            except discord.Forbidden:
                return False, "Missing permissions"
            except discord.NotFound:
                return False, "Member or role not found"
            except Exception as e:
                log.error(f"Unexpected error adding role: {e}", exc_info=True)
                return False, str(e)
        
        return False, "Max retries exceeded"

    async def _assign_role_to_members(
        self,
        ctx: commands.Context,
        members: List[discord.Member],
        role: discord.Role,
        reason: str = "Mass role assignment",
    ) -> Dict[str, int]:
        """Assign role to a list of members with progress tracking.
        
        Returns:
            dict with counts: {"success": int, "failed": int, "skipped": int, "errors": dict}
        """
        results = {
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "errors": {},  # error_type -> count
        }
        
        total = len(members)
        if total == 0:
            return results
        
        # Send initial progress message
        progress_msg = await ctx.send(
            f"Starting role assignment: {role.mention}\n"
            f"Processing {total} members...\n"
            f"Progress: 0/{total} (0%)"
        )
        
        processed = 0
        last_update = 0
        
        for member in members:
            # Check if member already has the role
            if role in member.roles:
                results["skipped"] += 1
                processed += 1
                continue
            
            # Add role with retry
            success, error = await self._add_role_with_retry(
                member, role, reason=reason
            )
            
            if success:
                results["success"] += 1
            else:
                results["failed"] += 1
                error_type = error or "Unknown error"
                results["errors"][error_type] = results["errors"].get(error_type, 0) + 1
            
            processed += 1
            
            # Small delay between requests to avoid hitting rate limits
            # Discord allows 10 requests per 10 seconds, so 1.1s delay keeps us safe
            if processed < total:
                await asyncio.sleep(1.1)
            
            # Update progress message every 10 members or every 5 seconds
            should_update = (
                processed - last_update >= 10
                or processed == total
            )
            
            if should_update:
                percentage = (processed / total) * 100
                status_lines = [
                    f"Role assignment: {role.mention}",
                    f"Progress: {processed}/{total} ({percentage:.1f}%)",
                    f"✅ Success: {results['success']}",
                    f"⏭️ Skipped (already has role): {results['skipped']}",
                    f"❌ Failed: {results['failed']}",
                ]
                
                if results["errors"]:
                    error_summary = ", ".join(
                        f"{k}: {v}" for k, v in list(results["errors"].items())[:3]
                    )
                    if len(results["errors"]) > 3:
                        error_summary += "..."
                    status_lines.append(f"Errors: {error_summary}")
                
                try:
                    await progress_msg.edit(content="\n".join(status_lines))
                except discord.NotFound:
                    # Message was deleted, create a new one
                    progress_msg = await ctx.send("\n".join(status_lines))
                except Exception as e:
                    log.debug(f"Could not update progress message: {e}")
                
                last_update = processed
        
        return results

    @commands.group(name="massrole", aliases=["mr"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def _massrole(self, ctx: commands.Context):
        """Mass role assignment commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @_massrole.command(name="torole", aliases=["tr"])
    async def _assign_to_role(
        self,
        ctx: commands.Context,
        target_role: discord.Role,
        role_to_assign: discord.Role,
    ):
        """Assign a role to all members who have a specific role.
        
        Example: [p]massrole torole @Members @Access
        This will assign the @Access role to everyone who has the @Members role.
        """
        # Check bot permissions
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send("❌ I don't have permission to manage roles.")
            return
        
        # Check role hierarchy
        if ctx.guild.me.top_role <= target_role:
            await ctx.send(
                f"❌ I cannot assign roles to members with {target_role.mention} "
                f"because it is higher than or equal to my highest role."
            )
            return
        
        if ctx.guild.me.top_role <= role_to_assign:
            await ctx.send(
                f"❌ I cannot assign {role_to_assign.mention} because it is higher "
                f"than or equal to my highest role."
            )
            return
        
        # Get all members with the target role
        members = [m for m in ctx.guild.members if target_role in m.roles]
        
        if not members:
            await ctx.send(
                f"❌ No members found with the role {target_role.mention}."
            )
            return
        
        # Confirm action
        confirm_msg = await ctx.send(
            f"⚠️ **Confirm Role Assignment**\n\n"
            f"**Target Role:** {target_role.mention} ({len(members)} members)\n"
            f"**Role to Assign:** {role_to_assign.mention}\n\n"
            f"This will assign {role_to_assign.mention} to all {len(members)} members "
            f"who currently have {target_role.mention}.\n\n"
            f"React with ✅ to confirm, or ❌ to cancel."
        )
        
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")
        
        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == confirm_msg.id
                and str(reaction.emoji) in ["✅", "❌"]
            )
        
        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=60.0, check=check
            )
        except asyncio.TimeoutError:
            await confirm_msg.edit(
                content="❌ Confirmation timed out. Operation cancelled."
            )
            return
        
        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(content="❌ Operation cancelled.")
            return
        
        # Remove reactions
        try:
            await confirm_msg.clear_reactions()
        except:
            pass
        
        # Perform assignment
        reason = f"Mass role assignment by {ctx.author} (from {target_role.name})"
        results = await self._assign_role_to_members(
            ctx, members, role_to_assign, reason=reason
        )
        
        # Send final summary
        summary_lines = [
            f"**Role Assignment Complete**",
            f"**Role Assigned:** {role_to_assign.mention}",
            f"**Target Role:** {target_role.mention}",
            "",
            f"✅ **Success:** {results['success']}",
            f"⏭️ **Skipped:** {results['skipped']} (already had role)",
            f"❌ **Failed:** {results['failed']}",
        ]
        
        if results["errors"]:
            summary_lines.append("")
            summary_lines.append("**Errors:**")
            for error_type, count in results["errors"].items():
                summary_lines.append(f"- {error_type}: {count}")
        
        summary = "\n".join(summary_lines)
        for page in pagify(summary, page_length=2000):
            await ctx.send(page)

    @_massrole.command(name="toall", aliases=["ta", "everyone"])
    async def _assign_to_all(
        self,
        ctx: commands.Context,
        role_to_assign: discord.Role,
    ):
        """Assign a role to everyone on the server.
        
        Example: [p]massrole toall @Access
        This will assign the @Access role to all members of the server.
        """
        # Check bot permissions
        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send("❌ I don't have permission to manage roles.")
            return
        
        # Check role hierarchy
        if ctx.guild.me.top_role <= role_to_assign:
            await ctx.send(
                f"❌ I cannot assign {role_to_assign.mention} because it is higher "
                f"than or equal to my highest role."
            )
            return
        
        # Get all members (excluding bots if you want, but we'll include them)
        members = [m for m in ctx.guild.members if not m.bot]
        
        if not members:
            await ctx.send("❌ No members found on this server.")
            return
        
        # Confirm action
        confirm_msg = await ctx.send(
            f"⚠️ **Confirm Role Assignment**\n\n"
            f"**Role to Assign:** {role_to_assign.mention}\n"
            f"**Target:** All {len(members)} members on the server\n\n"
            f"This will assign {role_to_assign.mention} to all {len(members)} members.\n\n"
            f"React with ✅ to confirm, or ❌ to cancel."
        )
        
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")
        
        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == confirm_msg.id
                and str(reaction.emoji) in ["✅", "❌"]
            )
        
        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=60.0, check=check
            )
        except asyncio.TimeoutError:
            await confirm_msg.edit(
                content="❌ Confirmation timed out. Operation cancelled."
            )
            return
        
        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(content="❌ Operation cancelled.")
            return
        
        # Remove reactions
        try:
            await confirm_msg.clear_reactions()
        except:
            pass
        
        # Perform assignment
        reason = f"Mass role assignment by {ctx.author} (to all members)"
        results = await self._assign_role_to_members(
            ctx, members, role_to_assign, reason=reason
        )
        
        # Send final summary
        summary_lines = [
            f"**Role Assignment Complete**",
            f"**Role Assigned:** {role_to_assign.mention}",
            f"**Target:** All server members",
            "",
            f"✅ **Success:** {results['success']}",
            f"⏭️ **Skipped:** {results['skipped']} (already had role)",
            f"❌ **Failed:** {results['failed']}",
        ]
        
        if results["errors"]:
            summary_lines.append("")
            summary_lines.append("**Errors:**")
            for error_type, count in results["errors"].items():
                summary_lines.append(f"- {error_type}: {count}")
        
        summary = "\n".join(summary_lines)
        for page in pagify(summary, page_length=2000):
            await ctx.send(page)


async def setup(bot: Red):
    await bot.add_cog(MassRole(bot))
    log.info("MassRole cog loaded")
