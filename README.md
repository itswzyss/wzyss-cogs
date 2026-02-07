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

### ChannelNotify
**Install:** `[p]cog install wzyss-cogs channelnotify`

Automatically ping roles when messages are sent in configured channels. Useful for creators posting to social media - when they link an X post, etc., in a channel, roles will be pinged. Supports multiple channels and roles per channel with configurable cooldowns.

**Tags:** notifications, utility, roles, channels

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

### RockstarNewswire
**Install:** `[p]cog install wzyss-cogs rockstarnewswire`

Track and post Rockstar Games newswire updates. Automatically tracks Rockstar Games newswire feed and posts updates to configured channels. Supports multiple news types including GTA V, GTA VI, RDR2, music, fanart, and more.

**Requirements:** aiohttp, playwright

**Tags:** news, rockstar, gta, rdr2, notifications, utility

---

### TextReplace
**Install:** `[p]cog install wzyss-cogs textreplace`

Replace text using Regex. Automatically replaces text using Regex. This cog can repost messages with text replaced according to configured regex patterns.

**Tags:** text, utility, moderation, regex

---

## Contact

Contact on Discord: `wzyss`

## Credits

All cogs created by Wzyss.
