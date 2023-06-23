# This should go to its own module or be contributed back to strawberry
import abc
import base64
import dataclasses
import functools
import inspect
import itertools
import math
import sys
import uuid
import warnings
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    ForwardRef,
    Generic,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Sized,
    Tuple,
    Type,
    TypeVar,
    Union,
    _eval_type,  # type: ignore
    cast,
    get_args,
    get_origin,
    overload,
)

import strawberry
from graphql import GraphQLID
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import StrawberryArgument
from strawberry.custom_scalar import ScalarDefinition
from strawberry.extensions.field_extension import FieldExtension
from strawberry.field import StrawberryField
from strawberry.lazy_type import LazyType
from strawberry.permission import BasePermission
from strawberry.schema.types.scalar import DEFAULT_SCALAR_REGISTRY
from strawberry.type import (
    StrawberryContainer,
    StrawberryList,
    StrawberryOptional,
    get_object_definition,
)
from strawberry.types import Info
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.types import StrawberryObjectDefinition
from strawberry.union import StrawberryUnion
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry.utils.str_converters import to_camel_case
from typing_extensions import Annotated

from .settings import config
from .utils import aio

__all__ = [
    "Connection",
    "ConnectionField",
    "Edge",
    "GlobalID",
    "GlobalIDValueError",
    "Node",
    "NodeField",
    "NodeType",
    "PageInfo",
    "RelayField",
    "from_base64",
    "to_base64",
    "node",
    "connection",
]

_T = TypeVar("_T")
_R = TypeVar("_R")
connection_typename = "arrayconnection"

NodeType = TypeVar("NodeType", bound="Node")


def from_base64(value: str) -> Tuple[str, str]:
    """Parse the base64 encoded relay value.

    Args:
        value:
            The value to be parsed

    Returns:
        A tuple of (TypeName, NodeID).

    Raises:
        ValueError:
            If the value is not in the expected format

    """
    try:
        res = base64.b64decode(value.encode()).decode().split(":", 1)
    except Exception as e:  # noqa: BLE001
        raise ValueError(str(e)) from e

    if len(res) != 2:  # noqa: PLR2004
        raise ValueError(f"{res} expected to contain only 2 items")

    return res[0], res[1]


def to_base64(type_: Union[str, type, StrawberryObjectDefinition], node_id: Any) -> str:
    """Encode the type name and node id to a base64 string.

    Args:
        type_:
            The GraphQL type, type definition or type name.
        node_id:
            The node id itself

    Returns:
        A tuple of (TypeName, NodeID).

    Raises:
        ValueError:
            If the value is not a valid GraphQL type or name

    """
    try:
        if isinstance(type_, str):
            type_name = type_
        elif isinstance(type_, StrawberryObjectDefinition):
            type_name = type_.name
        elif isinstance(type_, type):
            type_def = get_object_definition(type_, strict=True)
            type_name = type_def.name
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{type_} is not a valid GraphQL type or name") from e

    return base64.b64encode(f"{type_name}:{node_id}".encode()).decode()


class GlobalIDValueError(ValueError):
    """GlobalID value error, usually related to parsing or serialization."""


