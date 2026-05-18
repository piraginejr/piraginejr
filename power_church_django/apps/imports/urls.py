from __future__ import annotations

from django.urls import path

from . import views


app_name = "imports"

urlpatterns = [
    path("", views.index, name="index"),
    path("rules/", views.cent_rules, name="cent_rules"),
    path("<str:kind>/<int:lot_id>/", views.lot_detail, name="lot_detail"),
    path("<str:kind>/movement/<int:movement_id>/", views.movement_detail, name="movement_detail"),
]
