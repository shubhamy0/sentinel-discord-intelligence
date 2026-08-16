"""
Tests for the Sentinel Analysis Service (sentinel.services.analysis_service).

These tests exercise the pure-Python analysis logic only.
No Discord objects, no running bot, no mocks needed.

Coverage:
    - Empty result (no messages stored)
    - Guild isolation (user in multiple guilds — only target guild returned)
    - User filtering (multiple users in same guild — only target user returned)
    - Message count
    - Average message length (content-only rows)
    - Channel ranking (descending by message count)
    - NULL/empty content handling (pre-Milestone-2 rows)
    - All-NULL content (has_content=False path)
    - Link counting
    - Top-word extraction + stop-word exclusion
    - Date range (oldest → latest ordering)
    - Limit parameter (fetch cap is respected)
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sentinel.tracker.db import init_db_sync, log_message_sync
from sentinel.services.analysis_service import AnalysisResult, analyze_user


class TestAnalysisService(unittest.TestCase):
    """Unit tests for sentinel.services.analysis_service.analyze_user()."""

    # ── Fixtures ────────────────────────────────────────────────────────

    def setUp(self) -> None:
        self.db_fd, self.db_path = tempfile.mkstemp()
        init_db_sync(self.db_path)

    def tearDown(self) -> None:
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _insert(
        self,
        msg_id: int,
        user_id: int,
        username: str,
        ts: str,
        guild_id: int,
        channel_id: int,
        content: str | None = None,
        url: str | None = None,
    ) -> None:
        """Convenience wrapper around log_message_sync."""
        log_message_sync(
            self.db_path, msg_id, user_id, username,
            ts, guild_id, channel_id, content, url,
        )

    def _ts(self, **delta_kwargs) -> str:
        """Return an ISO timestamp offset from a fixed base time."""
        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        return (base + timedelta(**delta_kwargs)).isoformat()

    # ── Tests ────────────────────────────────────────────────────────────

    def test_returns_none_when_no_messages(self) -> None:
        """analyze_user returns None when the user has no stored messages."""
        result = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNone(result)

    def test_guild_isolation(self) -> None:
        """Messages from other guilds must not appear in the result."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100, "hello guild 111")
        self._insert(2, 1, "Alice", ts, 222, 100, "hello guild 222")
        self._insert(3, 1, "Alice", ts, 222, 100, "second msg guild 222")

        # Guild 111 should see only 1 message
        r1 = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r1)
        self.assertEqual(r1.message_count, 1)

        # Guild 222 should see only 2 messages
        r2 = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=222)
        self.assertIsNotNone(r2)
        self.assertEqual(r2.message_count, 2)

    def test_user_filtering(self) -> None:
        """Messages from other users in the same guild must not appear."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100, "Alice message")
        self._insert(2, 2, "Bob",   ts, 111, 100, "Bob message")
        self._insert(3, 2, "Bob",   ts, 111, 100, "Bob second message")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(r.message_count, 1)
        self.assertEqual(r.user_id, 1)

    def test_message_count(self) -> None:
        """message_count reflects the exact number of stored messages."""
        for i in range(7):
            self._insert(i + 1, 1, "Alice", self._ts(hours=i), 111, 100, f"msg {i}")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(r.message_count, 7)

    def test_avg_message_length_with_all_content(self) -> None:
        """Average message length is computed correctly from known-length strings."""
        ts = self._ts()
        # lengths: 3, 6, 9  →  average = 6.0
        self._insert(1, 1, "Alice", ts, 111, 100, "abc")          # 3 chars
        self._insert(2, 1, "Alice", ts, 111, 100, "abcdef")       # 6 chars
        self._insert(3, 1, "Alice", ts, 111, 100, "abcdefghi")    # 9 chars

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.avg_message_length, 6.0, places=2)
        self.assertEqual(r.messages_with_content, 3)

    def test_null_content_mixed(self) -> None:
        """
        NULL-content rows (pre-Milestone-2) must be counted in message_count
        but excluded from content stats.
        """
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100, None)              # no content
        self._insert(2, 1, "Alice", ts, 111, 100, None)              # no content
        self._insert(3, 1, "Alice", ts, 111, 100, "hello world")     # 11 chars

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(r.message_count, 3)
        self.assertEqual(r.messages_with_content, 1)
        self.assertAlmostEqual(r.avg_message_length, 11.0, places=1)
        self.assertTrue(r.has_content)

    def test_all_null_content(self) -> None:
        """When every row has NULL content, has_content is False and stats are zeroed."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100, None)
        self._insert(2, 1, "Alice", ts, 111, 100, None)

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(r.message_count, 2)
        self.assertEqual(r.messages_with_content, 0)
        self.assertFalse(r.has_content)
        self.assertEqual(r.avg_message_length, 0.0)
        self.assertEqual(r.total_links, 0)
        self.assertEqual(r.top_words, [])

    def test_empty_string_content_treated_as_null(self) -> None:
        """Empty-string content must behave the same as NULL content."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100, "")   # empty string
        self._insert(2, 1, "Alice", ts, 111, 100, "hi there friend")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        # Only the second row should count for content stats
        self.assertEqual(r.messages_with_content, 1)

    def test_channel_ranking(self) -> None:
        """Channels must be ranked by descending message count."""
        ts = self._ts()
        # Channel 100: 4 msgs, Channel 200: 2 msgs, Channel 300: 1 msg
        for i in range(4):
            self._insert(10 + i, 1, "Alice", ts, 111, 100, f"msg {i}")
        for i in range(2):
            self._insert(20 + i, 1, "Alice", ts, 111, 200, f"msg {i}")
        self._insert(30, 1, "Alice", ts, 111, 300, "msg")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.channel_activity), 3)
        self.assertEqual(r.channel_activity[0].channel_id, 100)
        self.assertEqual(r.channel_activity[0].message_count, 4)
        self.assertEqual(r.channel_activity[1].channel_id, 200)
        self.assertEqual(r.channel_activity[1].message_count, 2)
        self.assertEqual(r.channel_activity[2].channel_id, 300)
        self.assertEqual(r.channel_activity[2].message_count, 1)

    def test_channel_ranking_capped_at_top_n(self) -> None:
        """top_n_channels limits how many channels appear in the result."""
        ts = self._ts()
        for ch in range(10):   # 10 distinct channels
            self._insert(100 + ch, 1, "Alice", ts, 111, ch, f"msg {ch}")

        r = analyze_user(
            self.db_path, user_id=1, username="Alice", guild_id=111,
            top_n_channels=3,
        )
        self.assertIsNotNone(r)
        self.assertEqual(len(r.channel_activity), 3)

    def test_link_counting(self) -> None:
        """Total links is the count of http(s) URLs across all content rows."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100,
                     "check this https://example.com out")
        self._insert(2, 1, "Alice", ts, 111, 100,
                     "two links https://a.com and https://b.com here")
        self._insert(3, 1, "Alice", ts, 111, 100,
                     "no links here at all")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertEqual(r.total_links, 3)

    def test_top_words_and_stop_word_exclusion(self) -> None:
        """
        The most common non-stop word must rank first;
        common stop words must not appear in top_words.
        """
        ts = self._ts()
        # "python" appears 5×, stop words "the"/"is" appear many times but are filtered
        self._insert(1, 1, "Alice", ts, 111, 100,
                     "the python programming language is great")
        self._insert(2, 1, "Alice", ts, 111, 100,
                     "python is the best language")
        self._insert(3, 1, "Alice", ts, 111, 100,
                     "python python python rocks")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)

        top_words_names = [w for w, _ in r.top_words]
        self.assertIn("python", top_words_names)
        # Stop words must be absent
        for stop in ("the", "is", "a", "and"):
            self.assertNotIn(stop, top_words_names)
        # python should be the top-ranked word
        self.assertEqual(r.top_words[0][0], "python")

    def test_urls_excluded_from_keyword_count(self) -> None:
        """URLs must not bleed into keyword frequency after they are stripped."""
        ts = self._ts()
        self._insert(1, 1, "Alice", ts, 111, 100,
                     "visit https://python.org for documentation")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        top_words_names = [w for w, _ in r.top_words]
        # URL fragments like "https", "python" (from URL) should not dominate
        # "documentation" and "visit" are legitimate words
        self.assertIn("documentation", top_words_names)
        self.assertIn("visit", top_words_names)

    def test_date_range_ordering(self) -> None:
        """date_earliest must be before date_latest."""
        early_ts = datetime(2026, 1, 1,  10, 0, tzinfo=timezone.utc).isoformat()
        late_ts  = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc).isoformat()
        # Insert late first so we confirm ordering is from DB, not insertion order
        self._insert(2, 1, "Alice", late_ts,  111, 100, "last message")
        self._insert(1, 1, "Alice", early_ts, 111, 100, "first message")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertIsNotNone(r.date_earliest)
        self.assertIsNotNone(r.date_latest)

        dt_early = datetime.fromisoformat(r.date_earliest)
        dt_late  = datetime.fromisoformat(r.date_latest)
        self.assertLess(dt_early, dt_late,
                        "date_earliest should be strictly before date_latest")

    def test_limit_parameter_caps_rows(self) -> None:
        """The limit parameter must cap how many rows are returned from the DB."""
        for i in range(20):
            self._insert(i + 1, 1, "Alice", self._ts(minutes=i), 111, 100, f"msg {i}")

        r = analyze_user(
            self.db_path, user_id=1, username="Alice", guild_id=111,
            limit=5,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.message_count, 5)

    def test_avg_messages_per_active_day(self) -> None:
        """Average messages/active-day equals total / number of unique UTC days."""
        # 6 messages across 2 distinct days → avg = 3.0
        day1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        day2 = datetime(2026, 6, 2, tzinfo=timezone.utc)
        for i in range(3):
            self._insert(i + 1,   1, "Alice", (day1 + timedelta(hours=i)).isoformat(), 111, 100, "msg")
        for i in range(3):
            self._insert(i + 10,  1, "Alice", (day2 + timedelta(hours=i)).isoformat(), 111, 100, "msg")

        r = analyze_user(self.db_path, user_id=1, username="Alice", guild_id=111)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r.avg_messages_per_active_day, 3.0, places=2)


if __name__ == "__main__":
    unittest.main()
