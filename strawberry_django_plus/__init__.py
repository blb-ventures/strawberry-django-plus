from strawberry import auto as _auto
from strawberry import object_type as _object_type
from strawberry.field import StrawberryField as _StrawberryField
from strawberry.types.fields.resolver import StrawberryResolver as _StrawberryResolver
from strawberry_django import types as _types
from strawberry_django.fields import types as _ftypes

# Monkey patch strawberry_django to use strawberry's auto
_ftypes.auto = _auto
_types.auto = _auto  # type:ignore

_cls_docs = {}
_original_process_type = _object_type._process_type
_original_wrap_dataclass = _object_type._wrap_dataclass
_original_field_init = _StrawberryField.__init__
_original_field_call = _StrawberryField.__call__


def _process_type(cls, *args, **kwargs):
    if kwargs.get("description") is None:
        kwargs["description"] = _cls_docs.get(cls)
    return _original_process_type(cls, *args, **kwargs)


def _wrap_dataclass(cls):
    _cls_docs[cls] = cls.__doc__ or None
    return _original_wrap_dataclass(cls)


def _field_init(*args, **kwargs):
    if kwargs.get("description") is None:
        base_resolver = kwargs.get("base_resolver")
        if base_resolver is not None:
            while isinstance(base_resolver, _StrawberryResolver):
                base_resolver = base_resolver.wrapped_func
            kwargs["description"] = base_resolver.__doc__ or None
    return _original_field_init(*args, **kwargs)


def _field_call(self, resolver):
    ret = _original_field_call(self, resolver)
    if self.description is None:
        resolver = self.base_resolver
        while isinstance(resolver, _StrawberryResolver):
            resolver = resolver.wrapped_func
        self.description = resolver.__doc__ or None
    return ret


_object_type._process_type = _process_type
_object_type._wrap_dataclass = _wrap_dataclass
_StrawberryField.__init__ = _field_init
_StrawberryField.__call__ = _field_call
