# AutoVC

**Short description:** Automatically create voice channels when members join source VCs.

## Description

Joining a configured source voice channel creates a new VC: **Public**, **Personal** (visible, owner-controlled), or **Private** (hidden, owner-controlled). Owners use `[p]autovc` commands and buttons in the VC text chat — no extra Discord role, so 2FA-for-mods is not triggered. Empty channels are deleted; ownership can be claimed after the owner leaves.

Full dashboard walkthrough, naming pools, and troubleshooting: [autovc/README.md](../autovc/README.md).

## Install

```
[p]cog install wzyss-cogs autovc
[p]load autovc
```

## Requirements

None. The bot needs **Manage Channels**, **Move Members**, and **Connect**.

## Tags

voice, channels, utility, automation

## Admin commands

Group `[p]autovcset`. Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]autovcset setup` | Interactive dashboard (sources, types, categories, names, member role) |
| `[p]autovcset list` | Configured source VCs |
| `[p]autovcset settings` | Summary |
| `[p]autovcset memberrole [role]` | Base visibility role (omit to use @everyone) |

## User commands

Group `[p]autovc` (alias `avc`). Slash replies are ephemeral.

| Command | Description |
|---------|-------------|
| `[p]autovc lock [vc]` | Lock connect |
| `[p]autovc unlock [vc]` | Unlock |
| `[p]autovc hide [vc]` | Hide from the list |
| `[p]autovc show [vc]` | Show |
| `[p]autovc limit <0–99> [vc]` | User limit (`0` = none) |
| `[p]autovc name [new_name] [vc]` | Rename (blank resets) |
| `[p]autovc claim [vc]` | Claim 5 minutes after the owner leaves |

Rate limit: 3 creations per 30 seconds per user.