@dataclasses.dataclass(order=True, frozen=True)
class GlobalID:
    """Global ID for relay types.

    Different from `strawberry.ID`, this ID wraps the original object ID in a string
    that contains both its GraphQL type name and the ID itself, and encodes it
    to a base64_ string.

    This object contains helpers to work with that, including method to retrieve
    the python object type or even the encoded node itself.

    Attributes:
        type_name:
            The type name part of the id
        node_id:
            The node id part of the id

    .. _base64:
        https://en.wikipedia.org/wiki/Base64

    """

    _nodes_cache: ClassVar[Dict[Tuple[int, str], Type["Node"]]] = {}

    type_name: str
    node_id: str

    def __post_init__(self):
        if not isinstance(self.type_name, str):
            raise GlobalIDValueError(
                f"type_name is expected to be a string, found {self.type_name!r}",
            )
        if not isinstance(self.node_id, str):
            raise GlobalIDValueError(
                f"node_id is expected to be a string, found {self.node_id!r}",
            )

    def __str__(self):
        return to_base64(self.type_name, self.node_id)

    @classmethod
    def from_id(cls, value: Union[str, strawberry.ID]):
        """Create a new GlobalID from parsing the given value.

        Args:
            value:
                The value to be parsed, as a base64 string in the "TypeName:NodeID" format

        Returns:
            An instance of GLobalID

        Raises:
            GlobalIDValueError:
                If the value is not in a GLobalID format

        """
        try:
            type_name, node_id = from_base64(value)
        except ValueError as e:
            raise GlobalIDValueError(str(e)) from e

        return cls(type_name=type_name, node_id=node_id)

    def resolve_type(self, info: Info) -> Type["Node"]:
        """Resolve the internal type name to its type itself.

        Args:
            info:
                The strawberry execution info resolve the type name from

        Returns:
            The resolved GraphQL type for the execution info

        """
        schema = info.schema
        # Put the schema in the key so that different schemas can have different types
        key = (id(schema), self.type_name)
        origin = self._nodes_cache.get(key)

        if origin is None:
            type_def = info.schema.get_type_by_name(self.type_name)
            assert isinstance(type_def, StrawberryObjectDefinition)
            origin = type_def.origin
            if isinstance(origin, LazyType):
                origin = origin.resolve_type()
            assert issubclass(origin, Node)
            self._nodes_cache[key] = origin

        return origin

    @overload
    def resolve_node(
        self,
        info: Info,
        *,
        required: Literal[True] = ...,
        ensure_type: Type[_T],
    ) -> _T:
        ...

    @overload
    def resolve_node(
        self,
        info: Info,
        *,
        required: Literal[True],
        ensure_type: None = ...,
    ) -> "Node":
        ...

    @overload
    def resolve_node(
        self,
        info: Info,
        *,
        required: bool = ...,
        ensure_type: None = ...,
    ) -> Optional["Node"]:
        ...

    def resolve_node(self, info, *, required=False, ensure_type=None) -> Any:
        """Resolve the type name and node id info to the node itself.

        Tip: When you know the expected type, calling `ensure_type` should help
        not only to enforce it, but also help with typing since it will know that,
        if this function returns successfully, the retval should be of that
        type and not `Node`.

        Args:
            info:
                The strawberry execution info resolve the type name from
            required:
                If the value is required to exist. Note that asking to ensure
                the type automatically makes required true.
            ensure_type:
                Optionally check if the returned node is really an instance
                of this type.

        Returns:
            The resolved node

        Raises:
            TypeError:
                If ensure_type was provided and the type is not an instance of it

        """
        n_type = self.resolve_type(info)
        node = n_type.resolve_node(
            self.node_id,
            info=info,
            required=required or ensure_type is not None,
        )

        if node is not None and ensure_type is not None:
            origin = get_origin(ensure_type)
            if origin and issubclass(origin, Awaitable):
                warnings.warn("use `aresolve_node` instead", DeprecationWarning, stacklevel=1)
                ensure_type = get_args(ensure_type)[0]
            return aio.resolve(node, lambda n: n, info=info, ensure_type=ensure_type)

        return node

    @overload
    async def aresolve_node(
        self,
        info: Info,
        *,
        required: Literal[True] = ...,
        ensure_type: Type[_T],
    ) -> _T:
        ...

    @overload
    async def aresolve_node(
        self,
        info: Info,
        *,
        required: Literal[True],
        ensure_type: None = ...,
    ) -> "Node":
        ...

    @overload
    async def aresolve_node(
        self,
        info: Info,
        *,
        required: bool = ...,
        ensure_type: None = ...,
    ) -> Optional["Node"]:
        ...

    async def aresolve_node(self, info, *, required=False, ensure_type=None) -> Any:
        """Resolve the type name and node id info to the node itself.

        Tip: When you know the expected type, calling `ensure_type` should help
        not only to enforce it, but also help with typing since it will know that,
        if this function returns successfully, the retval should be of that
        type and not `Node`.

        Args:
            info:
                The strawberry execution info resolve the type name from
            required:
                If the value is required to exist. Note that asking to ensure
                the type automatically makes required true.
            ensure_type:
                Optionally check if the returned node is really an instance
                of this type.

        Returns:
            The resolved node

        Raises:
            TypeError:
                If ensure_type was provided and the type is not an instance of it

        """
        return await cast(
            Awaitable,
            self.resolve_node(info, required=required, ensure_type=ensure_type),
        )


