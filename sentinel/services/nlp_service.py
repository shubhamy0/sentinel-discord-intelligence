"""
Sentinel NLP Service — Named Entity Recognition & Topic Extraction.

Provides automated extraction of Named Entities (People, Organizations,
Locations, Products) and keyphrase topics from user message content using spaCy.

Features:
- Lazy loading of spaCy model (`en_core_web_sm`).
- Batch processing with `nlp.pipe` for maximum performance.
- Clean text preprocessing (stripping URLs/mentions).
- Safe fallback if spaCy or the language model is not installed.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('sentinel.services.nlp')

# Regex to strip HTTP(S) URLs prior to NLP extraction
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)

# Lazy-loaded spaCy model singletons
_NLP_MODEL = None
_MODEL_ATTEMPTED = False


def _get_nlp_model():
    """
    Lazily load and return the spaCy en_core_web_sm model.
    Returns None if spaCy or the model is not installed.
    """
    global _NLP_MODEL, _MODEL_ATTEMPTED
    if _MODEL_ATTEMPTED:
        return _NLP_MODEL

    _MODEL_ATTEMPTED = True
    try:
        import spacy
        try:
            _NLP_MODEL = spacy.load("en_core_web_sm")
            logger.info("spaCy language model 'en_core_web_sm' loaded successfully.")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' is not downloaded. "
                "Run: python -m spacy download en_core_web_sm"
            )
            _NLP_MODEL = None
    except ImportError:
        logger.warning("spaCy package is not installed. NLP features disabled.")
        _NLP_MODEL = None

    return _NLP_MODEL


@dataclass
class NLPExtractionResult:
    """
    Result container for NLP Named Entity & Topic Extraction.
    """
    persons: List[Tuple[str, int]] = field(default_factory=list)
    organizations: List[Tuple[str, int]] = field(default_factory=list)
    locations: List[Tuple[str, int]] = field(default_factory=list)
    products: List[Tuple[str, int]] = field(default_factory=list)
    topics: List[Tuple[str, int]] = field(default_factory=list)
    is_nlp_available: bool = False


def extract_entities_and_topics(
    texts: List[str],
    top_n_entities: int = 5,
    top_n_topics: int = 5,
) -> NLPExtractionResult:
    """
    Extract Named Entities and keyphrase topics from a collection of raw message texts.

    Parameters:
        texts           — list of message content strings.
        top_n_entities  — maximum entities to return per category.
        top_n_topics    — maximum topic keyphrases to return.

    Returns:
        NLPExtractionResult containing sorted entity and topic frequency pairs.
    """
    nlp = _get_nlp_model()
    if nlp is None or not texts:
        return NLPExtractionResult(is_nlp_available=False)

    # Clean URLs from texts before spaCy parsing
    clean_texts = [_URL_RE.sub("", text).strip() for text in texts]
    clean_texts = [t for t in clean_texts if t]

    if not clean_texts:
        return NLPExtractionResult(is_nlp_available=True)

    person_counter: Counter = Counter()
    org_counter: Counter = Counter()
    loc_counter: Counter = Counter()
    product_counter: Counter = Counter()
    topic_counter: Counter = Counter()

    try:
        # Batch process texts with spaCy
        for doc in nlp.pipe(clean_texts, batch_size=200):
            # 1. Named Entity Extraction
            for ent in doc.ents:
                clean_ent = ent.text.strip()
                # Filter out numbers, URLs, very short noise, or linebreaks
                if len(clean_ent) < 2 or clean_ent.isdigit() or "\n" in clean_ent:
                    continue

                label = ent.label_
                if label == "PERSON":
                    person_counter[clean_ent] += 1
                elif label in ("ORG", "NORP"):
                    org_counter[clean_ent] += 1
                elif label in ("GPE", "LOC", "FAC"):
                    loc_counter[clean_ent] += 1
                elif label in ("PRODUCT", "WORK_OF_ART", "LAW"):
                    product_counter[clean_ent] += 1

            # 2. Keyphrase / Topic Extraction via Noun Chunks
            if doc.has_annotation("DEP"):
                for chunk in doc.noun_chunks:
                    # Clean the noun chunk
                    phrase = chunk.text.strip().lower()
                    # Filter out pronouns, single short words, or stop phrases
                    words = phrase.split()
                    if (
                        len(phrase) < 3
                        or len(words) > 4
                        or phrase.isdigit()
                        or any(w in ("i", "you", "he", "she", "it", "we", "they", "this", "that", "something", "anything") for w in words)
                    ):
                        continue
                    
                    # Normalize whitespace
                    clean_phrase = " ".join(words)
                    topic_counter[clean_phrase] += 1

    except Exception as exc:
        logger.error("Error during spaCy NLP extraction: %s", exc)
        return NLPExtractionResult(is_nlp_available=False)

    return NLPExtractionResult(
        persons=person_counter.most_common(top_n_entities),
        organizations=org_counter.most_common(top_n_entities),
        locations=loc_counter.most_common(top_n_entities),
        products=product_counter.most_common(top_n_entities),
        topics=topic_counter.most_common(top_n_topics),
        is_nlp_available=True,
    )
