import functools
from typing import Dict, Type, Union, cast

from django.db import models
from django.db.models.fields import Field
from django.db.models.fields.reverse_related import ForeignObjectRel
from strawberry.utils.str_converters import to_camel_case
from strawberry_django.fields.types import resolve_model_field_name


@functools.lru_cache(maxsize=1024)
def get_model_fields(
    model: Type[models.Model],
    *,
    camel_case: bool = False,
    is_input: bool = False,
    is_filter: bool = False,
) -> Dict[str, Union[Field, ForeignObjectRel]]:
    """Get a list of model fields"""
    fields = {}
    for f in model._meta.get_fields():
        name = cast(str, resolve_model_field_name(f, is_input=is_input, is_filter=is_filter))
        if camel_case:
            name = to_camel_case(name)
        fields[name] = f
    return fields
