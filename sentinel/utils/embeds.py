"""
Sentinel Embed and Text Report Builders.

Converts AnalysisResult objects into formatted Discord outputs.
Provides both Discord Embed objects (for bot tokens) and clean Markdown
text reports (for selfbot / user accounts where rich embeds are restricted).
Includes spaCy Named Entity Recognition & Keyphrase Topic outputs.
"""

from datetime import datetime
from typing import Optional

import discord

from sentinel.services.analysis_service import AnalysisResult

_SENTINEL_COLOUR = 0x5865F2


def _fmt_date(iso_str: Optional[str]) -> str:
    """Format an ISO 8601 timestamp to a short human-readable date string."""
    if not iso_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return "Unknown"


def build_analyze_text(result: AnalysisResult, guild: discord.Guild) -> str:
    """
    Build a clean, beautifully formatted Markdown text report from an AnalysisResult.
    """
    date_range = f"{_fmt_date(result.date_earliest)} → {_fmt_date(result.date_latest)}"

    if result.peak_hour is not None:
        peak_str = f"`{result.peak_hour:02d}:00 – {(result.peak_hour + 1) % 24:02d}:00 UTC` ({result.peak_hour_count:,} msgs)"
    else:
        peak_str = "N/A"

    lines = [
        "🛡️ **Sentinel — User Intelligence Report**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 **Target:** `{result.username}` (<@{result.user_id}>)",
        f"📨 **Messages Analyzed:** **{result.message_count:,}**",
        f"📅 **Date Range:** {date_range}",
        "",
        f"📈 **Avg / Active Day:** **{result.avg_messages_per_active_day:.1f}** messages",
        f"⏰ **Peak Hour (UTC):** {peak_str}",
        "",
    ]

    if result.channel_activity:
        lines.append("📣 **Most Active Channels:**")
        for rank, ch in enumerate(result.channel_activity, start=1):
            lines.append(f"`{rank}.` <#{ch.channel_id}> — **{ch.message_count:,}** msgs")
        lines.append("")

    if result.has_content:
        lines.append(f"✏️ **Avg Message Length:** **{result.avg_message_length:.0f}** chars")
        lines.append(f"🔗 **Links Shared:** **{result.total_links:,}**")

        if result.top_topics:
            topic_parts = [f"`{t}` ×{c}" for t, c in result.top_topics]
            lines.append(f"💡 **Key Topics:**  {'  '.join(topic_parts)}")

        if result.top_words:
            word_parts = [f"`{w}` ×{c}" for w, c in result.top_words]
            lines.append(f"🔤 **Top Keywords:**  {'  '.join(word_parts)}")

        if result.top_entities:
            entity_lines = []
            if result.top_entities.get("persons"):
                p_str = ", ".join(f"`{name}`" for name, _ in result.top_entities["persons"])
                entity_lines.append(f"• **People:** {p_str}")
            if result.top_entities.get("organizations"):
                o_str = ", ".join(f"`{name}`" for name, _ in result.top_entities["organizations"])
                entity_lines.append(f"• **Organizations:** {o_str}")
            if result.top_entities.get("locations"):
                l_str = ", ".join(f"`{name}`" for name, _ in result.top_entities["locations"])
                entity_lines.append(f"• **Locations:** {l_str}")
            if result.top_entities.get("products"):
                pr_str = ", ".join(f"`{name}`" for name, _ in result.top_entities["products"])
                entity_lines.append(f"• **Products/Tech:** {pr_str}")

            if entity_lines:
                lines.append("\n🏷️ **Extracted Named Entities (spaCy):**")
                lines.extend(entity_lines)

        if result.messages_with_content < result.message_count:
            missing = result.message_count - result.messages_with_content
            lines.append(
                f"\n⚠️ *Partial Content Coverage: {missing:,} messages pre-date content logging.*"
            )
    else:
        lines.append(
            "ℹ️ *No message content available for keyword analysis. Run `/backfill` on history channels.*"
        )

    lines.append("\n*Server-scoped · " + guild.name + " · Bot-accessible data only*")

    return "\n".join(lines)


