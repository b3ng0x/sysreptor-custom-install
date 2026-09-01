from django.db import models
from sysreptor.utils.models import BaseModel


class BackupRun(BaseModel):
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    TRIGGER_MANUAL = 'manual'
    TRIGGER_DAILY = 'daily'
    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL, 'Manual'),
        (TRIGGER_DAILY, 'Daily automatic'),
    ]

    started = models.DateTimeField(auto_now_add=True)
    completed = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default=TRIGGER_MANUAL)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    # {"discord": {"ok": true, "detail": "..."}, "github": {...}, "gdrive": {...}}
    destination_results = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-started']
