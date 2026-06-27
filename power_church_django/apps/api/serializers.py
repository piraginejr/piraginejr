from __future__ import annotations

from django.contrib.auth.models import User
from rest_framework import serializers


class CurrentUserSerializer(serializers.ModelSerializer):
    groups = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "is_staff",
            "is_superuser",
            "groups",
        ]

    def get_groups(self, obj: User) -> list[str]:
        return list(obj.groups.order_by("name").values_list("name", flat=True))

    def get_full_name(self, obj: User) -> str:
        return obj.get_full_name().strip()
