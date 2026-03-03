# AutoVC

Automatically creates voice channels when members join configured "source" voice channels. Supports three VC types: Public (anyone can join), Personal (owner-controlled, visible), and Private (owner-controlled, hidden). Features automatic cleanup, owner claiming, and temporary roles for channel management.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add wzyss-cogs https://github.com/itswzyss/wzyss-cogs
[p]cog install wzyss-cogs autovc
[p]load autovc
```

## Features

- **Three VC Types**: Public, Personal, and Private
- **Automatic VC Creation**: Creates VCs when users join source channels
- **Owner Management**: Personal and Private VCs have owners; control is via bot commands and VC panel only (no Discord role, so 2FA-for-mods is not triggered)
- **VC Commands**: Owners use `[p]autovc lock`, `[p]autovc name`, etc. (user commands); admins use `[p]autovcset add`, `[p]autovcset panel toggle`, etc. (admin commands)
- **VC Panel**: Optional embed with buttons in designated channels for the same controls
- **Automatic Cleanup**: Empty VCs are automatically deleted
- **Owner Claiming**: Users can claim VCs after owner leaves (5-minute wait)
- **Rate Limiting**: Prevents abuse (3 creations per 30 seconds per user)
- **Member Role Support**: Handles servers with @Member role for permissions

## Setup

### 1. Configure Source VCs

Source VCs are the voice channels that trigger automatic VC creation when users join them.

#### Add a Source VC

```
[p]autovcset add <source_vc> <type> [category]
```

**Types:**
- `public` - Anyone can join, no owner
- `personal` - Owner-controlled, visible to everyone by default
- `private` - Owner-controlled, hidden by default

**Examples:**
```
[p]autovcset add #Create Public public
[p]autovcset add #Create Personal personal
[p]autovcset add #Create Private private #Private-VCs
```

If you don't specify a category, created VCs will be placed in the same category as the source VC.

#### List Source VCs

```
[p]autovcset list
```

Shows all configured source VCs and their types.

#### Remove a Source VC

```
[p]autovcset remove <source_vc>
```

Removes a source VC from the configuration.

### 2. Configure Member Role (Optional)

If your server uses a @Member role instead of @everyone for base permissions, configure it:

```
[p]autovcset memberrole @Member
```

To clear and use @everyone instead:

```
[p]autovcset memberrole
```

### 3. View Settings

```
[p]autovcset settings
```

Shows current configuration including source VCs count, active VCs, and member role.

## VC Types Explained

### Public VCs

- **Visibility**: Everyone can see and join
- **Owner**: None
- **Permissions**: @everyone/@Member can view and connect
- **Use Case**: General purpose voice channels for anyone to use

### Personal VCs

- **Visibility**: Visible to everyone by default
- **Owner**: Yes (the user who created it)
- **Control**: Owner uses bot commands or the VC panel to lock/unlock, hide/show, and set user limit (no Discord role is assigned, so server 2FA-for-mods is not affected)
- **Use Case**: Personal voice channels where the owner controls who can join

### Private VCs

- **Visibility**: Hidden from everyone by default
- **Owner**: Yes (the user who created it)
- **Control**: Same as Personal—owner uses bot commands or the VC panel
- **Use Case**: Private voice channels that are hidden until the owner invites people

## Owner Management

### Controlling Your VC

Owners of Personal or Private VCs do **not** receive a Discord role. Control is done only through:

- **User commands** (`[p]autovc` or `/autovc`): lock, unlock, hide, show, limit, name, claim
- **VC panel**: an embed with buttons in designated channel(s), toggled by admins via `[p]autovcset panel`

This avoids triggering server-wide 2FA requirements for moderators.

### VC commands (owner only)

You must be in your owned AutoVC (or specify it) to use these. All under `[p]autovc` or `/autovc`:

- `[p]autovc lock [vc]` – Lock the VC so others cannot connect
- `[p]autovc unlock [vc]` – Unlock the VC
- `[p]autovc hide [vc]` – Hide the VC from the channel list
- `[p]autovc show [vc]` – Show the VC in the channel list
- `[p]autovc limit <0-99> [vc]` – Set user limit (0 = no limit)
- `[p]autovc name [new_name] [vc]` – Rename your VC (leave name blank to reset to default, e.g. YourName's VC)

### VC panel (admin)

Admins can enable a **VC panel** in specific text channels: an embed with buttons (Lock VC, Unlock VC, Hide VC, Show VC, Set user limit, Rename VC). Only the owner of the VC they are currently in can use the buttons; the bot performs the action on their behalf.

- `[p]autovcset panel toggle` – Enable or disable the panel (sends or removes panel messages in designated channels)
- `[p]autovcset panel add <channel>` – Add a channel for the panel
- `[p]autovcset panel remove <channel>` – Remove a channel from the panel
- `[p]autovcset panel list` – List panel channels and whether the panel is enabled

### Claiming VCs

If the owner of a Personal or Private VC leaves, other users can claim ownership after 5 minutes:

```
[p]autovc claim [vc]
```

If you don't specify a VC, it will claim the VC you're currently in.

**Claim Process:**
1. Owner leaves the VC (but VC still has other members)
2. 5-minute waiting period begins
3. After 5 minutes, anyone in the VC can claim it
4. Claimer becomes the new owner (no role is created)

## Automatic Cleanup

- Empty VCs are automatically deleted every 30 seconds
- Any legacy owner roles are cleaned up when VCs are deleted
- Claim timers are managed automatically

## Rate Limiting

To prevent abuse, users are limited to creating 3 VCs per 30 seconds. If exceeded, they'll receive a DM notification and the VC creation will be blocked.

## Commands Reference

## Slash Commands and Privacy

Commands are split into two groups:

- **Admin** (`[p]autovcset` and `/autovcset`): source VCs, settings, member role, panel
- **User** (`[p]autovc` / `[p]avc` and `/autovc`): lock, unlock, hide, show, limit, name, claim

When you use **slash commands** for user commands, AutoVC replies **ephemerally by default** (only you can see the response). Prefix command responses remain normal channel messages.

### Admin Commands (autovcset)

- `[p]autovcset add <source_vc> <type> [category]` - Add a source VC
- `[p]autovcset remove <source_vc>` - Remove a source VC
- `[p]autovcset list` - List all source VCs
- `[p]autovcset settings` - Show current settings
- `[p]autovcset memberrole [role]` - Set or clear member role
- `[p]autovcset panel toggle` - Enable or disable the VC panel
- `[p]autovcset panel add <channel>` - Add a panel channel
- `[p]autovcset panel remove <channel>` - Remove a panel channel
- `[p]autovcset panel list` - List panel channels

### User Commands (autovc)

- `[p]autovc lock [vc]` - Lock your VC (owner only)
- `[p]autovc unlock [vc]` - Unlock your VC (owner only)
- `[p]autovc hide [vc]` - Hide your VC (owner only)
- `[p]autovc show [vc]` - Show your VC (owner only)
- `[p]autovc limit <0-99> [vc]` - Set user limit (owner only)
- `[p]autovc name [new_name] [vc]` - Rename your VC; blank = reset to default (owner only)
- `[p]autovc claim [vc]` - Claim ownership of a VC

## Permissions

### Bot Permissions Required

- `Manage Channels` - To create and delete voice channels and to apply lock/unlock/hide/show/limit for owners
- `Move Members` - To move users to newly created VCs
- `Connect` - To access voice channels

### User Permissions

- `Manage Server` - Required for admin commands (`autovcset`: source VCs, settings, panel)
- No special permissions needed for user commands (`autovc`: lock, unlock, hide, show, limit, name, claim)

## How It Works

1. **User joins source VC** → Bot detects the join event
2. **Rate limit check** → Verifies user hasn't exceeded limit
3. **VC creation** → Creates new VC based on source VC type
4. **Permission setup** → Configures permissions based on VC type
5. **User moved** → Moves user to newly created VC
6. **Tracking** → VC is tracked in configuration (owner has no role; control is via commands/panel)
7. **Cleanup** → When VC becomes empty, it's automatically deleted

## Member Role Handling

Some servers configure @everyone to deny "View Channels" and use a @Member role to grant access. AutoVC handles this:

- If `member_role_id` is configured, @Member role is used as the base for permissions
- If not configured, @everyone is used as the base
- Personal/Private VCs inherit this base visibility appropriately

**Example Setup:**
1. Server has @everyone denied view_channels
2. @Member role grants view_channels
3. Configure: `[p]autovcset memberrole @Member`
4. Public VCs: @Member can view/connect
5. Personal VCs: @Member can view/connect by default
6. Private VCs: Hidden from @Member by default (owner controls)

## Troubleshooting

### VCs aren't being created

1. Check if source VC is configured: `[p]autovcset list`
2. Verify bot has `Manage Channels` permission
3. Ensure source VC is in a category (or specify one)
4. Check bot logs for errors

### VC control (lock/unlock/hide/show/limit/name) not working

1. Ensure you are the owner of the VC (personal or private type) and are in that VC (or specify it)
2. Verify bot has `Manage Channels` permission
3. Use `[p]autovc lock` and related commands, or the VC panel in a designated channel (configured via `[p]autovcset panel`)

### VCs not being deleted

1. Check if VC is actually empty (no members)
2. Verify bot has `Manage Channels` permission
3. Check bot logs for cleanup errors

### Rate limit issues

- Users can create 3 VCs per 30 seconds
- If exceeded, they'll receive a DM notification
- Wait 30 seconds before creating another VC

### Permission issues with @Member role

1. Verify member role is configured: `[p]autovcset settings`
2. Check if role exists and is valid
3. Ensure role has appropriate permissions in server settings

## Notes

- Source VCs must be in a category (or you must specify one)
- Created VCs are automatically named based on the creator's display name
- Owner roles are automatically cleaned up when VCs are deleted
- Claim timers start when owner leaves, even if VC has other members
- Rate limiting is per-user and resets after 30 seconds
- Bot messages and bot users are ignored

## Data Storage

The cog stores:
- Source VC configurations (per-guild)
- Created VC tracking (per-guild)
- Claimable VC timers (per-guild)
- Member role ID (per-guild)
- Panel settings: panel_enabled, panel_channel_ids, panel_message_ids (per-guild)

No personal user data is stored beyond channel ownership associations.
