from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import MultipleObjectsReturned


class CaseInsensitiveUsernameBackend(ModelBackend):
    """Permite autenticar por username sem diferenciar maiusculas/minusculas."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        user_model = get_user_model()
        username_field = user_model.USERNAME_FIELD
        try:
            user = user_model._default_manager.get(**{f"{username_field}__iexact": username})
        except (user_model.DoesNotExist, MultipleObjectsReturned):
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
