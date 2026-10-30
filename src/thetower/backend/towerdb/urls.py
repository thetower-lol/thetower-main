from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

from thetower.backend.sus.api_views import BanPlayerAPI

# from .views import last_full_results, results_per_tourney, results_per_user

admin.site.site_url = "/admin"


base_patterns = [
    path("_nested_admin/", include("nested_admin.urls")),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="admin/", permanent=True)),
    path("api/ban_player/", BanPlayerAPI.as_view(), name="ban_player_api"),
]


urlpatterns = base_patterns

# json_patterns = [
#     path("<str:league>/full_results/", last_full_results),
#     path("<str:league>/results/<str:tourney_date>/", results_per_tourney),
#     path("player_id/<str:player_id>/", results_per_user),
# ]

# urlpatterns += json_patterns