# Register our GlobalID scalar
DEFAULT_SCALAR_REGISTRY[GlobalID] = ScalarDefinition(
    # Use the same name/description/parse_literal from GraphQLID as relay
    # specs expect this type to be "ID".
    name="GlobalID",
    description=GraphQLID.description,
    parse_literal=lambda v, vars=None: GlobalID.from_id(  # noqa: A002
        GraphQLID.parse_literal(v, vars),
    ),
    parse_value=GlobalID.from_id,
    serialize=str,
    specified_by_url="https://relay.dev/graphql/objectidentification.htm",
)


@strawberry.interface(description="An object with a Globally Unique ID")
class Node(abc.ABC):
    """Node interface for GraphQL types.

    All types that are relay ready should inherit from this interface and
    implement the following methods.

    Attributes:
        id_attr:
            (Optional) Define id field of node

    Methods:
        resolve_id:
            (Optional) Called to resolve the node's id.
            By default it returns `getattr(node, getattr(node, 'id_attr'. 'id'))`
            to use the one provided when creating the node itself.
        resolve_node:
            Called to retrieve a node given its id
        resolve_nodes:
            Called to retrieve an iterable of node given their ids
        resolve_connection:
            (Optional) Called to resolve a `Connection` to this node. Override
            this to modify how the connection is resolved.

    """

    @strawberry.field(description="The Globally Unique ID of this object")
    @classmethod
    def id(cls, root: "Node", info: Info) -> GlobalID:  # noqa: A003
        # FIXME: We want to support both integration objects that doesn't define a resolve_id
        # and also the ones that does override it. Is there a better way of handling this?
        if isinstance(root, Node):
            resolve_id = root.__class__.resolve_id
        else:
            # Try to use a custom resolve_id from the type itself. If it doesn't define one,
            # fallback to cls.resolve_id
            try:
                parent_type = info._raw_info.parent_type
                type_def = info.schema.get_type_by_name(parent_type.name)
                if not isinstance(type_def, StrawberryObjectDefinition):
                    raise TypeError  # noqa: TRY301

                resolve_id = cast(Node, type_def.origin).resolve_id
            except (RuntimeError, AttributeError):
                resolve_id = cls.resolve_id

        node_id = resolve_id(root, info=info)
        resolve_typename = (
            root.__class__.resolve_typename if isinstance(root, Node) else cls.resolve_typename
        )
        type_name = resolve_typename(root, info)
        assert type_name

        if isinstance(node_id, str):
            # str is the default and is faster to check for it than is_awaitable
            return GlobalID(type_name=type_name, node_id=node_id)

        if isinstance(node_id, (int, uuid.UUID)):
            # those are very common ids and are safe to convert to str
            return GlobalID(type_name=type_name, node_id=str(node_id))

        if aio.is_awaitable(node_id, info=info):
            return aio.resolve_async(  # type: ignore
                node_id,
                lambda resolved: GlobalID(type_name=type_name, node_id=resolved),
                info=info,
            )

        # If node_id is not str, GlobalID will raise an error for us
        return GlobalID(type_name=type_name, node_id=cast(str, node_id))

    @classmethod
    def resolve_id(
        cls: Type[NodeType],
        root: NodeType,
        *,
        info: Optional[Info] = None,
    ) -> AwaitableOrValue[str]:
        """Resolve the node id.

        By default this returns `getattr(node, getattr(node, 'id_attr'. 'id'))`.
        Override this to return something else.

        Args:
            info:
                The strawberry execution info resolve the type name from
            root:
                The node to resolve

        Returns:
            The resolved id (which is expected to be str)

        """
        id_attr = getattr(cls, "id_attr", "id")
        return getattr(root, id_attr)

    @classmethod
    def resolve_typename(cls: Type[NodeType], root: NodeType, info: Info):
        return info.path.typename

    @classmethod
    @abc.abstractmethod
    def resolve_nodes(
        cls: Type[NodeType],
        *,
        info: Optional[Info] = None,
        node_ids: Optional[Iterable[str]] = None,
    ) -> AwaitableOrValue[Iterable[NodeType]]:
        """Resolve a list of nodes.

        This method *should* be defined by anyone implementing the `Node` interface.

        Args:
            info:
                The strawberry execution info resolve the type name from
            node_ids:
                Optional list of ids that, when provided, should be used to filter
                the results to only contain the nodes of those ids. When empty,
                all nodes of this type shall be returned.

        Returns:
            An iterable of resolved nodes.

        """
        raise NotImplementedError  # pragma:nocover

    @overload
    @classmethod
    @abc.abstractmethod
    def resolve_node(
        cls: Type[NodeType],
        node_id: str,
        *,
        info: Optional[Info] = ...,
        required: Literal[True],
    ) -> AwaitableOrValue[NodeType]:
        ...

    @overload
    @classmethod
    @abc.abstractmethod
    def resolve_node(
        cls: Type[NodeType],
        node_id: str,
        *,
        info: Optional[Info] = ...,
        required: bool = ...,
    ) -> AwaitableOrValue[Optional[NodeType]]:
        ...

    @classmethod
    @abc.abstractmethod
    def resolve_node(
        cls,
        node_id: str,
        *,
        info: Optional[Info] = None,
        required: bool = False,
    ):
        """Resolve a node given its id.

        This method *should* be defined by anyone implementing the `Node` interface.

        Args:
            info:
                The strawberry execution info resolve the type name from
            node_id:
                The id of the node to be retrieved
            required:
                if the node is required or not to exist. If not, then None should
                be returned if it doesn't exist. Otherwise an exception should be raised.

        Returns:
            The resolved node or None if it was not found

        """
        raise NotImplementedError  # pragma:nocover


