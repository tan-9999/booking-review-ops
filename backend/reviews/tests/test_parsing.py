"""Parser tests. Pure unittest: no database, no network."""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from reviews.scraper.blocking import detect_block
from reviews.scraper.parsing import (
    parse_month_year,
    parse_rating,
    parse_review_date,
    parse_review_page,
)
from reviews.scraper.urls import build_reviewlist_url, hotel_page_url

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ValueParserTests(unittest.TestCase):
    def test_review_dates(self):
        self.assertEqual(parse_review_date("Reviewed: 12 November 2025"), date(2025, 11, 12))
        self.assertEqual(parse_review_date("Reviewed: November 3, 2025"), date(2025, 11, 3))
        self.assertEqual(parse_review_date("Reviewed: 3 Sept 2025"), date(2025, 9, 3))
        self.assertEqual(parse_review_date("2025-01-31"), date(2025, 1, 31))
        self.assertIsNone(parse_review_date("Reviewed: 31 February 2025"))
        self.assertIsNone(parse_review_date("Reviewed recently"))
        self.assertIsNone(parse_review_date(""))

    def test_month_year(self):
        self.assertEqual(parse_month_year("3 nights · October 2025"), date(2025, 10, 1))
        self.assertIsNone(parse_month_year("3 nights"))

    def test_ratings(self):
        self.assertEqual(parse_rating("Scored 8.0"), Decimal("8.0"))
        self.assertEqual(parse_rating("8,5"), Decimal("8.5"))
        self.assertEqual(parse_rating("10"), Decimal("10.0"))
        self.assertIsNone(parse_rating("Scored 11"))
        self.assertIsNone(parse_rating(""))


class LegacyMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = parse_review_page(fixture("reviewlist_legacy.html"), "https://example/reviews")

    def test_strategy_and_count(self):
        self.assertEqual(self.page.strategy, "legacy-reviewlist")
        self.assertEqual(self.page.cards_found, 3)
        self.assertEqual(self.page.card_errors, [])

    def test_full_review(self):
        r = self.page.reviews[0]
        self.assertEqual(r.external_id, "a1b2c3d4e5f60718")
        self.assertEqual(r.review_date, date(2025, 11, 12))
        self.assertEqual(r.rating, Decimal("8.0"))
        self.assertEqual(r.title, "Lovely stay, noisy street")
        self.assertEqual(r.positive_text, "The receptionist was very helpful and the room was spotless.")
        self.assertEqual(r.negative_text, "Traffic noise kept us awake. The AC was weak.")
        self.assertEqual(r.language, "en")
        self.assertEqual((r.reviewer_name, r.reviewer_country), ("Priya", "India"))
        self.assertEqual(r.room_type, "Deluxe Double Room")
        self.assertEqual(r.traveller_type, "Couple")
        self.assertEqual(r.nights, 3)
        self.assertEqual(r.stay_date, date(2025, 10, 1))
        self.assertEqual(r.hotel_response, "Thank you, Priya. We are adding double glazing.")
        self.assertEqual(r.helpful_votes, 2)
        self.assertEqual(r.validate(today=date(2025, 12, 1)), [])

    def test_score_only_review(self):
        r = self.page.reviews[1]
        self.assertEqual(r.rating, Decimal("10.0"))
        self.assertEqual((r.positive_text, r.negative_text), ("", ""))
        self.assertEqual(r.nights, 1)
        self.assertEqual(r.traveller_type, "Solo traveller")

    def test_us_date_comma_rating_and_no_id(self):
        r = self.page.reviews[2]
        self.assertEqual(r.external_id, "")
        self.assertEqual(r.review_date, date(2025, 11, 3))
        self.assertEqual(r.rating, Decimal("5.0"))
        self.assertEqual(r.positive_text, "")
        self.assertIn("smaller than the photos", r.negative_text)


