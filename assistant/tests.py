"""Regression tests for the validation layer, the price sheet and the flow.

Run with:  python manage.py test assistant -v 2
"""
import json
import os
from io import StringIO
from unittest.mock import patch

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.utils import translation
from django.utils.translation import ngettext
from pydantic import ValidationError

from assistant.auth import is_allowed_user
from assistant.context_processors import MAX_FLASH_MESSAGES, flash_messages
from assistant.models import (
    ConversationState,
    InboundMessage,
    IntegrationSettings,
    LeadRecord,
)
from assistant.services import agent
from assistant.services import extractor, odoo_client, runtime_settings, twilio_client
from assistant.services.extractor import ExtractedRequest
from assistant.services.price_sheet import build_quote, describe_row
from assistant.services.schemas import (
    clean_email, clean_person_name, normalize_format, normalize_platform,
    normalize_tier, QuoteRequest,
)

# The single allow-listed seat (see assistant/auth.py and settings).
DESK_USERNAME = "waleed"
DESK_PASSWORD = "desk-test-password-123"


class DeskClientMixin:
    """Signs the test client in as the allow-listed desk account."""

    @classmethod
    def setUpTestData(cls):
        cls.desk_user = get_user_model().objects.create_user(
            username=DESK_USERNAME,
            password=DESK_PASSWORD,
            is_staff=True,
            is_superuser=True,
        )

    def setUp(self):
        self.client.force_login(self.desk_user)


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
        self._say("skip")                                  # budget skipped

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

    def test_discount_is_not_offered_when_the_budget_already_fits(self):
        """A customer who can pay the full price is never nudged into a discount."""
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        reply = self._say("budget 60000")            # 60,000 ≥ 45,000 → full price
        self.assertEqual(self._stage(), "contact")
        self.assertNotIn("discount", reply.lower())
        state = ConversationState.objects.get(phone=self.PHONE).partial_request
        self.assertEqual(state["requested_discount_pct"], 0)

    def test_discount_is_not_offered_when_the_budget_is_skipped(self):
        self._seed_to_contact()                      # budget skipped → contact
        self.assertEqual(self._stage(), "contact")

    def test_discount_is_offered_when_the_budget_is_short(self):
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        reply = self._say("budget 40000")            # 45,000 - 15% = 38,250 → closable
        self.assertEqual(self._stage(), "discount")
        self.assertIn("40,000", reply)
        self.assertIn("maximum discount available", reply.lower())

    def test_no_discount_question_when_the_cap_cannot_reach_the_budget(self):
        """Budget far too low → the discount would not help, so don't ask."""
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        reply = self._say("budget 20000")            # best possible is 38,250
        self.assertEqual(self._stage(), "contact")
        self.assertNotIn("discount", reply.lower())

    def test_customer_can_ask_for_a_discount_at_any_point(self):
        self._seed_to_contact()
        reply = self._say("can I get a discount?")
        self.assertEqual(self._stage(), "discount")
        self.assertIn("percentage", reply.lower())

        self._say("10")
        state = ConversationState.objects.get(phone=self.PHONE).partial_request
        self.assertEqual(state["requested_discount_pct"], 10)
        self.assertEqual(state["_stage"], "contact")

    def test_bare_yes_at_discount_asks_for_percentage(self):
        self._say("haikal.ai", brand="haikal.ai")
        self._say("youtube", platform="youtube")
        self._say("integration", format="integration")
        self._say("budget 40000")                     # short budget → discount step
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


class LanguageSwitchTests(DeskClientMixin, TestCase):
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


