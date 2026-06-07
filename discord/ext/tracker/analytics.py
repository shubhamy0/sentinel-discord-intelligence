"""
Analytics Engine for the Discord User Activity Tracker.
Computes stats like peak hour, peak day, daily averages, and heatmap coordinates.
"""

import sqlite3
import logging
import asyncio
from typing import Optional, List, Dict, Any, Tuple
from .db import _connect

logger = logging.getLogger('discord.ext.tracker.analytics')

class AnalyticsEngine:
    """
    Computes activity metrics and leaderboards from SQLite data.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_most_active_hour_sync(self, user_id: Optional[int]) -> Optional[Tuple[int, int]]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Construct query based on whether we filter by user_id
            if user_id is not None:
                query = """
                    WITH hourly_activity AS (
                        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hr, COUNT(*) as cnt
                        FROM message_activity
                        WHERE user_id = ?
                        GROUP BY hr
                        UNION ALL
                        SELECT CAST(strftime('%H', join_time) AS INTEGER) as hr, COUNT(*) as cnt
                        FROM voice_activity
                        WHERE user_id = ?
                        GROUP BY hr
                    )
                    SELECT hr, SUM(cnt) as total_cnt
                    FROM hourly_activity
                    GROUP BY hr
                    ORDER BY total_cnt DESC
                    LIMIT 1
                """
                cursor.execute(query, (user_id, user_id))
            else:
                query = """
                    WITH hourly_activity AS (
                        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hr, COUNT(*) as cnt
                        FROM message_activity
                        GROUP BY hr
                        UNION ALL
                        SELECT CAST(strftime('%H', join_time) AS INTEGER) as hr, COUNT(*) as cnt
                        FROM voice_activity
                        GROUP BY hr
                    )
                    SELECT hr, SUM(cnt) as total_cnt
                    FROM hourly_activity
                    GROUP BY hr
                    ORDER BY total_cnt DESC
                    LIMIT 1
                """
                cursor.execute(query)
                
            row = cursor.fetchone()
            if row and row['hr'] is not None:
                return int(row['hr']), int(row['total_cnt'])
            return None
        except Exception as e:
            logger.exception("Error calculating active hour: %s", e)
            return None
        finally:
            conn.close()

    async def get_most_active_hour(self, user_id: Optional[int] = None) -> Optional[Tuple[int, int]]:
        """
        Asynchronously get the most active hour of the day (0-23) for a user or overall.
        Returns (hour, event_count) or None.
        """
        return await asyncio.to_thread(self._get_most_active_hour_sync, user_id)

    def _get_most_active_day_sync(self, user_id: Optional[int]) -> Optional[Tuple[int, int]]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            if user_id is not None:
                query = """
                    WITH daily_activity AS (
                        SELECT CAST(strftime('%w', timestamp) AS INTEGER) as day, COUNT(*) as cnt
                        FROM message_activity
                        WHERE user_id = ?
                        GROUP BY day
                        UNION ALL
                        SELECT CAST(strftime('%w', join_time) AS INTEGER) as day, COUNT(*) as cnt
                        FROM voice_activity
                        WHERE user_id = ?
                        GROUP BY day
                    )
                    SELECT day, SUM(cnt) as total_cnt
                    FROM daily_activity
                    GROUP BY day
                    ORDER BY total_cnt DESC
                    LIMIT 1
                """
                cursor.execute(query, (user_id, user_id))
            else:
                query = """
                    WITH daily_activity AS (
                        SELECT CAST(strftime('%w', timestamp) AS INTEGER) as day, COUNT(*) as cnt
                        FROM message_activity
                        GROUP BY day
                        UNION ALL
                        SELECT CAST(strftime('%w', join_time) AS INTEGER) as day, COUNT(*) as cnt
                        FROM voice_activity
                        GROUP BY day
                    )
                    SELECT day, SUM(cnt) as total_cnt
                    FROM daily_activity
                    GROUP BY day
                    ORDER BY total_cnt DESC
                    LIMIT 1
                """
                cursor.execute(query)
                
            row = cursor.fetchone()
            if row and row['day'] is not None:
                return int(row['day']), int(row['total_cnt'])
            return None
        except Exception as e:
            logger.exception("Error calculating active day: %s", e)
            return None
        finally:
            conn.close()

    async def get_most_active_day(self, user_id: Optional[int] = None) -> Optional[Tuple[int, int]]:
        """
        Asynchronously get the most active day of the week (0-6, where 0=Sunday) for a user or overall.
        Returns (day_index, event_count) or None.
        """
        return await asyncio.to_thread(self._get_most_active_day_sync, user_id)

    def _get_voice_duration_sync(self, user_id: int) -> float:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            query = """
                SELECT SUM(COALESCE(duration, strftime('%s', 'now') - strftime('%s', join_time))) as total_dur
                FROM voice_activity
                WHERE user_id = ?
            """
            cursor.execute(query, (user_id,))
            row = cursor.fetchone()
            if row and row['total_dur'] is not None:
                return float(row['total_dur'])
            return 0.0
        except Exception as e:
            logger.exception("Error calculating voice duration: %s", e)
            return 0.0
        finally:
            conn.close()

    async def get_voice_duration(self, user_id: int) -> float:
        """
        Asynchronously get the total voice activity duration (in seconds) for a user,
        including ongoing voice sessions.
        """
        return await asyncio.to_thread(self._get_voice_duration_sync, user_id)

    def _get_average_daily_activity_sync(self, user_id: int) -> Dict[str, float]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Average messages per day (only counting days with message activity)
            cursor.execute(
                """
                WITH daily_msg AS (
                    SELECT date(timestamp) as msg_date, COUNT(*) as msg_cnt
                    FROM message_activity
                    WHERE user_id = ?
                    GROUP BY msg_date
                )
                SELECT AVG(msg_cnt) as avg_msg FROM daily_msg
                """,
                (user_id,)
            )
            row_msg = cursor.fetchone()
            avg_msg = float(row_msg['avg_msg']) if row_msg and row_msg['avg_msg'] is not None else 0.0
            
            # Average voice duration per day (only counting days with voice activity)
            cursor.execute(
                """
                WITH daily_voice AS (
                    SELECT date(join_time) as voice_date,
                           SUM(COALESCE(duration, strftime('%s', 'now') - strftime('%s', join_time))) as voice_dur
                    FROM voice_activity
                    WHERE user_id = ?
                    GROUP BY voice_date
                )
                SELECT AVG(voice_dur) as avg_voice FROM daily_voice
                """,
                (user_id,)
            )
            row_voice = cursor.fetchone()
            avg_voice = float(row_voice['avg_voice']) if row_voice and row_voice['avg_voice'] is not None else 0.0
            
            return {
                "avg_messages": avg_msg,
                "avg_voice_duration": avg_voice
            }
        except Exception as e:
            logger.exception("Error calculating daily averages: %s", e)
            return {"avg_messages": 0.0, "avg_voice_duration": 0.0}
        finally:
            conn.close()

    async def get_average_daily_activity(self, user_id: int) -> Dict[str, float]:
        """
        Asynchronously get a user's average daily message volume and voice duration.
        """
        return await asyncio.to_thread(self._get_average_daily_activity_sync, user_id)

    def _get_heatmap_data_sync(self, user_id: Optional[int]) -> List[Tuple[int, int, int]]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            if user_id is not None:
                query = """
                    WITH combined AS (
                        SELECT CAST(strftime('%w', timestamp) AS INTEGER) as day,
                               CAST(strftime('%H', timestamp) AS INTEGER) as hour
                        FROM message_activity
                        WHERE user_id = ?
                        UNION ALL
                        SELECT CAST(strftime('%w', join_time) AS INTEGER) as day,
                               CAST(strftime('%H', join_time) AS INTEGER) as hour
                        FROM voice_activity
                        WHERE user_id = ?
                    )
                    SELECT day, hour, COUNT(*) as cnt
                    FROM combined
                    GROUP BY day, hour
                """
                cursor.execute(query, (user_id, user_id))
            else:
                query = """
                    WITH combined AS (
                        SELECT CAST(strftime('%w', timestamp) AS INTEGER) as day,
                               CAST(strftime('%H', timestamp) AS INTEGER) as hour
                        FROM message_activity
                        UNION ALL
                        SELECT CAST(strftime('%w', join_time) AS INTEGER) as day,
                               CAST(strftime('%H', join_time) AS INTEGER) as hour
                        FROM voice_activity
                    )
                    SELECT day, hour, COUNT(*) as cnt
                    FROM combined
                    GROUP BY day, hour
                """
                cursor.execute(query)
                
            return [(int(row['day']), int(row['hour']), int(row['cnt'])) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception("Error calculating heatmap data: %s", e)
            return []
        finally:
            conn.close()

    async def get_heatmap_data(self, user_id: Optional[int] = None) -> List[Tuple[int, int, int]]:
        """
        Asynchronously retrieve weekly heatmap data for a user or overall.
        Returns a list of tuples: (day_of_week, hour, activity_count).
        """
        return await asyncio.to_thread(self._get_heatmap_data_sync, user_id)

    def _get_top_active_users_sync(self, guild_id: Optional[int], limit: int) -> List[Dict[str, Any]]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            if guild_id is not None:
                query = """
                    WITH msg_counts AS (
                        SELECT user_id, COUNT(*) as msg_cnt
                        FROM message_activity
                        WHERE guild_id = ?
                        GROUP BY user_id
                    ),
                    voice_durs AS (
                        SELECT user_id, SUM(COALESCE(duration, strftime('%s', 'now') - strftime('%s', join_time))) as voice_dur
                        FROM voice_activity
                        WHERE guild_id = ?
                        GROUP BY user_id
                    )
                    SELECT
                        u.user_id,
                        u.username,
                        u.last_seen,
                        COALESCE(m.msg_cnt, 0) as msg_count,
                        COALESCE(v.voice_dur, 0.0) as voice_duration
                    FROM users u
                    LEFT JOIN msg_counts m ON u.user_id = m.user_id
                    LEFT JOIN voice_durs v ON u.user_id = v.user_id
                    WHERE m.msg_cnt IS NOT NULL OR v.voice_dur IS NOT NULL
                    ORDER BY (COALESCE(m.msg_cnt, 0) + COALESCE(v.voice_dur, 0.0) / 60.0) DESC
                    LIMIT ?
                """
                cursor.execute(query, (guild_id, guild_id, limit))
            else:
                query = """
                    WITH msg_counts AS (
                        SELECT user_id, COUNT(*) as msg_cnt
                        FROM message_activity
                        GROUP BY user_id
                    ),
                    voice_durs AS (
                        SELECT user_id, SUM(COALESCE(duration, strftime('%s', 'now') - strftime('%s', join_time))) as voice_dur
                        FROM voice_activity
                        GROUP BY user_id
                    )
                    SELECT
                        u.user_id,
                        u.username,
                        u.last_seen,
                        COALESCE(m.msg_cnt, 0) as msg_count,
                        COALESCE(v.voice_dur, 0.0) as voice_duration
                    FROM users u
                    LEFT JOIN msg_counts m ON u.user_id = m.user_id
                    LEFT JOIN voice_durs v ON u.user_id = v.user_id
                    WHERE m.msg_cnt IS NOT NULL OR v.voice_dur IS NOT NULL
                    ORDER BY (COALESCE(m.msg_cnt, 0) + COALESCE(v.voice_dur, 0.0) / 60.0) DESC
                    LIMIT ?
                """
                cursor.execute(query, (limit,))
                
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception("Error listing top active users: %s", e)
            return []
        finally:
            conn.close()

    async def get_top_active_users(self, guild_id: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Asynchronously get a leaderboard of top active users sorted by combined score
        (message count + voice minutes). Optionally filtered by guild.
        """
        return await asyncio.to_thread(self._get_top_active_users_sync, guild_id, limit)

    def _get_user_stats_sync(self, user_id: int) -> Dict[str, Any]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Total message count
            cursor.execute("SELECT COUNT(*) as cnt FROM message_activity WHERE user_id = ?", (user_id,))
            msg_row = cursor.fetchone()
            msg_count = msg_row['cnt'] if msg_row else 0
            
            # Total voice session count
            cursor.execute("SELECT COUNT(*) as cnt FROM voice_activity WHERE user_id = ?", (user_id,))
            voice_sess_row = cursor.fetchone()
            voice_sess_count = voice_sess_row['cnt'] if voice_sess_row else 0
            
            return {
                "message_count": msg_count,
                "voice_session_count": voice_sess_count
            }
        except Exception as e:
            logger.exception("Error fetching user simple stats: %s", e)
            return {"message_count": 0, "voice_session_count": 0}
        finally:
            conn.close()

    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Asynchronously get basic counts of messages and voice sessions for a user.
        """
        return await asyncio.to_thread(self._get_user_stats_sync, user_id)

    def _get_last_seen_activity_sync(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = _connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Fetch last message
            cursor.execute(
                """
                SELECT timestamp, channel_id, guild_id FROM message_activity
                WHERE user_id = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (user_id,)
            )
            msg_row = cursor.fetchone()
            
            # Fetch last voice session
            cursor.execute(
                """
                SELECT join_time, leave_time, channel_id, guild_id FROM voice_activity
                WHERE user_id = ?
                ORDER BY COALESCE(leave_time, join_time) DESC LIMIT 1
                """,
                (user_id,)
            )
            voice_row = cursor.fetchone()
            
            if not msg_row and not voice_row:
                return None
                
            msg_time = datetime.fromisoformat(msg_row['timestamp']) if msg_row else None
            voice_time = None
            if voice_row:
                v_time_str = voice_row['leave_time'] or voice_row['join_time']
                voice_time = datetime.fromisoformat(v_time_str)
                
            # Determine which is newer
            if msg_time and (not voice_time or msg_time > voice_time):
                return {
                    "type": "message",
                    "timestamp": msg_time.isoformat(),
                    "channel_id": msg_row['channel_id'],
                    "guild_id": msg_row['guild_id']
                }
            elif voice_row:
                is_join = voice_row['leave_time'] is None
                return {
                    "type": "voice_join" if is_join else "voice_leave",
                    "timestamp": voice_time.isoformat(),
                    "channel_id": voice_row['channel_id'],
                    "guild_id": voice_row['guild_id']
                }
            return None
        except Exception as e:
            logger.exception("Error calculating last seen activity: %s", e)
            return None
        finally:
            conn.close()

    async def get_last_seen_activity(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Asynchronously find the last recorded activity for a user.
        """
        return await asyncio.to_thread(self._get_last_seen_activity_sync, user_id)

