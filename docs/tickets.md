# Tickets

**Short description:** Button-based support tickets with embeds and management controls.

## Description

Ticket system with a customizable embed and button to create tickets. Users create tickets only by clicking the "Create ticket" button in the configured panel channel; there are no user-facing prefix or slash commands. Each ticket gets a dedicated channel under a configurable category. Ticket managers can claim and close tickets via buttons. Optional auto-assign (add roles to the ticket after a delay) and inactivity auto-close (close ticket after no messages for a set time) are supported. When a ticket is closed, a transcript can be sent to a configured log channel.

## Install

```
[p]cog install wzyss-cogs tickets
```

## Requirements

None.

## Tags

tickets, support, utility, moderation

## Setup (admin only)

All configuration is under the `[p]ticketset` group. No user-facing commands.

1. **Panel channel:** `[p]ticketset channel #channel` – Channel where the create-ticket panel will appear.
2. **Category:** `[p]ticketset category <category>` – Category under which ticket channels are created.
3. **Manager roles:** `[p]ticketset managerroles @Role1 @Role2` – Roles that can claim and close tickets.
4. **Panel embed:** `[p]ticketset panelembed` – Opens the interactive embed builder (Configure Embed, Preview, Save, Cancel) for the panel message.
5. **Send panel:** `[p]ticketset panel` – Sends or updates the panel message in the panel channel (embed + "Create ticket" button).
6. **Welcome embed (optional):** `[p]ticketset welcomeembed` – Builder for the embed shown in new ticket channels. Save with empty title to disable.
7. **Auto-assign (optional):** `[p]ticketset autoassign <delay> @Role1 @Role2` – After the given delay (e.g. 5m, 1h), add the given roles to the ticket channel overwrites and notify.
8. **Inactivity close (optional):** `[p]ticketset inactivity <delay>` – Auto-close ticket after no messages for the given time (e.g. 24h, 7d). Use `0` or `off` to disable.
9. **Log channel:** `[p]ticketset logchannel #channel` – Channel where transcripts are posted when tickets are closed. Omit to clear.

## Commands

- `[p]ticketset` – Show ticketset subcommands.
- `[p]ticketset channel <channel>` – Set panel channel.
- `[p]ticketset category <category>` – Set ticket category.
- `[p]ticketset managerroles [roles...]` – Set manager roles.
- `[p]ticketset autoassign <delay> [roles...]` – Set auto-assign delay and roles.
- `[p]ticketset inactivity <delay|0|off>` – Set inactivity auto-close delay.
- `[p]ticketset logchannel [channel]` – Set or clear transcript log channel.
- `[p]ticketset panelembed` – Open panel embed builder.
- `[p]ticketset welcomeembed` – Open welcome embed builder.
- `[p]ticketset panel` – Send or update the create-ticket panel.

## User flow

- Users see the panel in the configured channel and click "Create ticket".
- A new ticket channel is created; the user is pinged and sees an optional welcome embed plus management buttons.
- Managers use "Claim" to take the ticket and "Close ticket" to close it (transcript is sent to the log channel if configured).
- One open ticket per user per guild; duplicate clicks are rejected with a message to use the existing ticket.
