"""Request validation for the route endpoint."""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, allow_blank=False)
    finish = serializers.CharField(max_length=120, allow_blank=False)

    def validate_start(self, value: str) -> str:
        return value.strip()

    def validate_finish(self, value: str) -> str:
        return value.strip()