@strawberry.type(description="Information to aid in pagination.")
class PageInfo:
    """Information to aid in pagination.

    Attributes:
        has_next_page:
            When paginating forwards, are there more items?
        has_previous_page:
            When paginating backwards, are there more items?
        start_cursor:
            When paginating backwards, the cursor to continue
        end_cursor:
            When paginating forwards, the cursor to continue

    """

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
    """An edge in a connection.

    Attributes:
        cursor:
            A cursor for use in pagination
        node:
            The item at the end of the edge

    """

    cursor: str = strawberry.field(
        description="A cursor for use in pagination",
    )
    node: NodeType = strawberry.field(
        description="The item at the end of the edge",
    )

    @classmethod
    def from_node(cls, node: NodeType, *, cursor: Any = None):
        return cls(cursor=to_base64(connection_typename, cursor), node=node)


@strawberry.type(description="A connection to a list of items.")
class Connection(Generic[NodeType]):
    """A connection to a list of items.

    Attributes:
        page_info:
            Pagination data for this connection
        edges:
            Contains the nodes in this connection
        total_count:
            Total quantity of existing nodes

    """

    page_info: PageInfo = strawberry.field(
        description="Pagination data for this connection",
    )
    edges: List[Edge[NodeType]] = strawberry.field(
        description="Contains the nodes in this connection",
    )
    total_count: Optional[int] = strawberry.field(
        description="Total quantity of existing nodes",
        default=None,
    )

    @classmethod
    def from_nodes(
        cls,
        nodes: Iterable[NodeType],
        *,
        info: Optional[Info] = None,
        total_count: Optional[int] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        first: Optional[int] = None,
        last: Optional[int] = None,
        **kwargs,
    ):
        """Resolve a connection from the list of nodes.

        This uses the described Relay Pagination algorithm_

        Args:
            info:
                The strawberry execution info resolve the type name from
            nodes:
                An iterable of nodes to transform to a connection
            total_count:
                Optionally provide a total count so that the connection
                doesn't have to calculate it. Might be useful for some ORMs
                for performance reasons.
            before:
                Returns the items in the list that come before the specified cursor
            after:
                Returns the items in the list that come after the specified cursor
            first:
                Returns the first n items from the list
            last:
                Returns the items in the list that come after the specified cursor
            kwargs:
                Extra keyword arguments that are ignored

        Returns:
            The resolved `Connection`

        .. _Relay Pagination algorithm:
            https://relay.dev/graphql/connections.htm#sec-Pagination-algorithm

        """
        if total_count is None:
            # Support ORMs that define .count() (e.g. django)
            try:
                total_count = int(nodes.count())  # type: ignore
            except (AttributeError, ValueError, TypeError):
                if isinstance(nodes, Sized):
                    total_count = len(nodes)

        max_results = config.RELAY_MAX_RESULTS
        if max_results is None:
            max_results = math.inf

        start = 0
        end = total_count if total_count is not None else math.inf

        if after:
            after_type, after_parsed = from_base64(after)
            assert after_type == connection_typename
            start = int(after_parsed) + 1
        if before:
            before_type, before_parsed = from_base64(before)
            assert before_type == connection_typename
            end = int(before_parsed)

        if isinstance(first, int):
            if first < 0:
                raise ValueError("Argument 'first' must be a non-negative integer.")

            if first > max_results:
                raise ValueError(f"Argument 'first' cannot be higher than {max_results}.")

            end = min(end, start + first)
        if isinstance(last, int):
            if last < 0:
                raise ValueError("Argument 'last' must be a non-negative integer.")

            if last > max_results:
                raise ValueError(f"Argument 'last' cannot be higher than {max_results}.")

            if end == math.inf:
                # This is the worst case, someone is asking for last without specifying an
                # after argument. We basically want the total_count - last in here. If we don't
                # have the total_count (e.g. because nodes is a generator), the slice below
                # will have to iterate over it all, so we can transform it to a list here to
                # retrieve that total_count right now
                if total_count is None:
                    nodes = list(nodes)
                    total_count = len(nodes)

                start = max(start, total_count - last)
                end = None
            else:
                start = max(start, end - last)

        # If at this point end is still inf, consider it to be start + max_results
        if end == math.inf:
            end = start + max_results

        expected = end - start if end is not None else abs(start)
        # If no parameters are given, end could be total_results at this point.
        # Make sure we don't exceed max_results in here
        if expected > max_results:
            end = start + max_results
            expected = end - start

        # Overfetch by 1 to check if we have a next result
        type_def = get_object_definition(cls, strict=True)
        field_def = type_def.get_field("edges")
        assert field_def

        field = field_def.type
        while isinstance(field, StrawberryContainer):
            field = field.of_type

        edge_class = cast(Edge[NodeType], field)
        iterator = (
            cast(Sequence, nodes)[start : end + 1 if end is not None else None]
            if hasattr(nodes, "__getitem__")
            else itertools.islice(
                nodes,
                cast(int, start),
                cast(int, end + 1) if end is not None else None,
            )
        )
        edges = [edge_class.from_node(v, cursor=start + i) for i, v in enumerate(iterator)]

        # Remove the overfetched result
        if len(edges) == expected + 1:
            edges = edges[:-1]
            has_next_page = True
        else:
            has_next_page = False

        page_info = PageInfo(
            start_cursor=edges[0].cursor if edges else None,
            end_cursor=edges[-1].cursor if edges else None,
            has_previous_page=start > 0,
            has_next_page=has_next_page,
        )

        return cls(
            edges=edges,
            page_info=page_info,
            total_count=total_count,
        )


