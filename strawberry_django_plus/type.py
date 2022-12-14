import dataclasses
import types
from typing import (
    Callable,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
)

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRel
from django.core.exceptions import FieldDoesNotExist
from django.db.models.base import Model
from django.db.models.fields.reverse_related import ManyToManyRel, ManyToOneRel
import strawberry
from strawberry import UNSET
from strawberry.annotation import StrawberryAnnotation
from strawberry.field import UNRESOLVED, StrawberryField
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.unset import UnsetType
from strawberry.utils.typing import __dataclass_transform__
from strawberry_django.fields.field import field as _field
from strawberry_django.fields.types import get_model_field, resolve_model_field_name
from strawberry_django.type import StrawberryDjangoType as _StraberryDjangoType
from strawberry_django.utils import get_annotations, is_similar_django_type
from typing_extensions import Annotated

from strawberry_django_plus.optimizer import OptimizerStore, PrefetchType
from strawberry_django_plus.utils.typing import TypeOrSequence, is_auto

from . import field
from .descriptors import ModelProperty
from .field import StrawberryDjangoField, connection, node
from .relay import Connection, ConnectionField, Node
from .types import resolve_model_field_type
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
    "partial",
]

_T = TypeVar("_T")
_O = TypeVar("_O", bound=type)
_M = TypeVar("_M", bound=Model)


def _from_django_type(
    django_type: "StrawberryDjangoType",
    name: str,
    *,
    type_annotation: Optional[StrawberryAnnotation] = None,
) -> StrawberryDjangoField:
    origin = django_type.origin

    attr = getattr(origin, name, dataclasses.MISSING)
    if attr is UNSET or attr is dataclasses.MISSING:
        attr = getattr(StrawberryDjangoField, "__dataclass_fields__", {}).get(name, UNSET)

    if type_annotation:
        try:
            type_origin = get_origin(type_annotation.annotation)
            is_connection = issubclass(type_origin, Connection) if type_origin else False
        except Exception:
            is_connection = False
    else:
        is_connection = False

    if is_connection or isinstance(attr, ConnectionField):
        field = attr
        if not isinstance(field, ConnectionField):
            field = connection()

        field = cast(StrawberryDjangoField, field)

        # FIXME: Improve this...
        if not field.base_resolver:

            def conn_resolver(root):
                return getattr(root, name).all()

            field.base_resolver = StrawberryResolver(conn_resolver)
            if type_annotation is not None:
                field.type_annotation = type_annotation
    elif isinstance(attr, StrawberryDjangoField) and not attr.origin_django_type:
        field = attr
    elif isinstance(attr, dataclasses.Field):
        default = getattr(attr, "default", dataclasses.MISSING)
        default_factory = getattr(attr, "default_factory", dataclasses.MISSING)

        if type_annotation is None:
            type_annotation = getattr(attr, "type_annotation", None)
        if type_annotation is None:
            type_annotation = StrawberryAnnotation(attr.type)

        store = getattr(attr, "store", None)
        field = StrawberryDjangoField(
            django_name=getattr(attr, "django_name", None) or attr.name,
            graphql_name=getattr(attr, "graphql_name", None),
            origin=getattr(attr, "origin", None),
            is_subscription=getattr(attr, "is_subscription", False),
            description=getattr(attr, "description", None),
            base_resolver=getattr(attr, "base_resolver", None),
            permission_classes=getattr(attr, "permission_classes", ()),
            default=default,
            default_factory=default_factory,
            deprecation_reason=getattr(attr, "deprecation_reason", None),
            directives=getattr(attr, "directives", ()),
            type_annotation=type_annotation,
            filters=getattr(attr, "filters", UNSET),
            order=getattr(attr, "order", UNSET),
            only=store and store.only,
            select_related=store and store.select_related,
            prefetch_related=store and store.prefetch_related,
            disable_optimization=getattr(attr, "disable_optimization", False),
        )
    elif isinstance(attr, StrawberryResolver):
        field = StrawberryDjangoField(base_resolver=attr)
    elif callable(attr):
        field = cast(StrawberryDjangoField, StrawberryDjangoField()(attr))
    else:
        field = StrawberryDjangoField(default=attr)

    field.python_name = name
    # store origin django type for further usage
    field.origin_django_type = django_type

    # annotation of field is used as a class type
    if type_annotation is not None:
        field.type_annotation = type_annotation
        field.is_auto = is_auto(field.type_annotation)

    # resolve the django_name and check if it is relation field. django_name
    # is used to access the field data in resolvers
    try:
        model_field = get_model_field(
            django_type.model, getattr(field, "django_name", None) or name
        )
    except FieldDoesNotExist:
        model_attr = getattr(django_type.model, name, None)
        if model_attr is not None and isinstance(model_attr, ModelProperty):
            if field.is_auto:
                annotation = model_attr.type_annotation
                if get_origin(annotation) is Annotated:
                    annotation = get_args(annotation)[0]
                field.type_annotation = StrawberryAnnotation(annotation)
                field.is_auto = is_auto(field.type_annotation)

            if field.description is None:
                field.description = model_attr.description
        elif field.django_name or field.is_auto:
            raise  # field should exist, reraise caught exception
    else:
        field.is_relation = model_field.is_relation
        if not field.django_name:
            field.django_name = resolve_model_field_name(
                model_field,
                is_input=django_type.is_input,
                is_filter=bool(django_type.is_filter),
            )

        # change relation field type to auto if field is inherited from another
        # type. for example if field is inherited from output type but we are
        # configuring field for input type
        if field.is_relation and not is_similar_django_type(django_type, field.origin_django_type):
            field.is_auto = True

        # resolve type of auto field
        if field.is_auto:
            field.type_annotation = StrawberryAnnotation(
                resolve_model_field_type(model_field, django_type)
            )

        if field.description is None:
            if isinstance(model_field, (GenericRel, GenericForeignKey)):
                description = None
            elif isinstance(model_field, (ManyToOneRel, ManyToManyRel)):
                description = model_field.field.help_text
            else:
                description = getattr(model_field, "help_text")  # noqa:B009

            if description:
                field.description = str(description)

    return field


