# Counting

**Short description:** Count upwards in channels with optional math expressions.

## Description

Members count upwards in configured channels using whole numbers or simple math (`1+1`, `2*3`). Optional ruin mode resets the count on a wrong number. Optional saves let someone restore the last count after a ruin. Leaderboards track contributors and ruins.

## Install

```
[p]cog install wzyss-cogs counting
[p]load counting
```

Cog folder: [counting/README.md](../counting/README.md)

## Requirements

None. The bot needs **Send Messages**, **Embed Links**, **Add Reactions** (if reactions are on), and **Manage Messages** if invalid counts should be deleted. **Manage Channels** is needed to write the record into the channel topic.

## Tags

counting, utility, fun, math

## Setup

```
[p]countingset channel #counting
[p]countingset ruin #counting true
```

## Admin commands

Group `[p]countingset` (alias `countset`). Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]countingset channel <channel>` | Enable counting (starts at 1) |
| `[p]countingset disable <channel>` | Disable counting |
| `[p]countingset status [channel]` | Show current / next / settings |
| `[p]countingset milestone <channel> [n]` | Cap milestone, or omit to clear |
| `[p]countingset milestoneinterval <channel> [n]` | Repeat milestones every N |
| `[p]countingset milestonecontributors <channel> <true/false>` | Show top contributors on milestones |
| `[p]countingset consecutive <channel> <n>` | `1` = no double-counting; higher allows a streak |
| `[p]countingset ruin <channel> <true/false>` | Reset on wrong / invalid / double count |
| `[p]countingset ruinmessage <channel> <text>` | Ruin text (`{user}`, `{count}`) |
| `[p]countingset reactions <channel> <true/false>` | React ✅ on valid counts |
| `[p]countingset reset <channel>` | Reset the count to 0 |
| `[p]countingset setnext <channel> <n>` | Next expected number |
| `[p]countingset setrecord <channel> <n>` | Set the displayed record |
| `[p]countingset removerecord <channel>` | Clear record and channel topic |

### Saves

Disabled by default. When enabled, a **Use a Save** button appears after a ruin.

| Command | Description |
|---------|-------------|
| `[p]countingset saves enable` / `disable` | Toggle the save system |
| `[p]countingset saves timeout <duration>` | How long the button lasts (`60`, `5m`, `1h`, or `never`). Default **60 seconds**. Max 7 days |
| `[p]countingset saves maxsaves <n>` | Cap per user |
| `[p]countingset saves dropchance <0–1>` | Chance to earn a save per valid count |
| `[p]countingset saves threshold <n>` | Lifetime counts required before drops |
| `[p]countingset saves give <user> [n]` | Grant saves |
| `[p]countingset saves take <user> [n]` | Remove saves |
| `[p]countingset saves status` | Show save settings |

If someone already restarted from 0 when a save is used:

- New count **below** the saved count → restore the saved count and announce that the restart was overwritten.
- New count **at or above** the saved count → save is **not** consumed.

The save button survives bot restarts for the configured window. A new ruin invalidates the previous button.

## Member commands

| Command | Description |
|---------|-------------|
| `[p]countinginventory` (`cinv`, `mysaves`) | Your saves |
| `[p]countingleaderboard` (`clb`) | Contributors per counting channel |
| `[p]countingruins` (`crl`) | Ruin leaderboard |
