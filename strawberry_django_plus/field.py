import dataclasses
from functools import cached_property
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
    get_args,
    overload,
)

from django.db import models
from django.db.models import QuerySet
from django.db.models.fields.related_descriptors import (
    ForwardManyToOneDescriptor,
    ReverseManyToOneDescriptor,
    ReverseOneToOneDescriptor,
)
from django.db.models.query_utils import DeferredAttribute
import strawberry
from strawberry import UNSET
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import StrawberryArgument
from strawberry.lazy_type import LazyType
from strawberry.permission import BasePermission
from strawberry.type import StrawberryContainer, StrawberryType
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.types.types import TypeDefinition
from strawberry.union import StrawberryUnion
from strawberry_django.arguments import argument
from strawberry_django.fields.field import (
    StrawberryDjangoField as _StrawberryDjangoField,
)
from strawberry_django.utils import get_django_model, unwrap_type

from . import relay
from .descriptors import ModelProperty
from .optimizer import OptimizerStore, PrefetchType
from .permissions import filter_with_perms
from .utils import resolvers
from .utils.typing import TypeOrSequence

if TYPE_CHECKING:
    pass

__all__ = [
    "StrawberryDjangoField",
    "StrawberryDjangoNodeField",
    "StrawberryDjangoConnectionField",
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

    store: OptimizerStore

    def __init__(self, *args, **kwargs):
        self.disable_optimization = kwargs.pop("disable_optimization", False)
        self.store = OptimizerStore.with_hints(
            only=kwargs.pop("only", None),
            select_related=kwargs.pop("select_related", None),
            prefetch_related=kwargs.pop("prefetch_related", None),
        )
        super().__init__(*args, **kwargs)

    @cached_property
    def is_basic_field(self):
        return False

    @property
    def arguments(self) -> List[StrawberryArgument]:
        if isinstance(self, relay.NodeField):
            return []

        args = super().arguments
        is_node = isinstance(unwrap_type(self.type), relay.Node)
        return [
            (
                (
                    argument("ids", List[relay.GlobalID], is_optional=self.is_optional)
                    if self.is_list
                    else argument("id", relay.GlobalID, is_optional=self.is_optional)
                )
                if is_node
                and arg.python_name == "pk"
                and arg.type_annotation.annotation == strawberry.ID
                else arg
            )
            for arg in args
        ]

    @property
    def type(self) -> Union[StrawberryType, type]:  # noqa:A003
        return super().type

    @type.setter
    def type(self, type_: Any) -> None:  # noqa:A003
        if type_ is not None and self.origin_django_type is None:
            resolved = type_
            if isinstance(resolved, StrawberryAnnotation):
                resolved = type_.resolve()
            while isinstance(resolved, StrawberryContainer):
                resolved = resolved.of_type

            dj_type = getattr(resolved, "_django_type", None)
            if dj_type is None:
                contained = get_args(resolved)
                if contained:
                    dj_type = getattr(contained[0], "_django_type", None)

            if dj_type is not None:
                self.origin_django_type = dj_type
                if self.filters is UNSET or self.filters is None:
                    self.filters = dj_type.filters
                if self.order is UNSET or self.order is None:
                    self.order = dj_type.order
                if self.pagination is UNSET or self.pagination is None:
                    self.pagination = dj_type.pagination

        super(StrawberryDjangoField, self.__class__).type.fset(self, type_)  # type:ignore

    @cached_property
    def model(self) -> Type[models.Model]:
        type_ = unwrap_type(self.type)
        model = get_django_model(type_)
        if model:
            return model

        tdef = cast(Optional[TypeDefinition], getattr(type_, "_type_definition", None))
        if tdef and tdef.concrete_of and issubclass(tdef.concrete_of.origin, relay.Connection):
            n_type = tdef.type_var_map[relay.NodeType]  # type:ignore
            if isinstance(n_type, LazyType):
                n_type = n_type.resolve_type()

            return cast(Type[models.Model], get_django_model(n_type))

        origin = self.origin_django_type or getattr(self.origin, "_django_type", None)
        model = origin and origin.model

        if isinstance(self.type, StrawberryUnion):
            mlist = []
            for t in self.type.types:
                dj_type = getattr(t, "_django_type", None)
                if dj_type:
                    mlist.append(dj_type.model)
                else:
                    model = getattr(t, "model", None)
                    if model:
                        mlist.append(model)
            assert len(mlist) == 1
            model = mlist[0]

        return cast(Type[models.Model], model)

    @cached_property
    def safe_resolver(self):
        resolver = self.base_resolver
        assert resolver
        if not resolver.is_async:
            resolver = resolvers.async_safe(resolver)
        return resolver

    def get_result(
        self,
        source: Optional[models.Model],
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Union[Awaitable[Any], Any]:
        if self.base_resolver is not None:
            result = self.resolver(source, info, args, kwargs)
        elif source is None:
            result = self.model._default_manager.all()
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
                    result = attr.field.get_cached_value(source)  # type:ignore
                elif isinstance(attr, ReverseOneToOneDescriptor):
                    # This will raise KeyError if it is not cached
                    result = attr.related.get_cached_value(source)
                elif isinstance(attr, ReverseManyToOneDescriptor):
                    # This returns a queryset, it is async safe
                    result = getattr(source, attname)
                else:
                    raise KeyError
            except KeyError:
                result = resolvers.getattr_async_safe(source, attname)

        if self.is_list:
            qs_resolver = lambda qs: self.get_queryset_as_list(qs, info, kwargs)
        else:
            qs_resolver = lambda qs: self.get_queryset_one(qs, info, kwargs)

        return resolvers.resolve_result(result, info=info, qs_resolver=qs_resolver)

    def resolver(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        return self.safe_resolver(*args, **kwargs)

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
        if not skip_fetch and not isinstance(self, relay.ConnectionField):
            # This is what QuerySet does internally to fetch results.
            # After this, iterating over the queryset should be async safe
            qs._fetch_all()  # type:ignore
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
            return qs.get()
        except self.model.DoesNotExist:
            if not self.is_optional:
                raise

        return None


class StrawberryDjangoNodeField(relay.NodeField, StrawberryDjangoField):
    ...


class StrawberryDjangoConnectionField(relay.ConnectionField, StrawberryDjangoField):
    @property
    def arguments(self) -> List[StrawberryArgument]:
        args = super().arguments

        # NOTE: Because we have a base_resolver defined, our parents will not add
        # order/filters resolvers in here, so we need to add them by hand (unless they
        # are somewhat in there). We are not adding pagination because it doesn't make
        # sense together with a Connection
        args_names = [a.python_name for a in args]
        if "order" not in args_names and (order := self.get_order()) not in (None, UNSET):
            args.append(argument("order", order))
        if "filters" not in args_names and (filters := self.get_filters()) not in (None, UNSET):
            args.append(argument("filters", filters))

        return args

    def resolve_nodes(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
        *,
        nodes: Optional[QuerySet[Any]] = None,
    ):
        if nodes is None:
            nodes = self.model._default_manager.all()

            if self.origin_django_type and hasattr(self.origin_django_type.origin, "get_queryset"):
                nodes = cast(
                    QuerySet[Any],
                    self.origin_django_type.origin.get_queryset(nodes, info),
                )

        return self.get_queryset_as_list(nodes, info, kwargs, skip_fetch=True)


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
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
    pagination: Optional[bool] = UNSET,
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
        type_annotation=None,
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
    )
    if resolver:
        f = f(resolver)
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
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
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
    return StrawberryDjangoNodeField(
        python_name=None,
        graphql_name=name,
        type_annotation=None,
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
    )


@overload
def connection(
    *,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
) -> _T:
    ...


@overload
def connection(
    *,
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
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
) -> Any:
    ...


@overload
def connection(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
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
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    disable_optimization: bool = False,
) -> StrawberryDjangoConnectionField:
    ...


def connection(
    resolver=None,
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
    filters: Optional[type] = UNSET,
    order: Optional[type] = UNSET,
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
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
    f = StrawberryDjangoConnectionField(
        python_name=None,
        graphql_name=name,
        type_annotation=None,
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
    )
    if resolver is not None:
        f = f(resolver)
    return f
