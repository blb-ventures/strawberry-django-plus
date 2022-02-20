from typing import Any, Dict, Iterable, Sequence, TypeVar, Union

from django.contrib.auth.models import AbstractUser, AnonymousUser
from graphql.type.definition import GraphQLResolveInfo
from strawberry.django.context import StrawberryDjangoContext
from strawberry.types.info import Info
from typing_extensions import TypeAlias

_T = TypeVar("_T")

DictTree: TypeAlias = Dict[str, "DictTree"]
TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]
TypeOrIterable: TypeAlias = Union[_T, Iterable[_T]]
UserType: TypeAlias = Union[AbstractUser, AnonymousUser]
ResolverInfo: TypeAlias = Union[Info[StrawberryDjangoContext, Any], GraphQLResolveInfo]
