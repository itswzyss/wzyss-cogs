# Remindme

**Short description:** Set timers and get pinged or DMed when they complete.

## Description

Reminders from a duration string (`5m`, `1h`, `2d`) or an interactive panel (preset buttons + custom modal). Optional `dm` delivery. User presets and guild-published presets. Timers survive restarts.

## Install

```
[p]cog install wzyss-cogs remindme
[p]load remindme
```

Cog folder: [remindme/README.md](../remindme/README.md)

## Requirements

None.

## Tags

reminder, utility, timer

## Commands

| Command | Description |
|---------|-------------|
| `[p]remindme` | Open the interactive picker |
| `[p]remindme <duration> [dm] [text]` | Set a timer, e.g. `[p]remindme 25m Check the oven` |
| `[p]remindme 5m dm` | Ping in DM instead of the channel |

Aliases: `remind`, `rm`. Duration 1 second–365 days.

### Presets and management (`[p]remindmeset` / `rmset`)

| Command | Description |
|---------|-------------|
| `[p]remindmeset set <name> <duration>` | Personal preset |
| `[p]remindmeset unset <name>` | Delete personal preset |
| `[p]remindmeset presets` | List yours |
| `[p]remindmeset publish <name> [duration]` | Publish a guild preset (Manage Server) |
| `[p]remindmeset unpublish <name>` | Remove guild preset |
| `[p]remindmeset published` | List guild presets |
| `[p]remindmeset list` | Your pending timers |
| `[p]remindmeset cancel <id>` | Cancel one |