class LiveTestIdMarkupTests(unittest.TestCase):
    """Real markup captured from Booking's 'Read all reviews' panel."""

    @classmethod
    def setUpClass(cls):
        cls.page = parse_review_page(fixture("review_cards_testid.html"))
        cls.by_name = {r.reviewer_name: r for r in cls.page.reviews}

    def test_strategy_and_count(self):
        self.assertEqual(self.page.strategy, "testid-cards")
        self.assertEqual(self.page.cards_found, 4)
        self.assertEqual(self.page.card_errors, [])
        self.assertEqual(set(self.by_name), {"Kevin", "Kathy", "Patrick", "Karen"})

    def test_all_fields(self):
        r = self.by_name["Kevin"]
        self.assertEqual(r.external_id, "")  # live cards expose no id -> fingerprint dedup
        self.assertEqual(r.reviewer_country, "Australia")
        self.assertEqual(r.review_date, date(2026, 10, 25))
        self.assertEqual(r.rating, Decimal("2.0"))
        self.assertEqual(r.title, "Never again")
        self.assertTrue(r.positive_text.startswith("I liked nothing"))  # not mistaken for a label
        self.assertEqual(r.negative_text, "everything")
        self.assertEqual(r.room_type, "Standard Double Room with Private External Bathroom")
        self.assertEqual(r.nights, 2)
        self.assertEqual(r.stay_date, date(2026, 10, 1))
        self.assertEqual(r.traveller_type, "Solo traveller")
        self.assertEqual(r.validate(today=date(2026, 11, 25)), [])

    def test_hotel_response_without_heading(self):
        reply = self.by_name["Kevin"].hotel_response
        self.assertTrue(reply.startswith("Thank you for your feedback, Kevin."))
        self.assertNotIn("Hotel response", reply)
        self.assertEqual(self.by_name["Patrick"].hotel_response, "")

    def test_negative_only_review(self):
        r = self.by_name["Karen"]
        self.assertEqual(r.positive_text, "")
        self.assertIn("traffic noise", r.negative_text.lower())
        self.assertEqual(r.rating, Decimal("1.0"))

    def test_singular_night(self):
        self.assertEqual(self.by_name["Patrick"].nights, 1)

    def test_truncated_reply_drops_toggle_text(self):
        html = """<div data-testid="review-card">
          <span data-testid="review-date">Reviewed: 1 November 2026</span>
          <div data-testid="review-score">Scored 7.0</div>
          <div data-testid="review-partner-reply"><h3>Hotel response:</h3>
            <span>Thanks for staying...</span>
            <button data-testid="review-pr-toggle">Continue reading</button></div>
        </div>"""
        r = parse_review_page(html).reviews[0]
        self.assertEqual(r.hotel_response, "Thanks for staying...")
        self.assertTrue(r.hotel_response_truncated)

    def test_expanded_reply_is_not_flagged(self):
        self.assertFalse(self.by_name["Kevin"].hotel_response_truncated)


class EmptyAndBlockedPageTests(unittest.TestCase):
    def test_empty_review_list_is_end_of_pagination(self):
        page = parse_review_page('<html><body><ul class="review_list"></ul></body></html>')
        self.assertEqual(page.cards_found, 0)
        self.assertTrue(page.looks_empty)

    def test_unknown_layout_is_not_empty(self):
        page = parse_review_page("<html><body><div class='brand-new-layout'>Hi</div></body></html>")
        self.assertEqual(page.cards_found, 0)
        self.assertFalse(page.looks_empty)

    def test_property_page_mentioning_no_reviews_is_not_empty(self):
        # Regression: this text on a normal property page used to end the run as "Success, 0 reviews".
        html = "<html><body><h1>Hotel</h1><p>Rooms with no reviews yet are shown last.</p></body></html>"
        page = parse_review_page(html)
        self.assertEqual(page.cards_found, 0)
        self.assertFalse(page.looks_empty)

    def test_challenge_detection(self):
        html = fixture("waf_challenge.html")
        self.assertEqual(parse_review_page(html).cards_found, 0)
        self.assertIsNotNone(detect_block(200, html))
        self.assertIsNotNone(detect_block(403, ""))
        self.assertIsNotNone(detect_block(202, ""))
        self.assertIsNone(detect_block(200, "<html><ul class='review_list'></ul></html>"))

    def test_normal_booking_page_is_not_flagged(self):
        # Every normal Booking page embeds this WAF config; it must not count as a block.
        html = "<html><script>window.awsWafCookieDomainList=['booking.com']</script><h1>Hotel</h1></html>"
        self.assertIsNone(detect_block(200, html))


class UrlTests(unittest.TestCase):
    def test_reviewlist_url(self):
        url = build_reviewlist_url("in", "sample-inn", offset=50, rows=25)
        self.assertIn("cc1=in", url)
        self.assertIn("pagename=sample-inn", url)
        self.assertIn("offset=50", url)
        self.assertIn("sort=f_recent_desc", url)

    def test_hotel_page_url(self):
        self.assertEqual(
            hotel_page_url("au", "olympic-paddington"),
            "https://www.booking.com/hotel/au/olympic-paddington.en-gb.html",
        )


if __name__ == "__main__":
    unittest.main()
