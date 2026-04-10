from .announcer import Announcer

__red_end_user_data_statement__ = (
    "This cog stores no personal data. Role membership is read at broadcast time only."
)


async def setup(bot):
    await bot.add_cog(Announcer(bot))
