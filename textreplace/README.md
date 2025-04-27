# TextReplace

A Red-DiscordBot cog that automatically replaces text using regex.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add wzyss-cogs https://github.com/itswzyss/wzyss-cogs
[p]cog install wzyss-cogs textreplace
[p]load textreplace
```

## Features

- Replace text in messages using regular expressions
- Multiple replacement rules per server
- Test replacements before applying them
- Full control over regex patterns and replacements
- Uses webhooks to repost messages with modified content
- Support for codeblock input to preserve backslashes and special characters
- Mass import/export of replacement rules for backup or sharing
- Duplicate pattern prevention to avoid conflicting rules

## Commands

- `[p]textreplace add <pattern> <replacement>` - Add a new text replacement rule using regex
- `[p]textreplace remove <rule_id> [rule_id...]` - Remove one or more replacement rules by ID
- `[p]textreplace list` - List all configured replacement rules
- `[p]textreplace toggle [on_off]` - Enable or disable text replacement
- `[p]textreplace test <text>` - Test how replacements will transform text
- `[p]textreplace export` - Export all replacement rules as a JSON codeblock
- `[p]textreplace import <json_data>` - Import replacement rules from JSON

## Examples

```
# Replace "hello world" with "goodbye world" (case insensitive)
[p]textreplace add "hello world" "goodbye world"

# Use regex groups to capture and reuse parts of the original text
[p]textreplace add "hello(\s+)world" "goodbye$1world"

# Replace X/Twitter URLs with alternative URLs
[p]textreplace add "https://twitter\.com/([^/]+)/status/(\d+)" "https://vxtwitter.com/$1/status/$2"
[p]textreplace add "https://x\.com/([^/]+)/status/(\d+)" "https://vxtwitter.com/$1/status/$2"
```

## Using Codeblocks for Regex

Discord may sometimes modify your regex pattern by escaping or removing backslashes before the message is sent. To avoid this issue, you can enclose your patterns in codeblocks:

```
# Using codeblocks to preserve backslashes and special characters
[p]textreplace add ```hello(\s+)world``` ```goodbye$1world```

# Complex regex with many special characters
[p]textreplace add ```https://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)``` ```https://youtu.be/$1```
```

Both triple backtick codeblocks (\`\`\` \`\`\`) and single backtick inline code (\` \`) are supported.

## Import and Export

You can export your rules to share them with others or to back them up:

```
[p]textreplace export
```

This will generate a JSON codeblock that you can copy.

To import rules from an export, use:

```
[p]textreplace import ```json
{
  "1": {"pattern": "pattern1", "replacement": "replacement1"},
  "2": {"pattern": "pattern2", "replacement": "replacement2"}
}```
```

The JSON data **must** be enclosed in a codeblock (\`\`\` \`\`\`) to ensure backslashes and special characters are preserved correctly. The import process will:
- Skip any invalid patterns
- Skip patterns that already exist in your server
- Report which rules were imported, skipped as duplicates, or invalid

## Permissions

- Admin or "Manage Server" permission is required to configure replacement rules
- The bot needs "Manage Webhooks" permission to repost modified messages