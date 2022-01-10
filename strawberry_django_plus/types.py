from typing import TYPE_CHECKING, Union

from django.db.models.fields import Field
from django.db.models.fields.reverse_related import ForeignObjectRel
from strawberry import enum
from strawberry_django.fields.types import (
    resolve_model_field_type as _resolve_model_field,
)

if TYPE_CHECKING:
    from strawberry_django_plus.type import StrawberryDjangoType

try:
    from django_choices_field import IntegerChoicesField, TextChoicesField
except ImportError:
    has_choices_field = False
else:
    has_choices_field = True


def resolve_model_field_type(
    model_field: Union[Field, ForeignObjectRel],
    django_type: "StrawberryDjangoType",
):
    if has_choices_field and isinstance(model_field, (TextChoicesField, IntegerChoicesField)):
        field_type = model_field.choices_enum
        if not hasattr(field_type, "_enum_definition"):
            return enum(field_type, description=field_type.__doc__)

    return _resolve_model_field(model_field, django_type)
