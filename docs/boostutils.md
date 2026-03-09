# BoostUtils

Manage booster-dependent custom roles and configurable boost notifications.

## Description

BoostUtils tracks configured custom roles that require entitlement from either:

- the Discord Server Booster role, or
- one or more configured linked roles (for example Patreon supporter roles).

- If a member loses all entitlement (no booster role and no linked role), tracked custom roles are removed.
- If the same member later regains entitlement (booster role or linked role), previously removed tracked roles are restored.
- Optional boost gain announcements support one message with optional text and optional embed.
- Optional booster status-change notifications (gain/loss) support a target channel and optional ping role.

Both announcement systems are disabled by default.

## Install

```text
[p]cog install wzyss-cogs boostutils
```

## Quick setup

```text
[p]boostutils role add @CustomBoosterRole
[p]boostutils linkedrole add @PatreonSupporter
[p]boostutils announce channel #boosts
[p]boostutils announce text {member} just boosted {guild}!
[p]boostutils announce toggle true
```

Optional status-change notifications:

```text
[p]boostutils statusnotify channel #boost-logs
[p]boostutils statusnotify ping @Staff
[p]boostutils statusnotify toggle true
```

## Commands

- `boostutils role add <role>`: Add tracked custom role.
- `boostutils role remove <role>`: Remove tracked custom role.
- `boostutils role list`: List tracked custom roles.
- `boostutils linkedrole add <role>`: Add linked entitlement role.
- `boostutils linkedrole remove <role>`: Remove linked entitlement role.
- `boostutils linkedrole list`: List linked entitlement roles.
- `boostutils list`: List tracked custom roles with members who currently have them.
- `boostutils check`: Run a live compliance check and report tracked-role members who are missing entitlement.
- `boostutils check verbose`: Print each tracked role, members who currently have it, and each member's compliance result.
- `boostutils check run`: Manually run reconciliation to remove tracked roles from non-entitled members and restore tracked roles for entitled members with stored removals.

- `boostutils announce toggle <true|false>`: Enable/disable boost announcements (default off).
- `boostutils announce channel [#channel]`: Set or clear announcement channel.
- `boostutils announce text [text]`: Set or clear announcement text.
- `boostutils announce embed`: Open interactive embed builder.
- `boostutils announce embedclear`: Clear configured announcement embed.

- `boostutils statusnotify toggle <true|false>`: Enable/disable status-change notifications (default off). Notifications cover booster and linked-role gain/loss events.
- `boostutils statusnotify channel [#channel]`: Set or clear status notification channel.
- `boostutils statusnotify ping [@role]`: Set or clear optional status notification ping role.

- `boostutils dm toggle <true|false>`: Enable/disable DM notifications for tracked role add/remove (default off).
- `boostutils dm added [text]`: Set or clear DM template for role restoration/add.
- `boostutils dm removed [text]`: Set or clear DM template for role removal.
- `boostutils dm cooldown [seconds]`: View or set DM cooldown per member per action (default `300` seconds).

- `boostutils show`: Show current settings, including configured tracked and linked role lists.

## Template tokens

Announcement text and announcement embed fields support:

- `{member}`: Member mention
- `{member_name}`: Member display name
- `{guild}`: Guild name

DM templates support:

- `$guildname`: Guild name
- `$customrole`: Custom role name

DM variable values are rendered in bold when substituted.

DM cooldown notes:

- Cooldown is tracked per member and per action type (`added` vs `removed`).
- Setting cooldown to `0` disables rate limiting.
