from __future__ import annotations

from django.urls import path

from . import views


app_name = "people"

urlpatterns = [
    path("", views.index, name="index"),
    path("export/", views.export, name="export"),
    path("trash/", views.trash, name="trash"),
    path("trash/<int:trash_id>/purge/", views.purge_trash, name="purge_trash"),
    path("validate-field/", views.validate_field, name="validate_field"),
    path("search/", views.search, name="search"),
    path("new/", views.new, name="new"),
    path("families/", views.families, name="families"),
    path("photo/<int:person_id>/", views.photo, name="photo"),
    path("imports/", views.imports, name="imports"),
    path("imports/<int:lot_id>/", views.import_lot, name="import_lot"),
    path("<int:person_id>/delete/", views.delete, name="delete"),
    path("<int:person_id>/edit/", views.edit, name="edit"),
    path("<int:person_id>/", views.detail, name="detail"),
]
