import inspect
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
    cast,
    overload,
)

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import models
import strawberry
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument, is_unset
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry.utils.str_converters import to_camel_case

from strawberry_django_plus import relay
from strawberry_django_plus.field import StrawberryDjangoField
from strawberry_django_plus.types import (
    NodeInput,
    OperationMessage,
    OperationMessageList,
)
from strawberry_django_plus.utils import aio
from strawberry_django_plus.utils.resolvers import async_safe, resolve_sync

from . import resolvers

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


class DjangoInputMutationField(relay.InputMutationField, StrawberryDjangoField):
    """Input mutation for django models.

    This is basically the same as `relay.InputMutationField`, but it ensure that
    the mutation resolver gets called in an async safe environment.

    Do not instantiate this directly. Instead, use `@gql.django.input_mutation`

    """

    def __init__(self, *args, **kwargs):
        self.input_type: Optional[type] = kwargs.pop("input_type")

        super().__init__(*args, **kwargs)

        if self.input_type and not self.base_resolver:
            namespace = sys.modules[self.input_type.__module__].__dict__
            self.default_args["input"] = StrawberryArgument(
                python_name="input",
                graphql_name=None,
                type_annotation=StrawberryAnnotation(self.input_type, namespace=namespace),
                description=self.input_type.__doc__ and inspect.cleandoc(self.input_type.__doc__),
            )

    def __call__(self, resolver: Callable[..., Iterable[relay.Node]]):
        # No return means this is probably a lambda from this module
        if "return" not in resolver.__annotations__:
            return super().__call__(resolver)

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
            (annotation.resolve(), OperationMessageList),
        )
        return super().__call__(resolver)

    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        input_obj = kwargs.pop("input", None)
        try:
            return self.resolver(source, info, input_obj, args, kwargs)
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

    @async_safe
    def resolver(
        self,
        source: Any,
        info: Info,
        data: Optional[type],
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Any]:
        assert self.base_resolver
        return self.base_resolver(*args, **kwargs, **vars(data))


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
        return resolvers.create(info, self.model, data)


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

        if "filters" not in kwargs:
            node = kwargs["ids" if self.is_list else "id"]
            if node is None or is_unset(node):
                raise ValueError("No filters provided for update mutation")

        instances = self.get_queryset(
            queryset=self.model._default_manager.all(),
            info=info,
            data=data,
            **kwargs,
        )
        if not self.is_list:
            try:
                instances = instances.get()
            except self.model.MultipleObjectsReturned:
                if isinstance(data, NodeInput):
                    node = data.id
                elif "id" in kwargs:  # noqa:SIM401
                    node = kwargs["id"]
                else:
                    node = None

                if isinstance(node, relay.GlobalID):
                    instances = node.resolve_node(info)
                    if aio.is_awaitable(instances, info=info):
                        instances = resolve_sync(instances)
                    instances = cast(models.Model, instances)
                else:
                    raise

        return resolvers.update(info, instances, data)


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
        data: None,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        assert data is None

        if "filters" not in kwargs:
            node = kwargs["ids" if self.is_list else "id"]
            if node is None or is_unset(node):
                raise ValueError("No filters provided for update mutation")

        instances = self.get_queryset(
            queryset=self.model._default_manager.all(),
            info=info,
            **kwargs,
        )
        if not self.is_list:
            try:
                instances = instances.get()
            except self.model.MultipleObjectsReturned:
                node = kwargs.get("id")
                if isinstance(node, relay.GlobalID):
                    instances = node.resolve_node(
                        info,
                        ensure_type=cast(Type[relay.Node], self.model),
                    )
                    if aio.is_awaitable(instances, info=info):
                        instances = resolve_sync(instances)
                    instances = cast(models.Model, instances)
                else:
                    raise

        return resolvers.delete(info, instances)


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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
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
        directives=directives or (),
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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
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
        name=name,
        field_name=field_name,
        filters=filters,
        is_subscription=is_subscription,
        description=description,
        init=init,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
    )


def update(
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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Any:
    """Update mutation for django input fields.

    Examples:
        >>> @gql.django.input
        ... class ProductInput(NodeInput):
        ...     name: gql.auto
        ...     price: gql.auto
        ...
        >>> @strawberry.mutation
        >>> class Mutation:
        ...     create_product: ProductType = gql.django.update_mutation(ProductInput)

    """
    return DjangoUpdateMutationField(
        input_type=input_type,
        name=name,
        field_name=field_name,
        filters=filters,
        is_subscription=is_subscription,
        description=description,
        init=init,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
    )


def delete(
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
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
) -> Any:
    return DjangoDeleteMutationField(
        name=name,
        field_name=field_name,
        filters=filters,
        is_subscription=is_subscription,
        description=description,
        init=init,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
    )
