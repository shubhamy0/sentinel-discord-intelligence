"""
Discord User Activity Tracker Extension.
Provides telemetry tracking for messages and voice channel events.
"""

from .cog import ActivityTracker

async def setup(bot) -> None:
    """Standard entry point for loading the extension."""
    # Retrieve database path and ignore_bots settings dynamically from bot configurations
    db_path = getattr(bot, 'tracker_db_path', 'activity_tracker.db')
    ignore_bots = getattr(bot, 'tracker_ignore_bots', True)
    presence_log_channel_id = getattr(bot, 'tracker_presence_log_channel_id', None)
    
    await bot.add_cog(ActivityTracker(
        bot,
        db_path,
        ignore_bots=ignore_bots,
        presence_log_channel_id=presence_log_channel_id
    ))
