"""
Unit tests for the Discord User Activity Tracker.
Tests database operations, analytics engine, and heatmap rendering.
"""

import os
import unittest
import sqlite3
import tempfile
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Generator
from unittest.mock import MagicMock, AsyncMock, patch


from sentinel.tracker.db import (
    init_db_sync,
    log_message_sync,
    log_voice_join_sync,
    log_voice_leave_sync,
    fetch_db_user_sync,
    find_user_by_name_sync,
    log_presence_change_sync,
    fetch_messages_for_user_sync,
)
from sentinel.tracker.analytics import AnalyticsEngine
from sentinel.tracker.cog import format_duration, draw_ascii_heatmap


class TestActivityTracker(unittest.TestCase):
    def setUp(self) -> None:
        # Create a temporary file database to inspect schema and records
        self.db_fd, self.db_path = tempfile.mkstemp()
        init_db_sync(self.db_path)
        self.analytics = AnalyticsEngine(self.db_path)

    def tearDown(self) -> None:
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_database_creation(self) -> None:
        """Verify that all required tables and indices are created."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Verify tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("users", tables)
        self.assertIn("message_activity", tables)
        self.assertIn("voice_activity", tables)
        
        # Verify indexes exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indices = [row[0] for row in cursor.fetchall()]
        self.assertIn("idx_message_user", indices)
        self.assertIn("idx_voice_user", indices)
        
        conn.close()

    def test_log_message_and_duplicates(self) -> None:
        """Test logging messages and ensuring duplicates are ignored."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Log message for a new user — include Sentinel content fields
        log_message_sync(
            self.db_path,
            message_id=12345,
            user_id=999,
            username="TestUser#0001",
            timestamp=timestamp,
            guild_id=111,
            channel_id=222,
            content="Hello, world!",
            message_url="https://discord.com/channels/111/222/12345",
        )
        
        # Verify user was created
        user = fetch_db_user_sync(self.db_path, 999)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "TestUser#0001")
        self.assertEqual(user["last_seen"], timestamp)
        
        # Verify message was logged with content and URL
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM message_activity WHERE message_id = 12345")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["user_id"], 999)
        self.assertEqual(row["guild_id"], 111)
        self.assertEqual(row["channel_id"], 222)
        # Sentinel fields
        self.assertEqual(row["content"], "Hello, world!")
        self.assertEqual(row["message_url"], "https://discord.com/channels/111/222/12345")
        
        # Try logging duplicate message ID — should be ignored
        log_message_sync(
            self.db_path,
            message_id=12345,
            user_id=999,
            username="TestUser#0001",
            timestamp=timestamp,
            guild_id=111,
            channel_id=999 # different channel
        )
        
        cursor.execute("SELECT COUNT(*) as count FROM message_activity WHERE message_id = 12345")
        count = cursor.fetchone()["count"]
        self.assertEqual(count, 1) # count should still be 1
        
        # Verify backward-compat: logging without content/URL still works (defaults to None)
        log_message_sync(
            self.db_path,
            message_id=99999,
            user_id=999,
            username="TestUser#0001",
            timestamp=timestamp,
            guild_id=111,
            channel_id=222,
        )
        cursor.execute("SELECT content, message_url FROM message_activity WHERE message_id = 99999")
        compat_row = cursor.fetchone()
        self.assertIsNotNone(compat_row)
        self.assertIsNone(compat_row["content"])
        self.assertIsNone(compat_row["message_url"])
        
        conn.close()

    def test_fetch_messages_for_user(self) -> None:
        """Test Sentinel's per-user per-guild message retrieval helper."""
        ts_base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Insert 3 messages for user 1 in guild 111 across two channels
        for i in range(3):
            ts = (ts_base.replace(minute=i)).isoformat()
            log_message_sync(
                self.db_path,
                message_id=1000 + i,
                user_id=1,
                username="Alice",
                timestamp=ts,
                guild_id=111,
                channel_id=100 + i,
                content=f"Message {i}",
                message_url=f"https://discord.com/channels/111/{100 + i}/{1000 + i}",
            )

        # Insert 1 message for user 2 in guild 111 (should NOT appear in user 1 results)
        log_message_sync(
            self.db_path,
            message_id=2000,
            user_id=2,
            username="Bob",
            timestamp=ts_base.isoformat(),
            guild_id=111,
            channel_id=100,
            content="Bob's message",
        )

        # Insert 1 message for user 1 in a different guild (should NOT appear)
        log_message_sync(
            self.db_path,
            message_id=3000,
            user_id=1,
            username="Alice",
            timestamp=ts_base.isoformat(),
            guild_id=999,
            channel_id=100,
            content="Alice in another server",
        )

        # --- Fetch all messages for user 1 in guild 111 ---
        rows = fetch_messages_for_user_sync(self.db_path, user_id=1, guild_id=111)
        self.assertEqual(len(rows), 3, "Should return exactly 3 messages for user 1 in guild 111")

        # Verify ordering is oldest-first
        timestamps = [r["timestamp"] for r in rows]
        self.assertEqual(timestamps, sorted(timestamps), "Rows should be ordered oldest-first")

        # Verify content and URL are present
        for i, row in enumerate(rows):
            self.assertEqual(row["content"], f"Message {i}")
            self.assertIn("discord.com", row["message_url"])

        # --- Test limit parameter ---
        rows_limited = fetch_messages_for_user_sync(self.db_path, user_id=1, guild_id=111, limit=2)
        self.assertEqual(len(rows_limited), 2, "limit parameter should be respected")

        # --- Verify cross-guild isolation ---
        rows_other_guild = fetch_messages_for_user_sync(self.db_path, user_id=1, guild_id=999)
        self.assertEqual(len(rows_other_guild), 1)
        self.assertEqual(rows_other_guild[0]["content"], "Alice in another server")

        # --- Verify empty result for unknown user ---
        rows_unknown = fetch_messages_for_user_sync(self.db_path, user_id=9999, guild_id=111)
        self.assertEqual(rows_unknown, [])

    def test_username_lookup(self) -> None:
        """Test looking up user records by matching username substring."""
        timestamp = datetime.now(timezone.utc).isoformat()
        log_message_sync(self.db_path, 1, 999, "Shubham#1234", timestamp, 111, 222)
        
        # Lookup exact, partial, and case-insensitive
        user1 = find_user_by_name_sync(self.db_path, "Shubham")
        user2 = find_user_by_name_sync(self.db_path, "shubh")
        user3 = find_user_by_name_sync(self.db_path, "1234")
        
        self.assertIsNotNone(user1)
        self.assertEqual(user1["user_id"], 999)
        self.assertIsNotNone(user2)
        self.assertEqual(user2["user_id"], 999)
        self.assertIsNotNone(user3)
        self.assertEqual(user3["user_id"], 999)

    def test_voice_session_duration_calculation(self) -> None:
        """Test voice joins, leaves, duration resolution, and lingering session cleanup."""
        join_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        leave_dt = datetime.now(timezone.utc)
        
        # Log join
        log_voice_join_sync(
            self.db_path,
            user_id=999,
            username="VoiceUser",
            guild_id=111,
            channel_id=222,
            timestamp=join_dt.isoformat()
        )
        
        # Verify active voice session (duration and leave_time are NULL)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM voice_activity WHERE user_id = 999 AND leave_time IS NULL")
        sess = cursor.fetchone()
        self.assertIsNotNone(sess)
        self.assertEqual(sess["channel_id"], 222)
        
        # Log leave
        log_voice_leave_sync(
            self.db_path,
            user_id=999,
            username="VoiceUser",
            guild_id=111,
            channel_id=222,
            timestamp=leave_dt.isoformat()
        )
        
        # Verify session closed and duration populated
        cursor.execute("SELECT * FROM voice_activity WHERE user_id = 999")
        sessions = cursor.fetchall()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["leave_time"], leave_dt.isoformat())
        # Duration should be approximately 300 seconds (5 minutes)
        self.assertAlmostEqual(sessions[0]["duration"], 300.0, delta=2.0)
        
        # Test Double Joins (closes lingering sessions)
        join1 = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        join2 = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        
        log_voice_join_sync(self.db_path, 999, "VoiceUser", 111, 222, join1)
        # Verify join1 is active
        cursor.execute("SELECT COUNT(*) as cnt FROM voice_activity WHERE user_id = 999 AND leave_time IS NULL")
        self.assertEqual(cursor.fetchone()["cnt"], 1)
        
        # Join again without leaving (lingering session)
        log_voice_join_sync(self.db_path, 999, "VoiceUser", 111, 333, join2)
        
        # The previous join (join1) should be closed automatically
        cursor.execute("SELECT * FROM voice_activity WHERE user_id = 999 AND channel_id = 222 ORDER BY id DESC LIMIT 1")
        closed_sess = cursor.fetchone()
        self.assertIsNotNone(closed_sess["leave_time"])
        self.assertEqual(closed_sess["leave_time"], join2)
        self.assertAlmostEqual(closed_sess["duration"], 480.0, delta=2.0) # 10m - 2m = 8m = 480s
        
        conn.close()

    def test_analytics_engine(self) -> None:
        """Test peak day, hour, average calculations, and topactive leaderboards."""
        # Insert test messages at specific hours and days
        # Sunday is 0. Monday is 1. Friday is 5.
        # We will use hardcoded dates:
        # 2026-06-01 (Monday)
        # 2026-06-05 (Friday)
        
        # Add 3 messages on Monday at 14:00 UTC
        log_message_sync(self.db_path, 1, 999, "UserA", "2026-06-01T14:10:00+00:00", 111, 222)
        log_message_sync(self.db_path, 2, 999, "UserA", "2026-06-01T14:20:00+00:00", 111, 222)
        log_message_sync(self.db_path, 3, 999, "UserA", "2026-06-01T14:30:00+00:00", 111, 222)
        
        # Add 1 message on Friday at 09:00 UTC
        log_message_sync(self.db_path, 4, 999, "UserA", "2026-06-05T09:15:00+00:00", 111, 222)
        
        # Add a voice duration of 600s (10m) for UserA, and 1800s (30m) for UserB
        # UserA join/leave on Monday
        log_voice_join_sync(self.db_path, 999, "UserA", 111, 222, "2026-06-01T14:00:00+00:00")
        log_voice_leave_sync(self.db_path, 999, "UserA", 111, 222, "2026-06-01T14:10:00+00:00")
        
        # UserB join/leave on Friday
        log_voice_join_sync(self.db_path, 888, "UserB", 111, 222, "2026-06-05T09:00:00+00:00")
        log_voice_leave_sync(self.db_path, 888, "UserB", 111, 222, "2026-06-05T09:30:00+00:00")
        
        # Run Analytics checks using asyncio.run() — compatible with Python 3.12
        # (asyncio.get_event_loop() raises RuntimeError on 3.12 when no loop exists)
        # Test active hour (should be 14 UTC)
        hr_data = asyncio.run(self.analytics.get_most_active_hour(999))
        self.assertIsNotNone(hr_data)
        self.assertEqual(hr_data[0], 14) # peak hour is 14
        
        # Test active day (should be 1 = Monday)
        day_data = asyncio.run(self.analytics.get_most_active_day(999))
        self.assertIsNotNone(day_data)
        self.assertEqual(day_data[0], 1) # peak day is 1 (Monday)
        
        # Test voice duration
        voice_dur_a = asyncio.run(self.analytics.get_voice_duration(999))
        voice_dur_b = asyncio.run(self.analytics.get_voice_duration(888))
        self.assertEqual(voice_dur_a, 600.0)
        self.assertEqual(voice_dur_b, 1800.0)
        
        # Test daily averages for UserA (4 messages across 2 active days -> average 2.0 messages/day)
        averages = asyncio.run(self.analytics.get_average_daily_activity(999))
        self.assertEqual(averages["avg_messages"], 2.0)
        self.assertEqual(averages["avg_voice_duration"], 600.0) # 600s voice on 1 active voice day
        
        # Test Top Active Leaderboard
        top_users = asyncio.run(self.analytics.get_top_active_users(limit=10))
        self.assertEqual(len(top_users), 2)
        # UserA has score: 4 messages + 10m voice = 14
        # UserB has score: 0 messages + 30m voice = 30
        # So UserB should rank #1, UserA rank #2
        self.assertEqual(top_users[0]["user_id"], 888)
        self.assertEqual(top_users[1]["user_id"], 999)

    def test_heatmap_generation_visual(self) -> None:
        """Verify heatmap compiles and ASCII formats without errors."""
        # Insert a few events on Monday 14:00, Tuesday 15:00
        log_message_sync(self.db_path, 1, 999, "UserA", "2026-06-01T14:00:00+00:00", 111, 222)
        log_message_sync(self.db_path, 2, 999, "UserA", "2026-06-02T15:00:00+00:00", 111, 222)
        
        heatmap_data = asyncio.run(self.analytics.get_heatmap_data(999))
        ascii_grid = draw_ascii_heatmap(heatmap_data)
        
        # Verify formatting features are present
        self.assertIn("Sun |", ascii_grid)
        self.assertIn("Mon |", ascii_grid)
        self.assertIn("Key:", ascii_grid)
        self.assertIn("░░", ascii_grid) # monday hour 14 density character should be there

    def test_duration_formatter(self) -> None:
        """Test time formatting util helper."""
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(125), "2m 5s")
        self.assertEqual(format_duration(3665), "1h 1m")
        self.assertEqual(format_duration(90000), "1d 1h")

    def test_log_presence_change_and_durations(self) -> None:
        """Test logging presence transitions, status durations, and duplicate filtering."""
        t0 = datetime.now(timezone.utc)
        t1 = t0 + timedelta(seconds=15)
        t2 = t0 + timedelta(seconds=45)
        
        # Log initial presence change (offline -> online)
        dur1 = log_presence_change_sync(
            self.db_path,
            user_id=999,
            username="StatusUser",
            old_status="offline",
            new_status="online",
            timestamp=t0.isoformat()
        )
        # Initial state duration should be None as there's no prior status change event
        self.assertIsNone(dur1)
        
        # Log second presence change (online -> idle) after 15 seconds
        dur2 = log_presence_change_sync(
            self.db_path,
            user_id=999,
            username="StatusUser",
            old_status="online",
            new_status="idle",
            timestamp=t1.isoformat()
        )
        # Spent 15 seconds in 'online' state
        self.assertIsNotNone(dur2)
        self.assertAlmostEqual(dur2, 15.0, delta=0.5)
        
        # Log third presence change (idle -> online) after another 30 seconds
        dur3 = log_presence_change_sync(
            self.db_path,
            user_id=999,
            username="StatusUser",
            old_status="idle",
            new_status="online",
            timestamp=t2.isoformat()
        )
        # Spent 30 seconds in 'idle' state
        self.assertIsNotNone(dur3)
        self.assertAlmostEqual(dur3, 30.0, delta=0.5)
        
        # Log consecutive duplicate change (idle -> online again) - should be filtered
        dur_dup = log_presence_change_sync(
            self.db_path,
            user_id=999,
            username="StatusUser",
            old_status="idle",
            new_status="online",
            timestamp=(t2 + timedelta(seconds=10)).isoformat()
        )
        self.assertIsNone(dur_dup)
        
        # Fetch recent presence records and check order and values
        from sentinel.tracker.db import fetch_recent_presence_sync
        records = fetch_recent_presence_sync(self.db_path, 999, limit=10)
        self.assertEqual(len(records), 3) # dup should be ignored
        
        # Should be in reverse chronological order
        self.assertEqual(records[0]["new_status"], "online")
        self.assertEqual(records[0]["old_status"], "idle")
        self.assertAlmostEqual(records[0]["duration"], 30.0, delta=0.5)
        
        self.assertEqual(records[1]["new_status"], "idle")
        self.assertEqual(records[1]["old_status"], "online")
        self.assertAlmostEqual(records[1]["duration"], 15.0, delta=0.5)
        
        self.assertEqual(records[2]["new_status"], "online")
        self.assertEqual(records[2]["old_status"], "offline")
        self.assertIsNone(records[2]["duration"])



class TestConfigLoading(unittest.TestCase):
    @patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "token_from_env"}, clear=True)
    def test_load_config_env_precedence(self) -> None:
        """Verify environment variable DISCORD_BOT_TOKEN takes precedence over config.json."""
        from bot import load_config
        config = load_config()
        self.assertEqual(config["token"], "token_from_env")


if __name__ == "__main__":
    unittest.main()



