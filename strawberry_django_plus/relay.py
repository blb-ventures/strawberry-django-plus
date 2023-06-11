import inspect
from typing import TYPE_CHECKING, Any, Optional, Sized, cast

import strawberry
from strawberry import relay
from strawberry.relay.types import NodeIterableType
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Self

from .field import field

if TYPE_CHECKING:
    from django.db import models


@strawberry.type(name="Connection", description="A connection to a list of items.")
class ListConnectionWithTotalCount(relay.ListConnection[relay.NodeType]):
    nodes: strawberry.Private[Optional[NodeIterableType[relay.NodeType]]] = None

    @field
    def total_count(self) -> Optional[int]:
        """Total quantity of existing nodes."""
        assert self.nodes is not None
        total_count = None
        try:
            total_count = cast("models.QuerySet[models.Model]", self.nodes).count()
        except (AttributeError, ValueError, TypeError):
            if isinstance(self.nodes, Sized):
                total_count = len(self.nodes)

        return total_count

    @classmethod
    def resolve_connection(
        cls,
        nodes: NodeIterableType[relay.NodeType],
        *,
        info: Info,
        before: Optional[str] = None,
        after: Optional[str] = None,
        first: Optional[int] = None,
        last: Optional[int] = None,
        **kwargs: Any,
    ) -> AwaitableOrValue[Self]:
        conn = super().resolve_connection(
            nodes,
            info=info,
            before=before,
            after=after,
            first=first,
            last=last,
            **kwargs,
        )

        if inspect.isawaitable(conn):

            async def wrapper():
                resolved = await conn
                resolved.nodes = nodes
                return resolved

            return wrapper()

        conn = cast(Self, conn)
        conn.nodes = nodes
        return conn
