from django.urls import path

from .views import CustomCssView

"""
Accessible at /api/plugins/<plugin_id>/api/...
"""
urlpatterns = [
    path('config/', CustomCssView.as_view(), name='customcss-config'),
]
