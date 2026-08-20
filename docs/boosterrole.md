# BoosterRole

**Short description:** Track booster custom roles and remove them when a user stops boosting.

## Description

When a member loses the Server Booster role, the cog finds their custom role (manual mapping or auto-detect), removes it, and logs the event. Optional ping on the log message.

## Install

```
[p]cog install wzyss-cogs boosterrole
[p]load boosterrole
```

Cog folder: [boosterrole/README.md](../boosterrole/README.md)

## Requirements

None. The bot needs **Manage Roles** and **Send Messages** in the log channel.

## Tags

roles, utility, moderation

## Identification modes

Set with `[p]boosterrole mode <mode>`:

| Mode | Behavior |
|------|----------|
| `manual_only` | Only `[p]boosterrole setcustomrole` mappings |
| `auto_single` | The role only this member has |
| `auto_name` | Role name matches the configured pattern |
| `auto_position` | The single role directly below the booster role |

## Commands

Group `[p]boosterrole` (alias `boostrole`). Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]boosterrole logchannel <channel>` | Log channel |
| `[p]boosterrole logchannel clear` | Stop logging to a channel |
| `[p]boosterrole logping <role>` | Extra ping on log messages |
| `[p]boosterrole logping clear` | Clear ping role |
| `[p]boosterrole setboosterrole <role>` | Override auto-detected booster role |
| `[p]boosterrole clearboosterrole` | Back to Discord's Server Booster role |
| `[p]boosterrole mode <mode>` | Identification mode |
| `[p]boosterrole namepattern <pattern>` | Substring or regex for `auto_name` |
| `[p]boosterrole namepattern clear` | Clear pattern |
| `[p]boosterrole prefer_single <true/false>` | Prefer single-occupant when several match |
| `[p]boosterrole setcustomrole <user> <role>` | Manual mapping |
| `[p]boosterrole clearcustomrole <user>` | Remove mapping |
| `[p]boosterrole show` | Current settings |

For BoostUtils (tracked custom roles + announcements), see [boostutils.md](boostutils.md).
