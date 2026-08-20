# FixupXNudge

**Short description:** Gently nudge users to use fixupx.com for X/Twitter post links.

## Description

Watches for `x.com` / `twitter.com` **post** links (`/status/`) and suggests fixupx.com for better embeds. Profile links are ignored. Per-channel enable and a cooldown reduce spam.

## Install

```
[p]cog install wzyss-cogs fixupxnudge
[p]load fixupxnudge
```

Cog folder: [fixupxnudge/README.md](../fixupxnudge/README.md)

## Requirements

None.

## Tags

links, utility, twitter, x

## Commands

Group `[p]fixupxnudge`. Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]fixupxnudge toggle [true/false]` | Enable or disable (omit to flip) |
| `[p]fixupxnudge cooldown <seconds>` | Seconds between nudges per user |
| `[p]fixupxnudge channel <channel> [true/false]` | Per-channel on/off (omit flag to inspect) |
| `[p]fixupxnudge status` | Current settings |

To **replace** links instead of nudging, use [LinkReplacer](linkreplacer.md).
