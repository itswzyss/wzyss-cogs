# RoleToggle

**Short description:** Post self-role toggle buttons when configured roles are pinged.

## Description

When a mapped role is mentioned in a channel, the bot posts a button in the same channel. Members click it to add or remove the mapped toggle role. Configuration is an interactive menu (no long subcommand tree). Buttons stay valid after bot restarts.

## Install

```
[p]cog install wzyss-cogs roletoggle
[p]load roletoggle
```

Cog folder: [roletoggle/README.md](../roletoggle/README.md)

## Requirements

None. The bot needs **Manage Roles** (toggle role must be below the bot's top role).

## Tags

roles, self-role, utility, automation

## Setup

```
[p]roletoggle
```

or

```
[p]roletoggle setup
```

The menu is used to map a pinged role → a toggle role, and to customize the prompt message. After that, pinging the source role in any channel posts the button.

Prefix commands that ping the role are ignored so staff commands do not spawn extra buttons.

## Commands

Requires **Manage Roles**.

| Command | Description |
|---------|-------------|
| `[p]roletoggle` | Open the interactive settings menu |
| `[p]roletoggle setup` | Same menu |

## Notes

- Stores per-guild role mappings and message templates only; no user data.
- The posted button assigns or removes the **toggle** role, not the pinged role.
