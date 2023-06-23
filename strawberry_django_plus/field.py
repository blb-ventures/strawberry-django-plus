import dataclasses
import inspect
from functools import cached_property
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

import strawberry
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import QuerySet
from django.db.models.fields.related_descriptors import (
    ForwardManyToOneDescriptor,
    ReverseManyToOneDescriptor,
    ReverseOneToOneDescriptor,
)
from django.db.models.query_utils import DeferredAttribute
from strawberry import UNSET, relay
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import StrawberryArgument
from strawberry.auto import StrawberryAuto
from strawberry.extensions.field_extension import FieldExtension, SyncExtensionResolver
from strawberry.field import _RESOLVER_TYPE, UNRESOLVED, StrawberryField
from strawberry.lazy_type import LazyType
from strawberry.permission import BasePermission
from strawberry.relay.types import NodeIterableType
from strawberry.type import StrawberryContainer, StrawberryType, get_object_definition
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.union import StrawberryUnion
from strawberry_django.arguments import argument
from strawberry_django.fields.field import (
    StrawberryDjangoField as _StrawberryDjangoField,
)
from strawberry_django.fields.types import get_model_field, is_optional
from strawberry_django.utils import unwrap_type

from strawberry_django_plus.types import resolve_model_field_type
from strawberry_django_plus.utils import aio

from . import optimizer
from .descriptors import ModelProperty
from .permissions import filter_with_perms
from .utils import resolvers
from .utils.typing import TypeOrSequence

if TYPE_CHECKING:
    from strawberry_django_plus.type import StrawberryDjangoType

__all__ = [
    "StrawberryDjangoField",
    "StrawberryDjangoConnectionExtension",
    "field",
    "node",
    "connection",
]

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)


