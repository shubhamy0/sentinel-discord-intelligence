"""
Unit tests for Sentinel Social Network Service (/network).
"""

import os
import sqlite3
import tempfile
import unittest

from sentinel.tracker.db import init_db_sync, log_message_sync
from sentinel.services.network_service import analyze_user_network_sync, NetworkAnalysisResult


class TestNetworkService(unittest.TestCase):
    def setUp(self) -> None:
        self.db_fd, self.db_path = tempfile.mkstemp()
        init_db_sync(self.db_path)

    def tearDown(self) -> None:
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_analyze_user_network_mentions(self) -> None:
        """Test extraction of outgoing and incoming user mentions in a guild."""
        guild_id = 999
        t0 = "2026-08-16T12:00:00+00:00"

        # Log target user (100) mentioning partner 200 and partner 300
        log_message_sync(
            self.db_path, message_id=1, user_id=100, username="TargetUser",
            timestamp=t0, guild_id=guild_id, channel_id=1,
            content="Hey <@200>, check out <@!300>'s code!"
        )
        log_message_sync(
            self.db_path, message_id=2, user_id=100, username="TargetUser",
            timestamp=t0, guild_id=guild_id, channel_id=1,
            content="Another mention for <@200>"
        )

        # Log partner 200 mentioning target user 100
        log_message_sync(
            self.db_path, message_id=3, user_id=200, username="PartnerAlice",
            timestamp=t0, guild_id=guild_id, channel_id=1,
            content="Replying to <@100> right here."
        )

        # Log partner 300 with no mentions
        log_message_sync(
            self.db_path, message_id=4, user_id=300, username="PartnerBob",
            timestamp=t0, guild_id=guild_id, channel_id=1,
            content="Just a regular message."
        )

        res: NetworkAnalysisResult = analyze_user_network_sync(
            self.db_path, target_user_id=100, target_username="TargetUser", guild_id=guild_id
        )

        self.assertTrue(res.has_data)
        self.assertEqual(res.total_outgoing_mentions, 3) # 200 twice, 300 once
        self.assertEqual(res.total_incoming_mentions, 1) # 200 mentioned 100 once

        # Verify partner ranking
        self.assertEqual(len(res.top_partners), 2)
        top_partner = res.top_partners[0]
        self.assertEqual(top_partner.user_id, 200)
        self.assertEqual(top_partner.username, "PartnerAlice")
        self.assertEqual(top_partner.outgoing_mentions, 2)
        self.assertEqual(top_partner.incoming_mentions, 1)
        self.assertEqual(top_partner.total_interactions, 3)

        second_partner = res.top_partners[1]
        self.assertEqual(second_partner.user_id, 300)
        self.assertEqual(second_partner.username, "PartnerBob")
        self.assertEqual(second_partner.outgoing_mentions, 1)
        self.assertEqual(second_partner.incoming_mentions, 0)
        self.assertEqual(second_partner.total_interactions, 1)

    def test_empty_network_data(self) -> None:
        """Test behavior when user has no stored messages or mentions."""
        res: NetworkAnalysisResult = analyze_user_network_sync(
            self.db_path, target_user_id=9999, target_username="NonExistent", guild_id=888
        )
        self.assertFalse(res.has_data)
        self.assertEqual(len(res.top_partners), 0)


if __name__ == "__main__":
    unittest.main()
