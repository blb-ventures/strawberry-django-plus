import functools
import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    Optional,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from asgiref.sync import sync_to_async
from django.db.models import Model, QuerySet
from django.db.models.manager import BaseManager
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.utils import is_async
from typing_extensions import ParamSpec

from strawberry_django_plus.relay import Connection, GlobalID, Node

from .aio import is_awaitable, resolve, resolve_async

if TYPE_CHECKING:
    from strawberry_django_plus.types import StrawberryDjangoType

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_R = TypeVar("_R")
_P = ParamSpec("_P")


@overload
def sync_resolver(
    f: Callable[_P, _R],
    *,
    thread_sensitive: bool = True,
) -> Callable[_P, AwaitableOrValue[_R]]:
    ...


@overload
def sync_resolver(
    f=None,
    *,
    thread_sensitive: bool = True,
) -> Callable[[Callable[_P, _R]], Callable[_P, AwaitableOrValue[_R]]]:
    ...


def sync_resolver(f=None, *, thread_sensitive=True):
    """Ensure function is called in a sync context.

    - If `f` is a coroutine function, this is a noop.
    - When running, if an asyncio loop is running, the function will
      be called wrapped in an asgi.sync_to_async_ context.

    Args:
        f:
            The function to call
        thread_sensitive:
            If the sync function should run in the same thread as all other
            thread_sensitive functions

    Returns:
        The wrapped function

    .. _asgi.sync_to_async:
        https://docs.djangoproject.com/en/dev/topics/async/#asgiref.sync.sync_to_async

    """

    def make_resolver(func):
        if inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func):
            return func

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            resolver = func
            if is_async():
                resolver = sync_to_async(func, thread_sensitive=thread_sensitive)
            return resolver(*args, **kwargs)

        return wrapper

    if f is not None:
        return make_resolver(f)

    return make_resolver


resolve_getattr = sync_resolver(lambda obj, key, *args: getattr(obj, key, *args))


@overload
def resolve_qs(
    qs: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    resolver: Optional[Callable[[QuerySet[_M]], AwaitableOrValue[_R]]] = None,
    info: Optional[Info] = None,
) -> AwaitableOrValue[_R]:
    ...


@overload
def resolve_qs(
    qs: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    info: Optional[Info] = None,
) -> AwaitableOrValue[QuerySet[_M]]:
    ...


def resolve_qs(qs, *, resolver=None, info=None) -> Any:
    """Resolve the queryset, ensuring its db operations are executed in a sync context.

    Args:
        qs:
            The function to call
        resolver:
            An optional function that, when present, will be called in a thread
            sensitive context to resolve the queryset.
            thread_sensitive functions
        info:
            Optional gql execution info. If present, will use its implementation
            of `is_awaitable`, which might have some optimizations. Otherwise
            will fallback to `inspect.is_awaitable`

    Returns:
        The wrapped function

    .. _asgi.sync_to_async:
        https://docs.djangoproject.com/en/dev/topics/async/#asgiref.sync.sync_to_async

    """
    if isinstance(qs, BaseManager):
        qs = qs.all()

    if is_awaitable(qs, info=info):
        return resolve_async(qs, lambda r: resolve_qs(r, resolver=resolver, info=info))

    if resolver is None:
        # This is what QuerySet does internally to fetch results.
        # After this, iterating over the queryset should be async safe
        resolver = lambda r: r._fetch_all() or r  # type:ignore

    if is_async() and not (
        inspect.iscoroutinefunction(resolver) or inspect.isasyncgenfunction(resolver)
    ):
        resolver = sync_to_async(resolver, thread_sensitive=True)

    return resolver(qs)


resolve_qs_get_list = functools.partial(resolve_qs, resolver=list)
resolve_qs_get_first = functools.partial(resolve_qs, resolver=lambda qs: qs.first())
resolve_qs_get_one = functools.partial(resolve_qs, resolver=lambda qs: qs.get())


def resolve_result(
    res: Any,
    info: Info,
    *,
    qs_resolver: Optional[
        Callable[[Union[BaseManager[_M], QuerySet[_M]]], AwaitableOrValue[Any]]
    ] = None,
) -> AwaitableOrValue[Any]:
    """Resolve the result, ensuring any qs and callable are resolved in sync context."""
    if isinstance(res, (BaseManager, QuerySet)):
        config = getattr(info.context, "_django_optimizer_config", None)
        if config is not None:
            from strawberry_django_plus.optimizer import optimize

            # If optimizer extension is enabled, optimize this queryset
            res = optimize(qs=res, info=info, config=config)

        qs_resolver = qs_resolver or resolve_qs

        return qs_resolver(res)
    elif callable(res):
        return resolve_result(sync_resolver(res)(), info, qs_resolver=qs_resolver)
    elif is_awaitable(res, info=info):
        return resolve_async(res, lambda r: resolve_result(r, info, qs_resolver=qs_resolver))

    return res


def resolve_model_nodes(
    source: Type[Node[_M]],
    info: Info,
    node_ids: Optional[Iterable[Union[str, GlobalID]]] = None,
) -> AwaitableOrValue[QuerySet[_M]]:
    """Resolve model nodes, ensuring those are prefetched in a sync context."""
    django_type = cast("StrawberryDjangoType", source._django_type)  # type:ignore

    qs = django_type.model.objects.all()
    if node_ids is not None:
        qs = qs.filter(pk__in=[i.node_id if isinstance(i, GlobalID) else i for i in node_ids])

    return resolve_result(qs, info)


def resolve_connection(
    source: Type[Node[_M]],
    info: Info,
    *,
    nodes: Optional[AwaitableOrValue[QuerySet[_M]]] = None,
    total_count: Optional[int] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
) -> AwaitableOrValue[Connection[_M]]:
    """Resolve a model connection, ensuring those are prefetched in a sync context."""
    if nodes is None:
        django_type = cast("StrawberryDjangoType", source._django_type)  # type:ignore
        nodes = django_type.model.objects.all()
        assert nodes

    config = getattr(info.context, "_django_optimizer_config", None)
    if config is not None:
        from strawberry_django_plus.optimizer import optimize

        # If optimizer extension is enabled, optimize this queryset
        nodes = resolve(nodes, lambda _qs: optimize(qs=_qs, info=info, config=config))

    return resolve(
        nodes,
        lambda _qs: Connection.from_nodes(
            _qs,
            total_count=total_count,
            before=before,
            after=after,
            first=first,
            last=last,
        ),
    )


def resolve_model_node(
    source: Type[Node[_M]],
    info: Info,
    node_id: Union[str, GlobalID],
) -> Optional[AwaitableOrValue[_M]]:
    """Resolve model nodes, ensuring it is retrieved in a sync context."""
    if isinstance(node_id, GlobalID):
        node_id = node_id.node_id

    django_type = cast("StrawberryDjangoType", source._django_type)  # type:ignore
    qs = django_type.model.objects.filter(pk=node_id)

    return resolve_result(qs, info, qs_resolver=resolve_qs_get_first)


def resolve_model_id(
    source: Node[_M],
    info: Info,
    root: _M,
) -> AwaitableOrValue[str]:
    """Resolve the model id, ensuring it is retrieved in a sync context."""
    attr = root._meta.pk.attname  # type:ignore
    try:
        # Prefer to retrieve this from the cache
        return str(root.__dict__[attr])
    except KeyError:
        return resolve_getattr(root, attr)
