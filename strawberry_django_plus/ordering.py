from typing import Callable, Optional, Sequence, Type, TypeVar

from django.db.models.base import Model
from strawberry.field import StrawberryField
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.utils.typing import __dataclass_transform__
from strawberry_django.fields.field import field as _field

from . import field
from .relay import connection, node
from .type import input

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
    name: str = None,
    description: str = None,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Callable[[_T], _T]:
    return input(
        model,
        name=name,
        description=description,
        directives=directives,
        partial=True,
    )
