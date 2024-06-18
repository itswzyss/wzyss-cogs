# This init is required for each cog.
# Import your main class from the cog's folder.
from .mysecondcog import MySecondCog


async def setup(bot):
    # Add the cog to the bot.
    await bot.add_cog(MySecondCog())
