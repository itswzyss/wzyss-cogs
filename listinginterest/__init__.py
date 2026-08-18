from .listinginterest import setup as setup

__red_end_user_data_statement__ = (
    "This cog stores per-guild listing metadata (source and button message/channel IDs, labels), "
    "open interest channels (buyer id, listing id, timestamps), and notify configuration "
    "(ping/DM user and role IDs, category, manager roles). "
    "Interest DMs may be sent to configured users and members of configured roles. "
    "No listing message content is stored beyond Discord snowflake IDs."
)
