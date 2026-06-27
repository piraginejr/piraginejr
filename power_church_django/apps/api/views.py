from __future__ import annotations

from django.db.models import Q
from power_church_core.normalization import normalize_query
from power_church_django.apps.people.models import PersonSnapshot
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CurrentUserSerializer, PersonDetailSerializer, PersonListSerializer


class HealthAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    def get(self, request):
        return Response({"status": "ok"})


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)


class PeoplePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PeopleListAPIView(generics.ListAPIView):
    serializer_class = PersonListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = PeoplePagination

    # TODO: aplicar permissao por perfil (admin, secretaria, pastoral, financeiro, lider_celula, membro).
    def get_queryset(self):
        queryset = PersonSnapshot.objects.all().order_by("normalized_name", "legacy_id")

        search = normalize_query(self.request.GET.get("search"))
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(normalized_name__icontains=search)
                | Q(social_name__icontains=search)
                | Q(primary_email__icontains=search)
                | Q(normalized_email__icontains=search)
                | Q(primary_phone__icontains=search)
                | Q(primary_whatsapp__icontains=search)
                | Q(cpf__icontains=search)
            )

        status = normalize_query(self.request.GET.get("status"))
        if status:
            status_options = {status, status.lower(), status.replace(" ", "_"), status.replace(" ", "_").lower()}
            status_filter = Q()
            for option in status_options:
                status_filter |= Q(status__iexact=option)
            queryset = queryset.filter(status_filter)

        is_active = self._parse_bool(self.request.GET.get("is_active"))
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        is_archived = self._parse_bool(self.request.GET.get("is_archived"))
        if is_archived is not None:
            queryset = queryset.filter(is_archived=is_archived)

        return queryset

    @staticmethod
    def _parse_bool(value: object) -> bool | None:
        if value is None:
            return None
        normalized = normalize_query(value).lower()
        if normalized in {"1", "true", "yes", "sim", "on"}:
            return True
        if normalized in {"0", "false", "no", "nao", "não", "off"}:
            return False
        return None


class PeopleDetailAPIView(generics.RetrieveAPIView):
    serializer_class = PersonDetailSerializer
    permission_classes = [IsAuthenticated]
    queryset = PersonSnapshot.objects.all()

    # TODO: aplicar permissao por perfil (admin, secretaria, pastoral, financeiro, lider_celula, membro).
