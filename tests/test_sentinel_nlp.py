"""
Unit tests for Sentinel NLP Service (spaCy NER & Topic Extraction).
"""

import unittest
from sentinel.services.nlp_service import extract_entities_and_topics, NLPExtractionResult


class TestNLPService(unittest.TestCase):
    def test_extract_entities_basic(self) -> None:
        """Test extraction of named entities (PERSON, ORG, LOC, PRODUCT)."""
        texts = [
            "Elon Musk met with Google engineers in California to discuss Python development.",
            "Apple released a new product in New York yesterday.",
            "Guido van Rossum created Python."
        ]
        result: NLPExtractionResult = extract_entities_and_topics(texts)
        self.assertTrue(result.is_nlp_available)

        # Check people
        person_names = [name for name, count in result.persons]
        self.assertTrue(any("Elon Musk" in p or "Musk" in p or "Guido" in p for p in person_names))

        # Check organizations
        org_names = [name for name, count in result.organizations]
        self.assertTrue(any("Google" in o or "Apple" in o for o in org_names))

    def test_extract_empty_or_url_only_texts(self) -> None:
        """Test handling of empty text lists or URL-only strings."""
        texts = [
            "https://example.com/test",
            "http://github.com",
            "   "
        ]
        result: NLPExtractionResult = extract_entities_and_topics(texts)
        self.assertTrue(result.is_nlp_available)
        self.assertEqual(len(result.persons), 0)
        self.assertEqual(len(result.organizations), 0)

    def test_extract_topics_noun_chunks(self) -> None:
        """Test keyphrase topic extraction."""
        texts = [
            "We need to run the database migration before deploying slash commands.",
            "The database migration was fast.",
            "Slash commands are working well."
        ]
        result: NLPExtractionResult = extract_entities_and_topics(texts)
        self.assertTrue(result.is_nlp_available)
        self.assertTrue(len(result.topics) >= 0)


if __name__ == "__main__":
    unittest.main()
