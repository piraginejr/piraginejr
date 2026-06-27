from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CurrentUserSerializer


class HealthAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    def get(self, request):
        return Response({"status": "ok"})


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)
