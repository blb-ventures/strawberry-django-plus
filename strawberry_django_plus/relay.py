import base64
import inspect
from typing import (
    Any,
    Callable,
    Collection,
    Dict,
    Generic,
    List,
    Literal,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
    runtime_checkable,
)

import makefun
import strawberry
from strawberry.arguments import UNSET
from strawberry.field import StrawberryField
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import StrawberryContainer
from strawberry.types import Info
from strawberry.types.fields.resolver import StrawberryResolver
from typing_extensions import Annotated

_T = TypeVar("_T")
_N = TypeVar("_N")
_connection_type = "arrayconnection"
_nodes: Dict[str, "Node"] = {}


def _to_b64(type_: str, value: str) -> str:
    return base64.b64encode(f"{type_}:{value}".encode()).decode()


def _from_b64(value: str) -> Tuple[str, str]:
    type_, v = base64.b64decode(value.encode()).decode().split(":")
    return type_, v


@runtime_checkable
class _Countable(Protocol):
    def count(self) -> int:
        ...


@strawberry.interface(description="An object with a Globally Unique ID")
class Node(Generic[_T]):
    # FIXME: This should have a resolver to convert it to base64
    id: strawberry.ID  # noqa:A003

    @classmethod
    def is_type_of(cls, other: _T, info: Info) -> bool:
        # FIXME: How to properly check this?
        return True

    @classmethod
    def get_node(cls, node_id: Any) -> Optional[_T]:
        raise NotImplementedError

    @classmethod
    def get_edges(cls) -> Collection[_T]:
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
class Edge(Generic[_N]):
    cursor: str = strawberry.field(
        description="A cursor for use in pagination",
    )
    node: _N = strawberry.field(
        description="The item at the end of the edge",
    )


@strawberry.type(description="A connection to a list of items.")
class Connection(Generic[_N]):
    page_info: PageInfo = strawberry.field(
        description="Pagination data for this connection",
    )
    edges: List[Edge[_N]] = strawberry.field(
        description="Contains the nodes in this connection",
    )
    total_count: int = strawberry.field(
        description="Total quantity of existing nodes",
    )


class ConnectionField(StrawberryField):
    def __call__(
        self,
        resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod, None] = None,
    ) -> "ConnectionField":
        conn_resolver = connection_resolver(self)

        if resolver is None:

            @makefun.wraps(conn_resolver, remove_args=["__nodes", "kwargs"])
            def wrapper(*args, **kwargs):
                return conn_resolver(*args, **kwargs)

        else:
            params = {
                p: v
                for p, v in inspect.signature(conn_resolver).parameters.items()
                if p not in ["__nodes", "kwargs"]
            }

            @makefun.wraps(resolver, append_args=params.values())
            def wrapper(*args, **kwargs):
                conn_kwargs = {p: kwargs.pop(p, None) for p in params}
                nodes = resolver(*args, **kwargs)  # type:ignore
                return conn_resolver(__nodes=nodes, **conn_kwargs)

        return cast("ConnectionField", super().__call__(wrapper))


def node_resolver(field: StrawberryField):
    def resolver(
        id: Annotated[  # noqa:A002
            strawberry.ID,
            strawberry.argument(description="The ID of the object."),
        ]
    ):
        type_, node_id = _from_b64(id)
        field_type = field.type
        while isinstance(field_type, StrawberryContainer):
            field_type = field_type.of_type
        # FIXME: isinstance(field.type, Node) is not working
        return field_type.get_node(node_id)  # type:ignore

    return resolver


def connection_resolver(field: StrawberryField):
    def resolver(
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
        __nodes: Optional[Collection[Any]] = None,
        **kwargs,
    ):
        nodes = __nodes

        if nodes is None:
            # The user's resolver did not pass a list of edges, retrieve them from the field.
            field_type = field.type_annotation.annotation.__args__[0]
            # FIXME: isinstance(field.type, Node) is not working
            nodes = cast(Collection[Any], field_type.get_edges())  # type:ignore

        if isinstance(nodes, _Countable):
            # Support ORMs that define .count() (e.g. django)
            total_count = nodes.count()
        else:
            total_count = len(nodes)

        # https://relay.dev/graphql/connections.htm#sec-Pagination-algorithm
        start = 0
        end = total_count

        if after:
            after_type, after = _from_b64(after)
            assert after_type == _connection_type
            start = max(start, int(after))
        if before:
            before_type, before = _from_b64(before)
            assert before_type == _connection_type
            end = min(end, int(before))

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
                cursor=_to_b64(_connection_type, str(start + i)),
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

        return Connection(
            edges=edges,
            page_info=page_info,
            total_count=total_count,
        )

    return resolver


def node(
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Any:
    f = strawberry.field(
        name=name,
        is_subscription=is_subscription,
        description=description,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
    )
    resolver = node_resolver(f)
    return f(resolver)


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
) -> StrawberryField:
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
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    return ConnectionField(
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
    )(resolver)
