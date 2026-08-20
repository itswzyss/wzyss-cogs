# Clear

**Short description:** Clear/purge messages in a channel (by count, after a message, between messages, or by user).

## Description

Moderation purge tools. Prefix and slash commands. Caps at **500** messages per invocation. Messages older than 14 days are deleted individually (Discord bulk-delete limit).

## Install

```
[p]cog install wzyss-cogs clear
[p]load clear
```

Cog folder: [clear/README.md](../clear/README.md)

## Requirements

None. The bot and the user need **Manage Messages** (or the user must be a Red mod).

## Tags

moderation, utility, messages

## Commands

Hybrid group `[p]clear` / `/clear`. Text channels only.

| Command | Description |
|---------|-------------|
| `[p]clear <amount>` | Delete that many recent messages (1–500) |
| `[p]clear @user [amount]` | Delete that user's messages (optional cap) |
| `[p]clear after <message>` | Delete messages after a message ID or jump link |
| `[p]clear between <msg1> <msg2>` | Delete messages between two IDs or jump links |

## Notes

- Command messages are excluded from the delete set where possible.
- Jump links must point at the **current** channel.
