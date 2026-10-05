# ServerTemplate

**Short description:** Back up a server's roles, channels, permissions, and settings and replicate them to other servers.

Cog folder: [servertemplate/README.md](../servertemplate/README.md)

## Description

Captures a server's structure into a named template and applies it to the same server (as a backup restore) or to any other server the bot is in (as a clone). Everything is driven from an interactive panel of buttons, select menus, and modals.

Templates are stored bot-wide, not per server, so a template captured in one server can be applied in another.

### What a template can contain

| Component | Contents |
|-----------|----------|
| Roles | Name, color, permissions, hoist, mentionable, order, unicode role icon, `@everyone` permissions |
| Channels & permissions | Categories, text, announcement, voice, stage, forum, and media channels with their settings and permission overwrites |
| Server settings | Verification level, default notifications, content filter, AFK channel/timeout, system channel and flags, rules/updates channels (Community servers), locale, boost progress bar |
| Server name & icon | Guild name and icon image |
| Emojis | Custom emoji images, names, and role restrictions |

Not captured: messages, members, bans, threads, webhooks, integrations, stickers, and roles managed by bots/integrations/boosting.

## Install

```
[p]cog install wzyss-cogs servertemplate
[p]load servertemplate
```

## Requirements

- **Users:** the Discord **Administrator** permission in the server where they run the command. Red admin/mod roles and bot ownership do not bypass this.
- **Bot:** Administrator in the target server to apply a template. Capturing only needs the bot to be in the server.
- Red 3.5+ (discord.py 2).

## Tags

backup, template, clone, roles, channels, permissions, administration

## Commands

| Command | Description |
|---------|-------------|
| `[p]servertemplate panel` | Open the interactive template panel |
| `[p]servertemplate import [name]` | Import a template `.json` attached to the message (or the replied-to message) |

Aliases: `stemplate`, `serverbackup`. Panel aliases: `menu`, `open`.

## Panel

- **Create Template** - choose the source server (this one or any other server where you are an Administrator) and which components to capture, then press **Capture** and name it.
- **Browse Templates** - list templates you can access. Select one to **Apply to This Server**, **Export JSON**, **Rename**, or **Delete**.
- **Apply** - choose components and a mode, press **Preview Plan** to see counts of what will be created, updated, and deleted, then **Apply**.

## Access rules

- Every command, button, select, and modal checks that the user currently has Administrator.
- Panels only respond to the member who opened them.
- A template is visible to its creator and to anyone who is currently an Administrator of the server it was captured from.
- Capturing another server requires being an Administrator there at capture time.
- Up to 25 templates per creator.

## Apply modes

- **Merge** - matches existing items and updates them, creates missing ones, deletes nothing. On the source server, roles and channels are matched by ID first; elsewhere, by name (channels also by type and parent category).
- **Replace** - same as Merge, but also deletes roles, channels, and emojis that are not in the template. You must type the server name to confirm. Kept regardless: roles you hold, roles at or above the bot's highest role, managed roles, the channel the panel is running in, and Community rules/updates/safety channels.

## Notes

- Roles positioned above the bot's highest role cannot be edited and are reported as skipped. Move the bot's role to the top before applying.
- Permission overwrites for members are only restored if that member is in the target server. Overwrites for roles that could not be matched or created are skipped and counted.
- Announcement and stage channels need Community; on non-Community servers they are created as text and voice channels. Forums/media channels fall back to text channels if creation fails.
- Bitrate, user limits, and emoji counts are clamped to the target server's boost limits.
- Templates are JSON files in the cog's data folder (`templates/<id>.json`). Templates with emojis can be several MB; export is refused if the file exceeds the server's upload limit.
- Large applies take time because of Discord rate limits. Progress is shown on the panel message.
