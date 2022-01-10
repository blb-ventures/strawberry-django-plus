import dataclasses
import types
from typing import Callable, Generic, Optional, Sequence, Type, TypeVar, Union

from django.db.models.base import Model
import strawberry
from strawberry import auto
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET
from strawberry.field import StrawberryField
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.unset import _Unset
from strawberry.utils.typing import __dataclass_transform__
from strawberry_django.fields.field import field as _field
from strawberry_django.filters import StrawberryDjangoFieldFilters
from strawberry_django.ordering import StrawberryDjangoFieldOrdering
from strawberry_django.pagination import StrawberryDjangoPagination
from strawberry_django.type import StrawberryDjangoType as _StraberryDjangoType
from strawberry_django.utils import get_annotations

from .field import StrawberryDjangoField, field
from .relay import Node, connection, node
from .utils.resolvers import (
    resolve_connection,
    resolve_model_id,
    resolve_model_node,
    resolve_model_nodes,
)

__all = [
    "StrawberryDjangoType",
    "type",
    "interface",
    "input",
]

_T = TypeVar("_T")
_O = TypeVar("_O", bound=type)
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


def _has_own_node_resolver(cls, name: str) -> bool:
    resolver = getattr(cls, name, None)
    if resolver is None:
        return False

    if id(resolver.__func__) == id(getattr(Node, name).__func__):
        return False

    return True


def _process_type(
    cls: _O,
    model: Type[Model],
    *,
    filters: Optional[StrawberryDjangoFieldFilters] = UNSET,
    pagination: Optional[StrawberryDjangoPagination] = UNSET,
    order: Optional[StrawberryDjangoFieldOrdering] = UNSET,
    **kwargs,
) -> _O:
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

    # Make sure model is also considered a "virtual subclass" of cls
    if not hasattr(cls, "is_type_of"):
        cls.is_type_of = lambda obj, info: isinstance(obj, (cls, model))  # type:ignore

    # Default querying methods for relay
    if issubclass(cls, Node):
        if not _has_own_node_resolver(cls, "resolve_node"):
            cls.resolve_node = types.MethodType(resolve_model_node, cls)

        if not _has_own_node_resolver(cls, "resolve_nodes"):
            cls.resolve_nodes = types.MethodType(resolve_model_nodes, cls)

        if not _has_own_node_resolver(cls, "resolve_connection_resolver"):
            cls.resolve_connection = types.MethodType(resolve_connection, cls)

        if not _has_own_node_resolver(cls, "resolve_id"):
            cls.resolve_id = types.MethodType(
                lambda cls, root, *args, **kwargs: resolve_model_id(root),
                cls,
            )

    strawberry.type(cls, **kwargs)

    # restore original annotations for further use
    cls.__annotations__ = original_annotations
    cls._django_type = django_type  # type:ignore

    return cls


@dataclasses.dataclass
class StrawberryDjangoType(Generic[_O, _M], _StraberryDjangoType):
    """Strawberry django type metadata."""

    origin: _O
    model: Type[_M]
    is_input: bool
    is_filter: bool
    is_partial: bool
    order: Optional[Union[StrawberryDjangoFieldOrdering, _Unset]]
    filters: Optional[Union[StrawberryDjangoFieldFilters, _Unset]]
    pagination: Optional[Union[StrawberryDjangoPagination, _Unset]]


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(StrawberryField, field, _field, node, connection),
)
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
    """Annotates a class as a Django GraphQL type.

    Examples:
        It can be used like this:

        >>> @gql.django.type(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """

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


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(StrawberryField, field, _field, node, connection),
)
def interface(
    model: Type[Model],
    *,
    name: str = None,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Callable[[_T], _T]:
    """Annotates a class as a Django GraphQL interface.

    Examples:
        It can be used like this:

        >>> @gql.django.interface(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """
    return type(
        model,
        is_interface=True,
        name=name,
        description=description,
        directives=directives,
    )


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(StrawberryField, field, _field, node, connection),
)
def input(  # noqa:A001
    model: Type[Model],
    *,
    name: str = None,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    partial: bool = False,
) -> Callable[[_T], _T]:
    """Annotates a class as a Django GraphQL interface.

    Examples:
        It can be used like this:

        >>> @gql.django.input(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """
    return type(
        model,
        is_input=True,
        name=name,
        description=description,
        directives=directives,
    )
