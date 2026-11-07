"""Access control for the POWR Concierge desk.

The desk is the only surface that spends OpenRouter tokens, so sign-in is kept
to a short list: **superusers** (``python manage.py createsuperuser`` or
``python manage.py create_waleed``) plus any username explicitly allow-listed
in ``POWR_ALLOWED_USERNAMES`` (``waleed`` by default). A superuser therefore
opens both ``/admin/`` and the desk with the same credentials. The rule is
enforced in two places:

* :class:`AllowListedUserBackend` — refuses ``authenticate()`` and session
  checks for every account that is neither a superuser nor allow-listed.
* :func:`desk_login_required` — guards the operator views, answers ``401`` JSON
  to API/fetch callers and tears down stale sessions that belong to an account
  which is no longer allowed in.
"""
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.urls import reverse

UNAUTHENTICATED_API_MESSAGE = "Authentication required"


def allowed_usernames():
    """Lower-cased usernames allowed on the desk (settings driven)."""
    configured = getattr(settings, "ALLOWED_LOGIN_USERNAMES", [])
    return {str(name).strip().lower() for name in configured if str(name).strip()}


def is_superuser(user):
    """True only for an authenticated, active account with ``is_superuser``."""
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_superuser", False)
    )


def is_allowed_user(user):
    """True for an active superuser or an allow-listed, active account.

    Superusers bypass the username allow-list on purpose: the account created
    with ``manage.py createsuperuser`` (or ``create_waleed``) must be able to
    sign in to both ``/admin/`` and the desk with the same credentials.
    """
    if is_superuser(user):
        return True
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and user.get_username().strip().lower() in allowed_usernames()
    )


class AllowListedUserBackend(ModelBackend):
    """``ModelBackend`` that only authenticates superusers and the allow-list."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if not username:
            return None
        # Deliberately no allow-list short-circuit here: a superuser made with
        # ``createsuperuser`` is valid whatever its username is, and
        # ``user_can_authenticate`` below is the single gate for everyone else.
        return super().authenticate(request, username=username, password=password, **kwargs)

    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and is_allowed_user(user)


def wants_json(request):
    """Best-effort detection of programmatic (fetch/XHR) callers."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.content_type or "")
    )


def desk_login_required(view):
    """``login_required`` for the desk: JSON-aware and allow-list aware."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_allowed_user(request.user):
            return view(request, *args, **kwargs)

        # A session belonging to an account that is no longer allow-listed
        # must never keep spending tokens — drop it on sight.
        if request.user.is_authenticated:
            auth_logout(request)

        if wants_json(request):
            return JsonResponse({"error": UNAUTHENTICATED_API_MESSAGE}, status=401)

        return redirect_to_login(request.get_full_path(), reverse("login"))

    return wrapper
