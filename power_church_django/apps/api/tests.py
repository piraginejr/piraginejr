from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient


class ApiSmokeTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_health_is_public(self) -> None:
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_me_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/me/")
        self.assertEqual(response.status_code, 401)

    def test_me_returns_basic_authenticated_user_data(self) -> None:
        user = User.objects.create_user(
            username="api_user",
            email="api@example.com",
            password="SenhaSegura123",
            first_name="Api",
            last_name="User",
            is_staff=True,
        )
        self.client.force_authenticate(user=user)

        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": user.id,
                "username": "api_user",
                "email": "api@example.com",
                "first_name": "Api",
                "last_name": "User",
                "full_name": "Api User",
                "is_staff": True,
                "is_superuser": False,
                "groups": [],
            },
        )
