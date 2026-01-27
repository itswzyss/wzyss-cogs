# GuildAppNotifier

A Red-DiscordBot cog that automatically notifies you when new guild applications are received, members join after approval, or pass verification gates.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add wzyss-cogs https://github.com/yourusername/wzyss-cogs
[p]cog install wzyss-cogs guildappnotifier
[p]load guildappnotifier
```

## Usage

### Setting Up Notifications

#### Set a notification channel

```
[p]guildappnotifier channel #notifications
```

This will send notifications to the specified channel. To clear the channel setting:

```
[p]guildappnotifier channel
```

#### Add users to receive DM notifications

```
[p]guildappnotifier adduser @user
```

Users added this way will receive direct messages when applications are received.

#### Remove users from notifications

```
[p]guildappnotifier removeuser @user
```

#### List users receiving notifications

```
[p]guildappnotifier listusers
```

### Configuration

#### Toggle notifications on/off

```
[p]guildappnotifier toggle [True|False]
```

Without arguments, it toggles the current state. With `True` or `False`, it sets the state explicitly.

#### Toggle notifications on member join

```
[p]guildappnotifier notifyonjoin True
[p]guildappnotifier notifyonjoin False
```

This controls whether you receive notifications when members join the guild (after their application is approved).

#### Toggle notifications on verification

```
[p]guildappnotifier notifyonverification True
[p]guildappnotifier notifyonverification False
```

This controls whether you receive notifications when members pass the verification gate.

#### View current settings

```
[p]guildappnotifier settings
```

## How it works

The cog monitors the following events:

1. **Member Join** - When a member joins the guild (after their application is approved)
   - Detects both pending members (still in verification) and fully joined members
   
2. **Member Verification** - When a member's pending status changes (passes verification gate)
   - Notifies when a member transitions from pending to verified

Notifications are sent to:
- The configured notification channel (if set)
- All configured users via direct message (if any are set)

Each notification includes:
- Event type
- User information (mention, display name, ID)
- Account creation date
- Additional context about the event
- Guild information

## Permissions

- The bot needs `Send Messages` permission in the notification channel
- The bot needs `Embed Links` permission to send formatted notifications
- For DM notifications, users must have DMs enabled from server members

## Privacy

This cog stores notification channel and user settings on a per-guild basis. It does not store any user data beyond configuration preferences.
