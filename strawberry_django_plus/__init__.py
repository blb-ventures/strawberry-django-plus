import inspect
import re

from strawberry import auto as _auto
from strawberry import object_type as _object_type
from strawberry.enum import EnumDefinition as _EnumDefinition
from strawberry.field import StrawberryField as _StrawberryField
from strawberry.schema.name_converter import NameConverter as _NameConverter
from strawberry.schema_directive import (
    StrawberrySchemaDirective as _StrawberrySchemaDirective,
)
from strawberry.types.fields.resolver import StrawberryResolver as _StrawberryResolver
from strawberry_django import types as _types
from strawberry_django.fields import types as _ftypes

# Just import this for the monkey patch
from .utils.printer import print_schema  # noqa:F401

# Monkey patch strawberry_django to use strawberry's auto
_ftypes.auto = _auto
_types.auto = _auto  # type:ignore

_cls_docs = {}
_original_process_type = _object_type._process_type
_original_process_type = _object_type._process_type
_original_wrap_dataclass = _object_type._wrap_dataclass
_original_field_init = _StrawberryField.__init__
_original_field_call = _StrawberryField.__call__
_original_enum_init = _EnumDefinition.__init__
_original_schema_directive_init = _StrawberrySchemaDirective.__init__
_original_from_generic = _NameConverter.from_generic


def _get_doc(obj):
    if not obj.__doc__:
        return None
    return inspect.cleandoc(obj.__doc__)


def _process_type(cls, *args, **kwargs):
    if kwargs.get("description") is None:
        kwargs["description"] = _cls_docs.get(cls)
    ret = _original_process_type(cls, *args, **kwargs)
    for d in ret._type_definition.directives:
        d.instance.register(ret._type_definition)
    return ret


def _wrap_dataclass(cls):
    _cls_docs[cls] = _get_doc(cls)
    return _original_wrap_dataclass(cls)


def _field_init(self, *args, **kwargs):
    if kwargs.get("description") is None:
        base_resolver = kwargs.get("base_resolver")
        if base_resolver is not None:
            while isinstance(base_resolver, _StrawberryResolver):
                base_resolver = base_resolver.wrapped_func
            kwargs["description"] = _get_doc(base_resolver)
    ret = _original_field_init(self, *args, **kwargs)
    for d in self.directives:
        d.instance.register(self)
    return ret


def _field_call(self, resolver):
    ret = _original_field_call(self, resolver)
    if self.description is None:
        resolver = self.base_resolver
        while isinstance(resolver, _StrawberryResolver):
            resolver = resolver.wrapped_func
        self.description = _get_doc(resolver)
    return ret


def _enum_init(*args, **kwargs):
    if kwargs.get("description") is None:
        cls = kwargs.get("wrapped_cls")
        kwargs["description"] = _get_doc(cls)
    return _original_enum_init(*args, **kwargs)


def _schema_directive_init(self, *args, **kwargs):
    if kwargs.get("description") is None:
        cls = kwargs.get("wrap")
        kwargs["description"] = _get_doc(cls)
    return _original_schema_directive_init(self, *args, **kwargs)


def _from_generic(*args, **kwargs):
    from .settings import config

    v = _original_from_generic(*args, **kwargs)
    for p in config.REMOVE_DUPLICATED_SUFFIX:
        if not v.endswith(p):
            continue
        v = re.sub(rf"{p}(?!$)", "", v)

    return v


_object_type._process_type = _process_type
_object_type._wrap_dataclass = _wrap_dataclass
_StrawberryField.__init__ = _field_init
_StrawberryField.__call__ = _field_call
_EnumDefinition.__init__ = _enum_init
_StrawberrySchemaDirective.__init__ = _schema_directive_init
_NameConverter.from_generic = _from_generic
