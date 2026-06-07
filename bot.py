#!/usr/bin/env python3
"""
Launcher script for the Discord self-bot with Activity Tracker enabled.
"""

import os
import json
import logging
import asyncio
import sys
from typing import Dict, Any

import discord
from discord.ext import commands

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('bot_runner')

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json or fall back to environment variables."""
    config = {
        "token": os.environ.get("DISCORD_TOKEN", ""),
        "prefix": os.environ.get("DISCORD_PREFIX", "/"),
        "db_path": os.environ.get("TRACKER_DB_PATH", "activity_tracker.db"),
        "ignore_bots": os.environ.get("TRACKER_IGNORE_BOTS", "true").lower() == "true",
        "presence_log_channel_id": os.environ.get("PRESENCE_LOG_CHANNEL_ID")
    }

    config_path = "config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                file_config = json.load(f)
                config.update(file_config)
            logger.info("Configuration loaded from %s", config_path)
        except Exception as e:
            logger.error("Failed to parse config.json: %s. Using environment fallbacks.", e)
    else:
        logger.info("config.json not found. Using environment configurations.")

    return config

async def main() -> None:
    config = load_config()

    if not config["token"] or config["token"] == "YOUR_DISCORD_TOKEN_HERE":
        logger.critical(
            "CRITICAL: Discord Token is missing! Please configure 'token' in config.json "
            "or set the DISCORD_TOKEN environment variable."
        )
        sys.exit(1)

    # Initialize the self-bot with prefix from config
    bot = commands.Bot(
        command_prefix=config["prefix"],
        self_bot=True,
        description="Selfbot with user activity tracking"
    )

    # Attach tracker settings to bot instance (will be parsed in extension setup)
    bot.tracker_db_path = config["db_path"]
    bot.tracker_ignore_bots = config["ignore_bots"]
    
    # Parse presence log channel ID
    presence_channel = config.get("presence_log_channel_id")
    if presence_channel is not None:
        try:
            bot.tracker_presence_log_channel_id = int(presence_channel)
        except (ValueError, TypeError):
            logger.error("presence_log_channel_id must be an integer or null. Got: %s", presence_channel)
            bot.tracker_presence_log_channel_id = None
    else:
        bot.tracker_presence_log_channel_id = None

    @bot.event
    async def on_ready() -> None:
        logger.info("--------------------------------------------------")
        logger.info("Logged in as: %s (ID: %s)", bot.user, bot.user.id)
        logger.info("Command Prefix: '%s'", config["prefix"])
        logger.info("Database Path: %s", config["db_path"])
        logger.info("Ignore Bots: %s", config["ignore_bots"])
        logger.info("Presence Log Channel: %s", bot.tracker_presence_log_channel_id)
        logger.info("--------------------------------------------------")

    async def load_extensions() -> None:
        try:
            await bot.load_extension("discord.ext.tracker")
            logger.info("Activity Tracker extension loaded successfully.")
        except Exception as e:
            logger.critical("Failed to load Activity Tracker extension: %s", e)
            raise

    # Register loader and start bot
    async with bot:
        await load_extensions()
        try:
            await bot.start(config["token"])
        except discord.LoginFailure:
            logger.critical("Failed to log in: Invalid token provided.")
            sys.exit(1)
        except Exception as e:
            logger.exception("Unexpected error occurred while running the bot: %s", e)
            sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot execution stopped via keyboard interrupt.")
    except Exception as e:
        logger.exception("Fatal engine crash: %s", e)
