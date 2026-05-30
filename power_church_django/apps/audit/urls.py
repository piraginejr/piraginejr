from __future__ import annotations

from django.urls import path

from . import views


app_name = "audit"

urlpatterns = [
    path("", views.index, name="index"),
    path("emails/resend/", views.email_resend, name="email_resend"),
]
