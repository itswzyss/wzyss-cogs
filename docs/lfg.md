# LFG

**Short description:** Looking for Group: availability per game, who is free, DM notify, request games.

## Description

Per-server game masterlist. Members mark themselves available or unavailable per game, list who is free, request new games for admin approval, and optionally get DMs when someone pings that game.

## Install

```
[p]cog install wzyss-cogs lfg
[p]load lfg
```

Cog folder: [lfg/README.md](../lfg/README.md)

## Requirements

None. DMs require that users can receive messages from the bot.

## Tags

utility, games, lfg

## Member commands

Group `[p]lfg` (slash available).

| Command | Description |
|---------|-------------|
| `[p]lfg list` | Games on the masterlist |
| `[p]lfg who <game>` | Who is available |
| `[p]lfg available <game>` | Mark yourself available |
| `[p]lfg unavailable <game>` | Mark unavailable |
| `[p]lfg clear [game]` | Clear one game or all of yours |
| `[p]lfg notify <game>` | DM opted-in available users |
| `[p]lfg notify optin` / `optout` | DM preference |
| `[p]lfg request <game>` | Ask admins to add a game |

## Admin commands

| Command | Description |
|---------|-------------|
| `[p]lfg add <game>` | Add to the masterlist |
| `[p]lfg remove <game>` | Remove |
| `[p]lfg requests` | Pending requests |
| `[p]lfg approve <game>` | Approve a request |
| `[p]lfg deny <game>` | Deny a request |
