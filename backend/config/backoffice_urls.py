from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "File storage back office"
admin.site.site_title = "Back office"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/")),
]
