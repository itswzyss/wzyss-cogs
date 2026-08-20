# Giveaway

**Short description:** Reaction-based giveaways with optional claim system.

## Description

Run giveaways with reaction-based entry. Create them with an interactive builder or `[p]giveaway start`. Optional claim window: winners must click Claim or the prize is rerolled. Winners can be DMed.

## Install

```
[p]cog install wzyss-cogs giveaway
[p]load giveaway
```

Cog folder: [giveaway/README.md](../giveaway/README.md)

## Requirements

None. The bot needs **Add Reactions**, **Embed Links**, **Send Messages**, and **Read Message History**.

## Tags

giveaway, utility, reactions

## Commands

Group `[p]giveaway` (alias `gw`). Create/start/edit require **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]giveaway create` | Interactive builder (prizes, duration, emoji, claim, DM, channel, images) |
| `[p]giveaway start [options]` | Start from flags instead of the builder |
| `[p]giveaway edit [message_id]` | Edit a running giveaway (reply to it or pass the ID) |
| `[p]giveaway end [message_id]` | End early and draw winners |
| `[p]giveaway reroll [message_id]` | Draw new winner(s) |
| `[p]giveaway cancel [message_id]` | Cancel without drawing |

If `message_id` is omitted, reply to the giveaway message.

## Notes

- Entry is by reacting with the configured emoji.
- Claim buttons stay valid across restarts.
