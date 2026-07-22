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


class ReceiptOperationalButtonsTests(TestCase):
    def setUp(self) -> None:
        ensure_access_control()
        patchers = [
            patch(
                "power_church_django.apps.contributions.views.list_receipts_postgres",
                return_value={
                    "items": [],
                    "summary": {"quantidade": 0, "total_fmt": "R$ 0,00", "pessoas": 0, "ultima_data": ""},
                },
            ),
            patch(
                "power_church_django.apps.contributions.views._receipt_queue_snapshot",
                return_value={
                    "campaign_key": "",
                    "status": "",
                    "counts": {"pendente": 0, "enviado": 0, "falhou": 0, "cancelado": 0},
                    "total": 0,
                    "progress_percent": 0,
                    "latest_attempt": "",
                    "latest_sent": "",
                    "items": [],
                    "campaigns": [],
                },
            ),
            patch(
                "power_church_django.apps.contributions.views.email_runtime_snapshot",
                return_value={"provider": "microsoft_graph"},
            ),
            patch("power_church_django.apps.contributions.views.search_receipt_people_postgres", return_value=[]),
            patch(
                "power_church_django.apps.audit.views.list_system_email_events",
                return_value={
                    "total": 0,
                    "page": 1,
                    "total_pages": 1,
                    "shown": 0,
                    "kinds": [],
                    "statuses": [],
                    "items": [],
                    "smart_summary": [],
                    "page_size": 120,
                    "has_previous": False,
                    "has_next": False,
                    "previous_page": 1,
                    "next_page": 1,
                },
            ),
            patch("power_church_django.apps.audit.views.search_receipt_people_postgres", return_value=[]),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_receipts_page_shows_operational_buttons_for_operator(self) -> None:
        user = User.objects.create_user(username="operador", password="teste12345", is_active=True)
        user.groups.add(Group.objects.get(name="Operador de Recebimentos"))
        self.client.force_login(user)

        response = self.client.get("/receipts/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reenfileirar recibos pendentes")
        self.assertContains(response, "Processar fila de recibos")

    def test_audit_email_view_hides_receipt_buttons_without_manage_permission(self) -> None:
        user = User.objects.create_user(username="auditor", password="teste12345", is_active=True)
        user.groups.add(Group.objects.get(name="Auditor"))
        self.client.force_login(user)

        response = self.client.get("/audit/?modo=emails")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Reenfileirar recibos pendentes")
        self.assertNotContains(response, "Processar fila de recibos")

    def test_audit_email_view_shows_receipt_buttons_for_superuser(self) -> None:
        user = User.objects.create_superuser(
            username="admin_receipts",
            email="admin@example.com",
            password="teste12345",
        )
        self.client.force_login(user)

        response = self.client.get("/audit/?modo=emails")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reenfileirar recibos pendentes")
        self.assertContains(response, "Processar fila de recibos")

    def test_queue_monitor_backfill_action_redirects_to_receipts(self) -> None:
        user = User.objects.create_user(username="operador_backfill", password="teste12345", is_active=True)
        user.groups.add(Group.objects.get(name="Operador de Recebimentos"))
        self.client.force_login(user)

        with patch(
            "power_church_django.apps.contributions.views.backfill_native_event_receipts",
            return_value={"created": 2, "queued": 2, "without_email": 1, "failed": 0},
        ) as mocked:
            response = self.client.post(
                "/receipts/queue/",
                {
                    "action": "backfill_automatic_pending",
                    "return_to": "receipts",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/receipts/")
        mocked.assert_called_once()

    def test_queue_monitor_drain_action_redirects_back_to_audit(self) -> None:
        user = User.objects.create_superuser(
            username="admin_drain",
            email="admin_drain@example.com",
            password="teste12345",
        )
        self.client.force_login(user)

        with patch(
            "power_church_django.apps.contributions.views.drain_receipt_dispatch_queue",
            return_value={"sent": 3, "failed": 1, "selected": 4},
        ) as mocked:
            response = self.client.post(
                "/receipts/queue/",
                {
                    "action": "drain_pending_queue",
                    "return_to": "audit",
                    "return_email_kind": "",
                    "return_email_status": "",
                    "return_q": "",
                    "return_selected_person_id": "0",
                    "return_person_lookup": "",
                    "return_page": "1",
                    "return_page_size": "120",
                    "batch_limit": "40",
                    "sleep_seconds": "3",
                    "pause_every": "40",
                    "pause_seconds": "60",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/audit/?modo=emails")
        mocked.assert_called_once()


class AccountManagementActionsTests(TestCase):
    def setUp(self) -> None:
        ensure_access_control()
        self.admin = User.objects.create_superuser(
            username="admin_accounts",
            email="admin_accounts@example.com",
            password="teste12345",
        )
        self.client.force_login(self.admin)

    def test_accounts_page_shows_password_and_delete_actions(self) -> None:
        target = User.objects.create_user(username="colaborador", password="teste12345", is_active=True)

        response = self.client.get("/accounts/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trocar senha")
        self.assertContains(response, "Excluir usuario")
        self.assertContains(response, "Salvar credenciais")
        self.assertContains(response, "Criar novo tipo de credencial")
        self.assertContains(response, f'name="user_id" value="{target.id}"', html=False)

    def test_superuser_can_reset_user_password(self) -> None:
        target = User.objects.create_user(username="trocar_senha", password="senha_antiga_123", is_active=True)

        response = self.client.post(
            "/accounts/",
            {
                "action": "reset_password",
                "user_id": str(target.id),
                "new_password": "nova_senha_456",
                "new_password_confirm": "nova_senha_456",
            },
        )

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(target.check_password("nova_senha_456"))

    def test_superuser_can_delete_departed_user(self) -> None:
        target = User.objects.create_user(username="ex_colaborador", password="senha12345", is_active=True)

        response = self.client.post(
            "/accounts/",
            {
                "action": "delete_user",
                "user_id": str(target.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=target.pk).exists())

    def test_cannot_delete_own_logged_user(self) -> None:
        response = self.client.post(
            "/accounts/",
            {
                "action": "delete_user",
                "user_id": str(self.admin.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_group_admin_can_manage_users_without_superuser_flag(self) -> None:
        self.client.logout()
        group_admin = User.objects.create_user(
            username="admin_grupo",
            password="teste12345",
            is_active=True,
        )
        group_admin.groups.add(Group.objects.get(name="Administrador do Sistema"))
        self.client.force_login(group_admin)
        target = User.objects.create_user(username="usuario_alvo", password="senha_antiga_123", is_active=True)

        response = self.client.get("/accounts/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criar usuario")
        self.assertContains(response, "Trocar senha")
        self.assertContains(response, "Excluir usuario")
        self.assertNotContains(response, "entre com um superusuario")

        post_response = self.client.post(
            "/accounts/",
            {
                "action": "reset_password",
                "user_id": str(target.id),
                "new_password": "senha_nova_456",
                "new_password_confirm": "senha_nova_456",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(target.check_password("senha_nova_456"))

    def test_superuser_can_update_user_credentials(self) -> None:
        target = User.objects.create_user(username="ajustar_credenciais", password="senha_antiga_123", is_active=True)

        response = self.client.post(
            "/accounts/",
            {
                "action": "update_user_credentials",
                "user_id": str(target.id),
                "group": "Operador de Recebimentos",
                "is_active": "0",
                "is_superuser": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(target.is_superuser)
        self.assertTrue(target.groups.filter(name="Operador de Recebimentos").exists())

    def test_group_admin_cannot_escalate_superuser_credential(self) -> None:
        self.client.logout()
        group_admin = User.objects.create_user(
            username="gestor_contas",
            password="teste12345",
            is_active=True,
        )
        group_admin.groups.add(Group.objects.get(name="Administrador do Sistema"))
        self.client.force_login(group_admin)
        target = User.objects.create_user(username="sem_escalacao", password="senha_antiga_123", is_active=True)

        response = self.client.post(
            "/accounts/",
            {
                "action": "update_user_credentials",
                "user_id": str(target.id),
                "group": "Consulta",
                "is_active": "1",
                "is_superuser": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertFalse(target.is_superuser)
        self.assertTrue(target.groups.filter(name="Consulta").exists())

    def test_superuser_can_create_custom_credential_type(self) -> None:
        response = self.client.post(
            "/accounts/",
            {
                "action": "create_credential_type",
                "credential_name": "Operador Financeiro Senior",
                "permission_codes": ["view_dashboard", "view_contributions", "manage_contributions"],
            },
        )

        self.assertEqual(response.status_code, 302)
        group = Group.objects.get(name="Operador Financeiro Senior")
        self.assertEqual(
            list(group.permissions.order_by("codename").values_list("codename", flat=True)),
            ["manage_contributions", "view_contributions", "view_dashboard"],
        )
