"""Regression tests for the validation layer, the price sheet and the flow.

Run with:  python manage.py test assistant -v 2
"""
from unittest.mock import patch

from django.test import TestCase
from pydantic import ValidationError

from assistant.models import ConversationState, LeadRecord
from assistant.services import agent
from assistant.services.extractor import ExtractedRequest
from assistant.services.price_sheet import build_quote, describe_row
from assistant.services.schemas import (
    clean_email, clean_person_name, normalize_format, normalize_platform,
    normalize_tier, QuoteRequest,
)


class NormalisationTests(TestCase):
    """The LLM is sloppy; normalisation must be forgiving but never wrong."""

    def test_platform_aliases_and_typos(self):
        cases = {
            "YouTube": "youtube", "yotube": "youtube", "yt": "youtube", "يوتيوب": "youtube",
            "TikTok": "tiktok", "tik tok": "tiktok", "تيك توك": "tiktok",
            "IG": "instagram", "instgram": "instagram", "إنستقرام": "instagram",
            "Snap": "snapchat", "سناب": "snapchat",
            "Twitter": "x", "x.com": "x",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_platform(raw), expected, raw)

    def test_unknown_platform_becomes_none(self):
        for raw in ("facebook", "linkedin", "", None, "null", "غير محدد"):
            self.assertIsNone(normalize_platform(raw), raw)

    def test_format_normalisation_and_sheet_wording(self):
        self.assertEqual(normalize_format("Integration (60-90 sec inside a video)"), "integration")
        self.assertEqual(normalize_format("Dedicated video"), "dedicated")
        self.assertEqual(normalize_format("Story (3 snaps)"), "story")
        self.assertEqual(normalize_format("reels"), "reel")
        self.assertIsNone(normalize_format("banner"))

    def test_tier_normalisation(self):
        self.assertEqual(normalize_tier("Tier A (10M+ subscribers)"), "A")
        self.assertEqual(normalize_tier("tier b"), "B")
        self.assertEqual(normalize_tier("C"), "C")
        self.assertIsNone(normalize_tier("Tier Z"))

    def test_email_validation(self):
        self.assertEqual(clean_email(" Ali@Example.COM "), "ali@example.com")
        self.assertEqual(clean_email("<ops@noon.com>"), "ops@noon.com")
        for bad in ("ali", "ali@", "@x.com", "a b@x.com", "", None):
            self.assertIsNone(clean_email(bad), bad)

    def test_person_name_validation(self):
        self.assertEqual(clean_person_name("Faheed Khan"), "Faheed Khan")
        self.assertEqual(clean_person_name("نون ناصر"), "نون ناصر")
        for bad in ("ali@x.com", "12345", "a", "", None):
            self.assertIsNone(clean_person_name(bad), bad)


class ExtractedRequestValidationTests(TestCase):
    """Numbers from the model must be coerced or dropped, never trusted."""

    def test_numeric_coercion(self):
        req = ExtractedRequest(
            brand="  haikal.ai  ",
            platform="Yotube",
            format="Dedicated video",
            creator_tier="Tier A (10M+ subscribers)",
            requested_days="60 days",
            budget_sar="100,000 SAR",
            requested_discount_pct="30%",
            email="UPPER@Case.Com",
        )
        self.assertEqual(req.brand, "haikal.ai")
        self.assertEqual(req.platform, "youtube")
        self.assertEqual(req.format, "dedicated")
        self.assertEqual(req.creator_tier, "A")
        self.assertEqual(req.requested_days, 60)
        self.assertEqual(req.budget_sar, 100000)
        self.assertEqual(req.requested_discount_pct, 30)
        self.assertEqual(req.email, "upper@case.com")

    def test_impossible_values_become_none(self):
        req = ExtractedRequest(
            requested_days=900, budget_sar=-100, requested_discount_pct=140,
            email="not-an-email", platform="myspace",
        )
        self.assertIsNone(req.requested_days)
        self.assertIsNone(req.budget_sar)
        self.assertIsNone(req.requested_discount_pct)
        self.assertIsNone(req.email)
        self.assertIsNone(req.platform)

    def test_placeholders_become_none(self):
        req = ExtractedRequest(brand="null", contact_name="N/A", format="unknown")
        self.assertIsNone(req.brand)
        self.assertIsNone(req.contact_name)
        self.assertIsNone(req.format)