class StrawberryDjangoField(_StrawberryDjangoField):
    """A strawberry field for django attributes.

    Do not instantiate this directly. Instead, use `@field` decorator.

    """

    def __init__(
        self,
        *args,
        only: Optional[TypeOrSequence[str]] = None,
        select_related: Optional[TypeOrSequence[str]] = None,
        prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
        disable_optimization: bool = False,
        **kwargs,
    ):
        self.disable_optimization = disable_optimization
        self.store = optimizer.OptimizerStore.with_hints(
            only=only,
            select_related=select_related,
            prefetch_related=prefetch_related,
        )
        super().__init__(*args, **kwargs)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        args = super().arguments
        is_node = isinstance(unwrap_type(self.type), relay.Node)
        return [
            (
                (
                    argument("ids", List[relay.GlobalID], is_optional=self.is_optional)
                    if self.is_list
                    else argument("id", relay.GlobalID, is_optional=self.is_optional)
                )
                if (
                    is_node
                    and arg.python_name == "pk"
                    and arg.type_annotation.annotation == strawberry.ID
                )
                else arg
            )
            for arg in args
        ]

    @arguments.setter
    def arguments(self, value: List[StrawberryArgument]):
        return StrawberryField.arguments.fset(self, value)

    @property
    def type(self) -> Union[StrawberryType, type, Literal[UNRESOLVED]]:  # type: ignore # noqa: A003
        resolved = super().type
        if resolved is UNRESOLVED:
            return resolved

        resolved_type = resolved
        while isinstance(resolved_type, StrawberryContainer):
            resolved_type = resolved_type.of_type
        resolved_django_type: Optional["StrawberryDjangoType"] = getattr(
            resolved_type,
            "_django_type",
            None,
        )

        if self.origin_django_type and (
            # FIXME: Why does this come as Any sometimes when using future annotations?
            resolved is Any
            or isinstance(resolved, StrawberryAuto)
            # If the resolved type is an input but the origin is not, or vice versa,
            # resolve this again
            or (
                resolved_django_type
                and resolved_django_type.is_input != self.origin_django_type.is_input
            )
        ):
            model_field = get_model_field(
                self.origin_django_type.model,
                self.django_name or self.name,
            )
            resolved_type = resolve_model_field_type(model_field, self.origin_django_type)
            if is_optional(
                model_field,
                self.origin_django_type.is_input,
                self.origin_django_type.is_partial,
            ):
                resolved_type = Optional[resolved_type]

            self.type_annotation = StrawberryAnnotation(resolved_type)
            resolved = super().type

        return resolved

    @type.setter
    def type(self, type_: Any) -> None:  # noqa: A003
        super(StrawberryDjangoField, self.__class__).type.fset(self, type_)  # type: ignore

    def get_order(self) -> Optional[Type]:
        # FIXME: This should be done on strawberry-graphql-django
        order = super().get_order()
        if order in (None, UNSET):
            t_origin = self.type_origin
            if t_origin and (f_origin := getattr(t_origin, "_django_type", None)) is not None:
                order = f_origin.order

        return order if order is not UNSET else None

    def get_filters(self) -> Optional[Type]:
        # FIXME: This should be done on strawberry-graphql-django
        filters = super().get_filters()
        if filters in (None, UNSET):
            t_origin = self.type_origin
            if t_origin and (f_origin := getattr(t_origin, "_django_type", None)) is not None:
                filters = f_origin.filters

        return filters if filters is not UNSET else None

    @cached_property
    def type_origin(self) -> Optional[Type]:
        origin = self.type

        if (
            (tdef := get_object_definition(origin))
            and tdef.concrete_of
            and issubclass(tdef.concrete_of.origin, relay.Connection)
        ):
            origin = tdef.type_var_map[cast(TypeVar, relay.NodeType)]
            if isinstance(origin, LazyType):
                origin = origin.resolve_type()

        while isinstance(origin, StrawberryContainer):
            origin = origin.of_type

        if isinstance(origin, StrawberryUnion):
            olist = []
            for t in origin.types:
                while isinstance(t, StrawberryContainer):
                    t = t.of_type  # noqa: PLW2901

                if hasattr(t, "_django_type"):
                    olist.append(t)

            origin = olist[0] if len(olist) == 1 else None

        return origin

    @cached_property
    def model(self) -> Optional[Type[models.Model]]:
        return (type_origin := self.type_origin) and type_origin._django_type.model

    @cached_property
    def safe_resolver(self):
        resolver = self.base_resolver
        assert resolver
        if not resolver.is_async:
            return resolvers.async_safe(resolver)

        return resolver

    def get_result(
        self,
        source: Optional[models.Model],
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
        *,
        skip_base_resolver: bool = False,
    ) -> Union[Awaitable[Any], Any]:
        if not skip_base_resolver and self.base_resolver is not None:
            result = self.resolver(source, info, args, kwargs)
        elif source is None:
            model = self.model
            assert model is not None
            result = model._default_manager.all()
        else:
            # Small optimization to async resolvers avoid having to call it in an sync_to_async
            # context if the value is already cached, since it will not hit the db anymore
            attname = self.django_name or self.python_name
            attr = getattr(source.__class__, attname, None)
            try:
                if isinstance(attr, ModelProperty):
                    result = source.__dict__[attr.name]
                elif isinstance(attr, DeferredAttribute):
                    # If the value is cached, retrieve it with getattr because
                    # some fields wrap values at that time (e.g. FileField).
                    # If this next like fails, it will raise KeyError and get
                    # us out of the loop before we can do getattr
                    source.__dict__[attr.field.attname]
                    result = getattr(source, attr.field.attname)
                elif isinstance(attr, ForwardManyToOneDescriptor):
                    # This will raise KeyError if it is not cached
                    result = attr.field.get_cached_value(source)  # type: ignore
                elif isinstance(attr, ReverseOneToOneDescriptor):
                    # This will raise KeyError if it is not cached
                    result = attr.related.get_cached_value(source)
                elif isinstance(attr, ReverseManyToOneDescriptor):
                    # This returns a queryset, it is async safe
                    result = getattr(source, attname)
                else:
                    raise KeyError  # noqa: TRY301
            except KeyError:
                result = resolvers.getattr_async_safe(source, attname)

        def qs_resolver(qs):
            if self.is_list:
                retval = self.get_queryset_as_list(qs, info, kwargs)
            elif isinstance((f_type := self.type), type) and issubclass(f_type, relay.Connection):
                retval = self.get_queryset_as_list(qs, info, kwargs, skip_fetch=True)
            else:
                retval = self.get_queryset_one(qs, info, kwargs)

            return retval

        return resolvers.resolve_result(result, info=info, qs_resolver=qs_resolver)

    def resolver(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        return self.safe_resolver(*args, **kwargs)

    def get_queryset(self, queryset: QuerySet[_M], info: Info, *args, **kwargs) -> QuerySet[_M]:
        qs = super().get_queryset(queryset, info, *args, **kwargs)

        ext = optimizer.optimizer.get()
        if ext is not None:
            # If optimizer extension is enabled, optimize this queryset
            qs = ext.optimize(qs, info=info)

        return qs

    @resolvers.async_safe
    def get_queryset_as_list(
        self,
        qs: QuerySet[_M],
        info: Info,
        kwargs: Dict[str, Any],
        *,
        skip_fetch: bool = False,
    ) -> QuerySet[_M]:
        # Remove info from kwargs since we will pass it positionaly to get_queryset
        if "info" in kwargs:
            del kwargs["info"]

        if not self.base_resolver:
            nodes: Optional[List[relay.GlobalID]] = kwargs.get("ids")
            if isinstance(nodes, list):
                if nodes:
                    assert {n.resolve_type(info) for n in nodes} == {unwrap_type(self.type)}
                qs = qs.filter(pk__in=[n.node_id for n in nodes])

        qs = self.get_queryset(filter_with_perms(qs, info), info, **kwargs)
        if not skip_fetch and not any(
            isinstance(e, relay.ConnectionExtension) for e in self.extensions
        ):
            # This is what QuerySet does internally to fetch results.
            # After this, iterating over the queryset should be async safe
            qs._fetch_all()  # type: ignore
        return qs

    @resolvers.async_safe
    def get_queryset_one(
        self,
        qs: QuerySet[_M],
        info: Info,
        kwargs: Dict[str, Any],
    ) -> Optional[_M]:
        # Remove info from kwargs since we will pass it positionaly to get_queryset
        if "info" in kwargs:
            del kwargs["info"]

        try:
            qs = self.get_queryset(qs, info, **kwargs)
            if not self.base_resolver:
                node = kwargs.get("id")
                if isinstance(node, relay.GlobalID):
                    assert node.resolve_type(info) == unwrap_type(self.type)
                    qs = qs.filter(pk=node.node_id)
        except ObjectDoesNotExist:
            if not self.is_optional:
                raise
        else:
            return qs.get()

        return None


class StrawberryDjangoConnectionExtension(relay.ConnectionExtension):
    def apply(self, field: StrawberryDjangoField) -> None:
        # NOTE: Because we have a base_resolver defined, our parents will not add
        # order/filters resolvers in here, so we need to add them by hand (unless they
        # are somewhat in there). We are not adding pagination because it doesn't make
        # sense together with a Connection
        args: Dict[str, StrawberryArgument] = {a.python_name: a for a in field.arguments}

        if "filters" not in args and (filters := field.get_filters()) not in (None, UNSET):
            args["filters"] = argument("filters", filters)
        if "order" not in args and (order := field.get_order()) not in (None, UNSET):
            args["order"] = argument("order", order)

        field.arguments = list(args.values())

        if field.base_resolver is None:

            def default_resolver(
                root: Optional[models.Model],
                info: Info,
                **kwargs: Any,
            ) -> Iterable[Any]:
                if root is not None:
                    # If this is a nested field, call get_result instead because we want
                    # to retrieve the queryset from its RelatedManager
                    retval = field.get_result(root, info, [], kwargs, skip_base_resolver=True)
                else:
                    if (type_origin := field.type_origin) is None:
                        raise TypeError(
                            (
                                "Django connection without a resolver needs to define a connection "
                                "for one and only one django type. To use it in a union, define "
                                "your own resolver that handles each of those"
                            ),
                        )

                    retval = resolvers.resolve_model_nodes(
                        type_origin,
                        info=info,
                        required=True,
                        filter_perms=True,
                    )

                # If the type defines a custom get_queryset, use it on top of the returned queryset
                if (get_queryset := getattr(field.type_origin, "get_queryset", None)) is not None:
                    retval = aio.resolve(
                        retval,
                        lambda resolved: get_queryset(resolved, info),
                        info=info,
                    )

                return cast(Iterable[Any], retval)

            field.base_resolver = StrawberryResolver(default_resolver)

        return super().apply(field)

    def resolve(
        self,
        next_: SyncExtensionResolver,
        source: Any,
        info: Info,
        *,
        before: Optional[str] = None,
        after: Optional[str] = None,
        first: Optional[int] = None,
        last: Optional[int] = None,
        **kwargs: Any,
    ) -> Any:
        assert self.connection_type is not None
        nodes = cast(Iterable[relay.Node], next_(source, info, **kwargs))

        # We have a single resolver for both sync and async, so we need to check if
        # nodes is awaitable or not and resolve it accordingly
        if inspect.isawaitable(nodes):

            async def resolver():
                resolved = self.connection_type.resolve_connection(
                    await nodes,
                    info=info,
                    before=before,
                    after=after,
                    first=first,
                    last=last,
                )
                if inspect.isawaitable(resolved):
                    resolved = await resolved
                return resolved

            return resolver()

        return self.connection_type.resolve_connection(
            nodes,
            info=info,
            before=before,
            after=after,
            first=first,
            last=last,
        )


@overload
def field(
    *,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    extensions: List[FieldExtension] = (),  # type: ignore
) -> _T:
    ...


@overload
def field(
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    extensions: List[FieldExtension] = (),  # type: ignore
) -> Any:
    ...


@overload
def field(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    extensions: List[FieldExtension] = (),  # type: ignore
) -> StrawberryDjangoField:
    ...


def field(
    resolver=None,
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    extensions: List[FieldExtension] = (),  # type: ignore
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init: Literal[True, False, None] = None,
) -> Any:
    """Annotate a method or property as a Django GraphQL field.

    Examples:
        It can be used both as decorator and as a normal function:

        >>> @gql.type
        >>> class X:
        ...     field_abc: str = gql.django.field(description="ABC")
        ...     @gql.django.field(description="ABC")
        ...
        ...     def field_with_resolver(self) -> str:
        ...         return "abc"

    """
    f = StrawberryDjangoField(
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=StrawberryAnnotation.from_annotation(graphql_type),
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        metadata=metadata,
        directives=directives,
        filters=filters,
        pagination=pagination,
        order=order,
        only=only,
        select_related=select_related,
        prefetch_related=prefetch_related,
        disable_optimization=disable_optimization,
        extensions=extensions,
    )

    if resolver:
        return f(resolver)

    return f


def node(
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    extensions: List[FieldExtension] = (),  # type: ignore
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init: Literal[True, False, None] = None,
) -> Any:
    """Annotate a property to create a relay query field.

    Examples:
        Annotating something like this:

        >>> @strawberry.type
        >>> class X:
        ...     some_node: SomeType = relay.node(description="ABC")

        Will produce a query like this that returns `SomeType` given its id.

        ```
        query {
            someNode (id: ID) {
                id
                ...
            }
        }
        ```

    """
    extensions = [*list(extensions), relay.NodeExtension()]
    return StrawberryDjangoField(
        python_name=None,
        graphql_name=name,
        type_annotation=StrawberryAnnotation.from_annotation(graphql_type),
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        metadata=metadata,
        directives=directives or (),
        only=only,
        select_related=select_related,
        prefetch_related=prefetch_related,
        disable_optimization=disable_optimization,
        extensions=extensions,
    )


@overload
def connection(
    graphql_type: Optional[Type[relay.Connection[relay.NodeType]]] = None,
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    extensions: List[FieldExtension] = (),  # type: ignore
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
) -> Any:
    ...


@overload
def connection(
    graphql_type: Optional[Type[relay.Connection[relay.NodeType]]] = None,
    *,
    resolver: Optional[_RESOLVER_TYPE[NodeIterableType[Any]]] = None,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    extensions: List[FieldExtension] = (),  # type: ignore
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
) -> Any:
    ...


def connection(
    graphql_type: Optional[Type[relay.Connection[relay.NodeType]]] = None,
    *,
    resolver: Optional[_RESOLVER_TYPE[NodeIterableType[Any]]] = None,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    extensions: List[FieldExtension] = (),  # type: ignore
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[optimizer.PrefetchType]] = None,
    disable_optimization: bool = False,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init: Literal[True, False, None] = None,
) -> Any:
    """Annotate a property or a method to create a relay connection field.

    Relay connections_ are mostly used for pagination purposes. This decorator
    helps creating a complete relay endpoint that provides default arguments
    and has a default implementation for the connection slicing.

    Note that when setting a resolver to this field, it is expected for this
    resolver to return an iterable of the expected node type, not the connection
    itself. That iterable will then be paginated accordingly. So, the main use
    case for this is to provide a filtered iterable of nodes by using some custom
    filter arguments.

    Examples:
        Annotating something like this:

        >>> @strawberry.type
        >>> class X:
        ...     some_node: relay.Connection[SomeType] = relay.connection(description="ABC")
        ...
        ...     @relay.connection(description="ABC")
        ...     def get_some_nodes(self, age: int) -> Iterable[SomeType]:
        ...         ...

        Will produce a query like this:

        ```
        query {
            someNode (before: String, after: String, first: String, after: String, age: Int) {
                totalCount
                pageInfo {
                    hasNextPage
                    hasPreviousPage
                    startCursor
                    endCursor
                }
                edges {
                    cursor
                    node {
                        id
                        ...
                    }
                }
            }
        }
        ```

    .. _Relay connections:
        https://relay.dev/graphql/connections.htm

    """
    extensions = [*list(extensions), StrawberryDjangoConnectionExtension()]
    f = StrawberryDjangoField(
        python_name=None,
        graphql_name=name,
        type_annotation=StrawberryAnnotation.from_annotation(graphql_type),
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        metadata=metadata,
        directives=directives or (),
        filters=filters,
        order=order,
        only=only,
        select_related=select_related,
        prefetch_related=prefetch_related,
        disable_optimization=disable_optimization,
        extensions=extensions,
    )

    if resolver:
        f = f(resolver)

    return f
