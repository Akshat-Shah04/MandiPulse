from django.urls import path

from . import views

# No trailing slashes: the Next.js dev proxy strips them before forwarding.
urlpatterns = [
    path("commodities", views.commodities),
    path("dates", views.dates),
    path("map", views.price_map),
    path("series", views.series),
    path("status", views.status),
    path("forecast", views.forecast),
    path("models", views.model_scores),
    path("groups", views.groups),
]
