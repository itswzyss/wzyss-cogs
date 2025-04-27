# LinkReplacer

A Red-DiscordBot cog that automatically replaces links with configured alternatives.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add linkreplacer https://github.com/yourusername/red-discord-link-replacer
[p]cog install linkreplacer
[p]load linkreplacer
```

## Usage

### Adding a replacement rule

```
[p]linkreplacer add <source_url> <target_url>
```

You can use `*` as a wildcard in both the source and target URLs. The wildcard part of the source URL will be transferred to the target URL.

Example:
```
[p]linkreplacer add https://x.com/* https://fixupx.com/*
```

This will replace links like `https://x.com/username/status/123456789` with `https://fixupx.com/username/status/123456789`.

### Removing a replacement rule

```
[p]linkreplacer remove <source_url>
```

Example:
```
[p]linkreplacer remove https://x.com/*
```

### Listing all replacement rules

```
[p]linkreplacer list
```

### Enabling/Disabling the cog

```
[p]linkreplacer toggle [True|False]
```

Without arguments, it toggles the current state. With `True` or `False`, it sets the state explicitly.

## How it works

The cog monitors all messages in the server. When a message contains a link that matches one of the configured patterns, the bot:

1. Deletes the original message
2. Posts a new message with the replaced link, mimicking the original author's name and avatar (using webhooks if possible)

## Permissions

- The bot needs `Manage Messages` permission to delete the original messages
- For the best experience, the bot should have `Manage Webhooks` permission to create messages that appear to come from the original user

## Privacy

This cog does not store any user data. It only stores the link replacement configurations on a per-guild basis. 