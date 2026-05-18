from __future__ import annotations

from django.urls import path

from . import views


app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("destinations/", views.destinations, name="destinations"),
    path("contributions-period.pdf", views.contribution_period_pdf_view, name="contribution_period_pdf"),
    path("contributions-destinations.pdf", views.contribution_destinations_pdf_view, name="contribution_destinations_pdf"),
]
