# This should go to its own module or be contributed back to strawberry

import base64
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    List,
    Literal,
    Optional,
    Protocol,
    Sequence,
    Sized,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
    runtime_checkable,
)

import strawberry
from strawberry.arguments import UNSET, StrawberryArgument
from strawberry.field import StrawberryField
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types import Info
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Annotated

_T = TypeVar("_T")
NodeType = TypeVar("NodeType")

_connection_typename = "arrayconnection"


def _from_b64(node_id: str) -> Tuple[str, str]:
    node_type, v = base64.b64decode(node_id.encode()).decode().split(":")
    return node_type, v


def _to_b64(node_type: str, node_id: str) -> str:
    return base64.b64encode(f"{node_type}:{node_id}".encode()).decode()


async def _to_b64_async(node_type: str, node_id: Awaitable[str]) -> str:
    return _to_b64(node_type, await node_id)


@runtime_checkable
class _Countable(Protocol):
    def count(self) -> int:
        ...


@strawberry.interface(description="An object with a Globally Unique ID")  # type:ignore
class Node(Generic[NodeType]):
    @strawberry.field(description="The Globally Unique ID of this object")
    def id(self, info: Info) -> strawberry.ID:  # noqa:A003
        node_type = info.path.typename
        assert node_type

        # self might not be an instance of Node in case of ORMs
        node_id_getter = getattr(self, "resolve_node_id", None)
        if node_id_getter is not None:
            node_id = node_id_getter(info)
        else:
            # but in this case, the field must implement it
            field_id_getter = getattr(info._field, "resolve_node_id", None)
            if field_id_getter is None:
                raise NotImplementedError

            node_id = field_id_getter(info, self)

        # We are testing str first because the resolver is expected to return this,
        # and is_awaitable has a lot more overhead.
        if isinstance(node_id, str):
            return _to_b64(node_type, node_id)  # type:ignore
        elif info._raw_info.is_awaitable(node_id):
            return _to_b64_async(node_type, node_id)  # type:ignore

        raise AssertionError(f"expected either str or Awaitable, found: {repr(node_id)}")

    @classmethod
    def resolve_nodes(cls, info: Info) -> AwaitableOrValue[Iterable[NodeType]]:
        raise NotImplementedError

    @classmethod
    def resolve_node(cls, info: Info, node_id: str) -> AwaitableOrValue[Optional[NodeType]]:
        raise NotImplementedError

    def resolve_node_id(self, info: Info) -> AwaitableOrValue[str]:
        raise NotImplementedError


@strawberry.type(description="Information to aid in pagination.")
class PageInfo:
    has_next_page: bool = strawberry.field(
        description="When paginating forwards, are there more items?",
    )
    has_previous_page: bool = strawberry.field(
        description="When paginating backwards, are there more items?",
    )
    start_cursor: Optional[str] = strawberry.field(
        description="When paginating backwards, the cursor to continue.",
    )
    end_cursor: Optional[str] = strawberry.field(
        description="When paginating forwards, the cursor to continue.",
    )


@strawberry.type(description="An edge in a connection.")
class Edge(Generic[NodeType]):
    cursor: str = strawberry.field(
        description="A cursor for use in pagination",
    )
    node: NodeType = strawberry.field(
        description="The item at the end of the edge",
    )


@strawberry.type(description="A connection to a list of items.")
class Connection(Generic[NodeType]):
    page_info: PageInfo = strawberry.field(
        description="Pagination data for this connection",
    )
    edges: List[Edge[NodeType]] = strawberry.field(
        description="Contains the nodes in this connection",
    )
    total_count: int = strawberry.field(
        description="Total quantity of existing nodes",
    )

    @classmethod
    def from_nodes(
        cls,
        nodes: Iterable[Any],
        *,
        before: Annotated[
            Optional[str],
            strawberry.argument(
                description="Returns the items in the list that come before the specified cursor."
            ),
        ] = None,
        after: Annotated[
            Optional[str],
            strawberry.argument(
                description="Returns the items in the list that come after the specified cursor."
            ),
        ] = None,
        first: Annotated[
            Optional[int],
            strawberry.argument(description="Returns the first n items from the list."),
        ] = None,
        last: Annotated[
            Optional[int],
            strawberry.argument(
                description="Returns the items in the list that come after the specified cursor."
            ),
        ] = None,
    ):
        if isinstance(nodes, _Countable):
            # Support ORMs that define .count() (e.g. django)
            total_count = nodes.count()
        elif isinstance(nodes, Sized):
            total_count = len(nodes)
        else:
            nodes = list(nodes)
            total_count = len(nodes)

        # https://relay.dev/graphql/connections.htm#sec-Pagination-algorithm
        start = 0
        end = total_count

        if after:
            after_type, after_parsed = _from_b64(after)
            assert after_type == _connection_typename
            start = max(start, int(after_parsed))
        if before:
            before_type, before_parsed = _from_b64(before)
            assert before_type == _connection_typename
            end = min(end, int(before_parsed))

        if isinstance(first, int):
            if first < 0:
                raise ValueError("Argument 'first' must be a non-negative integer.")

            end = min(end, start + first)
        if isinstance(last, int):
            if last < 0:
                raise ValueError("Argument 'last' must be a non-negative integer.")

            start = max(start, end - last)

        edges = [
            Edge(
                cursor=_to_b64(_connection_typename, str(start + i)),
                node=v,
            )
            for i, v in enumerate(cast(Sequence, nodes)[start:end])
        ]
        page_info = PageInfo(
            start_cursor=edges[0].cursor if edges else None,
            end_cursor=edges[-1].cursor if edges else None,
            has_previous_page=start > 0,
            has_next_page=end < total_count,
        )

        return cls(
            edges=edges,
            page_info=page_info,
            total_count=total_count,
        )

    @classmethod
    def from_nodes_resolver(
        cls,
        *,
        before: Annotated[
            Optional[str],
            strawberry.argument(
                description="Returns the items in the list that come before the specified cursor."
            ),
        ] = None,
        after: Annotated[
            Optional[str],
            strawberry.argument(
                description="Returns the items in the list that come after the specified cursor."
            ),
        ] = None,
        first: Annotated[
            Optional[int],
            strawberry.argument(description="Returns the first n items from the list."),
        ] = None,
        last: Annotated[
            Optional[int],
            strawberry.argument(
                description="Returns the items in the list that come after the specified cursor."
            ),
        ] = None,
    ):
        def resolver(nodes: Iterable[Any]):
            return cls.from_nodes(nodes, before=before, after=after, first=first, last=last)

        return resolver


