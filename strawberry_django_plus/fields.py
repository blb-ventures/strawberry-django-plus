from functools import cached_property
from typing import (
    Any,
    Awaitable,
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

from django.db import models
from django.db.models import QuerySet
from django.db.models.base import Model
from django.db.models.query_utils import DeferredAttribute
from strawberry.arguments import UNSET
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import StrawberryContainer
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.fields.field import (
    StrawberryDjangoField as _StrawberryDjangoField,
)

from .relay import Connection, ConnectionField, NodeField
from .relay import connection as _connection
from .relay import node as _node
from .resolvers import callable_resolver, qs_resolver, resolve_qs_one, resolve_result

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)


class StrawberryDjangoField(_StrawberryDjangoField):
    def get_result(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Union[Awaitable[Any], Any]:
        if self.base_resolver is not None:
            # Unlike strawberry_django, we don't enforce this on sync_to_async since it adds
            # a lot of overhead which might be unnecessary. Leave it up to the implementation
            # to decide what to do...
            result = self.base_resolver(*args, **kwargs)
        elif source is None:
            assert self.django_model
            result = self.django_model.objects.all()
        else:
            # Small optimization to async resolvers avoid having to call it in an sync_to_async
            # context if the value is already cached, since it will not hit the db anymore
            attname = self.django_name or self.python_name
            attr = getattr(source.__class__, attname, None)
            if isinstance(attr, DeferredAttribute):
                try:
                    result = source.__dict__[attr.field.attname]
                except KeyError:
                    result = lambda: getattr(source, self.django_name or self.python_name)
            else:
                result = lambda: getattr(source, self.django_name or self.python_name)

        if self.is_list:
            qs_resolver = lambda qs: self.get_list(info, qs, **kwargs)
        else:
            qs_resolver = lambda qs: self.get_one(info, qs, **kwargs)

        return resolve_result(result, info, resolve_callable_func=qs_resolver)

    @qs_resolver(get_list=True)
    def get_list(self, info: Info, qs: QuerySet[Any], **kwargs) -> QuerySet[Any]:
        # The qs_resolver will ensure this returns a list
        return self.get_queryset(qs, info, **kwargs)

    @qs_resolver(get_one=True)
    def get_one(self, info: Info, qs: QuerySet[Any], **kwargs) -> QuerySet[Any]:
        # The qs_resolver will ensure this returns a single result
        return self.get_queryset(qs, info, **kwargs)


class StrawberryDjangoNodeField(NodeField):
    @cached_property
    def model(self) -> Type[Model]:
        field_type = self.type
        while isinstance(field_type, StrawberryContainer):
            field_type = field_type.of_type

        return field_type._django_type.model  # type:ignore

    def resolve_node(self, info: Info, node_id: str) -> Any:
        model = self.model
        qs = model.objects.filter(pk=node_id)
        return resolve_result(qs, info, resolve_qs_func=resolve_qs_one)


class StrawberryDjangoConnectionField(ConnectionField):
    @cached_property
    def model(self) -> Type[Model]:
        field_type = self.type_annotation.annotation.__args__[0]
        return field_type._django_type.model

    def resolve_edges(self, info: Info) -> AwaitableOrValue[QuerySet[Any]]:
        # We don't want this to be prefetched yet, just to be optimized
        return resolve_result(self.model.objects.all(), info, resolve_qs_func=lambda qs: qs)

    @callable_resolver
    def resolve_connection(
        self,
        info: Info,
        edges: AwaitableOrValue[Iterable[Any]],
        **kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Connection[Any]]:
        # Because we are decrated with callable_resolver, any calls to the db should be safe
        return super().resolve_connection(info, edges, **kwargs)

    async def async_resolve_connection(
        self,
        info: Info,
        edges: Awaitable[Iterable[_T]],
        **kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Connection[_T]]:
        # Make sure that, if super().resolve_connection call from above calls this,
        # we call it again instead of our implementation. There's no need for a double
        # callable_resolver.
        return super().resolve_connection(info, await edges, **kwargs)


@overload
def field(
    *,
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
def field(
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
    ...


@overload
def field(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
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
) -> StrawberryDjangoField:
    ...


def field(
    resolver=None,
    *,
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
    f = StrawberryDjangoField(
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
    )
    if resolver:
        f = f(resolver)
    return f


def node(
    *,
    name: Optional[str] = None,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    base_field: Type[StrawberryDjangoNodeField] = StrawberryDjangoNodeField,
) -> Any:
    return _node(
        name=name,
        is_subscription=is_subscription,
        description=description,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        base_field=base_field,
    )


@overload
def connection(
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
    base_field: Type[StrawberryDjangoConnectionField] = StrawberryDjangoConnectionField,
) -> _T:
    ...


@overload
def connection(
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
    base_field: Type[StrawberryDjangoConnectionField] = StrawberryDjangoConnectionField,
) -> Any:
    ...


@overload
def connection(
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
    base_field: Type[StrawberryDjangoConnectionField] = StrawberryDjangoConnectionField,
) -> StrawberryDjangoConnectionField:
    ...


def connection(
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
    base_field: Type[StrawberryDjangoConnectionField] = StrawberryDjangoConnectionField,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    return _connection(
        resolver=resolver,
        name=name,
        is_subscription=is_subscription,
        description=description,
        permission_classes=permission_classes,
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        base_field=base_field,
    )
