"""Regression tests for the validation layer, the price sheet and the flow.

Run with:  python manage.py test assistant -v 2
"""
from unittest.mock import patch

from django.contrib import messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import translation
from django.utils.translation import ngettext
from pydantic import ValidationError

from assistant.context_processors import MAX_FLASH_MESSAGES, flash_messages
from assistant.models import ConversationState, InboundMessage, LeadRecord
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


class LanguageSwitchTests(TestCase):
    """The EN/AR toggle: locale plumbing, RTL shell and the messages-framework flash."""

    def test_default_language_is_english_ltr(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="en"')
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, "Dashboard")

    def test_both_language_buttons_are_rendered(self):
        html = self.client.get("/").content.decode()
        self.assertIn('name="language" value="en"', html)
        self.assertIn('name="language" value="ar"', html)
        self.assertIn('action="/language/"', html)

    def test_switch_to_arabic_sets_cookie_rtl_and_flash(self):
        response = self.client.post("/language/", {"language": "ar", "next": "/"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.cookies["powr_language"].value, "ar")
        self.assertContains(response, 'lang="ar"')
        self.assertContains(response, 'dir="rtl"')
        self.assertContains(response, "لوحة التحكم")            # Dashboard
        self.assertContains(response, "تم تعيين لغة الواجهة إلى")  # flash message

    def test_language_cookie_persists_across_requests(self):
        self.client.post("/language/", {"language": "ar", "next": "/"})
        self.assertContains(self.client.get("/leads/"), "الفرص")

    def test_flash_message_is_shown_only_once(self):
        self.client.post("/language/", {"language": "ar", "next": "/"})
        first = self.client.get("/").content.decode()
        second = self.client.get("/").content.decode()
        self.assertIn("تم تعيين لغة الواجهة", first)
        self.assertNotIn("تم تعيين لغة الواجهة", second)

    def test_arabic_plural_forms(self):
        with translation.override("ar"):
            cases = {0: "لا رسائل", 1: "رسالة واحدة", 2: "رسالتان",
                     5: "5 رسائل", 15: "15 رسالة", 200: "200 رسالة"}
            for count, expected in cases.items():
                self.assertEqual(
                    ngettext("%(counter)s message", "%(counter)s messages", count)
                    % {"counter": count},
                    expected,
                    count,
                )

    def test_unknown_language_is_ignored(self):
        response = self.client.post("/language/", {"language": "xx", "next": "/"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("powr_language", self.client.cookies)
        self.assertContains(response, "Dashboard")


class ConciergeChatbotTests(TestCase):
    """The old "simulator" is now the POWR Concierge chatbot."""

    def test_chatbot_page_branding(self):
        response = self.client.get("/chat/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "POWR Concierge")
        self.assertContains(response, "Every web message is logged")
        self.assertNotContains(response, "Simulator")
        self.assertNotContains(response, "simulator")

    def test_navigation_labels_to_chatbot(self):
        html = self.client.get("/").content.decode()
        self.assertIn('href="/chat/"', html)
        self.assertIn("Chatbot", html)
        self.assertNotIn("Simulator", html)

    def test_composer_is_an_auto_growing_textarea(self):
        html = self.client.get("/chat/").content.decode()
        self.assertIn('<textarea id="msg-input"', html)
        self.assertIn('class="composer-input"', html)
        self.assertIn('class="composer-shell"', html)
        self.assertIn('id="send-btn" class="send-btn"', html)
        self.assertIn("Shift", html)          # keyboard hint
        self.assertIn("disabled", html)       # send stays disabled until there is text

    def test_composer_hint_is_translated(self):
        self.client.post("/language/", {"language": "ar", "next": "/"})
        html = self.client.get("/chat/").content.decode()
        self.assertIn("للإرسال", html)
        self.assertIn("لسطر جديد", html)


class PlanRevisionTests(TestCase):
    """A customer may change platform / plan / budget until they confirm.

    Scenario from the brief: the customer picked one platform (X), has not
    confirmed yet, and now wants a different package or a different budget.
    """

    PHONE = "whatsapp:+966500000777"
    BARE = "+966500000777"

    def setUp(self):
        ConversationState.objects.filter(phone=self.PHONE).delete()
        LeadRecord.objects.filter(phone=self.BARE).delete()

    def tearDown(self):
        ConversationState.objects.filter(phone=self.PHONE).delete()
        LeadRecord.objects.filter(phone=self.BARE).delete()

    def _say(self, text, **extracted):
        with patch.object(agent, "extract", return_value=ExtractedRequest(**extracted)):
            return agent.handle_message(self.PHONE, text)

    def _state(self):
        return ConversationState.objects.get(phone=self.PHONE).partial_request

    def _walk_to_confirm_on_x(self):
        """X · Post · 30 days · budget 20,000 · contact given → confirm stage."""
        self._say("noon", brand="noon")
        self._say("x", platform="x")
        self._say("post", format="post")
        self._say("budget 20000", requested_days=30, budget_sar=20000)
        self._say("no discount", requested_discount_pct=0)
        self._say("ali", contact_name="ali")
        self._say("ali@noon.com", email="ali@noon.com")
        return self._state()

    def test_walk_reaches_confirm_without_creating_a_lead(self):
        state = self._walk_to_confirm_on_x()
        self.assertEqual(state["_stage"], "confirm")
        self.assertEqual(state["platform"], "x")
        self.assertEqual(LeadRecord.objects.count(), 0)

    def test_platform_can_change_before_confirmation(self):
        self._walk_to_confirm_on_x()

        reply = self._say("actually make it tiktok", platform="tiktok")

        self.assertIn("Request updated before confirmation", reply)
        self.assertIn("Platform: X → TikTok", reply)
        self.assertIn("Format: Post → Video", reply)   # TikTok only runs Video
        state = self._state()
        self.assertEqual(state["platform"], "tiktok")
        self.assertEqual(state["format"], "video")
        self.assertEqual(state["_stage"], "confirm")   # still waiting for *1*
        self.assertEqual(LeadRecord.objects.count(), 0)

    def test_budget_can_change_before_confirmation(self):
        self._walk_to_confirm_on_x()

        reply = self._say("budget 12000")

        self.assertIn("Request updated before confirmation", reply)
        self.assertIn("Budget: 20,000 SAR → 12,000 SAR", reply)
        self.assertEqual(self._state()["budget_sar"], 12000)
        self.assertEqual(LeadRecord.objects.count(), 0)

    def test_duration_and_tier_can_change_before_confirmation(self):
        self._walk_to_confirm_on_x()

        reply = self._say("make it 60 days, tier B")

        self.assertIn("Request updated before confirmation", reply)
        state = self._state()
        self.assertEqual(state["requested_days"], 60)
        self.assertEqual(state["creator_tier"], "B")
        self.assertEqual(state["_stage"], "confirm")

    def test_yes_plus_a_change_does_not_book(self):
        self._walk_to_confirm_on_x()

        reply = self._say("yes but make it snapchat", platform="snapchat")

        self.assertEqual(LeadRecord.objects.count(), 0)
        self.assertNotIn("Reference:", reply)
        self.assertEqual(self._state()["platform"], "snapchat")
        self.assertEqual(self._state()["_stage"], "confirm")

    def test_menu_option_4_offers_a_plan_change(self):
        self._walk_to_confirm_on_x()

        reply = self._say("4")

        self.assertIn("change it before you confirm", reply)
        self.assertIn("X", reply)
        self.assertEqual(self._state()["_stage"], "confirm")

    def test_revision_then_confirm_books_the_new_package(self):
        self._walk_to_confirm_on_x()
        self._say("actually make it tiktok", platform="tiktok")

        self._say("1")

        lead = LeadRecord.objects.get(phone=self.BARE)
        self.assertEqual(lead.platform, "tiktok")
        self.assertEqual(lead.format, "video")
        self.assertEqual(lead.final_quote_sar, 15000)   # TikTok Video · Tier B
        self.assertFalse(lead.escalated)

    def test_bare_confirmation_ignores_stray_extraction(self):
        """A hallucinated value on "1" must not change what gets booked."""
        self._walk_to_confirm_on_x()

        self._say("1", platform="tiktok")

        lead = LeadRecord.objects.get(phone=self.BARE)
        self.assertEqual(lead.platform, "x")          # exactly what was shown
        self.assertEqual(lead.final_quote_sar, 10000)

    def test_lower_budget_on_revision_escalates_on_confirm(self):
        self._walk_to_confirm_on_x()

        summary = self._say("budget 5000")
        self.assertIn("Request updated before confirmation", summary)
        self.assertEqual(self._state()["budget_sar"], 5000)

        self._say("1")
        lead = LeadRecord.objects.get(phone=self.BARE)
        self.assertTrue(lead.escalated)               # 5,000 < cheapest X package
        self.assertEqual(lead.stage, "Escalated")


class FlashMessageHygieneTests(TestCase):
    """The toast stack must never dump app data, nor pile up.

    Regression: the History page used to expose its queryset as ``messages``,
    which shadowed the messages framework variable — every inbound message was
    rendered as a toast and the real flash messages were never consumed.
    """

    PHONE = "whatsapp:+966500000999"

    def setUp(self):
        InboundMessage.objects.create(
            twilio_sid="test-history-1",
            phone=self.PHONE,
            body="Hello from the test suite",
            outbound_reply="Hi there",
            source="Web Simulator",
        )

    def test_history_page_does_not_render_inbound_rows_as_toasts(self):
        html = self.client.get("/history/").content.decode()
        self.assertIn("Hello from the test suite", html)      # the row is rendered
        self.assertNotIn('<div class="flash-stack"', html)     # …but not as a toast
        self.assertNotIn("InboundMessage object", html)

    def test_chat_detail_page_does_not_render_toasts(self):
        html = self.client.get(f"/history/{self.PHONE}/").content.decode()
        self.assertIn("Hello from the test suite", html)
        self.assertNotIn('<div class="flash-stack"', html)

    def test_language_flash_shows_once_and_is_consumed(self):
        first = self.client.post(
            "/language/", {"language": "ar", "next": "/history/"}, follow=True
        ).content.decode()
        self.assertEqual(first.count('class="flash flash-success"'), 1)
        self.assertIn("تم تعيين لغة الواجهة", first)

        second = self.client.get("/history/").content.decode()
        self.assertNotIn('<div class="flash-stack"', second)
        self.assertNotIn("تم تعيين لغة الواجهة", second)
        self.assertNotIn("InboundMessage object", second)

    def test_toast_stack_dedupes_and_caps(self):
        request = RequestFactory().get("/")
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        for _ in range(2):
            messages.success(request, "Interface language set to English.")
        for index in range(5):
            messages.info(request, f"Extra {index}")

        payload = flash_messages(request)["flash_messages"]
        texts = [str(message) for message in payload]
        self.assertEqual(len(payload), MAX_FLASH_MESSAGES)
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(texts.count("Interface language set to English."), 1)

