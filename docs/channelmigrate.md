# ChannelMigrate

**Short description:** Copy every message from a channel to a channel in another server for migrations.

Cog folder: [channelmigrate/README.md](../channelmigrate/README.md)

## Description

Reposts a channel's entire message history, oldest first, into a destination channel (usually in another server). Messages are sent through a webhook in the destination that uses each original author's display name and avatar, so the copy reads like the original conversation.

Optionally, once the history is copied, the job keeps mirroring new messages live until you stop it, which covers the cutover period of a migration.

## Install

```
[p]cog install wzyss-cogs channelmigrate
[p]load channelmigrate
```

## Requirements

- **Users:** the Discord **Administrator** permission in both the source and destination servers (checked on every command and button press). Red admin/mod roles and bot ownership do not bypass this.
- **Bot:** View Channel and Read Message History in the source; View Channel and Manage Webhooks in the destination (the parent channel, if the destination is a thread).
- Red 3.5+ (discord.py 2).

## Tags

migration, messages, archive, webhook, mirror, administration

## Commands

| Command | Description |
|---------|-------------|
| `[p]migrate start <source> <destination>` | Open the setup panel for a migration. Channels can be mentions, IDs, or channel links from any server the bot is in |
| `[p]migrate list` | List jobs you can manage |
| `[p]migrate panel <job_id>` | Re-post a job's control panel here (alias `status`) |
| `[p]migrate remove <job_id>` | Delete a stopped/finished/failed job record (copied messages are untouched) |

Alias: `chmigrate`.

## Panels

- **Setup panel** - toggle **Dates in names** (adds `- YYYY-MM-DD` of the original post to the poster name) and **Live mirror after history**, then **Start Migration**.
- **Control panel** - live progress (copied, skipped, failed, copied-up-to date) with **Pause**, **Resume**, **Stop**, and **Refresh**. The buttons keep working after bot restarts.

## What is copied

- Message text, with user/role/channel mentions converted to plain text. Nothing pings anyone.
- Attachments, re-uploaded (batched to fit the destination's upload limit). Files larger than the limit are posted as a link to the original.
- Rich embeds from bots and webhooks. Link previews regenerate on their own.
- Replies, shown as a "Replying to" line that links to the copied message when it was copied by the same job.
- Forwarded messages, stickers (as names), and polls (as text).

Skipped: system messages (joins, pins, boosts), reactions, threads inside the source channel, and messages posted by the migration webhook itself.

## Notes

- Progress is saved after every message. Jobs that were copying or mirroring resume automatically when the bot restarts, without duplicates.
- If the destination becomes unreachable or the bot loses permissions, the job switches to **Error** without skipping anything. Fix the issue and press **Resume**.
- Reposted messages carry the time they were copied, not the original time; use **Dates in names** to keep the original date visible.
- Run the command from a channel other than the source, or the command message itself will be copied (the control panel is always skipped).
- Large channels take a while: Discord rate-limits webhook posts to roughly a couple of messages per second.
- Reply links only work for messages copied since the last bot restart; older ones show the quoted author and text without a link.
