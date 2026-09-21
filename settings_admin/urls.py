from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "hh_autoapply"
admin.site.site_title = "hh_autoapply"
admin.site.index_title = "Вакансии и отклики"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
]
