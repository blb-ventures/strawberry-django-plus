from functools import cached_property
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

from django.db.models.base import Model
from django.db.models.manager import BaseManager
from django.db.models.query import QuerySet
from strawberry.arguments import UNSET
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import StrawberryContainer
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue

from ._relay import Connection, ConnectionField, NodeField
from ._relay import connection as _connection
from ._relay import node as _node
from .optimizer import DjangoOptimizer, DjangoOptimizerConfig
from .resolvers import callable_resolver, qs_resolver

_T = TypeVar("_T")


class DjangoNodeField(NodeField):
    @cached_property
    def model(self) -> Type[Model]:
        field_type = self.type
        while isinstance(field_type, StrawberryContainer):
            field_type = field_type.of_type

        return field_type._django_type.model  # type:ignore

    @qs_resolver(get_one=True)
    def resolve_node(self, info: Info, node_id: str) -> Any:
        field_type = self.type
        while isinstance(field_type, StrawberryContainer):
            field_type = field_type.of_type

        model = self.model
        qs = model.objects
        config = cast(
            Optional[DjangoOptimizerConfig],
            getattr(
                info.context,
                "_django_optimizer_config",
                None,
            ),
        )
        if config is not None:
            qs = DjangoOptimizer(
                info=info,
                config=config,
                qs=qs,
            ).optimize()

        return qs.filter(pk=node_id)


class DjangoConnectionField(ConnectionField):
    @cached_property
    def model(self) -> Type[Model]:
        field_type = self.type_annotation.annotation.__args__[0]
        return field_type._django_type.model

    def resolve_edges(self, info: Info) -> QuerySet[Any]:
        return self.model.objects.all()

    @callable_resolver
    def resolve_connection(
        self,
        info: Info,
        edges: AwaitableOrValue[Iterable[Any]],
        **kwargs: Dict[str, Any],
    ) -> AwaitableOrValue[Connection[Any]]:
        if isinstance(edges, (QuerySet, BaseManager)):
            config = cast(
                Optional[DjangoOptimizerConfig],
                getattr(
                    info.context,
                    "_django_optimizer_config",
                    None,
                ),
            )
            if config is not None:
                edges = DjangoOptimizer(
                    info=info,
                    config=config,
                    qs=edges,
                ).optimize()

        return super().resolve_connection(info, edges, **kwargs)


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
    base_field: Type[DjangoNodeField] = DjangoNodeField,
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
    base_field: Type[DjangoConnectionField] = DjangoConnectionField,
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
    base_field: Type[DjangoConnectionField] = DjangoConnectionField,
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
    base_field: Type[DjangoConnectionField] = DjangoConnectionField,
) -> DjangoConnectionField:
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
    base_field: Type[DjangoConnectionField] = DjangoConnectionField,
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
