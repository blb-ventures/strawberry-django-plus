from typing import TYPE_CHECKING

import strawberry_django  # noqa: TCH002
from strawberry import (
    ID,
    UNSET,
    BasePermission,
    LazyType,
    Private,
    Schema,
    argument,
    auto,
    directive,
    enum,
    experimental,
    federation,
    field,
    input,
    interface,
    lazy,
    mutation,
    relay,
    scalar,
    schema_directive,
    subscription,
    type,
    union,
)
from strawberry.relay import (
    Connection,
    Node,
    connection,
    node,
)

from strawberry_django_plus.descriptors import model_cached_property, model_property
from strawberry_django_plus.types import (
    ListInput,
    NodeInput,
    NodeInputPartial,
    OperationInfo,
    OperationMessage,
)
from strawberry_django_plus.utils import aio, resolvers

from . import django

if TYPE_CHECKING:
    auto = strawberry_django.auto  # noqa: F811

__all__ = [
    # strawberry
    "BasePermission",
    "ID",
    "UNSET",
    "LazyType",
    "lazy",
    "Private",
    "Schema",
    "argument",
    "auto",
    "directive",
    "enum",
    "experimental",
    "federation",
    "field",
    "input",
    "interface",
    "mutation",
    "scalar",
    "schema_directive",
    "subscription",
    "type",
    "union",
    # relay
    "relay",
    "Node",
    "Connection",
    "node",
    "connection",
    # strawberry_django_plus
    "django",
    "NodeInput",
    "NodeInputPartial",
    "ListInput",
    "OperationInfo",
    "OperationMessage",
    "model_cached_property",
    "model_property",
    "resolvers",
    "aio",
    "relay",
]
