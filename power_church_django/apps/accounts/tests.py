from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase

from power_church_django.services.access_control import ensure_access_control


class ModulePermissionEnforcementTests(TestCase):
    def setUp(self) -> None:
        ensure_access_control()
        patchers = [
            patch("power_church_django.apps.imports.views.dashboard_summary_postgres", return_value={}),
            patch("power_church_django.apps.imports.views.list_import_lots_postgres", return_value=[]),
            patch("power_church_django.apps.people.views.list_people", return_value=[]),
            patch("power_church_django.apps.contributions.views.list_contributions_postgres", return_value=[]),
            patch("power_church_django.apps.contributions.views._envelope_hub", return_value={}),
            patch("power_church_django.apps.contributions.views.list_contributors_postgres", return_value=[]),
            patch(
                "power_church_django.apps.reports.views.contribution_report_postgres",
                return_value={"items": [], "summary": {}, "competencias": [], "truncated": False},
            ),
            patch("power_church_django.apps.audit.views.operational_audit_postgres", return_value={}),
            patch("power_church_django.apps.audit.views.technical_audit_postgres", return_value={}),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.routes = {
            "dashboard": "/",
            "people": "/people/",
            "contributors": "/contributors/",
            "contributions": "/contributions/",
            "imports": "/imports/",
            "reports": "/reports/",
            "audit": "/audit/",
            "accounts": "/accounts/",
        }

    def test_anonymous_user_is_redirected_to_login(self) -> None:
        for url in self.routes.values():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.headers.get("Location", ""))

    def test_authenticated_user_without_group_is_forbidden_on_sensitive_modules(self) -> None:
        user = User.objects.create_user(username="sem_grupo", password="teste12345", is_active=True)
        self.client.force_login(user)
        for url in self.routes.values():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_superuser_keeps_full_access(self) -> None:
        user = User.objects.create_superuser(
            username="supervisor",
            email="supervisor@example.com",
            password="teste12345",
        )
        self.client.force_login(user)
        for url in self.routes.values():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_consulta_group_cannot_access_restricted_modules(self) -> None:
        user = User.objects.create_user(username="consulta", password="teste12345", is_active=True)
        user.groups.add(Group.objects.get(name="Consulta"))
        self.client.force_login(user)

        allowed = ["/", "/people/", "/contributors/", "/contributions/", "/reports/"]
        blocked = ["/imports/", "/audit/", "/accounts/"]

        for url in allowed:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

        for url in blocked:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_write_views_require_manage_permissions(self) -> None:
        user = User.objects.create_user(username="somente_consulta", password="teste12345", is_active=True)
        user.groups.add(Group.objects.get(name="Consulta"))
        self.client.force_login(user)

        protected_urls = [
            "/people/new/",
            "/contributions/new/",
            "/contributions/envelopes/new/",
            "/imports/",
        ]
        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
