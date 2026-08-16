"""
Sentinel Analysis Cog (Official Discord Application Commands).

Slash Commands:
    /backfill channel: TextChannel [limit: int] — historical message ingestion
    /analyze  target: Member                   — per-user intelligence report
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from sentinel.tracker.db import log_message
from sentinel.services import analysis_service
from sentinel.utils.embeds import build_analyze_embed, build_analyze_text

logger = logging.getLogger('sentinel.cogs.analysis')

_BACKFILL_BASE_DELAY = 1.0   # seconds — initial backoff on rate limit
_BACKFILL_MAX_DELAY  = 60.0  # seconds — cap for exponential growth
_BACKFILL_PAGE_SIZE  = 100   # messages per Discord API call (Discord maximum)


async def _backoff_sleep(attempt: int) -> float:
    """
    Sleep for an exponentially increasing duration and return the delay used.
    Caps at _BACKFILL_MAX_DELAY to prevent indefinite stalls.
    """
    delay = min(_BACKFILL_BASE_DELAY * (2 ** attempt), _BACKFILL_MAX_DELAY)
    await asyncio.sleep(delay)
    return delay


class AnalysisCog(commands.Cog, name="Sentinel"):
    """
    Sentinel intelligence commands using official Discord Application Slash Commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /backfill  channel: TextChannel  [limit: int = 500]
    # ------------------------------------------------------------------

    @app_commands.command(
        name="backfill",
        description="Ingest historical messages from a server channel into the Sentinel database."
    )
    @app_commands.describe(
        channel="Target text channel in this server",
        limit="Maximum number of historical messages to fetch (default: 5000, max: 50000)"
    )
    async def slash_backfill(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: int = 5000,
    ) -> None:
        """
        Slash command for historical message ingestion into Sentinel DB.
        """
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ `/backfill` can only be run inside a server channel.",
                ephemeral=True,
            )
            return

        # Defer immediately to give us plenty of time for backfill without timing out interaction
        await interaction.response.defer(ephemeral=True)

        # Check permissions
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.read_message_history:
            await interaction.followup.send(
                f"❌ Bot does not have **Read Message History** permission in {channel.mention}.",
                ephemeral=True,
            )
            return

        MAX_LIMIT = 50_000
        if limit < 1:
            await interaction.followup.send("❌ Limit must be at least 1.", ephemeral=True)
            return
        if limit > MAX_LIMIT:
            limit = MAX_LIMIT

        db_path: str = getattr(self.bot, 'tracker_db_path', 'activity_tracker.db')
        ignore_bots: bool = getattr(self.bot, 'tracker_ignore_bots', True)
        guild_id = interaction.guild.id

        await interaction.followup.send(
            f"🔍 **Backfill started** for {channel.mention}\n"
            f"• Limit: **{limit:,}** messages\n"
            f"• Ignoring bots: **{ignore_bots}**\n"
            "Status update will be sent here upon completion.",
            ephemeral=True,
        )

        ingested = 0
        skipped = 0
        fetched = 0
        errors = 0
        last_message: Optional[discord.Message] = None

        logger.info(
            "Backfill started: channel=%s (%d), guild=%d, limit=%d",
            channel.name, channel.id, guild_id, limit,
        )

        backoff_attempt = 0

        while fetched < limit:
            page_size = min(_BACKFILL_PAGE_SIZE, limit - fetched)

            try:
                history_kwargs = {"limit": page_size}
                if last_message is not None:
                    history_kwargs["before"] = last_message

                page_messages = []
                async for msg in channel.history(**history_kwargs):
                    page_messages.append(msg)

            except discord.Forbidden:
                logger.warning("Backfill: lost read permission mid-flight in channel %d.", channel.id)
                errors += 1
                break

            except discord.HTTPException as exc:
                if exc.status == 429:
                    delay = await _backoff_sleep(backoff_attempt)
                    backoff_attempt += 1
                    logger.warning("Backfill rate-limited (attempt %d). Sleeping %.1fs.", backoff_attempt, delay)
                    continue
                else:
                    logger.error("Backfill HTTP error: %s", exc)
                    errors += 1
                    break

            except Exception as exc:
                logger.exception("Backfill unexpected error: %s", exc)
                errors += 1
                break

            backoff_attempt = 0

            if not page_messages:
                break

            last_message = page_messages[-1]
            fetched += len(page_messages)

            for msg in page_messages:
                if msg.author.bot and ignore_bots:
                    skipped += 1
                    continue

                if msg.guild is None:
                    skipped += 1
                    continue

                timestamp = msg.created_at.astimezone(timezone.utc).isoformat()

                try:
                    await log_message(
                        db_path,
                        msg.id,
                        msg.author.id,
                        str(msg.author),
                        timestamp,
                        guild_id,
                        msg.channel.id,
                        content=msg.content or None,
                        message_url=msg.jump_url or None,
                    )
                    ingested += 1
                except Exception as exc:
                    logger.error("Backfill: failed to store message %d: %s", msg.id, exc)
                    errors += 1

            await asyncio.sleep(0)

        oldest_ts = ""
        if last_message is not None:
            dt = last_message.created_at.astimezone(timezone.utc)
            oldest_ts = f"\n• Oldest message fetched: {dt.strftime('%Y-%m-%d %H:%M UTC')}"

        summary = (
            f"✅ **Backfill complete** for {channel.mention} in **{interaction.guild.name}**\n"
            f"• Messages fetched: **{fetched:,}**\n"
            f"• Messages stored in DB: **{ingested:,}**\n"
            f"• Skipped (bots/DMs): **{skipped:,}**\n"
            f"• Errors: **{errors:,}**"
            f"{oldest_ts}"
        )

        logger.info(
            "Backfill complete: channel=%d, fetched=%d, ingested=%d, skipped=%d, errors=%d",
            channel.id, fetched, ingested, skipped, errors,
        )

        await interaction.followup.send(summary, ephemeral=True)

    # ------------------------------------------------------------------
    # /analyze  target: Member
    # ------------------------------------------------------------------

    @app_commands.command(
        name="analyze",
        description="Generate a statistical intelligence report for a server member."
    )
    @app_commands.describe(
        target="Server member to analyze"
    )
    async def slash_analyze(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ) -> None:
        """
        Slash command generating a statistical intelligence report for a server member.
        """
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ `/analyze` can only be run inside a server channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        guild_id = interaction.guild.id
        db_path: str = getattr(self.bot, 'tracker_db_path', 'activity_tracker.db')

        try:
            result = await asyncio.to_thread(
                analysis_service.analyze_user,
                db_path=db_path,
                user_id=target.id,
                username=str(target),
                guild_id=guild_id,
            )
        except Exception as exc:
            logger.exception("Analysis failed for user %d in guild %d: %s", target.id, guild_id, exc)
            await interaction.followup.send("❌ Analysis failed due to a database error.")
            return

        if result is None:
            await interaction.followup.send(
                f"📭 No stored messages found for **{target}** in **{interaction.guild.name}**.\n"
                "Run `/backfill` on relevant channels to ingest history."
            )
            return

        embed = build_analyze_embed(result, interaction.guild)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /network  target: Member
    # ------------------------------------------------------------------

    @app_commands.command(
        name="network",
        description="Analyze social interaction connections and mentions for a server member."
    )
    @app_commands.describe(
        target="Server member to analyze"
    )
    async def slash_network(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ) -> None:
        """
        Slash command analyzing user mention patterns and social connections in the server.
        """
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ `/network` can only be run inside a server channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        guild_id = interaction.guild.id
        db_path: str = getattr(self.bot, 'tracker_db_path', 'activity_tracker.db')

        try:
            from sentinel.services import network_service
            from sentinel.utils.embeds import build_network_embed

            result = await network_service.analyze_user_network(
                db_path=db_path,
                target_user_id=target.id,
                target_username=str(target),
                guild_id=guild_id,
            )
        except Exception as exc:
            logger.exception("Network analysis failed for user %d in guild %d: %s", target.id, guild_id, exc)
            await interaction.followup.send("❌ Network analysis failed due to a database error.")
            return

        if not result.has_data:
            await interaction.followup.send(
                f"📭 No interaction or mention history found for **{target}** in **{interaction.guild.name}**.\n"
                "Run `/backfill` on server channels to ingest history."
            )
            return

        embed = build_network_embed(result, interaction.guild)
        await interaction.followup.send(embed=embed)



async def setup(bot: commands.Bot) -> None:
    """Standard entry point for loading this extension."""
    await bot.add_cog(AnalysisCog(bot))
