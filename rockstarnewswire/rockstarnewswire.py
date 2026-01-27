import asyncio
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

import aiohttp
import discord
from bs4 import BeautifulSoup
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.wzyss-cogs.rockstarnewswire")

# Rockstar newswire base URL
NEWSWIRE_BASE_URL = "https://www.rockstargames.com/newswire"

# News type mappings to URL paths
NEWS_TYPE_PATHS = {
    "latest": "",
    "gtav": "category/gta-v",
    "gtavi": "category/gta-vi",
    "rdr2": "category/red-dead-redemption-2",
    "music": "category/music",
    "fanart": "category/fan-art",
    "fanvideos": "category/fan-videos",
    "creator": "category/creator",
    "tips": "category/tips",
    "rockstar": "category/rockstar",
    "updates": "category/updates",
}


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
            "last_posts": {},  # {news_type: {"url": str, "title": str, "date": str}}
        }

        self.config.register_guild(**default_guild)
        self.session: Optional[aiohttp.ClientSession] = None
        log.info("RockstarNewswire cog initialized")

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.session = aiohttp.ClientSession()
        # Start the periodic check task
        self.check_task = self.bot.loop.create_task(self.periodic_check())

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        if hasattr(self, "check_task"):
            self.check_task.cancel()
        if self.session:
            await self.session.close()

    async def get_newswire_articles(
        self, news_type: str = "latest"
    ) -> List[Dict[str, str]]:
        """Fetch articles from Rockstar newswire for a specific type.

        Returns a list of article dictionaries with keys: url, title, date, description, image
        """
        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            # Build the URL
            path = NEWS_TYPE_PATHS.get(news_type, "")
            if path:
                url = f"{NEWSWIRE_BASE_URL}/{path}"
            else:
                url = NEWSWIRE_BASE_URL

            log.debug(f"Fetching newswire from: {url}")

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    log.error(f"Failed to fetch newswire: HTTP {response.status}")
                    return []

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                articles = []

                # Find article elements - this selector may need adjustment based on actual HTML structure
                # Common patterns for newswire sites
                article_selectors = [
                    "article",
                    ".article",
                    ".news-item",
                    ".newswire-item",
                    "[data-article-id]",
                ]

                article_elements = []
                for selector in article_selectors:
                    found = soup.select(selector)
                    if found:
                        article_elements = found
                        log.debug(f"Found {len(found)} articles using selector: {selector}")
                        break

                if not article_elements:
                    # Fallback: try to find links that look like article links
                    article_elements = soup.select('a[href*="/newswire/"]')

                for element in article_elements[:10]:  # Limit to 10 most recent
                    try:
                        article_data = self._parse_article_element(element, soup)
                        if article_data:
                            articles.append(article_data)
                    except Exception as e:
                        log.debug(f"Error parsing article element: {e}")
                        continue

                log.info(f"Successfully fetched {len(articles)} articles for type: {news_type}")
                return articles

        except aiohttp.ClientError as e:
            log.error(f"Network error fetching newswire: {e}")
            return []
        except Exception as e:
            log.error(f"Error fetching newswire: {e}", exc_info=True)
            return []

    def _parse_article_element(self, element, soup: BeautifulSoup) -> Optional[Dict[str, str]]:
        """Parse an article element and extract relevant data."""
        article = {}

        # Try to find the link
        link_elem = element if element.name == "a" else element.find("a")
        if not link_elem or not link_elem.get("href"):
            return None

        href = link_elem.get("href")
        if href.startswith("/"):
            article["url"] = urljoin(NEWSWIRE_BASE_URL, href)
        elif href.startswith("http"):
            article["url"] = href
        else:
            return None

        # Try to find title
        title_elem = (
            element.find(class_=re.compile(r"title|heading|headline", re.I))
            or element.find("h1")
            or element.find("h2")
            or element.find("h3")
            or link_elem
        )
        if title_elem:
            article["title"] = title_elem.get_text(strip=True)

        # Try to find description/excerpt
        desc_elem = (
            element.find(class_=re.compile(r"description|excerpt|summary|content", re.I))
            or element.find("p")
        )
        if desc_elem:
            article["description"] = desc_elem.get_text(strip=True)[:500]  # Limit length

        # Try to find image
        img_elem = element.find("img")
        if img_elem and img_elem.get("src"):
            img_src = img_elem.get("src")
            if img_src.startswith("/"):
                article["image"] = urljoin(NEWSWIRE_BASE_URL, img_src)
            elif img_src.startswith("http"):
                article["image"] = img_src

        # Try to find date
        date_elem = element.find(class_=re.compile(r"date|time|published", re.I))
        if date_elem:
            article["date"] = date_elem.get_text(strip=True)

        # Only return if we have at least URL and title
        if article.get("url") and article.get("title"):
            return article

        return None

    async def create_news_embed(self, article: Dict[str, str]) -> discord.Embed:
        """Create a Discord embed for a news article."""
        embed = discord.Embed(
            title=article.get("title", "Rockstar Newswire Update"),
            url=article.get("url"),
            color=discord.Color(0xFF0000),  # Rockstar red color
            timestamp=discord.utils.utcnow(),
        )

        if article.get("description"):
            embed.description = article["description"]

        if article.get("image"):
            embed.set_image(url=article["image"])

        embed.set_footer(text="Rockstar Games Newswire")

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
        last_posts = await self.config.guild(guild).last_posts()

        for news_type in news_types:
            try:
                articles = await self.get_newswire_articles(news_type)
                if not articles:
                    continue

                # Get the most recent article
                latest_article = articles[0]

                # Check if this is a new post
                last_post = last_posts.get(news_type, {})
                if last_post.get("url") == latest_article.get("url"):
                    # Same post, skip
                    continue

                # New post found!
                embed = await self.create_news_embed(latest_article)

                # Add news type to embed if not "latest"
                if news_type != "latest":
                    embed.add_field(
                        name="Category", value=news_type.upper(), inline=True
                    )

                try:
                    await channel.send(embed=embed)
                    log.info(
                        f"Posted new {news_type} article to {guild.name}: {latest_article.get('title')}"
                    )

                    # Update last post
                    last_posts[news_type] = {
                        "url": latest_article.get("url"),
                        "title": latest_article.get("title"),
                        "date": latest_article.get("date", ""),
                    }
                    await self.config.guild(guild).last_posts.set(last_posts)

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
                # Wait 2 hours (7200 seconds)
                await asyncio.sleep(7200)

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
            [f"• `{t}` - {self._get_type_description(t)}" for t in NEWS_TYPE_PATHS.keys()]
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
        if news_type not in NEWS_TYPE_PATHS:
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

        embed.add_field(name="Enabled", value="Yes" if settings.get("enabled") else "No", inline=True)
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
        if news_type not in NEWS_TYPE_PATHS:
            await ctx.send(
                f"Invalid news type. Use `{ctx.clean_prefix}rockstarnewswire types` to see available types."
            )
            return

        await ctx.send(f"Fetching {news_type} articles...")
        try:
            articles = await self.get_newswire_articles(news_type)
            if not articles:
                await ctx.send(f"No articles found for type `{news_type}`.")
                return

            # Show first 3 articles
            for i, article in enumerate(articles[:3], 1):
                embed = await self.create_news_embed(article)
                embed.set_footer(
                    text=f"Test - Article {i} of {min(3, len(articles))} | {news_type}"
                )
                await ctx.send(embed=embed)

            if len(articles) > 3:
                await ctx.send(f"... and {len(articles) - 3} more articles found.")

        except Exception as e:
            log.error(f"Error in test fetch: {e}", exc_info=True)
            await ctx.send(f"Error fetching articles: {e}")

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
