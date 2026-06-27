from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.utils import timezone


class AppLoginRequiredMiddleware:
    """Require authentication for app routes outside the explicit public allowlist."""

    EXEMPT_PREFIXES = (
        "/accounts/login/",
        "/accounts/relogin/",
        "/accounts/logout/",
        "/api/",
        "/branding/logo",
        "/admin/",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or self._is_exempt(request.path_info):
            return self.get_response(request)
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)


class SessionSecurityMiddleware:
    """Apply server-side session timeout and prevent sensitive page caching."""

    LAST_ACTIVITY_KEY = "power_church_last_activity_at"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            timeout_seconds = int(getattr(settings, "POWER_CHURCH_SESSION_IDLE_SECONDS", 0) or 0)
            if timeout_seconds > 0:
                last_seen_raw = request.session.get(self.LAST_ACTIVITY_KEY)
                if last_seen_raw:
                    try:
                        last_seen = timezone.datetime.fromisoformat(str(last_seen_raw))
                        if timezone.is_naive(last_seen):
                            last_seen = timezone.make_aware(last_seen, timezone.get_current_timezone())
                    except (TypeError, ValueError):
                        last_seen = None
                    if last_seen and timezone.now() - last_seen > timedelta(seconds=timeout_seconds):
                        logout(request)
                        request.session.flush()
                        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
                request.session[self.LAST_ACTIVITY_KEY] = timezone.now().isoformat()

        response = self.get_response(request)

        if request.user.is_authenticated:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response
