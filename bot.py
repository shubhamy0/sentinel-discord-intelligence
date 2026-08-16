#!/usr/bin/env python3
"""
Launcher script for the Sentinel Discord Bot application with Activity Tracker enabled.
"""

import os
import json
import logging
import asyncio
import sys
from typing import Dict, Any

import discord
from discord.ext import commands

# Try loading environment variables from .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('sentinel_bot')


def load_config() -> Dict[str, Any]:
    """Load configuration from environment variables (.env) with config.json as fallback."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    file_config = {}
    config_path = "config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                file_config = json.load(f)
            logger.info("Configuration loaded from %s", config_path)
        except Exception as e:
            logger.error("Failed to parse config.json: %s. Using environment fallbacks.", e)
    else:
        logger.info("config.json not found. Using environment configurations.")

    placeholders = {"YOUR_DISCORD_BOT_TOKEN_HERE", "YOUR_DISCORD_TOKEN_HERE"}

    # Token precedence: 1. Environment variables, 2. config.json
    env_token = (os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_TOKEN") or "").strip()
    file_token = (file_config.get("token") or "").strip()

    if file_token in placeholders:
        file_token = ""

    token = env_token if env_token else file_token

    prefix = os.environ.get("DISCORD_PREFIX") or file_config.get("prefix", "/")
    db_path = os.environ.get("TRACKER_DB_PATH") or file_config.get("db_path", "activity_tracker.db")

    env_ignore_bots = os.environ.get("TRACKER_IGNORE_BOTS")
    if env_ignore_bots is not None:
        ignore_bots = env_ignore_bots.lower() == "true"
    else:
        ignore_bots = file_config.get("ignore_bots", True)

    presence_log_channel_id = os.environ.get("PRESENCE_LOG_CHANNEL_ID") or file_config.get("presence_log_channel_id")

    return {
        "token": token,
        "prefix": prefix,
        "db_path": db_path,
        "ignore_bots": ignore_bots,
        "presence_log_channel_id": presence_log_channel_id
    }


async def main() -> None:
    config = load_config()
    placeholders = {"YOUR_DISCORD_BOT_TOKEN_HERE", "YOUR_DISCORD_TOKEN_HERE"}

    if not config["token"] or config["token"] in placeholders:
        logger.critical(
            "CRITICAL: Discord Bot Token is missing! Please configure DISCORD_BOT_TOKEN in .env "
            "or set 'token' in config.json."
        )
        sys.exit(1)

    # Configure explicit least-privilege Gateway Intents
    intents = discord.Intents.default()
    intents.message_content = True  # Privileged intent: required for message text & URLs in on_message and /backfill
    intents.members = True          # Privileged intent: required for resolving server members in /analyze
    intents.presences = True        # Privileged intent: required for user status change tracking

    bot = commands.Bot(
        command_prefix=config["prefix"],
        intents=intents,
        description="Sentinel Intelligence & Activity Tracker Bot"
    )

    # Attach tracker settings to bot instance
    bot.tracker_db_path = config["db_path"]
    bot.tracker_ignore_bots = config["ignore_bots"]
    
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
        
        # Sync Application Slash Commands with Discord
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %d Application Slash Command(s).", len(synced))
        except Exception as e:
            logger.error("Failed to sync application commands: %s", e)
        logger.info("--------------------------------------------------")

    async def load_extensions() -> None:
        try:
            await bot.load_extension("sentinel.tracker")
            logger.info("Sentinel Activity Tracker extension loaded successfully.")
        except Exception as e:
            logger.critical("Failed to load Activity Tracker extension: %s", e)
            raise

        sentinel_extensions = [
            "sentinel.cogs.analysis",
        ]
        for ext in sentinel_extensions:
            try:
                await bot.load_extension(ext)
                logger.info("Sentinel extension '%s' loaded successfully.", ext)
            except Exception as e:
                logger.error("Failed to load Sentinel extension '%s': %s", ext, e)

    async with bot:
        await load_extensions()
        try:
            await bot.start(config["token"])
        except discord.LoginFailure:
            logger.critical("Failed to log in: Invalid Discord Bot token provided.")
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
