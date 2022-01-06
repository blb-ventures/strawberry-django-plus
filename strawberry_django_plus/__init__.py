import functools

from strawberry import auto as _auto
from strawberry import object_type as _object_type
from strawberry.field import StrawberryField as _StrawberryField
from strawberry.types.fields.resolver import StrawberryResolver as _StrawberryResolver
from strawberry_django import types as _types
from strawberry_django.fields import types as _ftypes

# Monkey patch strawberry_django to use strawberry's auto
_ftypes.auto = _auto
_types.auto = _auto  # type:ignore

_original_process_type = _object_type._process_type
_original_field_init = _StrawberryField.__init__
_original_field_call = _StrawberryField.__call__


@functools.wraps(_original_process_type)
def _process_type(cls, *args, **kwargs):
    if kwargs.get("description") is None:
        kwargs["description"] = cls.__doc__
    return _original_process_type(cls, *args, **kwargs)


@functools.wraps(_original_field_init)
def _field_init(*args, **kwargs):
    if kwargs.get("description") is None:
        base_resolver = kwargs.get("base_resolver")
        if base_resolver is not None:
            while isinstance(base_resolver, _StrawberryResolver):
                base_resolver = base_resolver.wrapped_func
            kwargs["description"] = base_resolver.__doc__
    return _original_field_init(*args, **kwargs)


@functools.wraps(_original_field_call)
def _field_call(self, resolver):
    if self.description is None:
        while isinstance(resolver, _StrawberryResolver):
            resolver = resolver.wrapped_func
        self.description = resolver.__doc__
    return _original_field_call(self, resolver)


_object_type._process_type = _process_type
_StrawberryField.__init__ = _field_init
_StrawberryField.__call__ = _field_call