def _get_fields(django_type: "StrawberryDjangoType"):
    origin = django_type.origin
    fields = {}

    # collect all annotated fields
    for name, annotation in get_annotations(origin).items():
        fields[name] = _from_django_type(
            django_type,
            name,
            type_annotation=annotation,
        )

    # collect non-annotated strawberry fields
    for name in dir(origin):
        if name in fields:
            continue

        attr = getattr(origin, name, None)
        if not isinstance(attr, StrawberryField):
            continue

        fields[name] = _from_django_type(django_type, name)

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
    field_cls: Type[StrawberryDjangoField] = StrawberryDjangoField,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    pagination: Optional[bool] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
    **kwargs,
) -> _O:
    original_annotations = cls.__dict__.get("__annotations__", {})

    django_type = StrawberryDjangoType(
        origin=cls,
        model=model,
        field_cls=field_cls,
        is_input=kwargs.get("is_input", False),
        is_partial=kwargs.pop("partial", False),
        is_filter=kwargs.pop("is_filter", False),
        filters=filters,
        order=order,
        pagination=pagination,
        disable_optimization=disable_optimization,
        store=OptimizerStore.with_hints(
            only=only,
            select_related=select_related,
            prefetch_related=prefetch_related,
        ),
    )

    fields = list(_get_fields(django_type).values())
    cls.__annotations__ = {}

    # update annotations and fields
    for f in fields:
        annotation = f.type_annotation.annotation if f.type_annotation is not None else f.type
        if annotation is UNRESOLVED:
            annotation = None

        cls.__annotations__[f.name] = annotation
        setattr(cls, f.name, f)

    # Make sure model is also considered a "virtual subclass" of cls
    if "is_type_of" not in cls.__dict__:
        cls.is_type_of = lambda obj, info: isinstance(obj, (cls, model))  # type:ignore

    # Default querying methods for relay
    if issubclass(cls, Node):
        if not _has_own_node_resolver(cls, "resolve_node"):
            cls.resolve_node = types.MethodType(resolve_model_node, cls)

        if not _has_own_node_resolver(cls, "resolve_nodes"):
            cls.resolve_nodes = types.MethodType(
                lambda *args, **kwargs: resolve_model_nodes(
                    *args,
                    filter_perms=True,
                    **kwargs,
                ),
                cls,
            )

        if not _has_own_node_resolver(cls, "resolve_connection"):
            cls.resolve_connection = types.MethodType(
                lambda *args, **kwargs: resolve_connection(
                    *args,
                    filter_perms=True,
                    **kwargs,
                ),
                cls,
            )

        if not _has_own_node_resolver(cls, "resolve_id"):
            cls.resolve_id = types.MethodType(
                lambda cls, root, *args, **kwargs: resolve_model_id(cls, root),
                cls,
            )

    strawberry.type(cls, **kwargs)

    # restore original annotations for further use
    cls.__annotations__ = original_annotations
    cls._django_type = django_type  # type:ignore

    return cls


