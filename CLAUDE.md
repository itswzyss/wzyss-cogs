# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Reference

- **Red-DiscordBot docs:** https://docs.discord.red/en/stable/

## Project Overview

This is a **Red-DiscordBot cog repository** — a collection of 16 custom cogs (plugins) for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3.4.0+. There is no build system, test runner, or package manager. Cogs are loaded directly into a running Red bot instance via `[p]cog install` / `[p]cog load`.

## Development Workflow

There is no local test runner or linter configured. To test changes:
1. Load the cog into a running Red bot: `[p]reload <cogname>`
2. If adding a new cog: `[p]cog install wzyss-cogs <cogname>` then `[p]load <cogname>`

## Cog Architecture

Each cog is a self-contained directory with:
- `__init__.py` — exports `setup(bot)` and `__red_end_user_data_statement__`
- `<cogname>.py` — main implementation
- `info.json` — Red cog metadata (author, description, tags, min_bot_version)

### Core Framework Patterns

**Cog skeleton:**
```python
from redbot.core import Config, commands
from redbot.core.bot import Red

class MyCog(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=<UNIQUE_INT>)
        self.config.register_guild(**default_guild)
```

**Config** — Red's async data store, used for all persistence. Always scope to guild (`register_guild`) or member (`register_member`). No external databases.

**Logging:**
```python
import logging
log = logging.getLogger("red.wzyss-cogs.<cogname>")
```

### Command Groups

When using `@commands.group()`, use `pass` as the body — Red automatically sends help when no subcommand is invoked. Do **not** call `await ctx.send_help()` manually (it will show help twice):

```python
@commands.group()
async def mygroup(self, ctx: commands.Context):
    """Group description."""
    pass
```

### Customizable Embeds — Interactive Builder Pattern

Whenever a cog exposes customizable embeds (panels, welcome messages, etc.), use an **interactive embed builder** — not individual subcommands per field.

The pattern (see `selfroles/selfroles.py` as reference):
- A command (e.g. `[p]cogset panelembed`) sends a message with a `View` containing:
  - **Configure Embed** — opens a `Modal` with fields: title, description, color_hex, footer, thumbnail_url
  - **Preview** — shows current builder state as an ephemeral embed
  - **Save** — writes builder state to config
  - **Cancel** — clears state and disables the builder message
- Store per-user state in `_builder_states[(user_id, "embed_type")]`

Reference implementations: `selfroles/selfroles.py` (`EmbedConfigModal`, `RoleBuilderView`), `tickets/tickets.py`

### Persistent Views & Custom IDs

For buttons/views that must survive bot restarts, use `timeout=None` and deterministic `custom_id` strings in the format `"prefix:identifier"`. Register persistent views in `cog_load` or `__init__`.

## Commit Messages

Commit messages must describe only the change and its purpose. Do **not** include tool attribution ("Made with Cursor", "Built with Cursor", "Generated with Claude", "AI-assisted", etc.).
