# This should go to its own module or be contributed back to strawberry

import base64
import dataclasses
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

from graphql import GraphQLID
import strawberry
from strawberry.arguments import UNSET, StrawberryArgument
from strawberry.custom_scalar import ScalarDefinition
from strawberry.field import StrawberryField
from strawberry.permission import BasePermission
from strawberry.schema.types.scalar import DEFAULT_SCALAR_REGISTRY
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types import Info
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Annotated, TypeGuard

NodeType = TypeVar("NodeType")

_T = TypeVar("_T")
_R = TypeVar("_R")
_connection_typename = "arrayconnection"
_node_cache: Dict[str, Type["Node[Any]"]] = {}


def _is_awaitable(info: Info, value: _T) -> TypeGuard[Awaitable[_T]]:
    return info._raw_info.is_awaitable(value)


async def _async_callback(res: Awaitable[_T], callback: Callable[[_T], _R]) -> _R:
    return callback(await res)


def _from_base64(value: str) -> Tuple[str, str]:
    type_name, node_id = base64.b64decode(value.encode()).decode().split(":")
    return type_name, node_id


def _to_base64(type_name: str, node_id: Any) -> str:
    return base64.b64encode(f"{type_name}:{node_id}".encode()).decode()


def _ensure_type(info: Info, n_type: Type[NodeType], res: Any) -> AwaitableOrValue[NodeType]:
    if not isinstance(res, n_type):
        if _is_awaitable(info, res):
            return _async_callback(  # type:ignore
                res,
                lambda resolved: _ensure_type(info, n_type, resolved),
            )
        raise NodeException(f"{n_type} expected, found {repr(res)}")
    return res


@runtime_checkable
class _Countable(Protocol):
    def count(self) -> int:
        ...


class NodeException(Exception):
    """Base node exceptions."""


@dataclasses.dataclass(order=True, frozen=True)
class GlobalID:
    type_name: str
    node_id: str

    def __str__(self):
        return _to_base64(self.type_name, self.node_id)

    @property
    def as_id(self) -> strawberry.ID:
        return cast(strawberry.ID, self.__str__())

    @classmethod
    def from_id(cls, value: Union[str, strawberry.ID]):
        type_name, node_id = _from_base64(value)
        return cls(type_name=type_name, node_id=node_id)

    @classmethod
    async def from_node_id_async(cls, type_name: str, value: Awaitable[Union[str, strawberry.ID]]):
        return cls(type_name=type_name, node_id=await value)

    def get_type(self, info: Info) -> Type["Node[Any]"]:
        origin = _node_cache.get(self.type_name, None)
        if origin is None:
            type_def = info.schema.get_type_by_name(self.type_name)
            assert isinstance(type_def, TypeDefinition)
            origin = type_def.origin
            assert issubclass(origin, Node)
            _node_cache[self.type_name] = origin
        return origin

    @overload
    def resolve_node(self, info: Info, node_type: NodeType) -> NodeType:
        ...

    @overload
    def resolve_node(self, info: Info, node_type: Awaitable[NodeType]) -> Awaitable[NodeType]:
        ...

    @overload
    def resolve_node(self, info: Info, node_type=None) -> AwaitableOrValue[Any]:
        ...

    def resolve_node(self, info, node_type=None):
        n_type = self.get_type(info)
        node = n_type.resolve_node(info, self.node_id)

        if node_type is not None:
            return _ensure_type(info, node_type, node)

        return node


# Register our GlobalID scalar
DEFAULT_SCALAR_REGISTRY[GlobalID] = ScalarDefinition(
    # Use the same name/description/parse_literal from GraphQLID
    name=GraphQLID.name,
    description=GraphQLID.description,
    parse_literal=lambda v: GlobalID.from_id(GraphQLID.parse_literal(v)),
    parse_value=GlobalID.from_id,
    serialize=str,
)


