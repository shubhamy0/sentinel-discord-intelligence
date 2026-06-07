"""
Database layer for the Discord User Activity Tracker.
Manages schema initialization and performs asynchronous database operations using SQLite.
"""

import sqlite3
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger('discord.ext.tracker.db')

def _connect(db_path: str) -> sqlite3.Connection:
    """
    Establish a thread-safe connection to the SQLite database.
    Enables WAL mode and foreign key support.
    """
    conn = sqlite3.connect(db_path, timeout=30.0)
    # Enable WAL mode for high concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db_sync(db_path: str) -> None:
    """
    Synchronously initialize the SQLite database tables and indices.
    """
    conn = _connect(db_path)
    try:
        with conn:
            # Create Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
            """)
            
            # Create Message Activity table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_activity (
                    message_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    guild_id INTEGER,
                    channel_id INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );
            """)
            
            # Create Voice Activity table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    join_time TEXT NOT NULL,
                    leave_time TEXT,
                    duration REAL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );
            """)
            
            # Create Presence Activity table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS presence_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    old_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    duration REAL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );
            """)
            
            # Create Tracked Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracked_users (
                    user_id INTEGER PRIMARY KEY
                );
            """)
            
            # Create Indices for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_message_user ON message_activity(user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_message_timestamp ON message_activity(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_activity(user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_leave ON voice_activity(user_id, leave_time);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_presence_user ON presence_activity(user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_presence_timestamp ON presence_activity(timestamp);")
            
        logger.info("Database initialized successfully at %s", db_path)
    except Exception as e:
        logger.exception("Failed to initialize database: %s", e)
        raise
    finally:
        conn.close()

async def init_db(db_path: str) -> None:
    """
    Asynchronously initialize the database.
    """
    await asyncio.to_thread(init_db_sync, db_path)

def log_message_sync(
    db_path: str,
    message_id: int,
    user_id: int,
    username: str,
    timestamp: str,
    guild_id: Optional[int],
    channel_id: int
) -> None:
    """
    Synchronously insert a message log and update user last seen.
    """
    conn = _connect(db_path)
    try:
        with conn:
            # Insert or update user first to satisfy foreign key
            conn.execute(
                """
                INSERT INTO users (user_id, username, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (user_id, username, timestamp)
            )
            
            # Insert message activity
            conn.execute(
                """
                INSERT OR IGNORE INTO message_activity (message_id, user_id, timestamp, guild_id, channel_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, user_id, timestamp, guild_id, channel_id)
            )
    except Exception as e:
        logger.exception("Failed to log message sync: %s", e)
    finally:
        conn.close()

async def log_message(
    db_path: str,
    message_id: int,
    user_id: int,
    username: str,
    timestamp: str,
    guild_id: Optional[int],
    channel_id: int
) -> None:
    """
    Asynchronously log message activity.
    """
    await asyncio.to_thread(
        log_message_sync, db_path, message_id, user_id, username, timestamp, guild_id, channel_id
    )

def log_voice_join_sync(
    db_path: str,
    user_id: int,
    username: str,
    guild_id: int,
    channel_id: int,
    timestamp: str
) -> None:
    """
    Synchronously log a voice channel join. Closes any lingering open sessions first.
    """
    conn = _connect(db_path)
    try:
        with conn:
            # Upsert user record
            conn.execute(
                """
                INSERT INTO users (user_id, username, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (user_id, username, timestamp)
            )
            
            # Find and close any active voice sessions for this user (lingering sessions)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, join_time FROM voice_activity WHERE user_id = ? AND leave_time IS NULL",
                (user_id,)
            )
            active_sessions = cursor.fetchall()
            
            for sess in active_sessions:
                sess_id = sess['id']
                join_time_str = sess['join_time']
                try:
                    join_dt = datetime.fromisoformat(join_time_str)
                    leave_dt = datetime.fromisoformat(timestamp)
                    dur = (leave_dt - join_dt).total_seconds()
                except Exception:
                    dur = 0.0
                
                cursor.execute(
                    "UPDATE voice_activity SET leave_time = ?, duration = ? WHERE id = ?",
                    (timestamp, dur, sess_id)
                )
                
            # Insert the new voice join event
            conn.execute(
                """
                INSERT INTO voice_activity (user_id, guild_id, channel_id, join_time, leave_time, duration)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (user_id, guild_id, channel_id, timestamp)
            )
    except Exception as e:
        logger.exception("Failed to log voice join sync: %s", e)
    finally:
        conn.close()

async def log_voice_join(
    db_path: str,
    user_id: int,
    username: str,
    guild_id: int,
    channel_id: int,
    timestamp: str
) -> None:
    """
    Asynchronously log a voice channel join.
    """
    await asyncio.to_thread(
        log_voice_join_sync, db_path, user_id, username, guild_id, channel_id, timestamp
    )

def log_voice_leave_sync(
    db_path: str,
    user_id: int,
    username: str,
    guild_id: int,
    channel_id: int,
    timestamp: str
) -> None:
    """
    Synchronously log a voice channel leave. Closes the most recent open session.
    """
    conn = _connect(db_path)
    try:
        with conn:
            # Upsert user record
            conn.execute(
                """
                INSERT INTO users (user_id, username, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (user_id, username, timestamp)
            )
            
            cursor = conn.cursor()
            # Find the most recent active session
            cursor.execute(
                """
                SELECT id, join_time FROM voice_activity
                WHERE user_id = ? AND leave_time IS NULL
                ORDER BY join_time DESC LIMIT 1
                """,
                (user_id,)
            )
            sess = cursor.fetchone()
            
            if sess:
                sess_id = sess['id']
                join_time_str = sess['join_time']
                try:
                    join_dt = datetime.fromisoformat(join_time_str)
                    leave_dt = datetime.fromisoformat(timestamp)
                    dur = (leave_dt - join_dt).total_seconds()
                except Exception:
                    dur = 0.0
                    
                cursor.execute(
                    "UPDATE voice_activity SET leave_time = ?, duration = ? WHERE id = ?",
                    (timestamp, dur, sess_id)
                )
            else:
                # No active session found (e.g. joined before bot started). Record session starting and ending now.
                conn.execute(
                    """
                    INSERT INTO voice_activity (user_id, guild_id, channel_id, join_time, leave_time, duration)
                    VALUES (?, ?, ?, ?, ?, 0.0)
                    """,
                    (user_id, guild_id, channel_id, timestamp, timestamp)
                )
    except Exception as e:
        logger.exception("Failed to log voice leave sync: %s", e)
    finally:
        conn.close()

async def log_voice_leave(
    db_path: str,
    user_id: int,
    username: str,
    guild_id: int,
    channel_id: int,
    timestamp: str
) -> None:
    """
    Asynchronously log a voice channel leave.
    """
    await asyncio.to_thread(
        log_voice_leave_sync, db_path, user_id, username, guild_id, channel_id, timestamp
    )

def fetch_db_user_sync(db_path: str, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Synchronously fetch user info from database.
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, last_seen FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.exception("Failed to fetch user from DB sync: %s", e)
        return None
    finally:
        conn.close()

async def fetch_db_user(db_path: str, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Asynchronously fetch user info from database.
    """
    return await asyncio.to_thread(fetch_db_user_sync, db_path, user_id)

def find_user_by_name_sync(db_path: str, username_query: str) -> Optional[Dict[str, Any]]:
    """
    Synchronously find a user by their username (substring check, case insensitive).
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username, last_seen FROM users WHERE username LIKE ? ORDER BY last_seen DESC LIMIT 1",
            (f"%{username_query}%",)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.exception("Failed to find user by name sync: %s", e)
        return None
    finally:
        conn.close()

async def find_user_by_name(db_path: str, username_query: str) -> Optional[Dict[str, Any]]:
    """
    Asynchronously find a user by their username.
    """
    return await asyncio.to_thread(find_user_by_name_sync, db_path, username_query)

def log_presence_change_sync(
    db_path: str,
    user_id: int,
    username: str,
    old_status: str,
    new_status: str,
    timestamp: str
) -> Optional[float]:
    """
    Synchronously log user presence change.
    Filters out consecutive duplicates and calculates duration of the old status.
    """
    conn = _connect(db_path)
    try:
        with conn:
            # Upsert user record
            conn.execute(
                """
                INSERT INTO users (user_id, username, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (user_id, username, timestamp)
            )
            
            cursor = conn.cursor()
            # Fetch the user's last logged status
            cursor.execute(
                """
                SELECT id, new_status, timestamp FROM presence_activity
                WHERE user_id = ?
                ORDER BY timestamp DESC, id DESC LIMIT 1
                """,
                (user_id,)
            )
            last_row = cursor.fetchone()
            
            if last_row:
                last_status = last_row["new_status"]
                # Skip duplicate consecutive status updates
                if last_status == new_status:
                    return None
                
                last_time_str = last_row["timestamp"]
                try:
                    last_dt = datetime.fromisoformat(last_time_str)
                    current_dt = datetime.fromisoformat(timestamp)
                    duration = (current_dt - last_dt).total_seconds()
                    if duration < 0:
                        duration = 0.0
                except Exception:
                    duration = 0.0
            else:
                duration = None
                
            conn.execute(
                """
                INSERT INTO presence_activity (user_id, old_status, new_status, timestamp, duration)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, old_status, new_status, timestamp, duration)
            )
            return duration
    except Exception as e:
        logger.exception("Failed to log presence change sync: %s", e)
        return None
    finally:
        conn.close()

async def log_presence_change(
    db_path: str,
    user_id: int,
    username: str,
    old_status: str,
    new_status: str,
    timestamp: str
) -> Optional[float]:
    """
    Asynchronously log user presence change.
    """
    return await asyncio.to_thread(
        log_presence_change_sync, db_path, user_id, username, old_status, new_status, timestamp
    )

def fetch_recent_presence_sync(db_path: str, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Synchronously fetch recent presence updates for a user.
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT old_status, new_status, timestamp, duration FROM presence_activity
            WHERE user_id = ?
            ORDER BY timestamp DESC, id DESC LIMIT ?
            """,
            (user_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.exception("Failed to fetch recent presence sync: %s", e)
        return []
    finally:
        conn.close()

async def fetch_recent_presence(db_path: str, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Asynchronously fetch recent presence updates for a user.
    """
    return await asyncio.to_thread(fetch_recent_presence_sync, db_path, user_id, limit)

def add_tracked_user_sync(db_path: str, user_id: int) -> None:
    """
    Synchronously add a user ID to the list of persistently tracked presence users.
    """
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO tracked_users (user_id) VALUES (?)", (user_id,))
    except Exception as e:
        logger.exception("Failed to add tracked user sync: %s", e)
    finally:
        conn.close()

async def add_tracked_user(db_path: str, user_id: int) -> None:
    """
    Asynchronously add a user ID to the list of persistently tracked presence users.
    """
    await asyncio.to_thread(add_tracked_user_sync, db_path, user_id)

def remove_tracked_user_sync(db_path: str, user_id: int) -> None:
    """
    Synchronously remove a user ID from the list of persistently tracked presence users.
    """
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM tracked_users WHERE user_id = ?", (user_id,))
    except Exception as e:
        logger.exception("Failed to remove tracked user sync: %s", e)
    finally:
        conn.close()

async def remove_tracked_user(db_path: str, user_id: int) -> None:
    """
    Asynchronously remove a user ID from the list of persistently tracked presence users.
    """
    await asyncio.to_thread(remove_tracked_user_sync, db_path, user_id)

def get_tracked_users_sync(db_path: str) -> List[int]:
    """
    Synchronously fetch the list of persistently tracked user IDs.
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM tracked_users")
        return [row["user_id"] for row in cursor.fetchall()]
    except Exception as e:
        logger.exception("Failed to get tracked users sync: %s", e)
        return []
    finally:
        conn.close()

async def get_tracked_users(db_path: str) -> List[int]:
    """
    Asynchronously fetch the list of persistently tracked user IDs.
    """
    return await asyncio.to_thread(get_tracked_users_sync, db_path)

def is_tracked_user_sync(db_path: str, user_id: int) -> bool:
    """
    Synchronously check if a user is in the tracked_users table.
    """
    conn = _connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM tracked_users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None
    except Exception as e:
        logger.exception("Failed to check if user is tracked sync: %s", e)
        return False
    finally:
        conn.close()

async def is_tracked_user(db_path: str, user_id: int) -> bool:
    """
    Asynchronously check if a user is in the tracked_users table.
    """
    return await asyncio.to_thread(is_tracked_user_sync, db_path, user_id)




