import inspect
import re

from strawberry import object_type
from strawberry.enum import EnumDefinition
from strawberry.field import StrawberryField
from strawberry.schema.name_converter import NameConverter
from strawberry.types.fields.resolver import StrawberryResolver

_cls_docs = {}
_original_process_type = object_type._process_type
_original_process_type = object_type._process_type
_original_wrap_dataclass = object_type._wrap_dataclass
_original_field_init = StrawberryField.__init__
_original_field_call = StrawberryField.__call__
_original_enum_init = EnumDefinition.__init__
_original_from_generic = NameConverter.from_generic


def _get_doc(obj):
    if not obj.__doc__:
        return None
    return inspect.cleandoc(obj.__doc__)


def _process_type(cls, *args, **kwargs):
    from strawberry_django_plus.directives import SchemaDirectiveWithResolver

    if kwargs.get("description") is None:
        kwargs["description"] = _cls_docs.get(cls)
    ret = _original_process_type(cls, *args, **kwargs)

    for d in ret._type_definition.directives:
        if isinstance(d, SchemaDirectiveWithResolver):
            d.register(ret._type_definition)

    return ret


def _wrap_dataclass(cls):
    _cls_docs[cls] = _get_doc(cls)
    return _original_wrap_dataclass(cls)


def _field_init(self, *args, **kwargs):
    from strawberry_django_plus.directives import SchemaDirectiveWithResolver

    if kwargs.get("description") is None:
        base_resolver = kwargs.get("base_resolver")
        if base_resolver is not None:
            while isinstance(base_resolver, StrawberryResolver):
                base_resolver = base_resolver.wrapped_func
            kwargs["description"] = _get_doc(base_resolver)

    ret = _original_field_init(self, *args, **kwargs)

    for d in self.directives:
        if isinstance(d, SchemaDirectiveWithResolver):
            d.register(self)

    return ret


def _field_call(self, resolver):
    ret = _original_field_call(self, resolver)
    if self.description is None:
        resolver = self.base_resolver
        while isinstance(resolver, StrawberryResolver):
            resolver = resolver.wrapped_func
        self.description = _get_doc(resolver)
    return ret


def _enum_init(*args, **kwargs):
    if kwargs.get("description") is None:
        cls = kwargs.get("wrapped_cls")
        kwargs["description"] = _get_doc(cls)
    return _original_enum_init(*args, **kwargs)


def _from_generic(*args, **kwargs):
    from .settings import config

    v = _original_from_generic(*args, **kwargs)
    for p in config.REMOVE_DUPLICATED_SUFFIX:
        if not v.endswith(p):
            continue
        v = re.sub(rf"{p}(?!$)", "", v)

    return v


object_type._process_type = _process_type
object_type._wrap_dataclass = _wrap_dataclass
StrawberryField.__init__ = _field_init
StrawberryField.__call__ = _field_call
EnumDefinition.__init__ = _enum_init
NameConverter.from_generic = _from_generic