class QuoteRequestGateTests(TestCase):
    """Nothing invalid may reach the price sheet."""

    def test_rejects_unknown_platform(self):
        with self.assertRaises(ValidationError):
            QuoteRequest(brand="X", platform="facebook", format="post")

    def test_defaults_and_clamping(self):
        req = QuoteRequest(brand="X", platform="x", format="post", discount_pct=140)
        self.assertEqual(req.creator_tier, "B")
        self.assertEqual(req.requested_days, 30)
        self.assertEqual(req.discount_pct, 100.0)   # clamped
        self.assertIsNone(req.budget_sar)

class PriceSheetTests(TestCase):
    """The quote must always match the sheet."""

    def test_case_a_sixty_days(self):
        quote = build_quote("youtube", "integration", "B", 60, 0)
        self.assertEqual(quote["code"], "YT-INT-B")
        self.assertEqual(quote["final"], 54000)      # 45,000 + 20%

    def test_case_b_instagram_reel(self):
        quote = build_quote("instagram", "reel", "B", 30, 0)
        self.assertEqual(quote["final"], 18000)

    def test_case_c_discount_above_cap(self):
        quote = build_quote("youtube", "integration", "A", 30, 30)
        self.assertEqual(quote["status"], "ESCALATE")
        self.assertNotIn("final", quote)             # no price is ever issued

    def test_discount_within_cap(self):
        quote = build_quote("x", "post", "A", 30, 20)
        self.assertEqual(quote["status"], "OK")
        self.assertEqual(quote["final"], 8000)       # 10,000 - 20%

    def test_unknown_combination(self):
        self.assertEqual(build_quote("x", "story", "A", 30, 0)["status"], "UNKNOWN")

    def test_sheet_labels_used(self):
        self.assertIn("10M+ subscribers", describe_row("youtube", "integration", "A"))
        self.assertIn("3 snaps", describe_row("snapchat", "story", "B"))


class ConversationFlowTests(TestCase):
    """End-to-end behaviour of the state machine with the LLM stubbed out."""

    PHONE = "whatsapp:+966500000123"
    BARE = "+966500000123"

    def setUp(self):
        ConversationState.objects.filter(phone=self.PHONE).delete()
        LeadRecord.objects.filter(phone=self.BARE).delete()

    def tearDown(self):
        ConversationState.objects.filter(phone=self.PHONE).delete()
        LeadRecord.objects.filter(phone=self.BARE).delete()

    def _say(self, text, **extracted):
        with patch.object(agent, "extract", return_value=ExtractedRequest(**extracted)):
            return agent.handle_message(self.PHONE, text)

    def _stage(self):
        state = ConversationState.objects.filter(phone=self.PHONE).first()
        return state.partial_request.get("_stage") if state else "DONE"

    def _seed_to_contact(self):
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        self._say("skip")                                  # budget
        self._say("no discount", requested_discount_pct=0)  # discount

    def test_name_only_answer_reasks_for_email(self):
        self._seed_to_contact()
        reply = self._say("ali", contact_name="ali")
        self.assertEqual(self._stage(), "contact")
        self.assertIn("email", reply.lower())

    def test_email_is_stored_and_lead_created(self):
        self._seed_to_contact()
        self._say("ali", contact_name="ali")
        reply = self._say("ali@haikal.ai", email="ali@haikal.ai")
        self.assertEqual(self._stage(), "confirm")
        self.assertIn("ali@haikal.ai", reply)

        self._say("1")
        lead = LeadRecord.objects.get(phone=self.BARE)
        self.assertEqual(lead.email, "ali@haikal.ai")
        self.assertEqual(lead.contact_name, "ali")
        self.assertFalse(lead.escalated)

    def test_skip_email_is_allowed(self):
        self._seed_to_contact()
        self._say("ali", contact_name="ali")
        self._say("skip")
        self.assertEqual(self._stage(), "confirm")

    def test_bare_yes_at_discount_asks_for_percentage(self):
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        self._say("skip")
        reply = self._say("yes")
        self.assertEqual(self._stage(), "discount")
        self.assertIn("percentage", reply.lower())

    def test_no_lead_before_confirmation(self):
        self._seed_to_contact()
        self._say("ali@haikal.ai", email="ali@haikal.ai")
        self.assertEqual(LeadRecord.objects.count(), 0)

    def test_case_c_escalates_without_asking_budget(self):
        reply = self._say(
            "amazon youtube integration tier A 30% off",
            brand="amazon", platform="youtube", format="integration",
            creator_tier="A", requested_days=30, requested_discount_pct=30,
        )
        self.assertEqual(self._stage(), "contact")
        self.assertIn("contact", reply.lower())

        self._say("ali@amazon.sa", contact_name="ali", email="ali@amazon.sa")
        self._say("1")
        lead = LeadRecord.objects.get(phone=self.BARE)
        self.assertTrue(lead.escalated)
        self.assertEqual(lead.stage, "Escalated")

