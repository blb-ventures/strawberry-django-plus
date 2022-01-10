import dataclasses
from typing import Any, Union, cast

from django.contrib.auth.models import AbstractUser, AnonymousUser
from strawberry.django.context import StrawberryDjangoContext
from strawberry.permission import BasePermission
from strawberry.types.info import Info
from typing_extensions import TypeAlias

from .utils import resolvers
from .utils.typing import TypeOrSequence

UserType: TypeAlias = Union[AbstractUser, AnonymousUser]


def has_perms(
    user: UserType,
    perms: TypeOrSequence[str],
    *,
    any_perm: bool = True,
    with_superuser: bool = True,
):
    if with_superuser and user.is_superuser:
        return True

    if isinstance(perms, str):
        perms = [perms]

    user_permissions = user.get_all_permissions()
    f = any if any_perm else all
    return f(perm in user_permissions for perm in perms)


@dataclasses.dataclass
class IsAuthenticated(BasePermission):
    """Checks if the user is authenticated (is not anonymous)."""

    message: str = dataclasses.field(default="User is not authenticated.")

    def has_permission(
        self,
        source: Any,
        info: Info[StrawberryDjangoContext, Any],
        **kwargs,
    ) -> bool:
        user = cast(UserType, info.context.request.user)
        return user.is_authenticated


class IsSuperuser(BasePermission):
    """Checks if the user is authenticated and is a superuser."""

    message: str = dataclasses.field(default="User is not a superuser.")

    def has_permission(
        self,
        source: Any,
        info: Info[StrawberryDjangoContext, Any],
        **kwargs,
    ) -> bool:
        user = cast(UserType, info.context.request.user)
        return user.is_superuser


class IsStaff(BasePermission):
    """Checks if the user is authenticated and is a superuser."""

    message: str = dataclasses.field(default="User is not a staff member.")

    def has_permission(
        self,
        source: Any,
        info: Info[StrawberryDjangoContext, Any],
        **kwargs,
    ) -> bool:
        user = cast(UserType, info.context.request.user)
        return user.is_staff


class HasPerms(BasePermission):
    """Checks if the user is authenticated and is a superuser."""

    perms: TypeOrSequence[str]
    any_perm: bool = dataclasses.field(default=True)
    with_superuser: bool = dataclasses.field(default=True)
    message: str = dataclasses.field(default="User does not have required permissions.")

    @resolvers.async_unsafe
    def has_permission(
        self,
        source: Any,
        info: Info[StrawberryDjangoContext, Any],
        **kwargs,
    ) -> bool:
        user = cast(UserType, info.context.request.user)
        return has_perms(
            user,
            self.perms,
            any_perm=self.any_perm,
            with_superuser=self.with_superuser,
        )
