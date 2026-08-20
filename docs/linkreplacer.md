# LinkReplacer

**Short description:** Replace links with configured alternatives.

## Description

Deletes matching messages and reposts them with rewritten URLs (webhook impersonation when possible). Wildcards (`*`) copy the rest of the path from source to target.

## Install

```
[p]cog install wzyss-cogs linkreplacer
[p]load linkreplacer
```

Cog folder: [linkreplacer/README.md](../linkreplacer/README.md)

## Requirements

None. The bot needs **Manage Messages**. **Manage Webhooks** is recommended so the repost looks like the original author.

## Tags

links, utility, moderation

## Commands

Group `[p]linkreplacer`. Requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `[p]linkreplacer add <source> <target>` | Add a rule (`*` wildcard allowed) |
| `[p]linkreplacer remove <source>` | Remove a rule |
| `[p]linkreplacer list` | List rules |
| `[p]linkreplacer toggle [true/false]` | Enable or disable |

Example:

```
[p]linkreplacer add https://x.com/* https://fixupx.com/*
```

For regex replacements of arbitrary text, see [TextReplace](textreplace.md).
