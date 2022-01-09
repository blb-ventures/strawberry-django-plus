import functools
import inspect
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Literal,
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

from strawberry_django_plus.relay import Connection, GlobalID, Node, NodeType

from .aio import is_awaitable, resolve, resolve_async
from .inspect import get_django_type, get_optimizer_config

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_R = TypeVar("_R")
_P = ParamSpec("_P")
_sentinel = object()


@overload
def async_unsafe(
    f: Callable[_P, _R],
    *,
    thread_sensitive: bool = True,
) -> Callable[_P, AwaitableOrValue[_R]]:
    ...


@overload
def async_unsafe(
    f: None,
    *,
    thread_sensitive: bool = True,
) -> Callable[[Callable[_P, _R]], Callable[_P, AwaitableOrValue[_R]]]:
    ...


def async_unsafe(f=None, *, thread_sensitive=True):
    """Decorates a function as async unsafe, ensuring it is called in a sync context always.

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


getattr_async_unsafe = async_unsafe(lambda obj, key, *args: getattr(obj, key, *args))


@overload
def resolve_qs(
    qs: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    resolver: None = ...,
    info: Optional[Info] = ...,
) -> AwaitableOrValue[QuerySet[_M]]:
    ...


@overload
def resolve_qs(
    qs: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    resolver: Optional[Callable[[QuerySet[_M]], AwaitableOrValue[_R]]] = ...,
    info: Optional[Info] = ...,
) -> AwaitableOrValue[_R]:
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
        return resolve_async(
            qs,
            functools.partial(resolve_qs, resolver=resolver, info=info),
            info=info,
        )

    if resolver is None:
        # This is what QuerySet does internally to fetch results.
        # After this, iterating over the queryset should be async safe
        resolver = lambda r: r._fetch_all() or r

    if is_async() and not (
        inspect.iscoroutinefunction(resolver) or inspect.isasyncgenfunction(resolver)
    ):
        resolver = sync_to_async(resolver, thread_sensitive=True)

    return resolver(qs)


resolve_qs_get_list = cast(
    Callable[[AwaitableOrValue[QuerySet[_M]]], AwaitableOrValue[List[_M]]],
    functools.partial(resolve_qs, resolver=list),
)
resolve_qs_get_first = cast(
    Callable[[AwaitableOrValue[QuerySet[_M]]], AwaitableOrValue[Optional[_M]]],
    functools.partial(resolve_qs, resolver=lambda qs: qs.first()),
)
resolve_qs_get_one = cast(
    Callable[[AwaitableOrValue[QuerySet[_M]]], AwaitableOrValue[_M]],
    functools.partial(resolve_qs, resolver=lambda qs: qs.get()),
)


@overload
def resolve_result(
    res: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    info: Optional[Info] = None,
) -> AwaitableOrValue[QuerySet[_M]]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    info: Optional[Info] = None,
    qs_resolver: Callable[[Union[BaseManager[_M], QuerySet[_M]]], AwaitableOrValue[_T]] = ...,
) -> AwaitableOrValue[_T]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[Callable[[], _T]],
    *,
    info: Optional[Info] = None,
) -> AwaitableOrValue[_T]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[_T],
    *,
    info: Optional[Info] = None,
    qs_resolver: Optional[
        Callable[[Union[BaseManager[Any], QuerySet[Any]]], AwaitableOrValue[Any]]
    ] = None,
) -> AwaitableOrValue[_T]:
    ...


def resolve_result(res, *, info=None, qs_resolver=None):
    """Resolve the result, ensuring any qs and callables are resolved in a sync context.

    Args:
        res:
            The result to resolve
        info:
            Optional gql execution info. Make sure to always provide this or
            otherwise, the queryset cannot be optimized in case DjangoOptimizerExtension
            is enabled. This will also be used for `is_awaitable` check.
        qs_resolver:
            Optional qs_resolver to use to resolve any queryset. If not provided,
            `resolve_qs` will be used, which by default returns the queryset
            already prefetched from the database.

    Returns:
        The resolved result.

    """
    from strawberry_django_plus import optimizer  # avoid circular import

    if isinstance(res, (BaseManager, QuerySet)):
        if isinstance(res, BaseManager):
            res = cast(QuerySet, res.all())

        if info is not None:
            config = get_optimizer_config(info)
            if config is not None:
                # If optimizer extension is enabled, optimize this queryset
                res = optimizer.optimize(qs=res, info=info, config=config)

        qs_resolver = qs_resolver or resolve_qs
        return qs_resolver(res)
    elif callable(res):
        return resolve_result(async_unsafe(res)(), info=info, qs_resolver=qs_resolver)
    elif is_awaitable(res, info=info):
        return resolve_async(
            res,
            functools.partial(resolve_result, info=info, qs_resolver=qs_resolver),
            info=info,
        )

    return res


def resolve_model_nodes(
    source: Union[Type[Node], Type[_M]],
    *,
    info: Optional[Info] = None,
    node_ids: Optional[Iterable[Union[str, GlobalID]]] = None,
) -> AwaitableOrValue[QuerySet[_M]]:
    """Resolve model nodes, ensuring those are prefetched in a sync context.

    Args:
        source:
            The source model or the model type that implements the `Node` interface
        info:
            Optional gql execution info. Make sure to always provide this or
            otherwise, the queryset cannot be optimized in case DjangoOptimizerExtension
            is enabled. This will also be used for `is_awaitable` check.
        node_ids:
            Optional filter by those node_ids instead of retrieving everything

    Returns:
        The resolved queryset, already prefetched from the database

    """
    if not issubclass(source, Model):
        django_type = get_django_type(source, ensure_type=True)
        source = cast(Type[_M], django_type.model)

    qs = source.objects.all()
    if node_ids is not None:
        qs = qs.filter(pk__in=[i.node_id if isinstance(i, GlobalID) else i for i in node_ids])

    return resolve_result(qs, info=info)


@overload
def resolve_model_node(
    source: Union[Type[Node], Type[_M]],
    node_id: Union[str, GlobalID],
    *,
    info: Optional[Info] = ...,
    required: Literal[False] = ...,
) -> AwaitableOrValue[Optional[_M]]:
    ...


@overload
def resolve_model_node(
    source: Union[Type[Node], Type[_M]],
    node_id: Union[str, GlobalID],
    *,
    info: Optional[Info] = ...,
    required: Literal[True],
) -> AwaitableOrValue[_M]:
    ...


def resolve_model_node(source, node_id, *, info=None, required=False):
    """Resolve model nodes, ensuring it is retrieved in a sync context.

    Args:
        source:
            The source model or the model type that implements the `Node` interface
        node_id:
            The node it to retrieve the model from
        info:
            Optional gql execution info. Make sure to always provide this or
            otherwise, the queryset cannot be optimized in case DjangoOptimizerExtension
            is enabled. This will also be used for `is_awaitable` check.
        required:
            If the return value is required to exist. If true, `qs.get()` will be
            used, which might raise `model.DoesNotExist` error if the node doesn't exist.
            Otherwise, `qs.first()` will be used, which might return None.

    Returns:
        The resolved node, already prefetched from the database

    """
    if not issubclass(source, Model):
        django_type = get_django_type(source, ensure_type=True)
        source = cast(Type[_M], django_type.model)

    if isinstance(node_id, GlobalID):
        node_id = node_id.node_id

    qs = source.objects.filter(pk=node_id)

    if required:
        ret = resolve_result(qs, info=info, qs_resolver=resolve_qs_get_one)
    else:
        ret = resolve_result(qs, info=info, qs_resolver=resolve_qs_get_first)

    return ret


def resolve_model_id(root: Model) -> AwaitableOrValue[str]:
    """Resolve the model id, ensuring it is retrieved in a sync context.

    Args:
        root:
            The source model object.

    Returns:
        The resolved object id

    """
    assert isinstance(root, Model)
    attr = root._meta.pk.attname  # type:ignore
    try:
        # Prefer to retrieve this from the cache
        return str(root.__dict__[attr])
    except KeyError:
        return getattr_async_unsafe(root, attr)


def resolve_connection(
    source: Union[Type[NodeType], Type[_M]],
    *,
    info: Optional[Info] = None,
    nodes: Optional[AwaitableOrValue[QuerySet[_M]]] = None,
    total_count: Optional[int] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
) -> AwaitableOrValue[Connection[NodeType]]:
    """Resolve model connection, ensuring those are prefetched in a sync context.

    Args:
        source:
            The source model or the model type that implements the `Node` interface
        info:
            Optional gql execution info. Make sure to always provide this or
            otherwise, the queryset cannot be optimized in case DjangoOptimizerExtension
            is enabled. This will also be used for `is_awaitable` check.
        nodes:
            An iterable of already filtered queryset to use in the connection.
            If not provided, `model.objects.all()` will be used
        total_count:
            Optionally provide a total count so that the connection. This will
            avoid having to call `qs.count()` later.
        before:
            Returns the items in the list that come before the specified cursor
        after:
            Returns the items in the list that come after the specified cursor
        first:
            Returns the first n items from the list
        last:
            Returns the items in the list that come after the specified cursor

    Returns:
        The resolved connection

    """
    from strawberry_django_plus import optimizer  # avoid circular import

    if nodes is None:
        if not issubclass(source, Model):
            django_type = get_django_type(source, ensure_type=True)
            source = cast(Type[_M], django_type.model)

        nodes = source.objects.all()
        assert isinstance(nodes, QuerySet)

    if is_awaitable(nodes, info=info):
        return resolve_async(
            nodes,
            lambda resolved: resolve_connection(
                source,
                info=info,
                nodes=resolved,
                total_count=total_count,
                before=before,
                after=after,
                first=first,
                last=last,
            ),
        )

    if info is not None:
        config = get_optimizer_config(info)
        if config is not None:
            # If optimizer extension is enabled, optimize this queryset
            nodes = optimizer.optimize(nodes, info=info, config=config)

    return resolve(
        nodes,
        async_unsafe(
            functools.partial(
                Connection.from_nodes,
                total_count=total_count,
                before=before,
                after=after,
                first=first,
                last=last,
            )
        ),
    )
