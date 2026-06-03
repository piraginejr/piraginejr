from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from power_church_django.apps.contributions.views import (
    contributor_detail,
    contributors,
    receipt_detail,
    receipt_new,
    receipt_pdf_view,
    receipt_queue_monitor,
    receipts,
)
from power_church_django.apps.imports.views import dashboard
from power_church_site.views import brand_logo


urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("branding/logo", brand_logo, name="brand_logo"),
    path("admin/", admin.site.urls),
    path("accounts/", include("power_church_django.apps.accounts.urls")),
    path("people/", include("power_church_django.apps.people.urls")),
    path("contributors/", contributors, name="contributors"),
    path("contributors/<int:contributor_id>/", contributor_detail, name="contributor_detail"),
    path("contributions/", include("power_church_django.apps.contributions.urls")),
    path("receipts/", receipts, name="receipts"),
    path("receipts/new/", receipt_new, name="receipt_new"),
    path("receipts/queue/", receipt_queue_monitor, name="receipt_queue_monitor"),
    path("receipts/<int:receipt_id>/pdf/", receipt_pdf_view, name="receipt_pdf"),
    path("receipts/<int:receipt_id>/", receipt_detail, name="receipt_detail"),
    path("imports/", include("power_church_django.apps.imports.urls")),
    path("audit/", include("power_church_django.apps.audit.urls")),
    path("reports/", include("power_church_django.apps.reports.urls")),
]
