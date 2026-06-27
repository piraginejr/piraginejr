from __future__ import annotations

from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import HealthAPIView, MeAPIView, PeopleDetailAPIView, PeopleListAPIView


app_name = "api"

urlpatterns = [
    path("health/", HealthAPIView.as_view(), name="health"),
    path("me/", MeAPIView.as_view(), name="me"),
    path("people/", PeopleListAPIView.as_view(), name="people-list"),
    path("people/<int:pk>/", PeopleDetailAPIView.as_view(), name="people-detail"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
