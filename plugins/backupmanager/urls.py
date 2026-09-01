from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BackupRunViewSet

router = DefaultRouter()
router.register('runs', BackupRunViewSet, basename='backuprun')

"""
Accessible at /api/plugins/<plugin_id>/api/...
"""
urlpatterns = [
    path('', include(router.urls)),
]