@dataclasses.dataclass
class StrawberryDjangoType(_StraberryDjangoType[_O, _M]):
    """Strawberry django type metadata."""

    is_filter: Union[Literal["lookups"], bool]
    order: Optional[Union[type, UnsetType]]
    filters: Optional[Union[type, UnsetType]]
    pagination: Optional[Union[bool, UnsetType]]
    disable_optimization: bool
    store: OptimizerStore


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def type(  # noqa:A001
    model: Type[Model],
    *,
    name: Optional[str] = None,
    field_cls: Type[StrawberryDjangoField] = StrawberryDjangoField,
    is_input: bool = False,
    is_interface: bool = False,
    is_filter: Union[Literal["lookups"], bool] = False,
    description: Optional[str] = None,
    directives: Optional[Sequence[object]] = (),
    extend: bool = False,
    filters: Optional[type] = UNSET,
    pagination: Optional[bool] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
            field_cls=field_cls,
            is_input=is_input,
            is_filter=is_filter,
            is_interface=is_interface,
            description=description,
            directives=directives,
            extend=extend,
            filters=filters,
            pagination=pagination,
            order=order,
            only=only,
            select_related=select_related,
            prefetch_related=prefetch_related,
            disable_optimization=disable_optimization,
        )

    return wrapper


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def interface(
    model: Type[Model],
    *,
    name: Optional[str] = None,
    field_cls: Type[StrawberryDjangoField] = StrawberryDjangoField,
    description: Optional[str] = None,
    directives: Optional[Sequence[object]] = (),
) -> Callable[[_T], _T]:
    """Annotates a class as a Django GraphQL interface.

    Examples:
        It can be used like this:

        >>> @gql.django.interface(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """

    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            field_cls=field_cls,
            is_interface=True,
            description=description,
            directives=directives,
        )

    return wrapper


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def input(  # noqa:A001
    model: Type[Model],
    *,
    name: Optional[str] = None,
    field_cls: Type[StrawberryDjangoField] = StrawberryDjangoField,
    description: Optional[str] = None,
    directives: Optional[Sequence[object]] = (),
    is_filter: Union[Literal["lookups"], bool] = False,
    partial: bool = False,
) -> Callable[[_T], _T]:
    """Annotates a class as a Django GraphQL input.

    Examples:
        It can be used like this:

        >>> @gql.django.input(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """

    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            field_cls=field_cls,
            is_input=True,
            is_filter=is_filter,
            description=description,
            directives=directives,
            partial=partial,
        )

    return wrapper


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def partial(  # noqa:A001
    model: Type[Model],
    *,
    name: Optional[str] = None,
    field_cls: Type[StrawberryDjangoField] = StrawberryDjangoField,
    description: Optional[str] = None,
    directives: Optional[Sequence[object]] = (),
) -> Callable[[_T], _T]:
    """Annotates a class as a Django GraphQL partial.

    Examples:
        It can be used like this:

        >>> @gql.django.partial(SomeModel)
        ... class X:
        ...     some_field: gql.auto
        ...     otherfield: str = gql.django.field()

    """

    def wrapper(cls):
        return _process_type(
            cls,
            model,
            name=name,
            field_cls=field_cls,
            is_input=True,
            description=description,
            directives=directives,
            partial=True,
        )

    return wrapper
