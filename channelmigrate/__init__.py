from .channelmigrate import setup as setup

__red_end_user_data_statement__ = (
    "This cog stores migration job records: source and destination channel IDs and names, "
    "progress counters, the last copied message ID, and the user ID of who started the job. "
    "Message content is reposted to the destination channel but is not stored by the cog. "
    "Data deletion requests remove the user ID from job records."
)