def build_analyze_embed(result: AnalysisResult, guild: discord.Guild) -> discord.Embed:
    """
    Build a structured Discord Embed from an AnalysisResult, including spaCy NER outputs.
    """
    embed = discord.Embed(
        title="🛡️  Sentinel — User Analysis",
        colour=_SENTINEL_COLOUR,
    )

    date_range = f"{_fmt_date(result.date_earliest)} → {_fmt_date(result.date_latest)}"

    embed.add_field(
        name="🎯 Target",
        value=f"`{result.username}`\n<@{result.user_id}>",
        inline=True,
    )
    embed.add_field(
        name="📨 Messages Analyzed",
        value=f"**{result.message_count:,}**",
        inline=True,
    )
    embed.add_field(
        name="📅 Date Range",
        value=date_range,
        inline=True,
    )

    if result.peak_hour is not None:
        peak_str = (
            f"`{result.peak_hour:02d}:00 – "
            f"{(result.peak_hour + 1) % 24:02d}:00 UTC` "
            f"({result.peak_hour_count:,} msgs)"
        )
    else:
        peak_str = "N/A"

    embed.add_field(
        name="📈 Avg / Active Day",
        value=f"**{result.avg_messages_per_active_day:.1f}** messages",
        inline=True,
    )
    embed.add_field(
        name="⏰ Peak Hour (UTC)",
        value=peak_str,
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    if result.channel_activity:
        lines = []
        for rank, ch in enumerate(result.channel_activity, start=1):
            lines.append(f"`{rank}.` <#{ch.channel_id}> — **{ch.message_count:,}** msgs")
        embed.add_field(
            name="📣 Most Active Channels",
            value="\n".join(lines),
            inline=False,
        )

    if result.has_content:
        embed.add_field(
            name="✏️ Avg Message Length",
            value=f"**{result.avg_message_length:.0f}** chars",
            inline=True,
        )
        embed.add_field(
            name="🔗 Links Shared",
            value=f"**{result.total_links:,}**",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if result.top_topics:
            topic_parts = [f"`{t}` ×{c}" for t, c in result.top_topics]
            embed.add_field(
                name="💡 Extracted Topics",
                value="  ".join(topic_parts),
                inline=False,
            )

        if result.top_words:
            word_parts = [f"`{w}` ×{c}" for w, c in result.top_words]
            embed.add_field(
                name="🔤 Top Keywords",
                value="  ".join(word_parts),
                inline=False,
            )

        # spaCy Named Entity Fields
        if result.top_entities:
            entity_parts = []
            if result.top_entities.get("persons"):
                p_str = ", ".join(f"`{n}`" for n, _ in result.top_entities["persons"])
                entity_parts.append(f"👤 **People:** {p_str}")
            if result.top_entities.get("organizations"):
                o_str = ", ".join(f"`{n}`" for n, _ in result.top_entities["organizations"])
                entity_parts.append(f"🏢 **Organizations:** {o_str}")
            if result.top_entities.get("locations"):
                l_str = ", ".join(f"`{n}`" for n, _ in result.top_entities["locations"])
                entity_parts.append(f"📍 **Locations:** {l_str}")
            if result.top_entities.get("products"):
                pr_str = ", ".join(f"`{n}`" for n, _ in result.top_entities["products"])
                entity_parts.append(f"🛠️ **Products/Tech:** {pr_str}")

            if entity_parts:
                embed.add_field(
                    name="🏷️ Named Entities (spaCy)",
                    value="\n".join(entity_parts),
                    inline=False,
                )

        if result.messages_with_content < result.message_count:
            missing = result.message_count - result.messages_with_content
            embed.add_field(
                name="⚠️ Partial Content Coverage",
                value=(
                    f"{missing:,} message(s) were stored before content capture "
                    f"was enabled. Content stats are based on "
                    f"**{result.messages_with_content:,}** messages only.\n"
                    "Run `/backfill` to recover historical content."
                ),
                inline=False,
            )

    else:
        embed.add_field(
            name="ℹ️ No Message Content Available",
            value=(
                "All stored messages pre-date Sentinel's content capture (Milestone 2).\n"
                "Run `/backfill` on the relevant channels to ingest historical content, "
                "then re-run `/analyze`."
            ),
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Server-scoped · {guild.name} · "
            "Analysis based on bot-accessible messages only"
        )
    )

    return embed


def build_network_embed(result, guild: discord.Guild) -> discord.Embed:
    """
    Build a structured Discord Embed for social interaction network graph.
    """
    embed = discord.Embed(
        title="🕸️  Sentinel — Social Connection Graph",
        colour=_SENTINEL_COLOUR,
    )

    embed.add_field(
        name="🎯 Target User",
        value=f"`{result.target_username}`\n<@{result.target_user_id}>",
        inline=True,
    )
    embed.add_field(
        name="📤 Outgoing Mentions",
        value=f"**{result.total_outgoing_mentions:,}**",
        inline=True,
    )
    embed.add_field(
        name="📥 Incoming Mentions",
        value=f"**{result.total_incoming_mentions:,}**",
        inline=True,
    )

    if result.top_partners:
        partner_lines = []
        for rank, p in enumerate(result.top_partners, start=1):
            partner_lines.append(
                f"`{rank}.` <@{p.user_id}> (`{p.username}`) — **{p.total_interactions:,}** interactions "
                f"(*{p.outgoing_mentions} out / {p.incoming_mentions} in*)"
            )
        embed.add_field(
            name="👥 Top Connected Partners",
            value="\n".join(partner_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="👥 Top Connected Partners",
            value="*No direct mentions found in current database records.*",
            inline=False,
        )

    if result.top_shared_channels:
        channel_lines = [
            f"<#{ch_id}> — **{cnt:,}** msgs" for ch_id, cnt in result.top_shared_channels
        ]
        embed.add_field(
            name="💬 Primary Active Channels",
            value="\n".join(channel_lines),
            inline=False,
        )

    embed.set_footer(
        text=f"Server-scoped · {guild.name} · Mention network analysis"
    )

    return embed

