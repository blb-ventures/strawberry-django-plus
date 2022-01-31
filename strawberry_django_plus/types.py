import enum
import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
    get_args,
)

from django.db import models
from django.db.models.fields.reverse_related import ForeignObjectRel
import strawberry
from strawberry.arguments import UNSET
from strawberry.custom_scalar import ScalarWrapper
from strawberry.file_uploads import Upload
from strawberry.type import StrawberryType
from strawberry_django.fields.types import (
    ManyToManyInput,
    ManyToOneInput,
    OneToManyInput,
    OneToOneInput,
    field_type_map,
    input_field_type_map,
)
from strawberry_django.fields.types import (
    resolve_model_field_type as _resolve_model_field,
)
from strawberry_django.filters import DjangoModelFilterInput, FilterLookup

from . import relay
from .utils.typing import TypeOrIterable

if TYPE_CHECKING:
    from .type import StrawberryDjangoType

try:
    from django_choices_field import IntegerChoicesField, TextChoicesField
except ImportError:
    has_choices_field = False
else:
    has_choices_field = True


_T = TypeVar("_T", bound=Union[StrawberryType, ScalarWrapper])

input_field_type_map[models.FileField] = Upload
input_field_type_map[models.ImageField] = Upload


def register(
    fields: TypeOrIterable[Type[models.Field]],
    /,
    *,
    for_input: bool = False,
) -> Callable[[_T], _T]:
    """Register types to convert `auto` fields to.

    Args:
        field:
            Type or sequence of types to register
        for_input:
            If the type should be used for input only.

    Examples:
        To define a type that should be used for `ImageField`:

        >>> @register(ImageField)
        ... @strawberry.type
        ... class SomeType:
        ...     url: str

    """

    def _wrapper(type_):
        for f in fields if isinstance(fields, Iterable) else [fields]:
            if for_input:
                input_field_type_map[f] = type_
            else:
                field_type_map[f] = type_

        return type_

    return _wrapper


@strawberry.input(description="Input of a node interface object.")
class NodeInput:
    """Set the value to the selected node.

    Tip: Override this to add more fields to update the node.

    """

    id: Optional[relay.GlobalID]  # noqa:A003


@strawberry.input(
    description=(
        "Add/remove/set the selected nodes.\n\n"
        "NOTE: If passing `set`, `add`/`remove` should not be passed together'."
    )
)
class NodeListInput:
    """Add/remove/set the selected nodes.

    Tip: Override this and define `data` as an input to be passed to `through_data`.

    """

    add: Optional[List[relay.GlobalID]] = UNSET
    remove: Optional[List[relay.GlobalID]] = UNSET
    set: Optional[List[relay.GlobalID]] = UNSET  # noqa:A003

    @property
    def data(self) -> Optional[Union[Dict[str, Any], object]]:
        return None


@strawberry.type
class OperationMessage:
    """An error that happened while executing an operation."""

    @strawberry.enum(name="OperationMessageKind")
    class Kind(enum.Enum):
        """The kind of the returned message."""

        INFO = "info"
        WARNING = "warning"
        ERROR = "error"
        PERMISSION = "permission"
        VALIDATION = "validation"

    kind: Kind = strawberry.field(description="The kind of this message.")
    message: str = strawberry.field(description="The error message.")
    field: Optional[str] = strawberry.field(
        description=(
            "The field that caused the error, or `null` if it "
            "isn't associated with any particular field."
        ),
        default=None,
    )


@strawberry.type
class OperationMessageList:
    """Multiple messages returned by an operation."""

    messages: List[OperationMessage] = strawberry.field(
        description="List of messages returned by the operation.",
    )


def resolve_model_field_type(
    model_field: Union[models.Field, ForeignObjectRel],
    django_type: "StrawberryDjangoType",
):
    """Resolve type for model field."""
    if has_choices_field and isinstance(model_field, (TextChoicesField, IntegerChoicesField)):
        field_type = model_field.choices_enum
        enum_def = getattr(field_type, "_enum_definition", None)
        if enum_def is None:
            doc = field_type.__doc__ and inspect.cleandoc(field_type.__doc__)
            enum_def = strawberry.enum(field_type, description=doc)._enum_definition
        retval = enum_def
    else:
        retval = _resolve_model_field(model_field, django_type)

    if isinstance(django_type, relay.Node):
        is_lookup = False
        if isinstance(retval, FilterLookup):
            is_lookup = True
            retval = get_args(retval)[0]

        retval = {
            strawberry.ID: relay.GlobalID,
            DjangoModelFilterInput: NodeInput,
            OneToOneInput: NodeInput,
            OneToManyInput: NodeInput,
            ManyToOneInput: NodeListInput,
            ManyToManyInput: NodeListInput,
        }.get(
            retval,  # type:ignore
            retval,
        )

        if is_lookup:
            retval = FilterLookup[retval]

    return retval
