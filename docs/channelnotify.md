# ChannelNotify

**Short description:** Automatically ping roles when messages are sent in configured channels.

## Description

When any non-bot message is posted in a watched channel, configured roles are pinged once per cooldown. Typical use: a socials channel where a creator posts a link.

## Install

```
[p]cog install wzyss-cogs channelnotify
[p]load channelnotify
```

Cog folder: [channelnotify/README.md](../channelnotify/README.md)

## Requirements

None. The bot needs **Send Messages** and **Mention Roles** in those channels.

## Tags

notifications, utility, roles, channels

## Commands

`[p]channelnotify` (alias `chnotify`) and `[p]channelnotifyset` (alias `chnotifyset`). Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]channelnotify <channel> <role> [roles…]` | Watch a channel (same as `channelnotifyset add`) |
| `[p]channelnotifyset add <channel> <role> [roles…]` | Same as above |
| `[p]channelnotifyset remove <channel>` | Stop watching |
| `[p]channelnotifyset list` | List channels and roles |
| `[p]channelnotifyset cooldown <channel> <minutes>` | Per-channel cooldown (default 5) |
| `[p]channelnotifyset defaultcooldown <minutes>` | Default for newly added channels |

## Notes

- Cooldown is per channel.
- Deleted roles are skipped when listing.
