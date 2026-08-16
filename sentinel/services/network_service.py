"""
Sentinel Social Network Service.

Analyzes mention patterns (outgoing/incoming <@user> tags) and channel co-activity
for a target user in a specific guild using SQLite database records.

Data flow:
    analyze_user_network()
        └── analyze_user_network_sync()   [DB query & regex mention parsing]
        └── returns NetworkAnalysisResult [dataclass]
"""

import sqlite3
import logging
import re
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('sentinel.services.network')

# Regex to match Discord user mentions: <@123456789> or <@!123456789>
_MENTION_RE = re.compile(r'<@!?(\d+)>')


def _connect(db_path: str) -> sqlite3.Connection:
    """Establish thread-safe connection to SQLite DB."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class ConnectionPartner:
    """Represents an interaction partner connected to the target user."""
    user_id: int
    username: str
    outgoing_mentions: int  # Target ➔ Partner
    incoming_mentions: int  # Partner ➔ Target
    total_interactions: int # outgoing + incoming


@dataclass
class NetworkAnalysisResult:
    """
    Result container for social network graph analysis.
    """
    target_user_id: int
    target_username: str
    guild_id: int
    total_outgoing_mentions: int
    total_incoming_mentions: int
    top_partners: List[ConnectionPartner]
    top_shared_channels: List[Tuple[int, int]]  # [(channel_id, message_count)]
    has_data: bool


def analyze_user_network_sync(
    db_path: str,
    target_user_id: int,
    target_username: str,
    guild_id: int,
    limit: int = 5000,
    top_n: int = 5,
) -> NetworkAnalysisResult:
    """
    Synchronously compute interaction network for target_user_id in guild_id.
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()

        # 1. Fetch Target's messages to find OUTGOING mentions
        cursor.execute(
            """
            SELECT channel_id, content
            FROM message_activity
            WHERE user_id = ? AND guild_id = ? AND content IS NOT NULL
            LIMIT ?
            """,
            (target_user_id, guild_id, limit),
        )
        target_rows = cursor.fetchall()

        outgoing_counter: Counter = Counter()
        target_channel_counter: Counter = Counter()

        for row in target_rows:
            ch_id = row["channel_id"]
            text = row["content"]
            target_channel_counter[ch_id] += 1

            for match in _MENTION_RE.finditer(text):
                mentioned_id = int(match.group(1))
                if mentioned_id != target_user_id:
                    outgoing_counter[mentioned_id] += 1

        # 2. Fetch other users' messages in this guild to find INCOMING mentions
        # We look for messages containing the target's ID in content
        target_mention_pattern1 = f"%<@{target_user_id}>%"
        target_mention_pattern2 = f"%<@!{target_user_id}>%"

        cursor.execute(
            """
            SELECT user_id, channel_id, content
            FROM message_activity
            WHERE guild_id = ? AND user_id != ? AND (content LIKE ? OR content LIKE ?)
            LIMIT ?
            """,
            (guild_id, target_user_id, target_mention_pattern1, target_mention_pattern2, limit),
        )
        incoming_rows = cursor.fetchall()

        incoming_counter: Counter = Counter()

        for row in incoming_rows:
            sender_id = row["user_id"]
            incoming_counter[sender_id] += 1

        # Combine all partner IDs
        all_partner_ids = set(outgoing_counter.keys()) | set(incoming_counter.keys())

        if not all_partner_ids and not target_rows:
            return NetworkAnalysisResult(
                target_user_id=target_user_id,
                target_username=target_username,
                guild_id=guild_id,
                total_outgoing_mentions=0,
                total_incoming_mentions=0,
                top_partners=[],
                top_shared_channels=[],
                has_data=False,
            )

        # 3. Resolve Partner Usernames from SQLite users table
        partner_usernames: Dict[int, str] = {}
        if all_partner_ids:
            placeholders = ",".join("?" for _ in all_partner_ids)
            cursor.execute(
                f"SELECT user_id, username FROM users WHERE user_id IN ({placeholders})",
                tuple(all_partner_ids),
            )
            for row in cursor.fetchall():
                partner_usernames[row["user_id"]] = row["username"]

        # Build ConnectionPartner objects
        partners: List[ConnectionPartner] = []
        for pid in all_partner_ids:
            out_c = outgoing_counter[pid]
            in_c = incoming_counter[pid]
            total = out_c + in_c
            uname = partner_usernames.get(pid, f"User {pid}")
            partners.append(
                ConnectionPartner(
                    user_id=pid,
                    username=uname,
                    outgoing_mentions=out_c,
                    incoming_mentions=in_c,
                    total_interactions=total,
                )
            )

        # Sort by total interactions descending
        partners.sort(key=lambda p: p.total_interactions, reverse=True)
        top_partners = partners[:top_n]

        total_outgoing = sum(outgoing_counter.values())
        total_incoming = sum(incoming_counter.values())

        top_channels = target_channel_counter.most_common(top_n)

        return NetworkAnalysisResult(
            target_user_id=target_user_id,
            target_username=target_username,
            guild_id=guild_id,
            total_outgoing_mentions=total_outgoing,
            total_incoming_mentions=total_incoming,
            top_partners=top_partners,
            top_shared_channels=top_channels,
            has_data=True,
        )

    except Exception as exc:
        logger.exception("Error in analyze_user_network_sync: %s", exc)
        return NetworkAnalysisResult(
            target_user_id=target_user_id,
            target_username=target_username,
            guild_id=guild_id,
            total_outgoing_mentions=0,
            total_incoming_mentions=0,
            top_partners=[],
            top_shared_channels=[],
            has_data=False,
        )
    finally:
        conn.close()


async def analyze_user_network(
    db_path: str,
    target_user_id: int,
    target_username: str,
    guild_id: int,
    limit: int = 5000,
    top_n: int = 5,
) -> NetworkAnalysisResult:
    """
    Asynchronously compute interaction network.
    """
    return await asyncio.to_thread(
        analyze_user_network_sync,
        db_path,
        target_user_id,
        target_username,
        guild_id,
        limit,
        top_n,
    )
