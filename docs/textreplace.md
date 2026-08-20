# TextReplace

**Short description:** Replace text using regular expressions.

## Description

Applies guild regex rules to messages and reposts the result via webhook. Multiple rules, test-before-apply, and JSON import/export.

## Install

```
[p]cog install wzyss-cogs textreplace
[p]load textreplace
```

Cog folder: [textreplace/README.md](../textreplace/README.md)

## Requirements

None. The bot needs **Manage Webhooks** (and **Manage Messages** to remove the original).

## Tags

text, utility, moderation, regex

## Commands

Group `[p]textreplace`. Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]textreplace add <pattern> <replacement>` | Add a rule |
| `[p]textreplace remove <id> [ids…]` | Remove by ID |
| `[p]textreplace list` | List rules |
| `[p]textreplace toggle [on_off]` | Enable or disable |
| `[p]textreplace test <text>` | Preview replacements |
| `[p]textreplace export` | JSON dump |
| `[p]textreplace import <json>` | Import (put JSON in a code block) |

Put patterns in code blocks so Discord does not eat backslashes:

```
[p]textreplace add ```https://x\.com/([^/]+)/status/(\d+)``` ```https://fixupx.com/$1/status/$2```
```

Duplicate patterns are skipped on import. For simple URL rewrites, [LinkReplacer](linkreplacer.md) is easier.
