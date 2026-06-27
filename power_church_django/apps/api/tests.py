from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from power_church_django.apps.people.models import PersonSnapshot
from rest_framework.test import APIClient


class ApiSmokeTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="api_user",
            email="api@example.com",
            password="SenhaSegura123",
            first_name="Api",
            last_name="User",
            is_staff=True,
        )

    def test_health_is_public(self) -> None:
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_me_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/me/")
        self.assertEqual(response.status_code, 401)

    def test_me_returns_basic_authenticated_user_data(self) -> None:
        self.client.force_authenticate(user=self.user)

        response = self.client.get("/api/v1/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": self.user.id,
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

    def test_people_list_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/people/")
        self.assertEqual(response.status_code, 401)

    def test_people_list_returns_paginated_results(self) -> None:
        self.client.force_authenticate(user=self.user)
        self._person(legacy_id=101, name="Ana Maria", normalized_name="ANA MARIA")
        self._person(legacy_id=102, name="Bruno Souza", normalized_name="BRUNO SOUZA")

        response = self.client.get("/api/v1/people/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 2)
        self.assertIsNone(payload["previous"])
        self.assertEqual(len(payload["results"]), 2)
        self.assertIn("results", payload)

    def test_people_list_does_not_include_cpf(self) -> None:
        self.client.force_authenticate(user=self.user)
        self._person(legacy_id=103, name="Carla Dias", normalized_name="CARLA DIAS", cpf="12345678901")

        response = self.client.get("/api/v1/people/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cpf", response.json()["results"][0])

    def test_people_detail_returns_cpf(self) -> None:
        self.client.force_authenticate(user=self.user)
        person = self._person(legacy_id=104, name="Daniel Lima", normalized_name="DANIEL LIMA", cpf="12345678901")

        response = self.client.get(f"/api/v1/people/{person.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cpf"], "12345678901")

    def test_people_search_by_name_works(self) -> None:
        self.client.force_authenticate(user=self.user)
        self._person(legacy_id=105, name="Joao Pedro", normalized_name="JOAO PEDRO")
        self._person(legacy_id=106, name="Maria Clara", normalized_name="MARIA CLARA")

        response = self.client.get("/api/v1/people/?search=joao")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["name"], "Joao Pedro")

    def test_people_filter_is_active_works(self) -> None:
        self.client.force_authenticate(user=self.user)
        self._person(legacy_id=107, name="Ativo", normalized_name="ATIVO", is_active=True)
        self._person(legacy_id=108, name="Inativo", normalized_name="INATIVO", is_active=False)

        response = self.client.get("/api/v1/people/?is_active=true")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["name"], "Ativo")

    def test_people_detail_missing_returns_404(self) -> None:
        self.client.force_authenticate(user=self.user)

        response = self.client.get("/api/v1/people/999999/")

        self.assertEqual(response.status_code, 404)

    def test_people_list_post_returns_405(self) -> None:
        self.client.force_authenticate(user=self.user)

        response = self.client.post("/api/v1/people/", {}, format="json")

        self.assertEqual(response.status_code, 405)

    def test_people_detail_write_methods_return_405(self) -> None:
        self.client.force_authenticate(user=self.user)
        person = self._person(legacy_id=109, name="Eva Costa", normalized_name="EVA COSTA")

        self.assertEqual(self.client.put(f"/api/v1/people/{person.id}/", {}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(f"/api/v1/people/{person.id}/", {}, format="json").status_code, 405)
        self.assertEqual(self.client.delete(f"/api/v1/people/{person.id}/").status_code, 405)

    def _person(self, **overrides) -> PersonSnapshot:
        base = {
            "legacy_id": 1,
            "organization_id": 1,
            "name": "Pessoa Teste",
            "normalized_name": "PESSOA TESTE",
            "social_name": "",
            "cpf": "",
            "primary_email": "",
            "normalized_email": "",
            "primary_phone": "",
            "primary_whatsapp": "",
            "status": "membro_ativo",
            "is_active": True,
            "is_archived": False,
            "notes": "",
        }
        base.update(overrides)
        return PersonSnapshot.objects.create(**base)
