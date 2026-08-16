"""
Sentinel Analysis Service.

Fetches stored messages for a target user in a specific guild and computes
statistics entirely in Python, enhanced with spaCy NLP Named Entity Recognition.

Data flow:
    analyze_user()
        └── fetch_messages_for_user_sync()   [DB query — guild-scoped]
        └── extract_entities_and_topics()    [spaCy NLP extraction]
        └── returns AnalysisResult           [dataclass]
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sentinel.tracker.db import fetch_messages_for_user_sync
from sentinel.services.nlp_service import extract_entities_and_topics, NLPExtractionResult

logger = logging.getLogger('sentinel.services.analysis')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FETCH_LIMIT = 5_000

_STOP_WORDS: frozenset = frozenset({
    "the", "a", "an",
    "and", "but", "or", "nor", "for", "yet", "so",
    "in", "on", "at", "to", "of", "by", "as", "up", "out", "off", "into",
    "from", "with", "about", "above", "below", "between", "through", "during",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "who", "which", "what",
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "may", "might", "must", "can", "could",
    "get", "got", "go", "going", "gone", "come", "came",
    "just", "also", "here", "there", "then", "than", "when", "where",
    "more", "some", "any", "all", "one", "not", "no", "so", "if",
    "how", "use", "like", "even", "only", "other", "into", "over",
    "im", "ive", "id", "ill", "its", "dont", "doesnt", "didnt",
    "isnt", "wasnt", "wouldnt", "couldnt", "shouldnt", "cant", "wont",
    "youre", "theyre", "were", "hes", "shes",
    "yeah", "yes", "okay", "ok", "lol", "hmm", "hey", "hi", "oh", "ah",
    "yep", "nope", "haha", "hahaha", "lmao", "omg", "tbh", "imo", "irl",
})

_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_WORD_RE = re.compile(r'\b[a-zA-Z]{3,}\b')


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ChannelActivity:
    """Message count for a single channel."""
    channel_id: int
    message_count: int


@dataclass
class AnalysisResult:
    """
    All computed statistics for a user in a specific guild, including spaCy NLP.
    """
    user_id: int
    username: str
    guild_id: int

    # --- Volume ---
    message_count: int
    messages_with_content: int

    # --- Time range ---
    date_earliest: Optional[str]
    date_latest: Optional[str]

    # --- Channel activity ---
    channel_activity: List[ChannelActivity]

    # --- Time patterns ---
    avg_messages_per_active_day: float
    peak_hour: Optional[int]
    peak_hour_count: int
    hour_distribution: Dict[int, int]

    # --- Content stats ---
    avg_message_length: float
    total_links: int
    top_words: List[Tuple[str, int]]

    # --- Milestone 4: spaCy NLP Entities & Topics ---
    top_entities: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    top_topics: List[Tuple[str, int]] = field(default_factory=list)

    # Whether any content was available at all
    has_content: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_user(
    db_path: str,
    user_id: int,
    username: str,
    guild_id: int,
    limit: int = DEFAULT_FETCH_LIMIT,
    top_n_words: int = 10,
    top_n_channels: int = 5,
) -> Optional[AnalysisResult]:
    """
    Fetch stored messages for *user_id* in *guild_id* and compute statistics & spaCy entities.
    """
    rows = fetch_messages_for_user_sync(db_path, user_id, guild_id, limit=limit)

    if not rows:
        return None

    message_count = len(rows)
    date_earliest: Optional[str] = rows[0]["timestamp"]
    date_latest: Optional[str] = rows[-1]["timestamp"]

    unique_days: set = set()
    hour_distribution: Dict[int, int] = {}

    for row in rows:
        ts = row["timestamp"]
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        unique_days.add(dt.date())
        hr = dt.hour
        hour_distribution[hr] = hour_distribution.get(hr, 0) + 1

    active_day_count = len(unique_days) or 1
    avg_messages_per_active_day = message_count / active_day_count

    if hour_distribution:
        peak_hour = max(hour_distribution, key=hour_distribution.__getitem__)
        peak_hour_count = hour_distribution[peak_hour]
    else:
        peak_hour = None
        peak_hour_count = 0

    channel_counts: Dict[int, int] = {}
    for row in rows:
        ch_id = row["channel_id"]
        channel_counts[ch_id] = channel_counts.get(ch_id, 0) + 1

    channel_activity: List[ChannelActivity] = [
        ChannelActivity(channel_id=ch_id, message_count=cnt)
        for ch_id, cnt in sorted(channel_counts.items(), key=lambda kv: kv[1], reverse=True)
    ][:top_n_channels]

    content_rows = [r for r in rows if r.get("content")]
    messages_with_content = len(content_rows)
    has_content = messages_with_content > 0

    total_chars = 0
    total_links = 0
    word_counter: Counter = Counter()

    for row in content_rows:
        text: str = row["content"]
        total_chars += len(text)
        total_links += len(_URL_RE.findall(text))
        clean = _URL_RE.sub("", text).lower()

        for word in _WORD_RE.findall(clean):
            if word not in _STOP_WORDS:
                word_counter[word] += 1

    avg_message_length = total_chars / messages_with_content if messages_with_content else 0.0
    top_words: List[Tuple[str, int]] = word_counter.most_common(top_n_words)

    # --- spaCy NLP Entity & Topic Extraction ---
    if has_content:
        raw_texts = [r["content"] for r in content_rows if r.get("content")]
        nlp_res: NLPExtractionResult = extract_entities_and_topics(raw_texts)
        top_entities = {
            "persons": nlp_res.persons,
            "organizations": nlp_res.organizations,
            "locations": nlp_res.locations,
            "products": nlp_res.products,
        }
        top_topics = nlp_res.topics
    else:
        top_entities = {}
        top_topics = []

    return AnalysisResult(
        user_id=user_id,
        username=username,
        guild_id=guild_id,
        message_count=message_count,
        messages_with_content=messages_with_content,
        date_earliest=date_earliest,
        date_latest=date_latest,
        channel_activity=channel_activity,
        avg_messages_per_active_day=avg_messages_per_active_day,
        peak_hour=peak_hour,
        peak_hour_count=peak_hour_count,
        hour_distribution=hour_distribution,
        avg_message_length=avg_message_length,
        total_links=total_links,
        top_words=top_words,
        top_entities=top_entities,
        top_topics=top_topics,
        has_content=has_content,
    )
