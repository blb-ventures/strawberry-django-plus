from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
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
from django.db.models import QuerySet
from django.db.models.manager import BaseManager
from django.db.models.query_utils import DeferredAttribute
from strawberry.arguments import UNSET
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.fields.field import StrawberryDjangoField

from .optimizer import DjangoOptimizer, DjangoOptimizerConfig
from .resolvers import callable_resolver, qs_resolver

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)
_original_call = StrawberryDjangoField.__call__


_get_list = qs_resolver(
    StrawberryDjangoField.get_queryset,
    get_list=True,
)
_get_one = qs_resolver(
    lambda *args, **kwargs: StrawberryDjangoField.get_queryset(*args, **kwargs).get(),
    get_one=True,
)
_attr_getter = callable_resolver(lambda obj, attr: getattr(obj, attr))


def _resolver(
    ret: object,
    info: Info,
    qs_resolver: Callable[
        [Union[BaseManager[_M], QuerySet[_M]]],
        AwaitableOrValue[Union[DjangoOptimizer, List[_M], _M]],
    ],
):
    if isinstance(ret, (BaseManager, QuerySet)):
        config = cast(
            Optional[DjangoOptimizerConfig],
            getattr(
                info.context,
                "_django_optimizer_config",
                None,
            ),
        )
        if config is not None:
            return DjangoOptimizer(
                info=info,
                config=config,
                qs=ret,
                qs_resolver=qs_resolver,
            )
        return qs_resolver(ret)
    elif callable(ret):
        return _resolver(callable_resolver(ret)(), info, qs_resolver)
    elif info._raw_info.is_awaitable(ret):
        return _async_resolver(cast(Awaitable, ret), info, qs_resolver)

    return ret


async def _async_resolver(
    ret: Awaitable,
    info: Info,
    qs_resolver: Callable[
        [Union[BaseManager[_M], QuerySet[_M]]],
        AwaitableOrValue[Union[DjangoOptimizer, List[_M], _M]],
    ],
):
    return _resolver(await ret, info, qs_resolver)


def _call(
    self: StrawberryDjangoField,
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
) -> StrawberryDjangoField:
    return cast(StrawberryDjangoField, _original_call(self, callable_resolver(resolver)))


def _get_result(
    self: StrawberryDjangoField,
    source: Any,
    info: Info,
    args: List[Any],
    kwargs: Dict[str, Any],
) -> Union[Awaitable[Any], Any]:
    if self.base_resolver is not None:
        result = self.base_resolver(*args, **kwargs)
    elif source is None:
        assert self.django_model
        result = self.django_model.objects.all()[:50]
    else:
        # Small optimization to async resolvers avoid having to call it in an sync_to_async
        # context if the value is already cached, since it will not hit the db anymore
        attname = self.django_name or self.python_name
        attr = getattr(source.__class__, attname, None)
        if isinstance(attr, DeferredAttribute):
            try:
                result = source.__dict__[attr.field.attname]
            except KeyError:
                result = _attr_getter(source, self.django_name or self.python_name)
        else:
            result = getattr(source, self.django_name or self.python_name)

    qs_resolver = lambda qs: (_get_list if self.is_list else _get_one)(
        self,
        qs,
        info=info,
        **kwargs,
    )
    return _resolver(result, info, qs_resolver)


StrawberryDjangoField.get_result = _get_result
StrawberryDjangoField.__call__ = _call


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
