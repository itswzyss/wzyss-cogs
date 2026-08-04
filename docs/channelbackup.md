# ChannelBackup

**Short description:** Backup and restore channel structure, settings, and permissions.

## Description

Creates named backups of a guild's categories, text/news channels, voice channels, stage channels, and forums. Each backup includes channel settings and per-channel / per-category permission overwrites so you can recover from a bad configuration.

Restore matches channels and categories by Discord ID, updates their settings and overwrites to match the backup, and optionally recreates entries that were deleted. Channels that exist now but are not in the backup are never deleted.

## Install

```
[p]cog install wzyss-cogs channelbackup
[p]load channelbackup
```

## Requirements

None. The bot needs **Manage Channels** to restore.

## Tags

channels, backup, permissions, utility, moderation

## Commands

| Command | Description |
|---------|-------------|
| `[p]channelbackup create [name]` | Create a backup (default name is a UTC timestamp) |
| `[p]channelbackup list` | List stored backups for this server |
| `[p]channelbackup info <name>` | Show backup metadata and channel-type counts |
| `[p]channelbackup download <name>` | Download the backup JSON file |
| `[p]channelbackup delete <name>` | Delete a stored backup (confirmation required) |
| `[p]channelbackup import [name]` | Import a JSON attachment as a backup |
| `[p]channelbackup restore <name> [create_missing]` | Restore settings/permissions from a backup |

Aliases: `chbackup`, `cbackup`.

## Restore notes

- Pass `True` as `create_missing` to recreate deleted categories/channels from the backup.
- Overwrites targeting roles or members that no longer exist are skipped and reported.
- Backups are guild-scoped; importing/restoring a backup from another guild is refused because role IDs would not match.
- Up to 25 backups are kept per guild.
