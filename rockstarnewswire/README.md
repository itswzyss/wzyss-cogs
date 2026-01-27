# RockstarNewswire

A Red-DiscordBot cog that automatically tracks Rockstar Games newswire feed and posts updates to configured channels.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add wzyss-cogs https://github.com/itswzyss/wzyss-cogs
[p]cog install wzyss-cogs rockstarnewswire
[p]load rockstarnewswire
```

## Features

- Automatically tracks Rockstar Games newswire feed
- Supports multiple news types (GTA V, GTA VI, RDR2, music, fanart, etc.)
- Posts beautiful embeds with article information
- Configurable per-server channels and news types
- Automatic checking every 2 hours
- Manual check command for immediate updates
- Test command to verify fetching works

## Commands

### Configuration

- `[p]rockstarnewswire channel [channel]` - Set the channel for newswire notifications. Omit channel to clear.
- `[p]rockstarnewswire toggle [on_off]` - Enable or disable newswire tracking
- `[p]rockstarnewswire settings` - Show current settings

### News Types

- `[p]rockstarnewswire types` - List all available news types
- `[p]rockstarnewswire addtype <type>` - Add a news type to track
- `[p]rockstarnewswire removetype <type>` - Remove a news type from tracking
- `[p]rockstarnewswire listtypes` - List currently tracked news types

### Utility

- `[p]rockstarnewswire check` - Manually check for new posts right now
- `[p]rockstarnewswire test [type]` - Test fetching articles for a specific type (default: latest)

## Available News Types

- `latest` - Latest news from any category
- `gtav` - GTA V general news
- `gtavi` - GTA VI general news
- `rdr2` - Red Dead Redemption 2 general news
- `music` - Music production articles
- `fanart` - General fans' art articles
- `fanvideos` - General fans' showoff videos
- `creator` - Creator jobs articles featured by Rockstar
- `tips` - General game tips from Rockstar
- `rockstar` - Rockstar company updates
- `updates` - Any released game updates

## Usage Examples

### Basic Setup

1. Set a channel for notifications:
```
[p]rockstarnewswire channel #news
```

2. Add news types to track:
```
[p]rockstarnewswire addtype latest
[p]rockstarnewswire addtype gtav
[p]rockstarnewswire addtype gtavi
```

3. Enable tracking:
```
[p]rockstarnewswire toggle True
```

### Testing

Test if the cog can fetch articles:
```
[p]rockstarnewswire test latest
[p]rockstarnewswire test gtav
```

### Manual Check

Force an immediate check for new posts:
```
[p]rockstarnewswire check
```

## How It Works

1. The cog automatically checks for new posts every 2 hours
2. For each tracked news type, it fetches the latest articles from Rockstar's newswire
3. It compares the latest article URL with the last posted article URL
4. If a new article is found, it posts an embed to the configured channel
5. The embed includes the article title, description, image, and link

## Notes

- The cog stores the last posted article URL per news type to prevent duplicate posts
- If multiple news types are tracked, each type is checked independently
- The periodic check runs every 2 hours automatically
- Manual checks can be performed at any time using the `check` command

## Requirements

- `aiohttp` - For HTTP requests
- `beautifulsoup4` - For HTML parsing

These are automatically installed when you install the cog.

## Privacy

This cog stores notification channel and news type preferences on a per-guild basis. It does not store any user data beyond configuration preferences.
