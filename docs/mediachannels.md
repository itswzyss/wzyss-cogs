# MediaChannels

**Short description:** Enforce media-focused posting in selected channels.

## Description

Monitors configured channels and removes non-media chatter while allowing attachments, links, embeds, and stickers. Optional warning messages and a reply-grace window let brief discussion happen after a media post. Roles can be exempted from enforcement.

## Install

```
[p]cog install wzyss-cogs mediachannels
[p]load mediachannels
```

Cog folder: [mediachannels/README.md](../mediachannels/README.md)

## Requirements

None. The bot needs **Manage Messages** in enforced channels (and **Send Messages** if warnings are enabled).

## Tags

moderation, media, channels, utility

## Setup

```
[p]mediachannel add #clips
[p]mediachannel setup #clips
```

`setup` opens an interactive panel. Individual toggles can also be set with the commands below. Group aliases: `mcs`.

## Commands

Admin / **Manage Server**. Group: `[p]mediachannel`.

| Command | Description |
|---------|-------------|
| `[p]mediachannel setup <channel>` | Interactive setup UI |
| `[p]mediachannel add <channel>` | Start enforcing in a channel |
| `[p]mediachannel remove <channel>` | Stop enforcing and drop config |
| `[p]mediachannel toggle <channel> <true/false>` | Enable or disable without deleting config |
| `[p]mediachannel warn <channel> <true/false>` | Warn after deleting non-media |
| `[p]mediachannel warningmessage <channel> <text>` | Warning text (`{mention}`, `{channel}`) |
| `[p]mediachannel warningdeleteafter <channel> <seconds>` | Auto-delete warnings (0–300, `0` = keep) |
| `[p]mediachannel deletenonmedia <channel> <true/false>` | Toggle deleting non-media |
| `[p]mediachannel replygrace <channel> <count>` | Non-media messages allowed after a media post (0–20) |
| `[p]mediachannel gracewindow <channel> <seconds>` | How long that grace lasts (0–86400) |
| `[p]mediachannel gracerequiresreply <channel> <true/false>` | Require grace messages to be replies |
| `[p]mediachannel allowlinks <channel> <true/false>` | Treat links as media |
| `[p]mediachannel allowattachments <channel> <true/false>` | Treat attachments as media |
| `[p]mediachannel allowembeds <channel> <true/false>` | Treat embeds as media |
| `[p]mediachannel allowstickers <channel> <true/false>` | Treat stickers as media |
| `[p]mediachannel exemptrole add <channel> <role>` | Exempt a role |
| `[p]mediachannel exemptrole remove <channel> <role>` | Remove an exemption |
| `[p]mediachannel exemptrole list <channel>` | List exempt roles |
| `[p]mediachannel status [channel]` | Show settings |

## Notes

- Bot messages are ignored.
- Grace is counted from the last media post in that channel.
