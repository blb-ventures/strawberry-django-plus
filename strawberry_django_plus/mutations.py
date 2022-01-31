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

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
import strawberry
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry.utils.str_converters import to_camel_case

from . import relay
from .types import OperationMessage, OperationMessageList
from .utils.resolvers import async_unsafe

_T = TypeVar("_T")


def _get_validation_errors(error: ValidationError):
    if hasattr(error, "error_dict"):
        # convert field errors
        for field, field_errors in error.message_dict.items():
            for e in field_errors:
                yield OperationMessage(
                    kind=OperationMessage.Kind.VALIDATION,
                    field=to_camel_case(field) if field != NON_FIELD_ERRORS else None,
                    message=e,
                )
    elif hasattr(error, "error_list"):
        # convert non-field errors
        for e in error.error_list:
            yield OperationMessage(
                kind=OperationMessage.Kind.VALIDATION,
                message=e.message,
            )
    else:
        yield OperationMessage(
            kind=OperationMessage.Kind.VALIDATION,
            message=error.message,
        )


class DjangoInputMutationField(relay.InputMutationField):
    """Relay Mutation field for django models.

    This is basically the same as `relay.InputMutationField`, but it ensure that
    the mutation resolver gets called in an async safe environment.

    Do not instantiate this directly. Instead, use `@gql.django.input_mutation`

    """

    _obj_resolvers: Dict[str, type]

    def __call__(self, resolver: Callable[..., Iterable[relay.Node]]):
        name = to_camel_case(resolver.__name__)
        cap_name = name[0].upper() + name[1:]
        namespace = sys.modules[resolver.__module__].__dict__
        annotation = StrawberryAnnotation(resolver.__annotations__["return"], namespace=namespace)

        # Transform the return value into a union of it with OperationMessages
        resolver.__annotations__["return"] = strawberry.union(
            f"{cap_name}Payload",
            (annotation.resolve(), OperationMessageList),
        )

        return super().__call__(async_unsafe(resolver))  # type:ignore

    @async_unsafe
    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        assert self.base_resolver
        input_obj = kwargs.pop("input")

        try:
            return self.base_resolver(*args, **kwargs, **vars(input_obj))
        except ValidationError as e:
            return OperationMessageList(
                messages=list(_get_validation_errors(e)),
            )
        except (PermissionDenied, PermissionError) as e:
            return OperationMessageList(
                messages=[
                    OperationMessage(
                        kind=OperationMessage.Kind.PERMISSION,
                        message=str(e) or "Permission denied...",
                    )
                ]
            )


@overload
def input_mutation(
    *,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> _T:
    ...


@overload
def input_mutation(
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Any:
    ...


@overload
def input_mutation(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> DjangoInputMutationField:
    ...


def input_mutation(
    resolver=None,
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
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
        python_name=None,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives or (),
    )
    if resolver is not None:
        f = f(resolver)
    return f
