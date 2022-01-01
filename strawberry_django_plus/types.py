import dataclasses
from typing import Callable, Generic, Optional, Sequence, Type, TypeVar, Union

from django.db.models.base import Model
import strawberry
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET
from strawberry.field import StrawberryField
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.unset import _Unset
from strawberry.utils.typing import __dataclass_transform__
from strawberry_django.fields.types import auto
from strawberry_django.filters import StrawberryDjangoFieldFilters
from strawberry_django.ordering import StrawberryDjangoFieldOrdering
from strawberry_django.pagination import StrawberryDjangoPagination
from strawberry_django.type import StrawberryDjangoType as _StraberryDjangoType
from strawberry_django.utils import get_annotations

from .fields import StrawberryDjangoField

_T = TypeVar("_T")
_O = TypeVar("_O", bound=Type)
_M = TypeVar("_M", bound=Model)


def _get_fields(django_type: "StrawberryDjangoType"):
    origin = django_type.origin
    fields = {}

    # collect all annotated fields
    for name, annotation in get_annotations(origin).items():
        fields[name] = StrawberryDjangoField.from_django_type(
            django_type,
            name,
            type_annotation=annotation,
        )

    # collect non-annotated strawberry fields
    for name in dir(origin):
        if name in fields:
            continue

        attr = getattr(origin, name)
        if not isinstance(attr, StrawberryField):
            continue

        fields[name] = StrawberryDjangoField.from_django_type(django_type, name)

    return fields


def _process_type(
    cls,
    model: Type[Model],
    *,
    filters: Optional[StrawberryDjangoFieldFilters] = UNSET,
    pagination: Optional[StrawberryDjangoPagination] = UNSET,
    order: Optional[StrawberryDjangoFieldOrdering] = UNSET,
    **kwargs,
):
    original_annotations = cls.__dict__.get("__annotations__", {})

    django_type = StrawberryDjangoType(
        origin=cls,
        model=model,
        is_input=kwargs.get("is_input", False),
        is_partial=kwargs.pop("partial", False),
        is_filter=kwargs.pop("is_filter", False),
        filters=filters,
        order=order,
        pagination=pagination,
    )

    fields = list(_get_fields(django_type).values())
    cls.__annotations__ = {}

    # update annotations and fields
    for f in fields:
        annotation = f.type_annotation.annotation if f.type_annotation is not None else f.type
        if annotation is None:
            annotation = StrawberryAnnotation(auto)

        cls.__annotations__[f.name] = annotation
        setattr(cls, f.name, f)

    if not hasattr(cls, "is_type_of"):
        cls.is_type_of = lambda obj, _info: isinstance(obj, (cls, model))

    new_cls = strawberry.type(cls, **kwargs)

    # restore original annotations for further use
    cls.__annotations__ = original_annotations
    cls._django_type = django_type

    return new_cls


@dataclasses.dataclass
class StrawberryDjangoType(Generic[_O, _M], _StraberryDjangoType):
    origin: _O
    model: Type[_M]
    is_input: bool
    is_partial: bool
    is_filter: bool
    filters: Optional[Union[StrawberryDjangoFieldFilters, _Unset]]
    order: Optional[Union[StrawberryDjangoFieldOrdering, _Unset]]
    pagination: Optional[Union[StrawberryDjangoPagination, _Unset]]


@__dataclass_transform__(order_default=True, field_descriptors=(StrawberryField,))
def type(  # noqa:A001
    model: Type[Model],
    *,
    name: str = None,
    is_input: bool = False,
    is_interface: bool = False,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    extend: bool = False,
    filters: Optional[StrawberryDjangoFieldFilters] = UNSET,
    pagination: Optional[StrawberryDjangoPagination] = UNSET,
    order: Optional[StrawberryDjangoFieldOrdering] = UNSET,
) -> Callable[[_T], _T]:
    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            is_input=is_input,
            is_interface=is_interface,
            description=description,
            directives=directives,
            extend=extend,
            filters=filters,
            pagination=pagination,
            order=order,
        )

    return wrapper


@__dataclass_transform__(order_default=True, field_descriptors=(StrawberryField,))
def interface(
    model: Type[Model],
    *,
    name: str = None,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Callable[[_T], _T]:
    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            is_interface=True,
            description=description,
            directives=directives,
        )

    return wrapper


@__dataclass_transform__(order_default=True, field_descriptors=(StrawberryField,))
def input(  # noqa:A001
    model: Type[Model],
    *,
    name: str = None,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    partial: bool = False,
) -> Callable[[_T], _T]:
    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            is_input=True,
            description=description,
            directives=directives,
            partial=partial,
        )

    return wrapper