class RelayField(StrawberryField):
    """Base relay field, containing utilities for both Node and Connection fields."""

    default_args: Dict[str, StrawberryArgument]

    def __init__(self, *args, **kwargs):
        default_args = getattr(self.__class__, "default_args", {})
        if isinstance(default_args, dict):
            self.default_args = default_args.copy()
        base_resolver = kwargs.pop("base_resolver", None)
        super().__init__(*args, **kwargs)
        if base_resolver:
            self.__call__(base_resolver)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        args = {
            **self.default_args,
            **{arg.python_name: arg for arg in super().arguments},
        }
        return list(args.values())

    @functools.cached_property
    def is_basic_field(self):
        return False

    @functools.cached_property
    def is_optional(self):
        return isinstance(self.type, StrawberryOptional)

    @functools.cached_property
    def is_list(self):
        type_ = self.type
        if isinstance(type_, StrawberryOptional):
            type_ = type_.of_type

        return isinstance(type_, StrawberryList)


class NodeField(RelayField):
    """Relay Node field.

    Do not instantiate this directly. Instead, use `@relay.node`

    """

    @property
    def default_args(self) -> Dict[str, StrawberryArgument]:
        if self.base_resolver:
            return {}

        if self.is_list:
            return {
                "ids": StrawberryArgument(
                    python_name="ids",
                    graphql_name=None,
                    type_annotation=StrawberryAnnotation(List[GlobalID]),
                    description="The IDs of the objects.",
                ),
            }

        return {
            "id": StrawberryArgument(
                python_name="id",
                graphql_name=None,
                type_annotation=StrawberryAnnotation(GlobalID),
                description="The ID of the object.",
            ),
        }

    def __call__(self, resolver):
        raise TypeError("NodeField cannot have a resolver, use a common field instead.")

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        if self.is_list:
            return self.resolve_nodes(source, info, args, kwargs)

        return self.resolve_node(source, info, args, kwargs)

    def resolve_node(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Optional[Node]]:
        gid = kwargs["id"]
        assert isinstance(gid, GlobalID)
        return gid.resolve_type(info).resolve_node(
            gid.node_id,
            info=info,
            required=not self.is_optional,
        )

    def resolve_nodes(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Iterable[Node]]:
        nodes_map: Dict[Type[Node], List[str]] = {}
        for gid in kwargs["ids"]:
            node_t = gid.resolve_type(info)
            nodes_map.setdefault(node_t, []).append(gid.node_id)

        if len(nodes_map) == 0:
            return []
        if len(nodes_map) > 1:
            # FIXME: Maybe we want to support this in the future?
            raise TypeError("More than one node type found...")

        node_t, ids = next(iter(nodes_map.items()))
        return node_t.resolve_nodes(info=info, node_ids=ids)


