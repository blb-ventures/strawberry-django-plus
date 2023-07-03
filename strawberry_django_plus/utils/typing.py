from typing import TYPE_CHECKING, Any, Dict, Iterable, Sequence, TypeVar, Union

from django.contrib.auth.base_user import AbstractBaseUser
from graphql.type.definition import GraphQLResolveInfo
from strawberry.auto import StrawberryAuto
from strawberry.django.context import StrawberryDjangoContext
from strawberry.types.info import Info
from typing_extensions import TypeAlias

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser

_T = TypeVar("_T")

DictTree: TypeAlias = Dict[str, "DictTree"]
TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]
TypeOrIterable: TypeAlias = Union[_T, Iterable[_T]]
UserType: TypeAlias = Union[AbstractBaseUser, "AnonymousUser"]
ResolverInfo: TypeAlias = Union[Info[StrawberryDjangoContext, Any], GraphQLResolveInfo]


def is_auto(type_: Any) -> bool:
    if isinstance(type_, str):
        return type_ in ["strawberry.auto", "gql.auto", "auto"]

    return isinstance(type_, StrawberryAuto)
