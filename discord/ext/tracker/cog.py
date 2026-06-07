"""
Cog implementing the Discord User Activity Tracker listeners and commands.
Tracks message sending, voice connections, and generates rich telemetry reports.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

import discord
from discord.ext import commands

from .db import (
    log_message,
    log_voice_join,
    log_voice_leave,
    find_user_by_name,
    fetch_db_user,
    log_presence_change,
    fetch_recent_presence,
    add_tracked_user,
    remove_tracked_user,
    get_tracked_users,
    is_tracked_user
)
from .analytics import AnalyticsEngine

logger = logging.getLogger('discord.ext.tracker.cog')

def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        seconds_left = seconds % 60
        return f"{minutes}m {seconds_left}s"
    hours = minutes // 60
    if hours < 24:
        minutes_left = minutes % 60
        return f"{hours}h {minutes_left}m"
    days = hours // 24
    hours_left = hours % 24
    return f"{days}d {hours_left}h"

def draw_ascii_heatmap(data: List[Tuple[int, int, int]]) -> str:
    """
    Generate an ASCII-art representations of the activity heatmap.
    Rows: Sun-Sat, Columns: 24 hours.
    """
    grid = [[0 for _ in range(24)] for _ in range(7)]
    for day, hr, cnt in data:
        if 0 <= day < 7 and 0 <= hr < 24:
            grid[day][hr] = cnt

    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    density_chars = {
        0: "  ",
        1: "░░",
        2: "▒▒",
        3: "▓▓",
        4: "██"
    }

    def get_density(val: int) -> int:
        if val == 0: return 0
        if val <= 3: return 1
        if val <= 10: return 2
        if val <= 25: return 3
        return 4

    lines = []
    lines.append("      00  02  04  06  08  10  12  14  16  18  20  22")
    lines.append("     -------------------------------------------------")
    for day_idx in range(7):
        day_cells = []
        for hr in range(24):
            day_cells.append(density_chars[get_density(grid[day_idx][hr])])
        cells_str = "".join(day_cells)
        lines.append(f"{days[day_idx]} | {cells_str}")
    lines.append("     -------------------------------------------------")
    lines.append("Key:   ░ [1-3]  ▒ [4-10]  ▓ [11-25]  █ [26+] events (UTC)")
    return "\n".join(lines)


class ActivityTracker(commands.Cog):
    """
    Tracks and analyzes user message and voice activities.
    """
    def __init__(
        self,
        bot: commands.Bot,
        db_path: str,
        ignore_bots: bool = True,
        presence_log_channel_id: Optional[int] = None
    ):
        self.bot = bot
        self.db_path = db_path
        self.ignore_bots = ignore_bots
        self.presence_log_channel_id = presence_log_channel_id
        self.analytics = AnalyticsEngine(db_path)
        
        # Start initialization task
        self._db_init_task = self.bot.loop.create_task(self._initialize_database())

    async def _initialize_database(self) -> None:
        """Initialize SQLite database tables and configurations."""
        try:
            from .db import init_db, get_tracked_users
            await init_db(self.db_path)
            logger.info("Activity Tracker database is ready.")
            # Auto-subscribe to presence updates of tracked users
            self.bot.loop.create_task(self.subscribe_to_all_tracked_users())
        except Exception as e:
            logger.exception("Failed to initialize tracker database in Cog: %s", e)

    async def subscribe_to_all_tracked_users(self) -> None:
        """Fetch all persistently tracked user IDs and subscribe to them."""
        # Wait for the bot to be fully ready
        await self.bot.wait_until_ready()
        try:
            user_ids = await get_tracked_users(self.db_path)
            if not user_ids:
                return
            
            logger.info("Subscribing to presence updates for %d tracked users...", len(user_ids))
            count = 0
            for guild in self.bot.guilds:
                # Find which of these users are in which guild
                guild_member_ids = [uid for uid in user_ids if guild.get_member(uid) is not None]
                if guild_member_ids:
                    try:
                        # Request/subscribe to presence updates of those members
                        await guild.subscribe_to(members=[discord.Object(id=uid) for uid in guild_member_ids])
                        count += len(guild_member_ids)
                    except Exception as e:
                        logger.error("Failed to subscribe to members in guild %s: %s", guild.name, e)
            logger.info("Successfully subscribed to %d user presence connections across servers.", count)
        except Exception as e:
            logger.exception("Error in subscribe_to_all_tracked_users: %s", e)

    async def resolve_user(self, ctx: commands.Context, user_str: Optional[str] = None) -> Tuple[int, str]:
        """
        Robustly resolves a user argument.
        Supports Mentions, IDs, and Username searches in the DB.
        """
        if user_str is None:
            return ctx.author.id, str(ctx.author)

        user_id = None
        username = None

        # Try standard converter (resolves mentions/usernames in cache)
        try:
            converter = commands.UserConverter()
            user = await converter.convert(ctx, user_str)
            user_id, username = user.id, str(user)
        except Exception:
            pass

        # Try parsing as direct numeric ID
        if user_id is None and user_str.isdigit():
            u_id = int(user_str)
            try:
                user = await self.bot.fetch_user(u_id)
                user_id, username = user.id, str(user)
            except Exception:
                pass

            if user_id is None:
                # Check database users table
                db_user = await fetch_db_user(self.db_path, u_id)
                if db_user:
                    user_id, username = db_user['user_id'], db_user['username']

        if user_id is None:
            # Fallback: search usernames in database history
            db_user = await find_user_by_name(self.db_path, user_str)
            if db_user:
                user_id, username = db_user['user_id'], db_user['username']

        if user_id is None:
            raise commands.BadArgument(f"Could not resolve user '{user_str}' from cache or history.")

        # Automatically track presence for resolved users (if not self)
        if user_id != ctx.author.id:
            try:
                if not await is_tracked_user(self.db_path, user_id):
                    # Resolve current status from mutual guilds
                    current_status = "offline"
                    for guild in self.bot.guilds:
                        member = guild.get_member(user_id)
                        if member and member.status:
                            current_status = member.status.name if hasattr(member.status, 'name') else str(member.status)
                            break
                    
                    # Register tracked user
                    await add_tracked_user(self.db_path, user_id)
                    
                    # Log initial presence transition so the user gets immediate feedback
                    timestamp_str = datetime.now(timezone.utc).isoformat()
                    await log_presence_change(
                        self.db_path,
                        user_id,
                        username,
                        "offline",
                        current_status,
                        timestamp_str
                    )

                # Actively subscribe in mutual guilds
                for guild in self.bot.guilds:
                    if guild.get_member(user_id) is not None:
                        await guild.subscribe_to(members=[discord.Object(id=user_id)])
            except Exception:
                pass

        return user_id, username

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Re-verify and subscribe to tracked users on startup or reconnection."""
        self.bot.loop.create_task(self.subscribe_to_all_tracked_users())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Record messages sent by users."""
        if message.author.id == self.bot.user.id and getattr(self.bot, 'self_bot', False):
            # Don't skip logging self-bot activities, they count! But check user config.
            pass
        elif message.author.bot and self.ignore_bots:
            return

        timestamp = message.created_at.astimezone(timezone.utc).isoformat()
        guild_id = message.guild.id if message.guild else None
        
        # Run DB logging task in background
        self.bot.loop.create_task(
            log_message(
                self.db_path,
                message.id,
                message.author.id,
                str(message.author),
                timestamp,
                guild_id,
                message.channel.id
            )
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ) -> None:
        """Record voice channel joins, leaves, and switches."""
        if member.bot and self.ignore_bots:
            return

        # We only track voice channel movements (joins/leaves/switches)
        if before.channel == after.channel:
            return

        timestamp = datetime.now(timezone.utc).isoformat()

        # Handle voice channel leave/switch-out
        if before.channel is not None:
            self.bot.loop.create_task(
                log_voice_leave(
                    self.db_path,
                    member.id,
                    str(member),
                    before.channel.guild.id,
                    before.channel.id,
                    timestamp
                )
            )

        # Handle voice channel join/switch-in
        if after.channel is not None:
            self.bot.loop.create_task(
                log_voice_join(
                    self.db_path,
                    member.id,
                    str(member),
                    after.channel.guild.id,
                    after.channel.id,
                    timestamp
                )
            )

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        """Record status changes of users and log them in real-time if configured."""
        if getattr(after, 'bot', False) and self.ignore_bots:
            return

        if before.status == after.status:
            return

        old_status = before.status.name if hasattr(before.status, 'name') else str(before.status)
        new_status = after.status.name if hasattr(after.status, 'name') else str(after.status)

        timestamp = datetime.now(timezone.utc)
        timestamp_str = timestamp.isoformat()

        try:
            duration = await log_presence_change(
                self.db_path,
                after.id,
                str(after),
                old_status,
                new_status,
                timestamp_str
            )
        except Exception as e:
            logger.exception("Failed to write presence update to DB: %s", e)
            duration = None

        # Real-time channel logging
        if self.presence_log_channel_id:
            channel = self.bot.get_channel(self.presence_log_channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(self.presence_log_channel_id)
                except Exception:
                    logger.warning("Could not fetch presence logging channel with ID %s", self.presence_log_channel_id)

            if channel:
                time_str = timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
                duration_suffix = f" (duration: {format_duration(duration)})" if duration is not None else ""
                log_message_text = f"[{time_str}] | 👤 **Presence Update**: Status changed from `{old_status}` to `{new_status}`{duration_suffix}"
                
                try:
                    await channel.send(log_message_text)
                except Exception as e:
                    logger.error("Failed to send presence log message: %s", e)

    @commands.command(name="lastseen")
    async def lastseen(self, ctx: commands.Context, *, user_query: Optional[str] = None) -> None:
        """Displays when the target user was last active and what they did."""
        try:
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            await ctx.send(str(e))
            return

        activity = await self.analytics.get_last_seen_activity(user_id)
        if not activity:
            await ctx.send(f"No tracking activity found for **{username}**.")
            return

        act_time = datetime.fromisoformat(activity['timestamp'])
        epoch = int(act_time.timestamp())
        
        time_display = f"<t:{epoch}:F> (<t:{epoch}:R>)"
        channel_id = activity['channel_id']
        
        if activity['type'] == 'message':
            await ctx.send(
                f"**{username}** was last seen sending a message in <#{channel_id}> on {time_display}."
            )
        elif activity['type'] == 'voice_join':
            await ctx.send(
                f"**{username}** was last seen joining voice channel <#{channel_id}> on {time_display}."
            )
        elif activity['type'] == 'voice_leave':
            await ctx.send(
                f"**{username}** was last seen leaving voice channel <#{channel_id}> on {time_display}."
            )

    @commands.command(name="activity")
    async def activity(self, ctx: commands.Context, *, user_query: Optional[str] = None) -> None:
        """Displays a summary of a user's logged activity metrics."""
        try:
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            await ctx.send(str(e))
            return

        counts = await self.analytics.get_user_stats(user_id)
        voice_dur = await self.analytics.get_voice_duration(user_id)
        averages = await self.analytics.get_average_daily_activity(user_id)
        last_seen_data = await self.analytics.get_last_seen_activity(user_id)
        
        # Formulate last seen text
        if last_seen_data:
            last_seen_dt = datetime.fromisoformat(last_seen_data['timestamp'])
            last_seen_str = f"<t:{int(last_seen_dt.timestamp())}:R>"
        else:
            last_seen_str = "Never"

        # Check ongoing voice status
        voice_status = "Offline"
        if ctx.guild:
            member = ctx.guild.get_member(user_id)
            if member and member.voice and member.voice.channel:
                voice_status = f"In Voice: <#{member.voice.channel.id}>"

        voice_dur_str = format_duration(voice_dur)
        avg_voice_str = format_duration(averages['avg_voice_duration'])

        embed_text = (
            f"📊 **Activity Summary for {username}**\n"
            f"• **Messages Logged**: {counts['message_count']}\n"
            f"• **Voice Duration**: {voice_dur_str}\n"
            f"• **Daily Average Messages**: {averages['avg_messages']:.1f}/active day\n"
            f"• **Daily Average Voice**: {avg_voice_str}/active day\n"
            f"• **Voice Sessions**: {counts['voice_session_count']}\n"
            f"• **Voice Status**: {voice_status}\n"
            f"• **Last Active**: {last_seen_str}"
        )
        await ctx.send(embed_text)

    @commands.command(name="stats")
    async def stats(self, ctx: commands.Context, *, user_query: Optional[str] = None) -> None:
        """Displays peak usage statistics and a visual weekly activity heatmap."""
        try:
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            await ctx.send(str(e))
            return

        peak_hr_data = await self.analytics.get_most_active_hour(user_id)
        peak_day_data = await self.analytics.get_most_active_day(user_id)
        voice_dur = await self.analytics.get_voice_duration(user_id)
        counts = await self.analytics.get_user_stats(user_id)
        heatmap_data = await self.analytics.get_heatmap_data(user_id)

        # Parse Peak Hour
        if peak_hr_data:
            hr, hr_cnt = peak_hr_data
            peak_hr_str = f"{hr:02d}:00 - {(hr + 1) % 24:02d}:00 UTC ({hr_cnt} events)"
        else:
            peak_hr_str = "N/A"

        # Parse Peak Day
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        if peak_day_data:
            day_idx, day_cnt = peak_day_data
            peak_day_str = f"{days[day_idx]} ({day_cnt} events)"
        else:
            peak_day_str = "N/A"

        voice_dur_str = format_duration(voice_dur)
        heatmap_ascii = draw_ascii_heatmap(heatmap_data)

        report = (
            f"📈 **Detailed Analytics for {username}**\n"
            f"• **Total Messages**: {counts['message_count']}\n"
            f"• **Total Voice Time**: {voice_dur_str}\n"
            f"• **Peak Hour of Day**: {peak_hr_str}\n"
            f"• **Peak Day of Week**: {peak_day_str}\n\n"
            f"🗓️ **Weekly Activity Heatmap**:\n"
            f"```\n{heatmap_ascii}\n```"
        )
        await ctx.send(report)

    @commands.command(name="topactive")
    async def topactive(self, ctx: commands.Context) -> None:
        """Displays the guild's activity leaderboard."""
        guild_id = ctx.guild.id if ctx.guild else None
        guild_name = ctx.guild.name if ctx.guild else "Direct Messages"

        leaderboard = await self.analytics.get_top_active_users(guild_id=guild_id, limit=10)
        if not leaderboard:
            await ctx.send(f"No tracked user activity found for **{guild_name}**.")
            return

        lines = []
        lines.append(f"# Guild Leaderboard: {guild_name}")
        lines.append(f"{'Rank':<5}{'Username':<25}{'Messages':<10}{'Voice Duration':<15}")
        lines.append("-" * 55)
        
        for idx, row in enumerate(leaderboard, 1):
            uname = row['username'][:22]
            msg_cnt = str(row['msg_count'])
            v_dur_str = format_duration(row['voice_duration'])
            lines.append(f"{idx:<5}{uname:<25}{msg_cnt:<10}{v_dur_str:<15}")

        report = "```\n" + "\n".join(lines) + "\n```"
        await ctx.send(report)

    @commands.command(name="presence")
    async def presence(self, ctx: commands.Context, *, user_query: Optional[str] = None) -> None:
        """Displays recent presence (status) updates of the target user."""
        try:
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            await ctx.send(str(e))
            return

        records = await fetch_recent_presence(self.db_path, user_id, limit=10)
        if not records:
            await ctx.send(f"No status change logs found for **{username}**.")
            return

        lines = []
        now = datetime.now(timezone.utc)
        for row in records:
            event_dt = datetime.fromisoformat(row['timestamp'])
            time_str = event_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
            
            # Relative time calculation
            diff = now - event_dt
            seconds = int(diff.total_seconds())
            if seconds < 0:
                seconds = 0
                
            if seconds < 60:
                rel_str = f"{seconds} seconds ago"
            elif seconds < 3600:
                rel_str = f"{seconds // 60} minutes ago"
            elif seconds < 86400:
                rel_str = f"{seconds // 3600} hours ago"
            else:
                rel_str = f"{seconds // 86400} days ago"

            dur = row['duration']
            dur_str = f" (duration: {format_duration(dur)})" if dur is not None else ""
            
            old_s = row['old_status']
            new_s = row['new_status']
            lines.append(
                f"[{time_str}] {rel_str} | 👤 **Presence Update**: Status changed from `{old_s}` to `{new_s}`{dur_str}"
            )
            
        report = f"📋 **Recent Status Changes for {username}**:\n" + "\n".join(lines)
        await ctx.send(report)

    @commands.command(name="trackpresence")
    async def trackpresence(self, ctx: commands.Context, *, user_query: str) -> None:
        """Add a target user to the list of persistently tracked presence subscriptions."""
        try:
            # Check standard resolve (resolves from cache or DB)
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            # If not found, check if it's a numeric ID we can try fetching/subscribing directly
            if user_query.isdigit():
                user_id = int(user_query)
                try:
                    user = await self.bot.fetch_user(user_id)
                    username = str(user)
                except Exception:
                    username = f"User ID {user_id}"
            else:
                await ctx.send(str(e))
                return

        # Add to SQLite database persistent subscriptions
        await add_tracked_user(self.db_path, user_id)
        
        # Actively subscribe in mutual guilds
        subscribed = False
        for guild in self.bot.guilds:
            if guild.get_member(user_id) is not None:
                try:
                    await guild.subscribe_to(members=[discord.Object(id=user_id)])
                    subscribed = True
                except Exception as e:
                    logger.error("Failed to subscribe on trackpresence: %s", e)
        
        sub_msg = " and subscribed to updates" if subscribed else " (will activate once shared guild connection is found)"
        await ctx.send(f"✅ Added **{username}** (ID: `{user_id}`) to presence tracking list{sub_msg}.")

    @commands.command(name="untrackpresence")
    async def untrackpresence(self, ctx: commands.Context, *, user_query: str) -> None:
        """Remove a target user from the list of persistently tracked presence subscriptions."""
        try:
            user_id, username = await self.resolve_user(ctx, user_query)
        except commands.BadArgument as e:
            if user_query.isdigit():
                user_id = int(user_query)
                username = f"User ID {user_id}"
            else:
                await ctx.send(str(e))
                return

        # Delete from SQLite database persistent subscriptions
        await remove_tracked_user(self.db_path, user_id)
        
        # Unsubscribe in guilds
        unsubscribed = False
        for guild in self.bot.guilds:
            if guild.get_member(user_id) is not None:
                try:
                    await guild.unsubscribe_from(members=[discord.Object(id=user_id)])
                    unsubscribed = True
                except Exception as e:
                    logger.error("Failed to unsubscribe: %s", e)
                    
        await ctx.send(f"❌ Removed **{username}** (ID: `{user_id}`) from presence tracking list.")


