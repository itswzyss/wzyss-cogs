# Announcer

**Short description:** Forward channel messages to subscribed role members via DM.

## Description

When a message is posted in a configured channel, the bot reacts with 📢. The message author or any member with Manage Messages can click that reaction to broadcast the message as a DM to every member of the configured role. Supports multiple channel/role pairs and an optional jump link in the DM.

## Install

```
[p]cog install wzyss-cogs announcer
[p]load announcer
```

Cog folder: [announcer/README.md](../announcer/README.md)

## Requirements

None. The bot needs **Add Reactions**, **Read Message History**, and **Send Messages**. Members must be able to receive DMs from the bot.

## Tags

announcements, dm, notifications, roles, utility

## Setup

1. `[p]announcer add #announcements @Subscribers` — subscribe a role to a channel.
2. Post in that channel; the bot adds 📢.
3. The author (or anyone with Manage Messages) clicks 📢 to send DMs.
4. After a successful broadcast the bot adds ✅ so the same message is not sent twice.

## Commands

| Command | Description |
|---------|-------------|
| `[p]announcer add <channel> <role>` | Subscribe a role to DMs from a channel |
| `[p]announcer remove <channel>` | Remove the subscription for a channel |
| `[p]announcer jumplink <channel>` | Toggle whether DMs include a jump link to the original message |
| `[p]announcer list` | List channel → role subscriptions |

Requires **Manage Server** (or administrator).

## Notes

- Disable jump links with `[p]announcer jumplink` when subscribers cannot see the source channel.
- Role membership is read at broadcast time; nothing about individual users is stored.
- Only the message author or a member with Manage Messages in that channel can confirm the 📢 reaction.
