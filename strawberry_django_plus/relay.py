# This should go to its own module or be contributed back to strawberry

import base64
from typing import (
    Any,
    Awaitable,
    Callable,
    Collection,
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
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument
from strawberry.field import StrawberryField
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import StrawberryContainer, StrawberryType
from strawberry.types import Info
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Annotated

_T = TypeVar("_T")
NodeType = TypeVar("NodeType")

_connection_typename = "arrayconnection"


def _to_b64(type_: str, value: str) -> str:
    return base64.b64encode(f"{type_}:{value}".encode()).decode()


def _from_b64(value: str) -> Tuple[str, str]:
    type_, v = base64.b64decode(value.encode()).decode().split(":")
    return type_, v


async def _async_resolver(ret: Awaitable[_T], callback: Optional[Callable[[_T], Any]]):
    resolved = await ret
    if callback is not None:
        resolved = callback(resolved)
    return resolved


@runtime_checkable
class _Countable(Protocol):
    def count(self) -> int:
        ...


@strawberry.interface(description="An object with a Globally Unique ID")  # type:ignore
class Node(Generic[NodeType]):
    @strawberry.field(description="The Globally Unique ID of this object")
    def id(self, info: Info) -> strawberry.ID:  # noqa:A003
        type_ = info.path.typename
        assert type_

        # self might not be an instance of Node in case of ORMs
        id_getter = getattr(self, "get_node_id", None)
        if id_getter is None:
            # but in this case, the field must implement it
            id_getter = getattr(info._field, "get_node_id", None)

        assert id_getter
        node_id = id_getter(info, self)

        # We are testing str first because the resolver is expected to return this,
        # and is_awaitable has a lot more overhead.
        if isinstance(node_id, str):
            return cast(strawberry.ID, _to_b64(type_, node_id))
        elif info._raw_info.is_awaitable(node_id):
            return _async_resolver(
                node_id,
                lambda resolved: _to_b64(type_, resolved),
            )  # type:ignore

        raise AssertionError(f"expected either str or Awaitable, found: {repr(node_id)}")

    @classmethod
    def get_edges(cls, info: Info) -> AwaitableOrValue[Iterable[NodeType]]:
        raise NotImplementedError

    @classmethod
    def get_node(cls, info: Info, node_id: Any) -> Optional[AwaitableOrValue[NodeType]]:
        raise NotImplementedError

    def get_node_id(self, info: Info, source: Any) -> AwaitableOrValue[str]:
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


class NodeField(StrawberryField):
    def resolve_node(self, info: Info, node_id: str) -> Any:
        field_type = self.type
        while isinstance(field_type, StrawberryContainer):
            field_type = field_type.of_type
        return field_type.get_node(info, node_id)  # type:ignore


class ConnectionField(StrawberryField):
    conn_resolver: StrawberryResolver

    @property
    def arguments(self) -> List[StrawberryArgument]:
        if not hasattr(self, "conn_resolver"):
            self.conn_resolver = StrawberryResolver(connection_resolver(self))
        return super().arguments + cast(List[StrawberryArgument], self.conn_resolver.arguments)

    @property
    def type(self) -> Union[StrawberryType, type]:  # noqa:A003
        if isinstance(self.type_annotation, StrawberryAnnotation):
            return self.type_annotation.resolve()

        return self.type_annotation

    @type.setter
    def type(self, type_: Any) -> None:  # noqa:A003
        self.type_annotation = type_

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        if self.base_resolver:
            conn_args = ["before", "after", "first", "last"]
            resolver_kwargs = {k: v for k, v in kwargs.items() if k not in conn_args}
            kwargs = {k: v for k, v in kwargs.items() if k in conn_args}
            edges = self.base_resolver(*args, **resolver_kwargs)
        else:
            edges = self.resolve_edges(info)

        if edges is None:
            return edges

        return self.resolve_connection(info, edges, **kwargs)

    def resolve_edges(self, info: Info) -> Collection[Any]:
        field_type = self.type_annotation.annotation.__args__[0]
        return field_type.get_edges(info)

    def resolve_connection(
        self,
        info: Info,
        edges: AwaitableOrValue[Iterable[_T]],
        **kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Connection[_T]]:
        if info._raw_info.is_awaitable(edges):
            return self.async_resolve_connection(info, edges, **kwargs)  # type:ignore
        return self.conn_resolver(**kwargs)(edges)  # type:ignore

    async def async_resolve_connection(
        self,
        info: Info,
        edges: Awaitable[Iterable[_T]],
        **kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Connection[_T]]:
        return self.resolve_connection(info, await edges, **kwargs)


def node_resolver(field: NodeField):
    def resolver(
        info: Info,
        id: Annotated[  # noqa:A002
            strawberry.ID,
            strawberry.argument(description="The ID of the object."),
        ],
    ):
        type_, node_id = _from_b64(id)
        return field.resolve_node(info, node_id)

    return resolver


def connection_resolver(field: ConnectionField):
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
    ):
        def get_conn(nodes: Iterable[Any]):
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

            return Connection(
                edges=edges,
                page_info=page_info,
                total_count=total_count,
            )

        return get_conn

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
    base_field: Type[NodeField] = NodeField,
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
