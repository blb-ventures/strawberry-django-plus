import functools
from typing import Dict, Type, Union

from django.db import models
from django.db.models.fields import Field
from django.db.models.fields.reverse_related import ManyToOneRel
from strawberry.utils.str_converters import to_camel_case


@functools.lru_cache(maxsize=250)
def get_model_fields(
    model: Type[models.Model],
    camel_case: bool = False,
) -> Dict[str, Union[Field, ManyToOneRel]]:
    """Get a list of model fields"""
    fields = {}
    for f in model._meta.get_fields():
        name = f.related_name or f"{f.name}_set" if isinstance(f, ManyToOneRel) else f.name
        if camel_case:
            name = to_camel_case(name)
        fields[name] = f
    return fields
