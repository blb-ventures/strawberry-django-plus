import copy
import dataclasses
import inspect
import sys
import types
from functools import cached_property
from typing import (
    Callable,
    List,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
)

import strawberry
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRel
from django.core.exceptions import FieldDoesNotExist
from django.db.models.base import Model
from django.db.models.fields.reverse_related import ManyToManyRel, ManyToOneRel
from strawberry import UNSET, relay
from strawberry.annotation import StrawberryAnnotation
from strawberry.exceptions import (
    MissingFieldAnnotationError,
)
from strawberry.field import StrawberryField
from strawberry.private import is_private
from strawberry.type import get_object_definition
from strawberry.unset import UnsetType
from strawberry_django.fields.field import field as _field
from strawberry_django.fields.types import get_model_field, resolve_model_field_name
from strawberry_django.type import StrawberryDjangoType as _StraberryDjangoType
from strawberry_django.utils import get_annotations
from typing_extensions import dataclass_transform

from strawberry_django_plus.optimizer import OptimizerStore, PrefetchType
from strawberry_django_plus.utils.typing import TypeOrSequence, is_auto

from . import field
from .descriptors import ModelProperty
from .field import StrawberryDjangoField, connection, node
from .utils.resolvers import (
    resolve_model_id,
    resolve_model_id_attr,
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


def _has_own_node_resolver(cls, name: str) -> bool:
    resolver = getattr(cls, name, None)
    if resolver is None:
        return False

    if id(resolver.__func__) == id(getattr(relay.Node, name).__func__):
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
    partial: bool = False,
    is_filter: Union[Literal["lookups"], bool] = False,
    **kwargs,
) -> _O:
    is_input = kwargs.get("is_input", False)
    django_type = StrawberryDjangoType(
        origin=cls,
        model=model,
        field_cls=field_cls,
        is_partial=partial,
        is_input=is_input,
        is_filter=is_filter,
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

    auto_fields: set[str] = set()
    for field_name, field_annotation in get_annotations(cls).items():
        annotation = field_annotation.annotation
        if is_private(annotation):
            continue

        if is_auto(annotation):
            auto_fields.add(field_name)

        # FIXME: For input types it is imported to set the default value to UNSET
        # Is there a better way of doing this?
        if is_input:
            # First check if the field is defined in the class. If it is,
            # then we just need to set its default value to UNSET in case
            # it is MISSING
            if field_name in cls.__dict__:
                field = cls.__dict__[field_name]
                if isinstance(field, dataclasses.Field) and field.default is dataclasses.MISSING:
                    field.default = UNSET
                    if isinstance(field, StrawberryField):
                        field.default_value = UNSET

                continue

            if not hasattr(cls, field_name):
                base_field = getattr(cls, "__dataclass_fields__", {}).get(field_name)
                if base_field is not None and isinstance(base_field, StrawberryField):
                    new_field = copy.copy(base_field)
                    for attr in [
                        "_arguments",
                        "permission_classes",
                        "directives",
                        "extensions",
                    ]:
                        old_attr = getattr(base_field, attr)
                        if old_attr is not None:
                            setattr(new_field, attr, old_attr[:])
                else:
                    new_field = _field(default=UNSET)

                new_field.type_annotation = field_annotation
                new_field.default = UNSET
                if isinstance(base_field, StrawberryField):
                    new_field.default_value = UNSET
                setattr(cls, field_name, new_field)

    # Make sure model is also considered a "virtual subclass" of cls
    if "is_type_of" not in cls.__dict__:
        cls.is_type_of = lambda obj, info: isinstance(obj, (cls, model))  # type: ignore

    # Default querying methods for relay
    if issubclass(cls, relay.Node):
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

        if not _has_own_node_resolver(cls, "resolve_id"):
            cls.resolve_id = types.MethodType(
                lambda cls, root, *args, **kwargs: resolve_model_id(cls, root),
                cls,
            )

        if not _has_own_node_resolver(cls, "resolve_id"):
            cls.resolve_id = types.MethodType(
                lambda cls, root, *args, **kwargs: resolve_model_id(cls, root),
                cls,
            )

        if not _has_own_node_resolver(cls, "resolve_id_attr"):
            cls.resolve_id_attr = types.MethodType(
                resolve_model_id_attr,
                cls,
            )

        # Adjust types that inherit from other types/interfaces that implement Node
        # to make sure they pass themselves as the node type
        for attr in ["resolve_node", "resolve_nodes", "resolve_id"]:
            meth = getattr(cls, attr)
            if isinstance(meth, types.MethodType) and meth.__self__ is not cls:
                setattr(cls, attr, types.MethodType(cast(classmethod, meth).__func__, cls))

    strawberry.type(cls, **kwargs)

    # update annotations and fields
    type_def = get_object_definition(cls, strict=True)
    new_fields: List[StrawberryField] = []
    for f in type_def.fields:
        django_name: Optional[str] = getattr(f, "django_name", None) or f.python_name or f.name
        description: Optional[str] = getattr(f, "description", None)
        type_annotation: Optional[StrawberryAnnotation] = getattr(
            f,
            "type_annotation",
            None,
        )

        if f.name in auto_fields:
            f_is_auto = True
            # Force the field to be auto again for it to be re-evaluated
            if type_annotation:
                type_annotation.annotation = strawberry.auto
        else:
            f_is_auto = type_annotation is not None and is_auto(
                type_annotation.annotation,
            )

        try:
            if django_name is None:
                raise FieldDoesNotExist  # noqa: TRY301
            model_attr = get_model_field(django_type.model, django_name)
        except FieldDoesNotExist as e:
            model_attr = getattr(django_type.model, django_name, None)
            is_relation = False

            if model_attr is not None and isinstance(model_attr, ModelProperty):
                if type_annotation is None or f_is_auto:
                    type_annotation = StrawberryAnnotation(
                        model_attr.type_annotation,
                        namespace=sys.modules[model_attr.func.__module__].__dict__,
                    )

                if description is None:
                    description = model_attr.description
            elif model_attr is not None and isinstance(model_attr, (property, cached_property)):
                func = model_attr.fget if isinstance(model_attr, property) else model_attr.func

                if type_annotation is None or f_is_auto:
                    if (return_type := func.__annotations__.get("return")) is None:
                        raise MissingFieldAnnotationError(django_name, type_def.origin) from e

                    type_annotation = StrawberryAnnotation(
                        return_type,
                        namespace=sys.modules[func.__module__].__dict__,
                    )

                if description is None and func.__doc__:
                    description = inspect.cleandoc(func.__doc__)
        else:
            is_relation = model_attr.is_relation
            if not django_name:
                django_name = resolve_model_field_name(
                    model_attr,
                    is_input=django_type.is_input,
                    is_filter=bool(django_type.is_filter),
                )

            if description is None:
                if isinstance(model_attr, (GenericRel, GenericForeignKey)):
                    f_description = None
                elif isinstance(model_attr, (ManyToOneRel, ManyToManyRel)):
                    f_description = model_attr.field.help_text
                else:
                    f_description = getattr(model_attr, "help_text", None)

                if f_description:
                    description = str(f_description)

        if isinstance(f, StrawberryDjangoField) and not f.origin_django_type:
            # If the field is a StrawberryDjangoField, just update its annotations/description/etc
            f.type_annotation = type_annotation
            f.description = description
        elif (
            not isinstance(f, StrawberryDjangoField)
            and getattr(f, "base_resolver", None) is not None
        ):
            # If this is not a StrawberryDjangoField, but has a base_resolver, no need
            # avoid forcing it to be a StrawberryDjangoField
            new_fields.append(f)
            continue
        else:
            store = getattr(f, "store", None)
            f = StrawberryDjangoField(  # noqa: PLW2901
                django_name=django_name,
                description=description,
                type_annotation=type_annotation,
                python_name=f.python_name,
                graphql_name=getattr(f, "graphql_name", None),
                origin=getattr(f, "origin", None),
                is_subscription=getattr(f, "is_subscription", False),
                base_resolver=getattr(f, "base_resolver", None),
                permission_classes=getattr(f, "permission_classes", ()),
                default=getattr(f, "default", dataclasses.MISSING),
                default_factory=getattr(f, "default_factory", dataclasses.MISSING),
                deprecation_reason=getattr(f, "deprecation_reason", None),
                directives=getattr(f, "directives", ()),
                filters=getattr(f, "filters", UNSET),
                order=getattr(f, "order", UNSET),
                only=store and store.only,
                select_related=store and store.select_related,
                prefetch_related=store and store.prefetch_related,
                disable_optimization=getattr(f, "disable_optimization", False),
                extensions=getattr(f, "extensions", ()),
            )

        f.django_name = django_name
        f.is_relation = is_relation
        f.origin_django_type = django_type  # type: ignore

        new_fields.append(f)
        if f.base_resolver and f.python_name:
            setattr(cls, f.python_name, f)

    type_def = get_object_definition(cls, strict=True)
    type_def._fields = new_fields
    cls._django_type = django_type  # type: ignore

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


@dataclass_transform(
    order_default=True,
    field_specifiers=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def type(  # noqa: A001
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


@dataclass_transform(
    order_default=True,
    field_specifiers=(
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


@dataclass_transform(
    order_default=True,
    field_specifiers=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def input(  # noqa: A001
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


@dataclass_transform(
    order_default=True,
    field_specifiers=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def partial(
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