class ConnectionField(RelayField):
    """Relay Connection field.

    Do not instantiate this directly. Instead, use `@relay.connection`

    """

    default_args: Dict[str, StrawberryArgument] = {
        "before": StrawberryArgument(
            python_name="before",
            graphql_name=None,
            type_annotation=StrawberryAnnotation(Optional[str]),
            description="Returns the items in the list that come before the specified cursor.",
            default=None,
        ),
        "after": StrawberryArgument(
            python_name="after",
            graphql_name=None,
            type_annotation=StrawberryAnnotation(Optional[str]),
            description="Returns the items in the list that come after the specified cursor.",
            default=None,
        ),
        "first": StrawberryArgument(
            python_name="first",
            graphql_name=None,
            type_annotation=StrawberryAnnotation(Optional[int]),
            description="Returns the first n items from the list.",
            default=None,
        ),
        "last": StrawberryArgument(
            python_name="last",
            graphql_name=None,
            type_annotation=StrawberryAnnotation(Optional[int]),
            description="Returns the items in the list that come after the specified cursor.",
            default=None,
        ),
    }

    def __call__(self, resolver: Callable[..., Iterable[Node]]):
        if (nodes_type := resolver.__annotations__.get("return")) is not None:
            namespace = sys.modules[resolver.__module__].__dict__
            if isinstance(nodes_type, str):
                nodes_type = ForwardRef(nodes_type, is_argument=False)

            resolved = _eval_type(nodes_type, namespace, None)
            origin = get_origin(resolved)

            is_connection = origin and isinstance(origin, type) and issubclass(origin, Connection)
            is_iterable = origin and isinstance(origin, type) and issubclass(origin, Iterable)
            if not is_connection and not is_iterable:
                raise TypeError(
                    (
                        "Connection nodes resolver needs to return either a `Connection[<NodeType]`"
                        " or an Iterable like `Iterable[<NodeType>]`, `List[<NodeType>]`, etc"
                    ),
                )

            if is_iterable and not is_connection and self.type_annotation is None:
                ntype = get_args(resolved)[0]
                if isinstance(ntype, LazyType):
                    ntype = ntype.resolve_type()

                self.type_annotation = StrawberryAnnotation(
                    Connection[ntype],
                    namespace=namespace,
                )

        return super().__call__(resolver)

    @functools.cached_property
    def resolver_args(self) -> Set[str]:
        resolver = self.base_resolver
        if not resolver:
            return set()

        if isinstance(resolver, StrawberryResolver):
            resolver = resolver.wrapped_func

        return set(inspect.signature(cast(Callable, resolver)).parameters.keys())

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        if self.base_resolver is not None:
            # If base_resolver is not self.conn_resolver, then it is defined to something
            assert self.base_resolver

            resolver_args = self.resolver_args
            resolver_kwargs = {
                # Consider both args not in default args and the ones specified by the resolver,
                # in case they want to check "first"/"last"/"before"/"after"
                k: v
                for k, v in kwargs.items()
                if k in resolver_args
            }
            nodes = self.base_resolver(*args, **resolver_kwargs)
        else:
            nodes = None

        return self.resolver(source, info, args, kwargs, nodes=nodes)

    def resolver(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
        *,
        nodes: AwaitableOrValue[Optional[Union[Iterable[Node], Connection[Node]]]] = None,
    ):
        # The base_resolver might have resolved to a Connection directly
        if isinstance(nodes, Connection):
            return nodes

        return_type = info.return_type
        if isinstance(return_type, StrawberryUnion):
            candidates = [
                rt
                for rt in return_type.types
                if isinstance(rt, type) and issubclass(rt, Connection)
            ]
            if len(candidates) > 1:
                raise TypeError("Multiple connection types found in union")

            return_type = candidates[0]

        type_def = get_object_definition(return_type, strict=True)

        field_type = type_def.type_var_map[NodeType]
        if isinstance(field_type, LazyType):
            field_type = field_type.resolve_type()

        if nodes is None:
            nodes = cast(Node, field_type).resolve_nodes(info=info)

        if aio.is_awaitable(nodes, info=info):
            return aio.resolve_async(
                nodes,
                lambda resolved: self.resolver(
                    source,
                    info,
                    args,
                    kwargs,
                    nodes=resolved,
                ),
                info=info,
            )

        # Avoid info being passed twice in case the custom resolver has one
        kwargs.pop("info", None)
        return self.resolve_connection(cast(Iterable[Node], nodes), info, **kwargs)

    def resolve_connection(
        self,
        nodes: Iterable[Node],
        info: Info,
        **kwargs,
    ):
        return_type = cast(Connection[Node], info.return_type)
        return return_type.from_nodes(nodes, **kwargs)


