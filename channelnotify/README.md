# ChannelNotify

Automatically ping configured roles when any message is sent in specified channels. This is particularly useful for creators posting to social media - when they link an X post, etc., in a channel, roles will be automatically pinged.

## Features

- Configure multiple channels with role notifications
- Each channel can have multiple roles to ping
- Per-channel cooldown to prevent spam (configurable, defaults to 5 minutes)
- Easy configuration via commands

## Commands

### `[p]channelnotify <channel> <role1> [role2] [role3] ...`
Add or update a channel with roles to ping. When any message is sent in the specified channel, all listed roles will be pinged.

**Example:**
```
[p]channelnotify #social-media @Creators @Announcements
```

You can also use `[p]channelnotifyset add` as an alternative.

### `[p]channelnotifyset remove <channel>`
Remove a channel from notifications.

**Example:**
```
[p]channelnotifyset remove #social-media
```

### `[p]channelnotifyset list`
List all configured channels and their associated roles.

### `[p]channelnotifyset cooldown <channel> <minutes>`
Set the cooldown for a specific channel in minutes. The bot will only ping roles once per cooldown period, even if multiple messages are sent.

**Example:**
```
[p]channelnotifyset cooldown #social-media 10
```

### `[p]channelnotifyset defaultcooldown <minutes>`
Set the default cooldown for newly configured channels. Default is 5 minutes.

**Example:**
```
[p]channelnotifyset defaultcooldown 5
```

## How It Works

1. When a message is sent in a configured channel, the bot checks if roles are configured for that channel.
2. If roles are configured, the bot checks if the cooldown period has passed since the last ping.
3. If the cooldown has passed, the bot pings all configured roles for that channel.
4. The cooldown timer resets, preventing additional pings until the cooldown expires.

## Permissions

- Users need `Manage Server` permission to configure channel notifications.
- The bot needs `Send Messages` and `Mention Roles` permissions in the configured channels.

## Notes

- The cooldown is per-channel, so different channels can have different cooldown periods.
- Bot messages are ignored (won't trigger pings).
- Messages in DMs are ignored (guild-only feature).
- If a role is deleted, it will be automatically filtered out when listing configurations.
