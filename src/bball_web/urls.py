from django.urls import path

from . import views

urlpatterns = [
    path("players/", views.player_list),
    path("players/<int:player_id>/seasons/", views.player_seasons),
]
