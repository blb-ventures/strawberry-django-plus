import sys
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    overload,
)

from django.core.exceptions import (
    NON_FIELD_ERRORS,
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
import strawberry
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument
from strawberry.permission import BasePermission
from strawberry.type import StrawberryType
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry.utils.str_converters import to_camel_case

from strawberry_django_plus import relay
from strawberry_django_plus.field import StrawberryDjangoField
from strawberry_django_plus.optimizer import DjangoOptimizerExtension
from strawberry_django_plus.permissions import get_with_perms
from strawberry_django_plus.types import NodeInput, OperationInfo, OperationMessage
from strawberry_django_plus.utils import aio
from strawberry_django_plus.utils.inspect import get_possible_types
from strawberry_django_plus.utils.resolvers import async_safe

from . import resolvers

_T = TypeVar("_T")


def _get_validation_errors(error: Exception):
    if isinstance(error, PermissionDenied):
        kind = OperationMessage.Kind.PERMISSION
    elif isinstance(error, ValidationError):
        kind = OperationMessage.Kind.VALIDATION
    elif isinstance(error, ObjectDoesNotExist):
        kind = OperationMessage.Kind.ERROR
    else:
        kind = OperationMessage.Kind.ERROR

    if isinstance(error, ValidationError) and hasattr(error, "error_dict"):
        # convert field errors
        for field, field_errors in error.message_dict.items():
            for e in field_errors:
                yield OperationMessage(
                    kind=kind,
                    field=to_camel_case(field) if field != NON_FIELD_ERRORS else None,
                    message=e,
                )
    elif isinstance(error, ValidationError) and hasattr(error, "error_list"):
        # convert non-field errors
        for e in error.error_list:
            yield OperationMessage(
                kind=kind,
                message=e.message,
            )
    else:
        msg = getattr(error, "msg", None)
        if msg is None:
            msg = str(error)

        yield OperationMessage(
            kind=kind,
            message=msg,
        )


def _map_exception(error: Exception):
    if isinstance(error, (ValidationError, PermissionDenied, ObjectDoesNotExist)):
        return OperationInfo(
            messages=list(_get_validation_errors(error)),
        )

    return error


class DjangoInputMutationField(relay.InputMutationField, StrawberryDjangoField):
    """Input mutation for django models.

    This is basically the same as `relay.InputMutationField`, but it ensure that
    the mutation resolver gets called in an async safe environment.

    Do not instantiate this directly. Instead, use `@gql.django.input_mutation`

    """

    def __init__(self, *args, **kwargs):
        input_type: Optional[type] = kwargs.pop("input_type", None)

        super().__init__(*args, **kwargs)

        self.input_type = input_type
        if self.input_type and not self.base_resolver:
            namespace = sys.modules[self.input_type.__module__].__dict__
            type_def = getattr(input_type, "_type_definition", None)
            self.default_args["input"] = StrawberryArgument(
                python_name="input",
                graphql_name=None,
                type_annotation=StrawberryAnnotation(self.input_type, namespace=namespace),
                description=type_def and type_def.description,
            )

    def __call__(self, resolver: Callable[..., Iterable[relay.Node]]):
        name = to_camel_case(resolver.__name__)
        cap_name = name[0].upper() + name[1:]
        namespace = sys.modules[resolver.__module__].__dict__
        annotation = StrawberryAnnotation(
            resolver.__annotations__["return"],
            namespace=namespace,
        )
        # Transform the return value into a union of it with OperationMessages
        resolver.__annotations__["return"] = strawberry.union(
            f"{cap_name}Payload",
            (annotation.resolve(), OperationInfo),
        )
        return super().__call__(resolver)

    @property
    def type(self) -> Union[StrawberryType, type]:  # noqa:A003
        return super().type

    @type.setter
    def type(self, type_: Any) -> None:  # noqa:A003
        if type_ is not None:
            name = to_camel_case(self.python_name)
            cap_name = name[0].upper() + name[1:]

            if isinstance(type_, StrawberryAnnotation):
                type_ = type_.annotation

            types_ = tuple(get_possible_types(type_))
            type_ = strawberry.union(f"{cap_name}Payload", types_ + (OperationInfo,))

        self.type_annotation = type_

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        input_obj = kwargs.pop("input", None)

        # FIXME: Any other exception types that we should capture here?
        resolver = aio.resolver(self.resolver, on_error=_map_exception, info=info)
        return resolver(source, info, input_obj, args, kwargs)

    def resolver(
        self,
        source: Any,
        info: Info,
        data: Optional[object],
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        return self.safe_resolver(*args, **kwargs, **vars(data))


class DjangoCreateMutationField(DjangoInputMutationField):
    """Create mutation for django models.

    Do not instantiate this directly. Instead, use
    `@gql.django.create_mutation`

    """

    @async_safe
    def resolver(
        self,
        source: Any,
        info: Info,
        data: type,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        assert data is not None
        return resolvers.create(info, self.model, resolvers.parse_input(info, vars(data)))


class DjangoUpdateMutationField(DjangoInputMutationField):
    """Update mutation for django models.

    Do not instantiate this directly. Instead, use
    `@gql.django.update_mutation`

    """

    @async_safe
    def resolver(
        self,
        source: Any,
        info: Info,
        data: type,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        assert data is not None

        vdata = vars(data)
        pk = vdata.pop("id", UNSET)
        if pk is UNSET:
            pk = vdata.pop("pk")

        # Do not optimize anything while retrieving the object to update
        token = DjangoOptimizerExtension.enabled.set(False)
        try:
            instance = get_with_perms(pk, info, required=True, model=self.model)
            return resolvers.update(info, instance, resolvers.parse_input(info, vdata))
        finally:
            DjangoOptimizerExtension.enabled.reset(token)


class DjangoDeleteMutationField(DjangoInputMutationField):
    """Delete mutation for django models.

    Do not instantiate this directly. Instead, use
    `@gql.django.delete_mutation`

    """

    @async_safe
    def resolver(
        self,
        source: Any,
        info: Info,
        data: type,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        assert data is not None

        vdata = vars(data)
        pk = vdata.pop("id")
        if pk is UNSET:
            pk = vdata.pop("pk")

        # Do not optimize anything while retrieving the object to delete
        token = DjangoOptimizerExtension.enabled.set(False)
        try:
            instance = get_with_perms(pk, info, required=True, model=self.model)
            return resolvers.delete(info, instance, data=resolvers.parse_input(info, vdata))
        finally:
            DjangoOptimizerExtension.enabled.reset(token)


@overload
def input_mutation(
    *,
    input_type: Optional[type] = None,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> _T:
    ...


@overload
def input_mutation(
    *,
    input_type: Optional[type] = None,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> Any:
    ...


@overload
def input_mutation(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
    input_type: Optional[type] = None,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> DjangoInputMutationField:
    ...


def input_mutation(
    resolver=None,
    *,
    input_type: Optional[type] = None,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    """Annotate a property or a method to create an input mutation field.

    This is basically the same as `@relay.input_mutation`, but it ensure
    that the mutation resolver gets called in an async safe environment.

    """
    f = DjangoInputMutationField(
        input_type=input_type,
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        filters=filters,
    )
    if resolver is not None:
        f = f(resolver)
    return f


def create(
    input_type: type,
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> Any:
    """Create mutation for django input fields.

    Automatically create data for django input fields.

    Examples:
        >>> @gql.django.input
        ... class ProductInput:
        ...     name: gql.auto
        ...     price: gql.auto
        ...
        >>> @strawberry.mutation
        >>> class Mutation:
        ...     create_product: ProductType = gql.django.create_mutation(ProductInput)

    """
    return DjangoCreateMutationField(
        input_type=input_type,
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        filters=filters,
    )


def update(
    input_type: Type[NodeInput],
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> Any:
    """Update mutation for django input fields.

    Examples:
        >>> @gql.django.input
        ... class ProductInput(IdInput):
        ...     name: gql.auto
        ...     price: gql.auto
        ...
        >>> @strawberry.mutation
        >>> class Mutation:
        ...     create_product: ProductType = gql.django.update_mutation(ProductInput)

    """
    return DjangoUpdateMutationField(
        input_type=input_type,
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        filters=filters,
    )


def delete(
    input_type: Type[NodeInput] = NodeInput,
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[object]] = (),
) -> Any:
    return DjangoDeleteMutationField(
        input_type=input_type,
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        filters=filters,
    )
