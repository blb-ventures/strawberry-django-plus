import contextlib
import dataclasses
import weakref
from typing import TYPE_CHECKING, Optional, Union, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from guardian import backends as _guardian_backends
from guardian.conf import settings as guardian_settings
from guardian.core import ObjectPermissionChecker as _ObjectPermissionChecker
from guardian.models.models import GroupObjectPermissionBase, UserObjectPermissionBase
from guardian.utils import get_anonymous_user as _get_anonymous_user
from guardian.utils import get_group_obj_perms_model, get_user_obj_perms_model
from typing_extensions import TypeAlias

from strawberry_django_plus.utils.typing import UserType

if TYPE_CHECKING:
    from django.contrib.auth.models import Group

UserOrGroup: TypeAlias = Union["Group", UserType]


_cache = weakref.WeakKeyDictionary()


@dataclasses.dataclass
class ObjectPermissionModels:
    user: UserObjectPermissionBase
    group: GroupObjectPermissionBase


def get_object_permission_models(model: models.Model):
    return ObjectPermissionModels(
        user=cast(UserObjectPermissionBase, get_user_obj_perms_model(model)),
        group=cast(GroupObjectPermissionBase, get_group_obj_perms_model(model)),
    )


def get_user_or_anonymous(user: UserType) -> UserType:
    username = guardian_settings.ANONYMOUS_USER_NAME or ""
    if user.is_anonymous and user.get_username() != username:
        with contextlib.suppress(ObjectDoesNotExist):
            return cast(UserType, _get_anonymous_user())
    return user


class ObjectPermissionChecker(_ObjectPermissionChecker):
    def __new__(cls, user_or_group: Optional[UserOrGroup] = None):
        if user_or_group is not None and user_or_group in _cache:
            return _cache[user_or_group]

        obj = _ObjectPermissionChecker(user_or_group=user_or_group)
        _cache[user_or_group] = obj

        return obj


# Use our implementation that reuses the checker for the same user/group
_guardian_backends.ObjectPermissionChecker = ObjectPermissionChecker
