# Applications

**Short description:** Server application system for member screening.

## Description

New members apply before getting full access. Each applicant gets a private channel and a modal form (max 5 fields). Staff approve, deny, or close from that channel or with commands. Optional lobby panel, access vs restricted roles, denial/approval DMs, and delayed channel cleanup.

The complete setup walkthrough (role layout, form builder, troubleshooting) is in [applications/README.md](../applications/README.md).

## Install

```
[p]cog install wzyss-cogs applications
[p]load applications
```

## Requirements

None. The bot needs **Manage Roles**, **Manage Channels**, **Send Messages**, **Embed Links**, and **Read Message History**.

## Tags

applications, moderation, utility, screening

## Quick setup

1. Pick **restricted role** (deny view everywhere except the application category) **or** **access roles** (grant on approve) — not both.
2. `[p]applications toggle true`
3. `[p]applications channel lobby <category>` and `[p]applications channel log #logs`
4. `[p]applications field manager` to edit the form
5. `[p]applications lobby send` if using a public Apply panel

## Commands

Group `[p]applications` (alias `app`). Most config requires **Manage Server**.

| Area | Commands |
|------|----------|
| Toggle / inspect | `toggle`, `settings`, `setup`, `check` |
| Roles | `role restricted`, `role access`, `role bypass`, `role manager`, `role notification` |
| Channels | `channel log`, `channel lobby`, `channel review` |
| Lobby | `lobby embed`, `lobby send` |
| Policy | `policy cleanupdelay`, `policy kicktimeout`, `policy rejoininvite`, `policy denial *`, `policy approval *`, `policy early_close *` |
| Form | `field add/remove/list/preview/confirmtext/manager` |
| Review | `list`, `view`, `approve`, `deny`, `close`, `bypass` |
| Maintenance | `maintenance cleanup`, `maintenance clearorphaned`, `maintenance removeuser`, `maintenance backfillbypassaccess` |

Approve/deny also have aliases `a` / `d`.
