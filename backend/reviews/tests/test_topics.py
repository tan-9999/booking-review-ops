"""Topic classifier tests. Pure unittest. Several cases are regressions found on real reviews."""
import unittest
from decimal import Decimal

from reviews.insights.topics import (
    NEGATIVE,
    POSITIVE,
    SNIPPET_MAX,
    classify_review,
    classify_text,
    is_placeholder,
    sentiment_from_rating,
)


def topics(mentions):
    return {(m.topic, m.polarity) for m in mentions}


class PolarityTests(unittest.TestCase):
    def test_polarity_comes_from_liked_or_disliked_field(self):
        result = topics(classify_review("Great location and friendly staff.", "The bathroom was dirty."))
        self.assertIn(("location", POSITIVE), result)
        self.assertIn(("staff", POSITIVE), result)
        self.assertIn(("cleanliness", NEGATIVE), result)

    def test_negation_is_handled_by_the_field(self):
        # "not clean" in the disliked box is a cleanliness complaint, no negation parsing needed
        self.assertIn(("cleanliness", NEGATIVE), topics(classify_text("Room was not clean", NEGATIVE)))

    def test_one_sentence_can_hit_several_topics(self):
        result = topics(classify_text("The shared kitchen was filthy.", NEGATIVE))
        self.assertEqual(result, {("cleanliness", NEGATIVE), ("facilities", NEGATIVE)})

    def test_topic_counted_once_per_field_with_first_sentence_as_evidence(self):
        mentions = classify_text("Dirty floors. Also dirty sheets.", NEGATIVE)
        clean = [m for m in mentions if m.topic == "cleanliness"]
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0].snippet, "Dirty floors.")
        self.assertEqual(clean[0].matched, "dirty")


class PlaceholderTests(unittest.TestCase):
    def test_placeholders_are_ignored(self):
        for text in ["", "-", "Nothing", "nothing.", "N/A", "No complaints", "Tout était parfait"]:
            self.assertTrue(is_placeholder(text), text)
            self.assertEqual(classify_text(text, NEGATIVE), [])

    def test_real_complaint_is_not_a_placeholder(self):
        self.assertFalse(is_placeholder("Nothing worked in the room"))


class FalsePositiveRegressions(unittest.TestCase):
    """Each case was a wrong tag seen while auditing real reviews."""

    def test_better_is_not_german_bett(self):
        self.assertNotIn("room", {m.topic for m in classify_text("Could be better organised", NEGATIVE)})

    def test_dining_area_is_not_location(self):
        self.assertNotIn("location", {m.topic for m in classify_text("The dining area was messy", NEGATIVE)})

    def test_walked_into_is_not_location(self):
        self.assertNotIn("location", {m.topic for m in classify_text("I felt like I walked into a poor house", NEGATIVE)})

    def test_nowhere_near_enough_is_not_location(self):
        self.assertNotIn("location", {m.topic for m in classify_text("Nowhere near enough toilets", NEGATIVE)})

    def test_french_parties_communes_is_not_noise(self):
        self.assertNotIn("noise", {m.topic for m in classify_text("Les parties communes sont propres", POSITIVE)})


class CoverageTests(unittest.TestCase):
    def test_true_location_phrases(self):
        for text in ["Great location", "5 minute walk to the station", "Close to Darling Harbour", "nice area"]:
            self.assertIn("location", {m.topic for m in classify_text(text, POSITIVE)}, text)

    def test_virtual_reception_is_staff_and_check_in(self):
        result = {m.topic for m in classify_text("No reception, just a tablet for check-in", NEGATIVE)}
        self.assertTrue({"staff", "check_in"} <= result)

    def test_common_non_english_terms(self):
        self.assertIn("cleanliness", {m.topic for m in classify_text("El baño estaba sucio", NEGATIVE)})
        self.assertIn("noise", {m.topic for m in classify_text("Beaucoup de bruit la nuit", NEGATIVE)})
        self.assertIn("location", {m.topic for m in classify_text("Die Lage ist super", POSITIVE)})
        self.assertIn("food", {m.topic for m in classify_text("La cena incluida", POSITIVE)})

    def test_noise_sleep_phrases(self):
        self.assertIn("noise", {m.topic for m in classify_text("Couldn't sleep because of slamming doors", NEGATIVE)})
        self.assertIn("noise", {m.topic for m in classify_text("The walls could be a little thicker", NEGATIVE)})

    def test_long_snippet_is_clipped(self):
        mention = classify_text("Dirty " + "very " * 100 + "room.", NEGATIVE)[0]
        self.assertLessEqual(len(mention.snippet), SNIPPET_MAX)


class SentimentTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(sentiment_from_rating(Decimal("10")), POSITIVE)
        self.assertEqual(sentiment_from_rating(Decimal("8.0")), POSITIVE)
        self.assertEqual(sentiment_from_rating(Decimal("7.9")), "mixed")
        self.assertEqual(sentiment_from_rating(Decimal("6.0")), "mixed")
        self.assertEqual(sentiment_from_rating(Decimal("5.9")), NEGATIVE)
        self.assertEqual(sentiment_from_rating(Decimal("1.0")), NEGATIVE)
