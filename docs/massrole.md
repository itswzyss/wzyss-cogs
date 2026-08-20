# MassRole

**Short description:** Assign roles to all members of a role or everyone on the server.

## Description

Bulk-assign a role to everyone who already has another role, or to every member. Confirms with reactions, tracks progress, and retries on Discord rate limits.

## Install

```
[p]cog install wzyss-cogs massrole
[p]load massrole
```

Cog folder: [massrole/README.md](../massrole/README.md)

## Requirements

None. The bot needs **Manage Roles**. The role to assign must be **below** the bot's top role.

## Tags

roles, utility, moderation

## Commands

Group `[p]massrole` (alias `mr`). Requires **Manage Roles**.

| Command | Description |
|---------|-------------|
| `[p]massrole torole <have_role> <assign_role>` | Assign `assign_role` to everyone who has `have_role` |
| `[p]massrole toall <assign_role>` | Assign to every member (`ta`, `everyone`) |

## Notes

- React ✅ to confirm or ❌ to cancel (60s).
- Useful when migrating to an access-role layout.
