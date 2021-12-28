from typing import (
    Any,
    Callable,
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

from django.db import models
from strawberry.arguments import UNSET
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.resolvers import django_resolver

from .optimizer import DjangoOptimizer, DjangoOptimizerConfig

_T = TypeVar("_T")


@django_resolver
def _qs_resolver(qs: models.QuerySet, field: StrawberryDjangoField):
    ret = qs
    if not field.is_list:
        ret = qs.get()
    return ret


def _resolver(self: StrawberryDjangoField, source: Any, info: Info, *args, **kwargs):
    config = cast(
        Optional[DjangoOptimizerConfig],
        getattr(
            info.context,
            "_django_optimizer_config",
            None,
        ),
    )

    if source is None:
        assert self.django_model
        result = self.django_model.objects.all()[:20]
    else:
        result = getattr(source, self.django_name or self.python_name)

    if config is not None and isinstance(result, (models.QuerySet, models.manager.BaseManager)):
        result = DjangoOptimizer(
            info=info,
            config=config,
            qs=result,
            qs_resolver=lambda qs: _qs_resolver(
                self.get_queryset(qs, info=info, **kwargs) if source is None else qs,
                self,
            ),
        )
    elif isinstance(result, models.manager.BaseManager):
        result = result.all()
    elif callable(result):
        result = django_resolver(result)()

    if isinstance(result, models.QuerySet):
        result = _qs_resolver(
            self.get_queryset(result, info=info, **kwargs) if source is None else result,
            self,
        )

    return result


StrawberryDjangoField.get_django_result = _resolver


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
        f = f(django_resolver(resolver))
    return f
