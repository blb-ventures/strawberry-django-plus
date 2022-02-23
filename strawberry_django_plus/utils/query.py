import functools
from typing import List, Optional, Set, Type, TypeVar

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, F, Model, Q, QuerySet
from strawberry_django.utils import is_async

from .typing import TypeOrIterable, UserType

try:
    from strawberry_django_plus.integrations.guardian import (
        get_object_permission_models,
    )

    has_guardian = True
except ImportError:  # pragma:nocover
    has_guardian = False

_M = TypeVar("_M", bound=Model)
_Q = TypeVar("_Q", bound=QuerySet)


def _filter(
    qs: _Q,
    perms: List[str],
    *,
    lookup: str = "",
    model: Type[Model],
    any_perm: bool = True,
    ctype: Optional[ContentType] = None,
) -> _Q:
    lookup = lookup and f"{lookup}__"
    ctype_attr = f"{lookup}content_type"

    if ctype is not None:
        q = Q(**{ctype_attr: ctype})
    else:
        meta = model._meta
        q = Q(
            **{
                f"{ctype_attr}__app_label": meta.app_label,
                f"{ctype_attr}__model": meta.model_name,
            }
        )

    if len(perms) == 1:
        q &= Q(**{f"{lookup}codename": perms[0]})
    elif any_perm:
        q &= Q(**{f"{lookup}codename__in": perms})
    else:
        q = functools.reduce(lambda acu, p: acu & Q(**{f"{lookup}codename": p}), perms, q)

    return qs.filter(q)


def filter_for_user(
    qs: QuerySet,
    user: UserType,
    perms: TypeOrIterable[str],
    *,
    any_perm: bool = True,
    with_groups: bool = True,
    with_superuser: bool = False,
):
    if with_superuser and user.is_active and user.is_superuser:
        return qs

    if user.is_anonymous:
        return qs.none()

    if isinstance(perms, str):
        perms = [perms]

    model = qs.model
    if model._meta.concrete_model:
        model = model._meta.concrete_model

    # We don't want to query the database here because this might not be async safe
    # Try to retrieve the ContentType from cache. If it is not there, we will
    # query it through the queryset
    ctype: Optional[ContentType] = None
    try:
        meta = model._meta
        ctype = ContentType.objects._get_from_cache(meta)  # type:ignore
    except KeyError:  # pragma:nocover
        # If we are not running async, retrieve it
        if not is_async():
            ctype = ContentType.objects.get_for_model(model)

    app_labels = set()
    perms_list = []
    for p in perms:
        parts = p.split(".")
        if len(parts) > 1:
            app_labels.add(parts[0])
        perms_list.append(parts[-1])

    if len(app_labels) == 1 and ctype is not None:
        app_label = app_labels.pop()
        if app_label != ctype.app_label:  # pragma:nocover
            raise ValueError(
                f"Given perms must have same app label ({app_label!r} != {ctype.app_label!r})"
            )
    elif len(app_labels) > 1:  # pragma:nocover
        raise ValueError(f"Cannot mix app_labels ({app_labels!r})")

    # Small optimization if the user's permissions are cached
    if hasattr(user, "_perm_cache"):  # pragma:nocover
        f = any if any_perm else all
        user_perms: Set[str] = {
            p.codename for p in user._perm_cache  # type:ignore
        }
        if f(p in user_perms for p in perms_list):
            return qs

    q = Q(
        Exists(
            _filter(
                user.user_permissions,
                perms_list,
                model=model,
                ctype=ctype,
            )
        )
    )
    if with_groups:
        q |= Q(
            Exists(
                _filter(
                    user.groups,
                    perms_list,
                    lookup="permissions",
                    model=model,
                    ctype=ctype,
                )
            )
        )

    if has_guardian:
        perm_models = get_object_permission_models(qs.model)

        user_model = perm_models.user
        user_qs = _filter(
            user_model.objects.filter(user=user),
            perms_list,
            lookup="permission",
            model=model,
            ctype=ctype,
        )
        if user_model.objects.is_generic():
            user_qs = user_qs.filter(content_type=F("permission__content_type"))
        else:
            user_qs = user_qs.annotate(object_pk=F("content_object"))

        obj_qs = user_qs.values_list("object_pk", flat=True).distinct()

        if with_groups:
            group_model = perm_models.group
            groups_field = get_user_model()._meta.get_field("groups")
            group_qs = _filter(
                group_model.objects.filter(
                    **{
                        f"group__{groups_field.related_query_name()}": user,  # type:ignore
                    },
                ),
                perms_list,
                lookup="permission",
                model=model,
                ctype=ctype,
            )
            if group_model.objects.is_generic():
                group_qs = group_qs.filter(content_type=F("permission__content_type"))
            else:
                group_qs = group_qs.annotate(object_pk=F("content_object"))

            obj_qs = obj_qs.union(group_qs.values_list("object_pk", flat=True).distinct())

        q |= Q(pk__in=obj_qs)

    return qs.filter(q)
