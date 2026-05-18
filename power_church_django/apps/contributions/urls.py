from __future__ import annotations

from django.urls import path

from . import views


app_name = "contributions"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.new, name="new"),
    path("manual/", views.manual_batch, name="manual_batch"),
    path("envelopes/", views.envelopes, name="envelopes"),
    path("envelopes/new/", views.envelope_new, name="envelope_new"),
    path("envelopes/lots/new/", views.envelope_lot_new, name="envelope_lot_new"),
    path("envelopes/lots/<int:lot_id>/", views.envelope_lot_detail, name="envelope_lot_detail"),
    path("envelopes/lots/<int:lot_id>/next/", views.envelope_lot_next, name="envelope_lot_next"),
    path("envelopes/<int:envelope_id>/launch/", views.envelope_launch, name="envelope_launch"),
    path("envelopes/<int:envelope_id>/edit/", views.envelope_edit, name="envelope_edit"),
    path("envelopes/<int:envelope_id>/ignore/", views.envelope_ignore, name="envelope_ignore"),
    path("envelopes/<int:envelope_id>/", views.envelope_detail, name="envelope_detail"),
    path("envelopes/<int:envelope_id>/image/", views.envelope_image, name="envelope_image"),
    path("statements/<int:person_id>/", views.person_statement, name="person_statement"),
    path("<int:contribution_id>/split/", views.split, name="split"),
    path("<int:contribution_id>/", views.detail, name="detail"),
]