class ConciergeChatbotTests(DeskClientMixin, TestCase):
    """The old "simulator" is now the POWR Concierge chatbot."""

    def test_chatbot_page_branding(self):
        response = self.client.get("/chat/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "POWR Concierge")
        self.assertContains(response, "Logged for audit")
        # The right-hand "desk context" column was removed so the workspace
        # reads as a single full-width enterprise chat surface.
        self.assertNotContains(response, "What this desk handles")
        self.assertNotContains(response, "Session")
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

    def test_customer_can_ask_for_a_discount_at_the_summary(self):
        """Typed instead of *2*: a discount request is honoured, not ignored."""
        self._walk_to_confirm_on_x()

        reply = self._say("can I get a discount?")

        self.assertEqual(self._state()["_stage"], "discount")
        self.assertIn("percentage", reply.lower())
        self.assertEqual(LeadRecord.objects.count(), 0)

        self._say("10")
        self.assertEqual(self._state()["requested_discount_pct"], 10)
        self.assertEqual(self._state()["_stage"], "confirm")

    def test_no_discount_question_on_a_full_price_walk(self):
        """The walk above never mentions a discount — the price already fits."""
        self._walk_to_confirm_on_x()
        self.assertNotIn("_discount_requested", self._state())
        self.assertEqual(self._state()["requested_discount_pct"], 0)

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


class FlashMessageHygieneTests(DeskClientMixin, TestCase):
    """The toast stack must never dump app data, nor pile up.

    Regression: the History page used to expose its queryset as ``messages``,
    which shadowed the messages framework variable — every inbound message was
    rendered as a toast and the real flash messages were never consumed.
    """

    PHONE = "whatsapp:+966500000999"

    def setUp(self):
        super().setUp()
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



class DeskAccessTests(TestCase):
    """Superusers and the allow-listed seat guard the desk (and its OpenRouter spend)."""

    PASSWORD = "desk-test-password-123"

    def _create(self, username, **extra):
        return get_user_model().objects.create_user(
            username=username, password=self.PASSWORD, **extra
        )

    def test_login_page_is_reachable_without_a_session(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أهلاً وسهلاً يا وليد")     # Arabic greeting
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    def test_operator_pages_redirect_anonymous_users_to_the_login_page(self):
        for url in ("/", "/chat/", "/history/", "/leads/", "/settings/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/login/"))

    def test_chat_api_refuses_anonymous_callers_and_saves_nothing(self):
        response = self.client.post(
            "/api/chat/",
            data=json.dumps({"text": "hello"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Authentication required")
        self.assertEqual(InboundMessage.objects.count(), 0)

    def test_allow_listed_account_signs_in_and_is_greeted_in_arabic(self):
        self._create(DESK_USERNAME)
        response = self.client.post(
            "/login/",
            {"username": DESK_USERNAME, "password": self.PASSWORD},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أهلاً وسهلاً يا وليد")
        self.assertContains(response, "Dashboard")
        self.assertIn("_auth_user_id", self.client.session)

    def test_other_accounts_cannot_sign_in_even_with_a_valid_password(self):
        # A non-superuser staff account is not allow-listed, so the desk and
        # the admin stay closed to it.
        self._create("someone-else", is_staff=True)
        response = self.client.post(
            "/login/", {"username": "someone-else", "password": self.PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "do not match the desk account")

    def test_superuser_outside_the_allow_list_signs_in_to_the_desk_and_admin(self):
        # ``createsuperuser`` account: same credentials for both surfaces.
        superuser = self._create("faheed", is_staff=True, is_superuser=True)

        desk = self.client.post(
            "/login/", {"username": "faheed", "password": self.PASSWORD}, follow=True
        )
        self.assertEqual(desk.status_code, 200)
        self.assertContains(desk, "Dashboard")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(int(self.client.session["_auth_user_id"]), superuser.pk)

        self.client.logout()
        admin = self.client.post(
            "/admin/login/",
            {"username": "faheed", "password": self.PASSWORD, "next": "/admin/"},
            follow=True,
        )
        self.assertEqual(admin.status_code, 200)
        # The project rebrands the admin index (<title>Desk data</title>), so
        # assert on the rendered dashboard rather than Django's default text.
        self.assertContains(admin, "Desk data")
        self.assertIn(("/admin/", 302), admin.redirect_chain)
        self.assertIn("_auth_user_id", self.client.session)

    def test_backend_only_authenticates_the_allow_listed_account(self):
        self._create(DESK_USERNAME)
        self._create("someone-else")
        self.assertIsNone(authenticate(username="someone-else", password=self.PASSWORD))
        self.assertIsNotNone(authenticate(username=DESK_USERNAME, password=self.PASSWORD))

    def test_backend_authenticates_a_superuser_outside_the_allow_list(self):
        superuser = self._create("faheed", is_staff=True, is_superuser=True)
        self.assertIsNotNone(authenticate(username="faheed", password=self.PASSWORD))
        self.assertTrue(is_allowed_user(superuser))
        self.assertFalse(is_allowed_user(self._create("someone-else")))
        self.assertEqual(self.client.get("/admin/").status_code, 302)  # still closed

    def test_stale_session_for_another_account_is_dropped(self):
        other = self._create("someone-else")
        self.client.force_login(other)
        response = self.client.get("/chat/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))
        # The stale credential is gone: the next request still needs a sign-in.
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_sign_out_is_post_only_and_ends_the_session(self):
        self.client.force_login(self._create(DESK_USERNAME))
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cross_origin_chat_calls_are_rejected(self):
        self.client.force_login(self._create(DESK_USERNAME))
        response = self.client.post(
            "/api/chat/",
            data=json.dumps({"text": "hello"}),
            content_type="application/json",
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(InboundMessage.objects.count(), 0)

    def test_allow_listed_account_also_opens_the_django_admin(self):
        self.client.force_login(
            self._create(DESK_USERNAME, is_staff=True, is_superuser=True)
        )
        self.assertEqual(self.client.get("/admin/").status_code, 200)


class DeskAccountCommandTests(TestCase):
    """`manage.py create_waleed` provisions the single testing seat."""

    def test_command_creates_the_allow_listed_superuser(self):
        call_command("create_waleed", password="S3cret-desk-pass", stdout=StringIO())
        user = get_user_model().objects.get(username=DESK_USERNAME)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("S3cret-desk-pass"))

    def test_command_refuses_a_username_outside_the_allow_list(self):
        with self.assertRaises(CommandError):
            call_command("create_waleed", username="someone-else", stdout=StringIO())



class IntegrationSettingsResolutionTests(TestCase):
    """Saved settings win over `.env`; blank fields keep using the environment."""

    def test_environment_is_used_when_nothing_is_saved(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key-1234"}):
            self.assertEqual(runtime_settings.openrouter_api_key(), "env-key-1234")
            self.assertEqual(
                runtime_settings.source_of("openrouter_api_key"),
                runtime_settings.SOURCE_ENVIRONMENT,
            )

    def test_saved_values_override_the_environment(self):
        IntegrationSettings.objects.create(
            openrouter_api_key="db-key-9999",
            odoo_url="https://db.example.com/",
        )
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key-1234"}):
            self.assertEqual(runtime_settings.openrouter_api_key(), "db-key-9999")
            self.assertEqual(runtime_settings.odoo_url(), "https://db.example.com")
            self.assertEqual(
                runtime_settings.source_of("openrouter_api_key"),
                runtime_settings.SOURCE_DATABASE,
            )

    def test_blank_fields_fall_back_to_the_environment(self):
        IntegrationSettings.objects.create(odoo_db="")
        with patch.dict(os.environ, {"ODOO_DB": "env-db"}):
            self.assertEqual(runtime_settings.odoo_config()["db"], "env-db")
            self.assertEqual(
                runtime_settings.source_of("odoo_db"),
                runtime_settings.SOURCE_ENVIRONMENT,
            )

    def test_missing_values_are_reported_as_missing(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "   "}):
            self.assertEqual(
                runtime_settings.source_of("openrouter_api_key"),
                runtime_settings.SOURCE_MISSING,
            )

    def test_dev_mode_tri_state_prefers_the_saved_choice(self):
        with patch.dict(os.environ, {"DEV_MODE": "true"}):
            self.assertTrue(runtime_settings.dev_mode())
            row = IntegrationSettings.objects.create(dev_mode=False)
            self.assertFalse(runtime_settings.dev_mode())
            row.dev_mode = None
            row.save()
            self.assertTrue(runtime_settings.dev_mode())

    def test_mask_only_reveals_the_tail(self):
        masked = runtime_settings.mask("sk-or-v1-1234567890abcdef")
        self.assertTrue(masked.endswith("cdef"))
        self.assertNotIn("1234567890", masked)
        self.assertEqual(runtime_settings.mask(""), "")

    def test_only_one_row_is_ever_stored(self):
        IntegrationSettings.objects.create(openrouter_api_key="first")
        IntegrationSettings.objects.create(openrouter_api_key="second")
        self.assertEqual(IntegrationSettings.objects.count(), 1)
        self.assertEqual(IntegrationSettings.load().openrouter_api_key, "second")




class IntegrationSettingsPageTests(DeskClientMixin, TestCase):
    """The /settings/ console Waleed uses while testing."""

    def test_page_requires_the_signed_in_seat(self):
        self.client.logout()
        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))

    def test_page_renders_every_integration_group(self):
        html = self.client.get("/settings/").content.decode()
        for probe in ("OpenRouter", "Odoo", "Twilio WhatsApp", "Save settings", ".env"):
            self.assertIn(probe, html)

    def test_saving_audits_the_user_and_normalises_values(self):
        response = self.client.post(
            "/settings/",
            {
                "openrouter_api_key": "sk-or-v1-test",
                "odoo_url": "https://test.odoo.com/",
                "odoo_db": "test-db",
                "odoo_username": "waleed@example.com",
                "odoo_api_key": "odoo-secret",
                "dev_mode": "True",
                "twilio_account_sid": "AC123",
                "twilio_auth_token": "twilio-secret",
                "twilio_whatsapp_from": "+14155238886",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Integration settings saved.")

        row = IntegrationSettings.load()
        self.assertEqual(row.openrouter_api_key, "sk-or-v1-test")
        self.assertEqual(row.odoo_url, "https://test.odoo.com")               # trailing / trimmed
        self.assertEqual(row.twilio_whatsapp_from, "whatsapp:+14155238886")   # prefix added
        self.assertTrue(row.dev_mode)
        self.assertEqual(row.updated_by, self.desk_user)

    def test_secrets_are_never_rendered_back_to_the_page(self):
        IntegrationSettings.objects.create(openrouter_api_key="sk-or-v1-super-secret-9876")
        html = self.client.get("/settings/").content.decode()
        self.assertNotIn("super-secret", html)
        self.assertIn("9876", html)                                   # masked tail only

    def test_blank_secret_keeps_the_value_already_stored(self):
        IntegrationSettings.objects.create(openrouter_api_key="keep-me-1234")
        self.client.post("/settings/", {"odoo_db": "just-the-db"})
        row = IntegrationSettings.load()
        self.assertEqual(row.openrouter_api_key, "keep-me-1234")
        self.assertEqual(row.odoo_db, "just-the-db")

    def test_clear_checkbox_falls_back_to_the_environment(self):
        IntegrationSettings.objects.create(openrouter_api_key="remove-me-1234")
        self.client.post("/settings/", {"clear_openrouter_api_key": "on"})
        self.assertEqual(IntegrationSettings.load().openrouter_api_key, "")


class IntegrationConsumerTests(TestCase):
    """The OpenRouter / Odoo / Twilio clients read the resolved values."""

    def test_odoo_client_uses_saved_credentials(self):
        IntegrationSettings.objects.create(
            odoo_url="https://saved.odoo.com/",
            odoo_db="saved-db",
            odoo_username="user@example.com",
            odoo_api_key="saved-key",
        )
        config = odoo_client._config()
        self.assertEqual(config["url"], "https://saved.odoo.com")
        self.assertEqual(config["db"], "saved-db")
        self.assertEqual(config["api_key"], "saved-key")

    def test_odoo_client_reports_missing_configuration(self):
        with patch.dict(os.environ, {
            "ODOO_URL": "", "ODOO_DB": "", "ODOO_USERNAME": "", "ODOO_API_KEY": "",
        }):
            with self.assertRaises(odoo_client.OdooError):
                odoo_client._config()

    def test_extractor_refuses_to_run_without_a_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                extractor._client()

    def test_extractor_rebuilds_its_client_when_the_key_changes(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "key-one"}):
            first = extractor._client()
        IntegrationSettings.objects.create(openrouter_api_key="key-two")
        second = extractor._client()
        self.assertIsNot(first, second)
        self.assertEqual(len(extractor._clients), 1)

    def test_twilio_client_uses_saved_credentials(self):
        IntegrationSettings.objects.create(
            twilio_account_sid="ACsaved",
            twilio_auth_token="tok-saved",
            twilio_whatsapp_from="whatsapp:+10000000000",
        )
        with patch("assistant.services.twilio_client.requests.post") as post:
            post.return_value.status_code = 201
            post.return_value.json.return_value = {"sid": "SM123"}
            sid = twilio_client.send_whatsapp("whatsapp:+966500000000", "hi")

        self.assertEqual(sid, "SM123")
        self.assertIn("ACsaved", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["auth"], ("ACsaved", "tok-saved"))
        self.assertEqual(post.call_args.kwargs["data"]["From"], "whatsapp:+10000000000")