class NodeField(StrawberryField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.node_resolver = StrawberryResolver(self.default_resolver)
        if not self.base_resolver:
            self(self.node_resolver)

    def default_resolver(
        self,
        info: Info,
        id: Annotated[  # noqa:A002
            strawberry.ID,
            strawberry.argument(description="The ID of the object."),
        ],
    ):
        node_type, node_id = _from_b64(id)
        type_def = info.schema.get_type_by_name(node_type)
        assert isinstance(type_def, TypeDefinition)
        return self.resolve_node(info, type_def.origin, node_id)

    def resolve_node(
        self,
        info: Info,
        source: Node[NodeType],
        node_id: str,
    ) -> AwaitableOrValue[Optional[NodeType]]:
        return source.resolve_node(info, node_id)


class ConnectionField(StrawberryField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.connection_resolver = StrawberryResolver(Connection.from_nodes_resolver)
        if not self.base_resolver:
            self(self.connection_resolver)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        args = super().arguments
        if self.base_resolver is self.connection_resolver:
            return args

        return args + cast(List[StrawberryArgument], self.connection_resolver.arguments)

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        # If self.base_resolver is our connection_resolver, let super handle it
        if self.base_resolver is self.connection_resolver:
            type_def = info.return_type._type_definition  # type:ignore
            assert isinstance(type_def, TypeDefinition)
            field_type = type_def.type_var_map[NodeType]
            nodes = self.resolve_nodes(info, field_type)  # type:ignore
        else:
            # If base_resolver is not self.conn_resolver, then it is defined to something
            assert self.base_resolver
            default_args = ["before", "after", "first", "last"]
            kwargs = {k: v for k, v in kwargs.items() if k in default_args}

            base_kwargs = {k: v for k, v in kwargs.items() if k not in default_args}
            nodes = self.base_resolver(*args, **base_kwargs)

        if nodes is None:
            return nodes

        if info._raw_info.is_awaitable(nodes):
            return self.resolve_connection_async(info, cast(Awaitable, nodes), **kwargs)

        return self.resolve_connection(info, cast(Any, nodes), **kwargs)

    def resolve_nodes(
        self,
        info: Info,
        source: Node[NodeType],
    ) -> AwaitableOrValue[Iterable[NodeType]]:
        return source.resolve_nodes(info)

    def resolve_connection(
        self,
        info: Info,
        nodes: Iterable[NodeType],
        **kwargs,
    ) -> AwaitableOrValue[Connection[NodeType]]:
        resolver = self.connection_resolver(**kwargs)
        return resolver(nodes)  # type:ignore

    async def resolve_connection_async(
        self,
        info: Info,
        nodes: AwaitableOrValue[Iterable[NodeType]],
        **kwargs,
    ) -> AwaitableOrValue[Connection[NodeType]]:
        if info._raw_info.is_awaitable(nodes):
            nodes = await cast(Awaitable[Iterable[NodeType]], nodes)

        res = self.resolve_connection(info, cast(Iterable[NodeType], nodes), **kwargs)
        if info._raw_info.is_awaitable(res):
            res = await cast(Awaitable[Connection[NodeType]], res)

        return res


@overload
def node(
    *,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[NodeField] = NodeField,
) -> _T:
    ...


@overload
def node(
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[NodeField] = NodeField,
) -> Any:
    ...


@overload
def node(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[NodeField] = NodeField,
) -> ConnectionField:
    ...


def node(
    resolver=None,
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[NodeField] = NodeField,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    f = base_field(
        python_name=None,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives or (),
    )
    if resolver is not None:
        f = f(resolver)
    return f


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
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[ConnectionField] = ConnectionField,
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
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[ConnectionField] = ConnectionField,
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
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[ConnectionField] = ConnectionField,
) -> ConnectionField:
    ...


def connection(
    resolver=None,
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[ConnectionField] = ConnectionField,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    f = base_field(
        python_name=None,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives or (),
    )
    if resolver is not None:
        f = f(resolver)
    return f
