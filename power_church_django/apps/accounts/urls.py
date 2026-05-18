from __future__ import annotations

from django.urls import path
from django.contrib.auth import views as auth_views

from . import views


app_name = "accounts"

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", auth_views.LoginView.as_view(template_name="power_church_django/accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/accounts/login/"), name="logout"),
]
