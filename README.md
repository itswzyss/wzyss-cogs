# Wzyss-Cogs

A collection of custom cogs for Red-DiscordBot.

## Installation

To add this repository to your Red bot:

```
[p]repo add wzyss-cogs https://github.com/itswzyss/wzyss-cogs
```

Then install individual cogs:

```
[p]cog install wzyss-cogs <cog_name>
[p]load <cog_name>
```

## Available Cogs

### Applications
**Install:** `[p]cog install wzyss-cogs applications`

Server application system for member screening. Implements a server application system where new members must apply before gaining full access. Creates private channels for each applicant with configurable forms.

**Tags:** applications, moderation, utility, screening

---

### AutoVC
**Install:** `[p]cog install wzyss-cogs autovc`

Automatically create voice channels when members join source VCs. Supports three types: Public (anyone can join), Personal (owner-controlled, visible), and Private (owner-controlled, hidden). Owners control their VC via bot commands and an optional VC panel; features automatic cleanup and owner claiming.

**Tags:** voice, channels, utility, automation

---

### BoosterRole
**Install:** `[p]cog install wzyss-cogs boosterrole`

Track booster custom roles and remove them when a user stops boosting. When a user loses the Server Booster role, identifies their custom role (manual or auto: single-occupant, name pattern, position below booster), removes that role from the user, and logs to a channel with optional role ping.

**Tags:** roles, utility, moderation

---

### ChannelNotify
**Install:** `[p]cog install wzyss-cogs channelnotify`

Automatically ping roles when messages are sent in configured channels. Useful for creators posting to social media - when they link an X post, etc., in a channel, roles will be pinged. Supports multiple channels and roles per channel with configurable cooldowns.

**Tags:** notifications, utility, roles, channels

---

### Clear
**Install:** `[p]cog install wzyss-cogs clear`

Clear/purge messages in a channel (by count, after message, between messages, or by user). Allows mods and admins to manage messages; supports both slash and prefix commands. Requires Manage Messages (or mod) permission.

**Tags:** moderation, utility, messages

---

### Counting
**Install:** `[p]cog install wzyss-cogs counting`

Count upwards in channels with optional math expressions. Allows members to count upwards in configured channels. Members can use numbers or math expressions (e.g., '1+1' for 2). Starts at 1 and can have an optional goal. Features include:
- Math expression support (e.g., "1+1", "2*2")
- Configurable consecutive counting limits
- Ruin mode (reset count on wrong number)
- Customizable ruin messages
- Goal tracking (single or consecutive goals)
- Reaction feedback (configurable)
- Highest record tracking with channel description updates

**Tags:** counting, utility, fun, math

---

### FixupXNudge
**Install:** `[p]cog install wzyss-cogs fixupxnudge`

Gently nudge users to use fixupx.com for X/Twitter post links. Monitors messages for X/Twitter post links and suggests users use fixupx.com for better embed support. Only nudges for post links (containing /status/), not profile links.

**Tags:** links, utility, twitter, x

---

### Giveaway
**Install:** `[p]cog install wzyss-cogs giveaway`

Reaction-based giveaways with optional claim system. Run giveaways with reaction-based entry, interactive or command-based setup, and management (reroll, end, edit, cancel). Optional configurable claim window with automatic reroll if not claimed.

**Tags:** giveaway, utility, reactions

---

### LFG
**Install:** `[p]cog install wzyss-cogs lfg`

Looking for Group: register availability per game, see who's available, notify via DM, request games. Guild-scoped: masterlist of games, per-user available/unavailable per game, view who is available, notify (DM) opted-in users, and request new games for admin approval.

**Tags:** utility, games, lfg

---

### LinkReplacer
**Install:** `[p]cog install wzyss-cogs linkreplacer`

Replace links with configured alternatives. Automatically replaces configured links with alternatives. For example, replace X.com links with fixupx.com links.

**Tags:** links, utility, moderation

---

### MassRole
**Install:** `[p]cog install wzyss-cogs massrole`

Assign roles to all members of a role or everyone on the server. Useful for migrating to access role systems or bulk role assignments. Features include:
- Assign a role to all members who have a specific role
- Assign a role to everyone on the server
- Automatic rate limit handling with graceful retries
- Real-time progress tracking
- Detailed success/failure reporting

**Tags:** roles, utility, moderation

---

### Remindme
**Install:** `[p]cog install wzyss-cogs remindme`

Set timers and get pinged or DMed when they complete. Set reminders via command or interactive embed with buttons for popular times and a custom duration modal. Supports user presets and guild-published presets. Timers persist across bot restarts.

**Tags:** reminder, utility, timer

---

### RockstarNewswire
**Install:** `[p]cog install wzyss-cogs rockstarnewswire`

Track and post Rockstar Games newswire updates. Automatically tracks Rockstar Games newswire feed and posts updates to configured channels. Supports multiple news types including GTA V, GTA VI, RDR2, music, fanart, and more.

**Requirements:** aiohttp, playwright

**Tags:** news, rockstar, gta, rdr2, notifications, utility

---

### SelfRoles
**Install:** `[p]cog install wzyss-cogs selfroles`

Interactive builder for self-assignable roles with buttons, reactions, and commands. Create embed messages with configurable buttons and reactions for users to self-assign roles. Supports exclusive role groups where only one role in a group can be active at a time.

**Tags:** roles, utility, self-assign, buttons, reactions

---

### TextReplace
**Install:** `[p]cog install wzyss-cogs textreplace`

Replace text using Regex. Automatically replaces text using Regex. This cog can repost messages with text replaced according to configured regex patterns.

**Tags:** text, utility, moderation, regex

---

### Tickets
**Install:** `[p]cog install wzyss-cogs tickets`

Button-based support tickets. Configure a channel with a customizable embed and "Create ticket" button; users open tickets by clicking. Each ticket gets a dedicated channel with manager buttons (claim, close), optional auto-assign and inactivity auto-close, and transcript logging. No user-facing commands.

**Tags:** tickets, support, utility, moderation

---

## Documentation

Full documentation for each cog is in the [docs](docs/) folder. See [docs/README.md](docs/README.md) for an index of all cogs.

## Contact

Contact on Discord: `wzyss`

## Credits

All cogs created by Wzyss.
