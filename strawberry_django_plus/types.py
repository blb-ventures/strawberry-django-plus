import enum
import inspect
from typing import (
    TYPE_CHECKING,
    Callable,
    Generic,
    Iterable,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
    get_args,
)

from django.db import models
from django.db.models.fields import NOT_PROVIDED
from django.db.models.fields.related import ManyToManyField
from django.db.models.fields.reverse_related import ForeignObjectRel
import strawberry
from strawberry.arguments import UNSET
from strawberry.custom_scalar import ScalarWrapper
from strawberry.file_uploads import Upload
from strawberry.type import StrawberryType
from strawberry_django.fields.types import (
    DjangoModelType,
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
from .settings import config

if TYPE_CHECKING:
    from .type import StrawberryDjangoType

try:
    from django_choices_field import IntegerChoicesField, TextChoicesField
except ImportError:
    has_choices_field = False
else:
    has_choices_field = True


_T = TypeVar("_T", bound=Union[StrawberryType, ScalarWrapper, type])

K = TypeVar("K")
D = TypeVar("D")

input_field_type_map[models.FileField] = Upload
input_field_type_map[models.ImageField] = Upload


def register(
    fields: Union[Type[models.Field], List[Type[models.Field]]],
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


@strawberry.type(
    description="Generic type for objects that implements the `Node` interface.",
)
class NodeType(relay.Node):
    """Set the value to the selected node."""

    id: relay.GlobalID  # noqa:A003


@strawberry.input(
    description="Input of an object that implements the `Node` interface.",
)
class NodeInput:
    """Set the value to the selected node.

    Notes:
        This can be used as a base class for input types that receive an
        `id` of type `GlobalID` when inheriting from it.

    """

    id: relay.GlobalID  # noqa:A003


@strawberry.input(
    description="Input of an object that implements the `Node` interface.",
)
class NodeInputPartial(NodeInput):
    """Set the value to the selected node.

    Notes:
        This can be used as a base class for input types that receive an
        `id` of type `GlobalID` when inheriting from it.

    """

    # FIXME: Without this pyright will not let any class inheric from this and define
    # a field that doesn't contain a default value...
    if TYPE_CHECKING:
        id: Optional[relay.GlobalID]  # noqa:A001
    else:
        id: Optional[relay.GlobalID] = UNSET  # noqa:A001


@strawberry.input(description=("Add/remove/set the selected nodes."))
class ListInput(Generic[K]):
    """Add/remove/set the selected nodes."""

    # FIXME: Without this pyright will not let any class inheric from this and define
    # a field that doesn't contain a default value...
    if TYPE_CHECKING:
        set: Optional[List[K]]  # noqa:A001
        add: Optional[List[K]]
        remove: Optional[List[K]]
    else:
        set: Optional[List[K]] = UNSET  # noqa:A001
        add: Optional[List[K]] = UNSET
        remove: Optional[List[K]] = UNSET


@strawberry.input(description=("Add/remove/set the selected nodes, passing `data` through."))
class ListThroughInput(ListInput[K], Generic[K, D]):
    """Add/remove/set the selected nodes."""

    # FIXME: Without this pyright will not let any class inheric from this and define
    # a field that doesn't contain a default value...
    if TYPE_CHECKING:
        data: Optional[D]
    else:
        data: Optional[D] = UNSET


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
class OperationInfo:
    """Multiple messages returned by an operation."""

    messages: List[OperationMessage] = strawberry.field(
        description="List of messages returned by the operation.",
    )


def resolve_model_field_type(
    field: Union[models.Field, ForeignObjectRel],
    django_type: "StrawberryDjangoType",
):
    """Resolve type for model field."""
    if has_choices_field and isinstance(field, (TextChoicesField, IntegerChoicesField)):
        field_type = field.choices_enum
        enum_def = getattr(field_type, "_enum_definition", None)
        if enum_def is None:
            doc = field_type.__doc__ and inspect.cleandoc(field_type.__doc__)
            enum_def = strawberry.enum(field_type, description=doc)._enum_definition
        retval = enum_def.wrapped_cls
    else:
        retval = _resolve_model_field(field, django_type)

        if config.FIELDS_USE_GLOBAL_ID:
            is_lookup = False
            if isinstance(retval, FilterLookup):
                is_lookup = True
                retval = get_args(retval)[0]

            retval = {
                strawberry.ID: relay.GlobalID,
                DjangoModelType: NodeType,
                DjangoModelFilterInput: NodeInput,
                OneToOneInput: NodeInput,
                OneToManyInput: NodeInput,
                ManyToOneInput: ListInput[NodeInput],
                ManyToManyInput: ListInput[NodeInputPartial],
            }.get(
                retval,  # type:ignore
                retval,
            )

            if is_lookup:
                retval = FilterLookup[retval]

    is_input = django_type.is_input
    is_partial = django_type.is_partial
    is_filter = bool(django_type.is_filter)

    if getattr(field, "primary_key", False):
        # Primary keys are always required, unless this is a filter
        optional = False
    elif isinstance(field, ManyToManyField):
        # Many to many is always a list on get, but optional otherwise
        optional = is_input or is_partial or is_filter
    elif isinstance(field, ForeignObjectRel):
        optional = is_input or is_partial or is_filter or field.null
    else:
        if is_partial or is_filter:
            optional = True
        elif is_input:
            optional = field.blank or field.default is not NOT_PROVIDED
        else:
            optional = field.null

    if optional:
        retval = Optional[retval]

    return retval
