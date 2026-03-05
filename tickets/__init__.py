from .tickets import setup

__red_end_user_data_statement__ = (
    "This cog stores ticket configuration (panel channel, category, roles, embed settings) "
    "and open ticket state (channel id, creator, timestamps, assigned_to) on a per-guild basis. "
    "Ticket transcripts may be sent to a configured log channel. "
    "No user data is stored beyond what Discord provides through channel membership and messages."
)
