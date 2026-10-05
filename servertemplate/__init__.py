from .servertemplate import setup as setup

__red_end_user_data_statement__ = (
    "This cog stores server templates on disk: role, channel, emoji, and server settings "
    "captured from a guild, plus the creator's user ID and name and any member IDs used in "
    "channel permission overwrites. It does not store message content. Templates can be "
    "deleted from the template panel; data deletion requests anonymize the creator and "
    "remove the user's permission overwrites."
)
