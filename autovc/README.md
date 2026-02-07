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
- **Owner Management**: Personal and Private VCs have owners with full control
- **Temporary Roles**: Owners get temporary roles to manage their VC permissions
- **Automatic Cleanup**: Empty VCs are automatically deleted
- **Owner Claiming**: Users can claim VCs after owner leaves (5-minute wait)
- **Rate Limiting**: Prevents abuse (3 creations per 30 seconds per user)
- **Member Role Support**: Handles servers with @Member role for permissions

## Setup

### 1. Configure Source VCs

Source VCs are the voice channels that trigger automatic VC creation when users join them.

#### Add a Source VC

```
[p]autovc add <source_vc> <type> [category]
```

**Types:**
- `public` - Anyone can join, no owner
- `personal` - Owner-controlled, visible to everyone by default
- `private` - Owner-controlled, hidden by default

**Examples:**
```
[p]autovc add #Create Public public
[p]autovc add #Create Personal personal
[p]autovc add #Create Private private #Private-VCs
```

If you don't specify a category, created VCs will be placed in the same category as the source VC.

#### List Source VCs

```
[p]autovc list
```

Shows all configured source VCs and their types.

#### Remove a Source VC

```
[p]autovc remove <source_vc>
```

Removes a source VC from the configuration.

### 2. Configure Member Role (Optional)

If your server uses a @Member role instead of @everyone for base permissions, configure it:

```
[p]autovc memberrole @Member
```

To clear and use @everyone instead:

```
[p]autovc memberrole
```

### 3. View Settings

```
[p]autovc settings
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
- **Permissions**: 
  - @everyone/@Member can view and connect by default (owner can change)
  - Owner gets a temporary role with `manage_channels` permission
- **Use Case**: Personal voice channels where the owner controls who can join

### Private VCs

- **Visibility**: Hidden from everyone by default
- **Owner**: Yes (the user who created it)
- **Permissions**:
  - @everyone/@Member cannot see the channel by default
  - Owner gets a temporary role with `manage_channels` permission
  - Owner can grant view/connect permissions to specific users/roles
- **Use Case**: Private voice channels that are hidden until the owner invites people

## Owner Management

### Temporary Roles

When a Personal or Private VC is created, the owner receives a temporary role that grants them `manage_channels` permission on their VC. This allows them to:

- Access the channel's permission settings in Discord
- Modify who can see/join the channel
- Manage all channel settings

The role is automatically deleted when the VC is deleted.

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
4. Claimer receives the owner role and full control

## Automatic Cleanup

- Empty VCs are automatically deleted every 30 seconds
- Owner roles are cleaned up when VCs are deleted
- Claim timers are managed automatically

## Rate Limiting

To prevent abuse, users are limited to creating 3 VCs per 30 seconds. If exceeded, they'll receive a DM notification and the VC creation will be blocked.

## Commands Reference

### Configuration Commands

- `[p]autovc add <source_vc> <type> [category]` - Add a source VC
- `[p]autovc remove <source_vc>` - Remove a source VC
- `[p]autovc list` - List all source VCs
- `[p]autovc settings` - Show current settings
- `[p]autovc memberrole [role]` - Set or clear member role

### User Commands

- `[p]autovc claim [vc]` - Claim ownership of a VC

## Permissions

### Bot Permissions Required

- `Manage Channels` - To create and delete voice channels
- `Manage Roles` - To create and delete temporary owner roles
- `Move Members` - To move users to newly created VCs
- `Connect` - To access voice channels

### User Permissions

- `Manage Server` - Required to configure source VCs and settings
- No special permissions needed to use source VCs or claim VCs

## How It Works

1. **User joins source VC** → Bot detects the join event
2. **Rate limit check** → Verifies user hasn't exceeded limit
3. **VC creation** → Creates new VC based on source VC type
4. **Permission setup** → Configures permissions based on VC type
5. **Owner role** → Creates temporary role for Personal/Private VCs
6. **User moved** → Moves user to newly created VC
7. **Tracking** → VC is tracked in configuration
8. **Cleanup** → When VC becomes empty, it's automatically deleted

## Member Role Handling

Some servers configure @everyone to deny "View Channels" and use a @Member role to grant access. AutoVC handles this:

- If `member_role_id` is configured, @Member role is used as the base for permissions
- If not configured, @everyone is used as the base
- Personal/Private VCs inherit this base visibility appropriately

**Example Setup:**
1. Server has @everyone denied view_channels
2. @Member role grants view_channels
3. Configure: `[p]autovc memberrole @Member`
4. Public VCs: @Member can view/connect
5. Personal VCs: @Member can view/connect by default
6. Private VCs: Hidden from @Member by default (owner controls)

## Troubleshooting

### VCs aren't being created

1. Check if source VC is configured: `[p]autovc list`
2. Verify bot has `Manage Channels` permission
3. Ensure source VC is in a category (or specify one)
4. Check bot logs for errors

### Owner role not working

1. Verify bot has `Manage Roles` permission
2. Check bot's role hierarchy (must be above created roles)
3. Ensure bot can edit channel permissions

### VCs not being deleted

1. Check if VC is actually empty (no members)
2. Verify bot has `Manage Channels` permission
3. Check bot logs for cleanup errors

### Rate limit issues

- Users can create 3 VCs per 30 seconds
- If exceeded, they'll receive a DM notification
- Wait 30 seconds before creating another VC

### Permission issues with @Member role

1. Verify member role is configured: `[p]autovc settings`
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

No personal user data is stored beyond channel ownership associations.
