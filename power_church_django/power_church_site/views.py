from __future__ import annotations

from django.http import FileResponse, Http404, HttpRequest, HttpResponse

from power_church_django.services.branding import brand_logo_available, brand_logo_path


def brand_logo(request: HttpRequest) -> HttpResponse:
    if not brand_logo_available():
        raise Http404("Logo nao encontrada")
    return FileResponse(brand_logo_path().open("rb"), content_type="image/jpeg")

