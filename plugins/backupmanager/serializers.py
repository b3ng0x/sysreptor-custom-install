from rest_framework import serializers

from .models import BackupRun


class BackupRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupRun
        fields = ['id', 'started', 'completed', 'status', 'trigger', 'size_bytes', 'error_message', 'destination_results']
        read_only_fields = fields