@strawberry.interface(description="An object with a Globally Unique ID")  # type:ignore
class Node(Generic[NodeType]):
    @strawberry.field(description="The Globally Unique ID of this object")
    @classmethod
    def id(cls, info: Info, root: "Node[NodeType]") -> GlobalID:  # noqa:A003
        type_name = info.path.typename
        assert type_name

        node_id = cls.resolve_id(info, root)
        # We are testing str first because the resolver is expected to return this,
        # and is_awaitable has a lot more overhead.
        if isinstance(node_id, str):
            return GlobalID(type_name=type_name, node_id=node_id)
        elif _is_awaitable(info, node_id):
            return GlobalID.from_node_id_async(type_name=type_name, value=node_id)  # type:ignore

        raise AssertionError(f"expected either str or Awaitable, found: {repr(node_id)}")

    @classmethod
    def resolve_id(
        cls,
        info: Info,
        root: "Node[NodeType]",
    ) -> AwaitableOrValue[Any]:
        raise NotImplementedError

    @classmethod
    def resolve_node(
        cls,
        info: Info,
        node_id: Union[str, GlobalID],
    ) -> AwaitableOrValue[Optional[NodeType]]:
        raise NotImplementedError

    @classmethod
    def resolve_nodes(
        cls,
        info: Info,
        node_ids: Optional[Iterable[Union[str, GlobalID]]] = None,
    ) -> AwaitableOrValue[Iterable[NodeType]]:
        raise NotImplementedError

    @classmethod
    def get_connection_resolver(
        cls,
        info: Info,
        *,
        before: Optional[str] = None,
        after: Optional[str] = None,
        first: Optional[int] = None,
        last: Optional[int] = None,
    ) -> Callable[[Iterable[NodeType]], AwaitableOrValue["Connection[NodeType]"]]:
        return Connection.from_nodes_resolver(  # type:ignore
            before=before,
            after=after,
            first=first,
            last=last,
        )


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
            after_type, after_parsed = _from_base64(after)
            assert after_type == _connection_typename
            start = max(start, int(after_parsed))
        if before:
            before_type, before_parsed = _from_base64(before)
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
            Edge(cursor=_to_base64(_connection_typename, start + i), node=v)
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
        def resolver(nodes: Iterable[NodeType]):
            return cls.from_nodes(nodes, before=before, after=after, first=first, last=last)

        return resolver


class NodeField(StrawberryField):
    def __init__(self, *args, **kwargs):
        self.node_type = kwargs.pop("node_type", None)

        super().__init__(*args, **kwargs)

        self.node_resolver = StrawberryResolver(self.default_resolver)
        if not self.base_resolver:
            self(self.node_resolver)

    def __call__(self, resolver):
        if self.node_type is not None and not isinstance(resolver, StrawberryResolver):
            resolver.__annotations__["return"] = self.node_type  # type:ignore

        return super().__call__(resolver)

    def default_resolver(
        self,
        info: Info,
        id: Annotated[  # noqa:A002
            GlobalID,
            strawberry.argument(description="The ID of the object."),
        ],
    ):
        return id.resolve_node(info)


class ConnectionField(StrawberryField):
    def __init__(self, *args, **kwargs):
        self.node_type = kwargs.pop("node_type", None)

        super().__init__(*args, **kwargs)

        self.connection_resolver = StrawberryResolver(Connection.from_nodes_resolver)
        if not self.base_resolver:
            self(self.connection_resolver)

    def __call__(self, resolver):
        if self.node_type is not None and not isinstance(resolver, StrawberryResolver):
            resolver.__annotations__["return"] = Connection[self.node_type]  # type:ignore

        return super().__call__(resolver)

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
        type_def = info.return_type._type_definition  # type:ignore
        assert isinstance(type_def, TypeDefinition)
        field_type = cast(Node, type_def.type_var_map[NodeType])

        # If self.base_resolver is our connection_resolver, let super handle it
        if self.base_resolver is self.connection_resolver:
            nodes = field_type.resolve_nodes(info)
        else:
            # If base_resolver is not self.conn_resolver, then it is defined to something
            assert self.base_resolver

            default_args = ["before", "after", "first", "last"]
            base_kwargs = {k: v for k, v in kwargs.items() if k not in default_args}
            kwargs = {k: v for k, v in kwargs.items() if k in default_args}

            nodes = self.base_resolver(*args, **base_kwargs)

        if nodes is None:
            return nodes

        resolver = field_type.get_connection_resolver(info, **kwargs)

        if _is_awaitable(info, nodes):

            async def _async_resolver(res):
                res = resolver(await res)
                if _is_awaitable(info, res):
                    return await res  # type:ignore
                return res

            return _async_resolver

        return resolver(nodes)  # type:ignore


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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    f = NodeField(
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
        node_type=node_type,
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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
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
    node_type: Optional[Any] = None,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    f = ConnectionField(
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
        node_type=node_type,
    )
    if resolver is not None:
        f = f(resolver)
    return f
