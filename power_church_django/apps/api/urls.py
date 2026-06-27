from __future__ import annotations

from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import HealthAPIView, MeAPIView


app_name = "api"

urlpatterns = [
    path("health/", HealthAPIView.as_view(), name="health"),
    path("me/", MeAPIView.as_view(), name="me"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
