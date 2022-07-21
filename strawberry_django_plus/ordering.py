from typing import Callable, Optional, Sequence, Type, TypeVar, cast

from django.db.models.base import Model
import strawberry
from strawberry import UNSET
from strawberry.field import StrawberryField
from strawberry.utils.typing import __dataclass_transform__
from strawberry_django.fields.field import field as _field
from strawberry_django.ordering import Ordering

from strawberry_django_plus.utils.typing import is_auto

from . import field
from .relay import connection, node

_T = TypeVar("_T")


@__dataclass_transform__(
    order_default=True,
    field_descriptors=(
        StrawberryField,
        _field,
        node,
        connection,
        field.field,
        field.node,
        field.connection,
    ),
)
def order(  # noqa:A001
    model: Type[Model],
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    directives: Optional[Sequence[object]] = (),
) -> Callable[[_T], _T]:
    def wrapper(cls):
        for fname, type_ in cls.__annotations__.items():
            if is_auto(type_):
                type_ = Ordering

            cls.__annotations__[fname] = Optional[type_]
            setattr(cls, fname, UNSET)

        return strawberry.input(
            cls,
            name=cast(str, name),
            description=cast(str, description),
            directives=directives,
        )

    return wrapper
