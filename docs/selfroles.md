# SelfRoles

**Short description:** Interactive builder for self-assignable roles with buttons, reactions, and commands.

## Description

Build an embed with buttons and/or reactions that assign or remove roles. Exclusive groups allow only one role in the group at a time. `[p]role` is a command-based fallback. Buttons use stable custom IDs so they work after restarts.

## Install

```
[p]cog install wzyss-cogs selfroles
[p]load selfroles
```

Cog folder: [selfroles/README.md](../selfroles/README.md)

## Requirements

None. The bot needs **Manage Roles**, **Embed Links**, **Add Reactions** (if using reactions), and **Send Messages**.

## Tags

roles, utility, self-assign, buttons, reactions

## Commands

Group `[p]selfroles` (alias `sr`). Requires **Manage Roles** / Manage Server as applicable.

| Command | Description |
|---------|-------------|
| `[p]selfroles build` | Interactive builder (embed, buttons, reactions, exclusive groups) |
| `[p]selfroles edit <message_id>` | Edit an existing panel |
| `[p]selfroles list` | List panels |
| `[p]selfroles delete <message_id>` | Remove a panel |
| `[p]selfroles refresh <message_id>` | Re-register views / reactions |
| `[p]selfroles refreshall <channel>` | Refresh every panel in a channel |
| `[p]role <role name>` | Toggle a self-role by name |

The builder matches the pattern used by Tickets: Configure Embed, Preview, Save, Cancel.
