from .gambling import Gambling

__red_end_user_data_statement__ = (
    "This cog stores per-member virtual credit balances, game statistics, "
    "and last daily bonus claim timestamps. All data is guild-specific. "
    "No real money or financial data is stored."
)


async def setup(bot):
    await bot.add_cog(Gambling(bot))
