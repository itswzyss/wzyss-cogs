# Gambling

**Short description:** Virtual credits casino with Blackjack and leaderboards.

## Description

A guild-scoped virtual casino. Members earn and spend credits on games (Blackjack with standard casino rules). Includes daily bonus, stats, leaderboards, and admin credit controls. **No real money is involved.**

## Install

```
[p]cog install wzyss-cogs gambling
[p]load gambling
```

Cog folder: [gambling/README.md](../gambling/README.md)

## Requirements

None.

## Tags

gambling, casino, blackjack, credits, economy, fun, games

## Player commands

Hybrid group `[p]gambling` (aliases: `casino`, `gam`). Slash commands are also available.

| Command | Description |
|---------|-------------|
| `[p]gambling balance [member]` | Check credit balance |
| `[p]gambling daily` | Claim the 24-hour daily bonus |
| `[p]gambling stats [member]` | View win/loss stats, streaks, and Blackjack naturals |
| `[p]gambling leaderboard [board]` | Top 10. Boards: `credits`, `won`, `winrate`, `naturals`, `streak` |
| `[p]gambling blackjack <bet>` | Play Blackjack (`bj`) |

## Admin commands

Require **Manage Server**. Nested under `[p]gambling admin` and `[p]gambling settings`.

| Command | Description |
|---------|-------------|
| `[p]gambling admin give <member> <amount>` | Give credits |
| `[p]gambling admin take <member> <amount>` | Remove credits |
| `[p]gambling admin set <member> <amount>` | Set an exact balance |
| `[p]gambling admin reset <member>` | Reset a member's gambling data |
| `[p]gambling settings` | Show economy settings |
| `[p]gambling settings starting <amount>` | Starting credits for new players |
| `[p]gambling settings daily <amount>` | Daily bonus amount |
| `[p]gambling settings minbet <amount>` | Minimum bet |
| `[p]gambling settings maxbet <amount>` | Maximum bet |

## Notes

- Credits, stats, and daily-claim timestamps are stored per member per guild.
- New players are initialized with the configured starting balance on first use.
