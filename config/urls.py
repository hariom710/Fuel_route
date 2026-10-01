"""URL configuration: a single endpoint, POST /api/route/."""

from django.urls import include, path

urlpatterns = [
    path("api/", include("route_api.urls")),
]
