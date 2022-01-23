import dataclasses
from typing import Any, Generic, Iterable, Optional, Sequence, Type, TypeVar, Union

from django.contrib.auth.models import AbstractUser, AnonymousUser
from graphql.type.definition import GraphQLResolveInfo
from strawberry.django.context import StrawberryDjangoContext
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.info import Info
from typing_extensions import TypeAlias

_T = TypeVar("_T")

TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]
TypeOrIterable: TypeAlias = Union[_T, Iterable[_T]]
UserType: TypeAlias = Union[AbstractUser, AnonymousUser]
ResolverInfo: TypeAlias = Union[Info[StrawberryDjangoContext, Any], GraphQLResolveInfo]


# FIXME: Use schema_directive once it is properly typed
class SchemaDirective(Generic[_T], StrawberrySchemaDirective):
    wrap: Type[_T]
    instance: Optional[_T] = dataclasses.field(init=False)