class InputMutationField(RelayField):
    """Relay Mutation field.

    Do not instantiate this directly. Instead, use `@relay.mutation`

    """

    default_args: Dict[str, StrawberryArgument] = {}

    def __call__(self, resolver: Callable[..., Iterable[Node]]):
        name = to_camel_case(resolver.__name__)
        cap_name = name[0].upper() + name[1:]
        namespace = sys.modules[resolver.__module__].__dict__
        annotations = resolver.__annotations__
        resolver = StrawberryResolver(resolver)

        args = resolver.arguments
        type_dict: dict[str, Any] = {
            "__doc__": f"Input data for `{name}` mutation",
            "__annotations__": {},
        }
        f_types = {}
        for arg in args:
            annotation = annotations[arg.python_name]
            if get_origin(annotation) is Annotated:
                directives = tuple(
                    d for d in get_args(annotation)[1:] if hasattr(d, "__strawberry_directive__")
                )
            else:
                directives = ()

            type_dict["__annotations__"][arg.python_name] = annotation
            arg_field = strawberry.field(
                name=arg.graphql_name,
                is_subscription=arg.is_subscription,
                description=arg.description,
                default=arg.default,
                directives=directives,
            )
            arg_field.graphql_name = arg.graphql_name
            f_types[arg_field] = arg.type_annotation
            type_dict[arg.python_name] = arg_field

        # TODO: We are not creating a type for the output payload, as it is not easy to
        # do that with the typing system. Is there a way to solve that automatically?
        new_type = strawberry.input(type(f"{cap_name}Input", (), type_dict))
        self.default_args["input"] = StrawberryArgument(
            python_name="input",
            graphql_name=None,
            type_annotation=StrawberryAnnotation(new_type, namespace=namespace),
            description=type_dict["__doc__"],
        )

        # FIXME: We need to set this after strawberry.input() or else it will have problems
        # with Annotated annotations for scalar types. Find out why in the future...
        for f, annotation in f_types.items():
            f.type = annotation

        return super().__call__(resolver)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        return list(self.default_args.values())

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        assert self.base_resolver
        input_obj = kwargs.pop("input")
        return self.base_resolver(*args, **kwargs, **vars(input_obj))


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
    return NodeField(
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
        extensions=extensions or (),
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
    graphql_type: Optional[Any] = None,
    extensions: List[FieldExtension] = (),  # type: ignore
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
    graphql_type: Optional[Any] = None,
    extensions: List[FieldExtension] = (),  # type: ignore
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
    graphql_type: Optional[Any] = None,
    extensions: List[FieldExtension] = (),  # type: ignore
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
    default: Any = dataclasses.MISSING,
    default_factory: Union[Callable[..., object], object] = dataclasses.MISSING,
    metadata: Optional[Mapping[Any, Any]] = None,
    directives: Optional[Sequence[object]] = (),
    graphql_type: Optional[Any] = None,
    extensions: List[FieldExtension] = (),  # type: ignore
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
    f = ConnectionField(
        python_name=None,
        graphql_name=name,
        description=description,
        type_annotation=StrawberryAnnotation.from_annotation(graphql_type),
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        metadata=metadata,
        directives=directives or (),
        extensions=extensions or (),
    )

    if resolver is not None:
        return f(resolver)

    return f


@overload
def input_mutation(
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
    extensions: List[FieldExtension] = (),  # type: ignore
) -> _T:
    ...


@overload
def input_mutation(
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
    extensions: List[FieldExtension] = (),  # type: ignore
) -> Any:
    ...


@overload
def input_mutation(
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
    extensions: List[FieldExtension] = (),  # type: ignore
) -> InputMutationField:
    ...


def input_mutation(
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
    extensions: List[FieldExtension] = (),  # type: ignore
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init: Literal[True, False, None] = None,
) -> Any:
    """Annotate a property or a method to create an input mutation field.

    The difference from this mutation to the default one from strawberry is that
    all arguments found in the resolver will be converted to a single input type,
    named using the mutation name, capitalizing the first letter and append "Input"
    at the end. e.g. `doSomeMutation` will generate an input type `DoSomeMutationInput`.

    Examples:
        Annotating something like this:

        >>> @strawberry.type
        ... class CreateUserPayload:
        ...     user: UserType
        ...
        >>> @strawberry.mutation
        >>> class X:
        ...     @relay.input_mutation
        ...     def create_user(self, name: str, age: int) -> UserPayload:
        ...         ...

        Will create a type and an input type like

        ```
        input CreateUserInput {
            name: String!
            age: Int!
        }

        mutation {
            createUser (input: CreateUserInput!) {
                user: UserType
            }
        }
        ```

    """
    f = InputMutationField(
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
        extensions=extensions or (),
    )

    if resolver is not None:
        return f(resolver)

    return f
