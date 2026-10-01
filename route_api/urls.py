from django.urls import path

from route_api.views import RouteView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
]
