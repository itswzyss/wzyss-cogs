import asyncio
import json
import logging
from typing import Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
import discord
from playwright.async_api import async_playwright, Browser, Page
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.rockstarnewswire")

# Rockstar GraphQL API endpoint
GRAPHQL_URL = "https://graph.rockstargames.com"

# Genre IDs mapping (from reference.js)
GENRE_IDS = {
    "latest": None,
    "music": 30,
    "rockstar": 43,
    "tips": 121,
    "gtavi": 666,
    "gtav": 702,
    "updates": 705,
    "fanvideos": 706,
    "fanart": 708,
    "creator": 728,
    "rdr2": 736,
}

# Refresh interval: 2 hours in seconds
REFRESH_INTERVAL = 7200


class RockstarNewswire(commands.Cog):
    """Track and post Rockstar Games newswire updates."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9876543212, force_registration=True
        )

        default_guild = {
            "enabled": False,
            "channel": None,  # Channel ID for notifications
            "news_types": ["latest"],  # List of news types to track
            "article_ids": {},  # {news_type: [article_id1, article_id2, ...]}
        }

        self.config.register_guild(**default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        self.news_hash: Optional[str] = None
        self.playwright_browser: Optional[Browser] = None
        self.check_task: Optional[asyncio.Task] = None
        log.info("RockstarNewswire cog initialized")

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.session = aiohttp.ClientSession()
        # Get initial hash token
        try:
            self.news_hash = await self.get_hash_token()
            log.info("Successfully obtained news hash token")
        except Exception as e:
            error_msg = str(e)
            if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                log.error(
                    "Playwright browsers not installed. Please run: playwright install chromium"
                )
            else:
                log.error(f"Failed to get initial hash token: {e}", exc_info=True)
        # Start the periodic check task
        self.check_task = self.bot.loop.create_task(self.periodic_check())

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if self.check_task:
            self.check_task.cancel()
        if self.session:
            await self.session.close()
        if self.playwright_browser:
            await self.playwright_browser.close()

    async def get_hash_token(self) -> str:
        """Extract the hash token from Rockstar's newswire page using Playwright."""
        log.debug("Extracting hash token from Rockstar newswire...")
        
        playwright = None
        browser = None
        
        try:
            playwright = await async_playwright().start()
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as e:
                if "Executable doesn't exist" in str(e) or "browser" in str(e).lower():
                    raise Exception(
                        "Playwright browsers not installed. Please run: playwright install chromium"
                    )
                raise
            page = await browser.new_page()

            hash_token = None
            hash_extracted = asyncio.Event()

            async def handle_request(request):
                nonlocal hash_token
                url = request.url
                if "operationName=NewswireList" in url:
                    # Extract hash from URL parameters
                    if "?" in url:
                        from urllib.parse import parse_qs, unquote
                        params_str = url.split("?")[1]
                        params = parse_qs(params_str)

                        if "extensions" in params:
                            extensions_str = unquote(params["extensions"][0])
                            try:
                                extensions = json.loads(extensions_str)
                                if "persistedQuery" in extensions:
                                    hash_token = extensions["persistedQuery"]["sha256Hash"]
                                    hash_extracted.set()
                                    await request.abort()
                                    return
                            except (json.JSONDecodeError, KeyError) as e:
                                log.debug(f"Error parsing extensions: {e}")

                await request.continue_()

            page.on("request", handle_request)

            # Navigate to newswire page
            await page.goto("https://www.rockstargames.com/newswire", wait_until="networkidle", timeout=30000)

            # Wait for hash to be extracted (with timeout)
            try:
                await asyncio.wait_for(hash_extracted.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                log.warning("Timeout waiting for hash token extraction")

            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()

            if hash_token:
                log.info(f"Successfully extracted hash token: {hash_token[:20]}...")
                return hash_token
            else:
                raise Exception("Failed to extract hash token from newswire page")

        except Exception as e:
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            if playwright:
                try:
                    await playwright.stop()
                except:
                    pass
            log.error(f"Error extracting hash token: {e}", exc_info=True)
            raise

    async def process_graphql_request(
        self, news_type: str
    ) -> Optional[Dict]:
        """Make a GraphQL request to Rockstar's API for a specific news type."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        if not self.news_hash:
            log.warning("No hash token available, fetching new one...")
            self.news_hash = await self.get_hash_token()

        genre_id = GENRE_IDS.get(news_type)
        if genre_id is None and news_type != "latest":
            log.error(f"Invalid news type: {news_type}")
            return None

        # Build query parameters (POST request with query string, as per reference)
        variables = {
            "page": 1,
            "tagId": genre_id,
            "metaUrl": "/newswire",
            "locale": "en_us",
        }

        extensions = {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": self.news_hash,
            }
        }

        params = {
            "operationName": "NewswireList",
            "variables": json.dumps(variables),
            "extensions": json.dumps(extensions),
        }

        query_string = urlencode(params)
        url = f"{GRAPHQL_URL}?{query_string}"

        try:
            # Note: Reference uses POST method with query string (unusual but that's how it works)
            async with self.session.post(
                url,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    log.error(
                        f"GraphQL request failed: HTTP {response.status} - {error_text}"
                    )
                    return None

                data = await response.json()

                # Check for errors
                if data.get("errors"):
                    error_msg = data["errors"][0].get("message", "Unknown error")
                    if error_msg == "PersistedQueryNotFound":
                        log.warning("Hash token expired, fetching new one...")
                        self.news_hash = await self.get_hash_token()
                        # Retry the request
                        return await self.process_graphql_request(news_type)
                    else:
                        log.error(f"GraphQL error: {error_msg}")
                        return None

                return data

        except aiohttp.ClientError as e:
            log.error(f"Network error in GraphQL request: {e}")
            return None
        except Exception as e:
            log.error(f"Error processing GraphQL request: {e}", exc_info=True)
            return None

    async def get_new_article(self, news_type: str) -> Optional[Dict]:
        """Get the latest article for a news type, checking if it's new."""
        log.debug(f"Checking for new articles in {news_type}")

        response = await self.process_graphql_request(news_type)
        if not response or not response.get("data"):
            return None

        try:
            article = response["data"]["posts"]["results"][0]
        except (KeyError, IndexError):
            log.warning(f"No articles found for type: {news_type}")
            return None

        article_id = str(article["id"])

        # Extract tags
        tags = []
        if "primary_tags" in article:
            for tag in article["primary_tags"]:
                if "name" in tag:
                    tags.append(tag["name"])

        # Build article URL
        article_url = article.get("url", "")
        if article_url and not article_url.startswith("http"):
            article_url = f"https://www.rockstargames.com{article_url}"

        # Get image
        image_url = None
        if "preview_images_parsed" in article:
            preview = article["preview_images_parsed"]
            if "newswire_block" in preview:
                image_url = preview["newswire_block"].get("d16x9")

        return {
            "id": article_id,
            "title": article.get("title", "Untitled"),
            "link": article_url,
            "img": image_url,
            "date": article.get("created", ""),
            "tags": tags,
        }

    async def create_news_embed(self, article: Dict[str, str]) -> discord.Embed:
        """Create a Discord embed for a news article."""
        # Format tags
        tags_str = " ".join([f"`{tag}`" for tag in article.get("tags", [])])

        embed = discord.Embed(
            title=article.get("title", "Rockstar Newswire Update"),
            url=article.get("link"),
            description=tags_str if tags_str else None,
            color=15258703,  # Rockstar orange color from reference
            timestamp=discord.utils.utcnow(),
        )

        embed.set_author(
            name="Newswire",
            url="https://www.rockstargames.com/newswire",
            icon_url="https://img.icons8.com/color/48/000000/rockstar-games.png",
        )

        if article.get("img"):
            embed.set_image(url=article["img"])

        embed.set_footer(
            text=article.get("date", ""),
            icon_url="https://img.icons8.com/color/48/000000/rockstar-games.png",
        )

        return embed

    async def check_and_post_news(self, guild: discord.Guild):
        """Check for new posts and post them to the configured channel."""
        if not await self.config.guild(guild).enabled():
            return

        channel_id = await self.config.guild(guild).channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            log.warning(
                f"Channel {channel_id} not found in guild {guild.name}, clearing config"
            )
            await self.config.guild(guild).channel.set(None)
            return

        news_types = await self.config.guild(guild).news_types()
        article_ids = await self.config.guild(guild).article_ids()

        for news_type in news_types:
            try:
                article = await self.get_new_article(news_type)
                if not article:
                    continue

                article_id = article["id"]
                news_type_ids = article_ids.get(news_type, [])

                # Check if we've seen this article before
                if article_id in news_type_ids:
                    log.debug(
                        f"Article {article_id} already posted for {news_type} in {guild.name}"
                    )
                    continue

                # New article found!
                embed = await self.create_news_embed(article)

                try:
                    await channel.send(embed=embed)
                    log.info(
                        f"Posted new {news_type} article to {guild.name}: {article.get('title')}"
                    )

                    # Add article ID to the list
                    news_type_ids.append(article_id)
                    article_ids[news_type] = news_type_ids
                    await self.config.guild(guild).article_ids.set(article_ids)

                except discord.Forbidden:
                    log.error(
                        f"Permission denied: Cannot send messages to channel {channel.name} "
                        f"in guild {guild.name}"
                    )
                except discord.HTTPException as e:
                    log.error(f"HTTP error posting news to {guild.name}: {e}")

            except Exception as e:
                log.error(
                    f"Error checking news type {news_type} for guild {guild.name}: {e}",
                    exc_info=True,
                )

    async def periodic_check(self):
        """Periodic task to check for new posts every 2 hours."""
        await self.bot.wait_until_ready()

        while True:
            try:
                # Wait 2 hours
                await asyncio.sleep(REFRESH_INTERVAL)

                log.info("Starting periodic newswire check")

                for guild in self.bot.guilds:
                    try:
                        await self.check_and_post_news(guild)
                    except Exception as e:
                        log.error(
                            f"Error in periodic check for guild {guild.name}: {e}",
                            exc_info=True,
                        )

            except asyncio.CancelledError:
                log.info("Periodic check task cancelled")
                break
            except Exception as e:
                log.error(f"Error in periodic check task: {e}", exc_info=True)
                # Wait a bit before retrying
                await asyncio.sleep(300)  # 5 minutes

    @commands.group(name="rockstarnewswire", aliases=["rnewswire", "rsnw"])
    @commands.admin_or_permissions(manage_guild=True)
    async def _rockstarnewswire(self, ctx: commands.Context):
        """Rockstar Newswire settings."""
        pass

    @_rockstarnewswire.command(name="channel")
    async def _set_channel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """Set the channel for newswire notifications.

        If no channel is provided, clears the current setting.
        """
        if channel is None:
            await self.config.guild(ctx.guild).channel.set(None)
            await ctx.send("Notification channel cleared.")
        else:
            await self.config.guild(ctx.guild).channel.set(channel.id)
            await ctx.send(f"Notification channel set to {channel.mention}")

    @_rockstarnewswire.command(name="types")
    async def _list_types(self, ctx: commands.Context):
        """List all available news types."""
        types_list = "\n".join(
            [
                f"• `{t}` - {self._get_type_description(t)}"
                for t in GENRE_IDS.keys()
            ]
        )
        embed = discord.Embed(
            title="Available News Types",
            description=types_list,
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed)

    @_rockstarnewswire.command(name="addtype")
    async def _add_type(self, ctx: commands.Context, news_type: str):
        """Add a news type to track.

        Use `[p]rockstarnewswire types` to see available types.
        """
        news_type = news_type.lower()
        if news_type not in GENRE_IDS:
            await ctx.send(
                f"Invalid news type. Use `{ctx.clean_prefix}rockstarnewswire types` to see available types."
            )
            return

        async with self.config.guild(ctx.guild).news_types() as types:
            if news_type not in types:
                types.append(news_type)
                await ctx.send(f"Added `{news_type}` to tracked news types.")
            else:
                await ctx.send(f"`{news_type}` is already being tracked.")

    @_rockstarnewswire.command(name="removetype")
    async def _remove_type(self, ctx: commands.Context, news_type: str):
        """Remove a news type from tracking."""
        news_type = news_type.lower()
        async with self.config.guild(ctx.guild).news_types() as types:
            if news_type in types:
                types.remove(news_type)
                await ctx.send(f"Removed `{news_type}` from tracked news types.")
            else:
                await ctx.send(f"`{news_type}` is not being tracked.")

    @_rockstarnewswire.command(name="listtypes")
    async def _list_tracked_types(self, ctx: commands.Context):
        """List currently tracked news types for this server."""
        types = await self.config.guild(ctx.guild).news_types()
        if not types:
            await ctx.send("No news types are currently being tracked.")
            return

        types_list = "\n".join([f"• `{t}`" for t in types])
        embed = discord.Embed(
            title="Tracked News Types",
            description=types_list or "None",
            color=await ctx.embed_color(),
        )
        await ctx.send(embed=embed)

    @_rockstarnewswire.command(name="toggle")
    async def _toggle(self, ctx: commands.Context, on_off: Optional[bool] = None):
        """Toggle newswire tracking on or off."""
        if on_off is None:
            current = await self.config.guild(ctx.guild).enabled()
            await self.config.guild(ctx.guild).enabled.set(not current)
            state = "enabled" if not current else "disabled"
        else:
            await self.config.guild(ctx.guild).enabled.set(on_off)
            state = "enabled" if on_off else "disabled"

        await ctx.send(f"Rockstar Newswire tracking is now {state}.")

    @_rockstarnewswire.command(name="check")
    async def _check_now(self, ctx: commands.Context):
        """Manually check for new posts right now."""
        await ctx.send("Checking for new posts...")
        try:
            await self.check_and_post_news(ctx.guild)
            await ctx.send("Check complete!")
        except Exception as e:
            log.error(f"Error in manual check: {e}", exc_info=True)
            await ctx.send(f"Error checking for news: {e}")

    @_rockstarnewswire.command(name="settings")
    async def _show_settings(self, ctx: commands.Context):
        """Show current newswire settings."""
        settings = await self.config.guild(ctx.guild).all()

        channel_id = settings.get("channel")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        news_types = settings.get("news_types", [])

        embed = discord.Embed(
            title="Rockstar Newswire Settings",
            color=await ctx.embed_color(),
        )

        embed.add_field(
            name="Enabled", value="Yes" if settings.get("enabled") else "No", inline=True
        )
        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Tracked Types",
            value=f"{len(news_types)} type(s)" if news_types else "None",
            inline=True,
        )

        if news_types:
            types_list = ", ".join([f"`{t}`" for t in news_types])
            embed.add_field(name="Types", value=types_list, inline=False)

        await ctx.send(embed=embed)

    @_rockstarnewswire.command(name="test")
    async def _test_fetch(self, ctx: commands.Context, news_type: str = "latest"):
        """Test fetching articles for a specific news type."""
        news_type = news_type.lower()
        if news_type not in GENRE_IDS:
            await ctx.send(
                f"Invalid news type. Use `{ctx.clean_prefix}rockstarnewswire types` to see available types."
            )
            return

        if not self.news_hash:
            await ctx.send("No hash token available. Attempting to fetch one...")
            try:
                self.news_hash = await self.get_hash_token()
            except Exception as e:
                error_msg = str(e)
                if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                    await ctx.send(
                        "❌ **Playwright browsers not installed!**\n\n"
                        "Please run: `playwright install chromium`\n\n"
                        "Use `[p]rockstarnewswire checkplaywright` to verify installation."
                    )
                    return
                else:
                    await ctx.send(f"Error fetching hash token: {e}")
                    return

        await ctx.send(f"Fetching {news_type} articles...")
        try:
            article = await self.get_new_article(news_type)
            if not article:
                await ctx.send(f"No articles found for type `{news_type}`.")
                return

            embed = await self.create_news_embed(article)
            embed.set_footer(
                text=f"Test - {news_type} | Article ID: {article.get('id')}",
                icon_url="https://img.icons8.com/color/48/000000/rockstar-games.png",
            )
            await ctx.send(embed=embed)

        except Exception as e:
            log.error(f"Error in test fetch: {e}", exc_info=True)
            error_msg = str(e)
            if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                await ctx.send(
                    "❌ **Playwright browsers not installed!**\n\n"
                    "Please run: `playwright install chromium`"
                )
            else:
                await ctx.send(f"Error fetching articles: {e}")

    @_rockstarnewswire.command(name="refreshhash")
    async def _refresh_hash(self, ctx: commands.Context):
        """Manually refresh the hash token (admin only)."""
        await ctx.send("Refreshing hash token...")
        try:
            self.news_hash = await self.get_hash_token()
            await ctx.send(f"Hash token refreshed successfully: `{self.news_hash[:20]}...`")
        except Exception as e:
            log.error(f"Error refreshing hash: {e}", exc_info=True)
            error_msg = str(e)
            if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                await ctx.send(
                    "❌ **Playwright browsers not installed!**\n\n"
                    "Please run the following command in your bot's environment:\n"
                    "```bash\nplaywright install chromium\n```\n\n"
                    "If you're using Red's virtual environment, activate it first:\n"
                    "```bash\n# Activate venv, then:\nplaywright install chromium\n```"
                )
            else:
                await ctx.send(f"Error refreshing hash token: {e}")

    @_rockstarnewswire.command(name="checkplaywright")
    async def _check_playwright(self, ctx: commands.Context):
        """Check if Playwright is properly installed and configured."""
        await ctx.send("Checking Playwright installation...")
        try:
            playwright = await async_playwright().start()
            try:
                browser = await playwright.chromium.launch(headless=True)
                await browser.close()
                await playwright.stop()
                await ctx.send("✅ Playwright is properly installed and working!")
            except Exception as e:
                await playwright.stop()
                error_msg = str(e)
                if "Executable doesn't exist" in error_msg or "browser" in error_msg.lower():
                    await ctx.send(
                        "❌ **Playwright browsers not installed!**\n\n"
                        "Please run the following command:\n"
                        "```bash\nplaywright install chromium\n```\n\n"
                        "If you're using Red's virtual environment, activate it first."
                    )
                else:
                    await ctx.send(f"❌ Playwright error: {e}")
        except Exception as e:
            await ctx.send(f"❌ Failed to check Playwright: {e}")

    def _get_type_description(self, news_type: str) -> str:
        """Get a human-readable description for a news type."""
        descriptions = {
            "latest": "Latest news from any category",
            "gtav": "GTA V general news",
            "gtavi": "GTA VI general news",
            "rdr2": "Red Dead Redemption 2 general news",
            "music": "Music production articles",
            "fanart": "General fans' art articles",
            "fanvideos": "General fans' showoff videos",
            "creator": "Creator jobs articles featured by Rockstar",
            "tips": "General game tips from Rockstar",
            "rockstar": "Rockstar company updates",
            "updates": "Any released game updates",
        }
        return descriptions.get(news_type, "News articles")


async def setup(bot: Red):
    """Load the RockstarNewswire cog."""
    cog = RockstarNewswire(bot)
    await bot.add_cog(cog)
    log.info("RockstarNewswire cog loaded successfully")
