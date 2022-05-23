import functools
import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
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
import warnings

from asgiref.sync import async_to_sync, sync_to_async
from django.db.models import Model, QuerySet
from django.db.models.manager import BaseManager
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.utils import is_async
from typing_extensions import ParamSpec

from strawberry_django_plus.relay import Connection, GlobalID, Node, NodeType

from .aio import is_awaitable, resolve, resolve_async
from .inspect import get_django_type

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_R = TypeVar("_R")
_P = ParamSpec("_P")
_sentinel = object()
_async_to_sync = cast(
    Callable[[Callable[[Awaitable[_T]], Coroutine[Any, Any, _T]]], Callable[[Awaitable[_T]], _T]],
    async_to_sync,
)


@overload
def async_safe(
    func: Callable[_P, _R],
    /,
    *,
    thread_sensitive: bool = True,
) -> Callable[_P, AwaitableOrValue[_R]]:
    ...


@overload
def async_safe(
    func: None,
    /,
    *,
    thread_sensitive: bool = True,
) -> Callable[[Callable[_P, _R]], Callable[_P, AwaitableOrValue[_R]]]:
    ...


def async_safe(func=None, /, *, thread_sensitive=True):
    """Decorates a function to be async safe, ensuring it is called in a sync context always.

    - If `f` is a coroutine function, this is a noop.
    - When running, if an asyncio loop is running, the function will
      be called wrapped in an asgi.sync_to_async_ context.

    Args:
        func:
            The function to call
        thread_sensitive:
            If the sync function should run in the same thread as all other
            thread_sensitive functions

    Returns:
        The wrapped function

    .. _asgi.sync_to_async:
        https://docs.djangoproject.com/en/dev/topics/async/#asgiref.sync.sync_to_async

    """

    def make_resolver(f):
        if (
            inspect.iscoroutinefunction(f)
            or inspect.isasyncgenfunction(f)
            or (isinstance(f, StrawberryResolver) and f.is_async)
        ):
            return f

        async_resolver = sync_to_async(f, thread_sensitive=thread_sensitive)

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if is_async():
                resolver = async_resolver
            else:
                resolver = f

            return resolver(*args, **kwargs)

        return wrapper

    if func is not None:
        return make_resolver(func)

    return make_resolver


def async_unsafe(*args, **kwargs):
    warnings.warn("use `async_safe` instead", DeprecationWarning)
    return async_safe(*args, **kwargs)


@_async_to_sync
async def resolve_sync(value: Awaitable[_T]) -> _T:
    """Resolves the given value, resolving any returned awaitable.

    Args:
        value:
            The awaitable to be resolved

    Returns:
        The resolved value.

    """
    return await value


getattr_async_safe = async_safe(lambda obj, key, *args: getattr(obj, key, *args))
getattr_str_async_safe = async_safe(lambda obj, key, *args: str(getattr(obj, key, *args)))


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
    resolver: Optional[Callable[[QuerySet[_M]], AwaitableOrValue[_R]]],
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
    qs_resolver: None = ...,
    info: Optional[Info] = ...,
) -> AwaitableOrValue[QuerySet[_M]]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]],
    *,
    info: Optional[Info] = ...,
    qs_resolver: Callable[[Union[BaseManager[_M], QuerySet[_M]]], AwaitableOrValue[_T]],
) -> AwaitableOrValue[_T]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[Callable[[], _T]],
    *,
    qs_resolver: None = ...,
    info: Optional[Info] = ...,
) -> AwaitableOrValue[_T]:
    ...


@overload
def resolve_result(
    res: AwaitableOrValue[_T],
    *,
    info: Optional[Info] = ...,
    qs_resolver: Callable[[Union[BaseManager[Any], QuerySet[Any]]], AwaitableOrValue[Any]],
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
            ext = optimizer.optimizer.get()
            if ext is not None:
                # If optimizer extension is enabled, optimize this queryset
                res = ext.optimize(qs=res, info=info)

        qs_resolver = qs_resolver or resolve_qs
        return qs_resolver(res)
    elif callable(res):
        return resolve_result(async_safe(res)(), info=info, qs_resolver=qs_resolver)
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
    filter_perms: bool = False,
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
    # avoid circular import
    from strawberry_django_plus.permissions import filter_with_perms

    if issubclass(source, Model):
        origin = None
    else:
        origin = source
        django_type = get_django_type(source, ensure_type=True)
        source = cast(Type[_M], django_type.model)

    qs = source._default_manager.all()

    if origin and hasattr(origin, "get_queryset"):
        qs = origin.get_queryset(qs, info)  # type:ignore

    if node_ids is not None:
        id_attr = getattr(origin, "id_attr", "pk")
        qs = qs.filter(
            **{f"{id_attr}__in": [i.node_id if isinstance(i, GlobalID) else i for i in node_ids]}
        )

    if filter_perms:
        assert info
        qs = filter_with_perms(qs, info)

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


def resolve_model_node(source, node_id, *, info: Optional[Info] = None, required=False):
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
    if issubclass(source, Model):
        origin = None
    else:
        origin = source
        django_type = get_django_type(source, ensure_type=True)
        source = cast(Type[_M], django_type.model)

    if isinstance(node_id, GlobalID):
        node_id = node_id.node_id

    id_attr = getattr(origin, "id_attr", "pk")

    qs = source._default_manager.filter(**{id_attr: node_id})

    if origin and hasattr(origin, "get_queryset"):
        qs = origin.get_queryset(qs, info)

    if required:
        ret = resolve_result(qs, info=info, qs_resolver=resolve_qs_get_one)
    else:
        ret = resolve_result(qs, info=info, qs_resolver=resolve_qs_get_first)

    return ret


def resolve_model_id(source: Union[Type[Node], Type[_M]], root: Model) -> AwaitableOrValue[str]:
    """Resolve the model id, ensuring it is retrieved in a sync context.

    Args:
        source:
            The source model or the model type that implements the `Node` interface
        root:
            The source model object.

    Returns:
        The resolved object id

    """
    id_attr = getattr(source, "id_attr", "pk")
    assert isinstance(root, Model)
    if id_attr == "pk":
        pk = root.__class__._meta.pk
        assert pk
        id_attr = pk.attname
    assert id_attr
    try:
        # Prefer to retrieve this from the cache
        return str(root.__dict__[id_attr])
    except KeyError:
        return getattr_str_async_safe(root, id_attr)


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
    filter_perms: bool = False,
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
    # avoid circular import
    from strawberry_django_plus import optimizer
    from strawberry_django_plus.permissions import filter_with_perms

    if nodes is None:
        if issubclass(source, Model):
            origin = None
        else:
            origin = source
            django_type = get_django_type(source, ensure_type=True)
            source = cast(Type[_M], django_type.model)

        nodes = source._default_manager.all()
        assert isinstance(nodes, QuerySet)

        if origin and hasattr(origin, "get_queryset"):
            nodes = origin.get_queryset(nodes, info)  # type:ignore

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
                filter_perms=filter_perms,
            ),
        )

    # FIXME: Remove cast once pyright resolves the negative TypeGuard form
    nodes = cast(QuerySet[_M], nodes)

    if filter_perms:
        assert info
        nodes = filter_with_perms(nodes, info)

    if info is not None:
        ext = optimizer.optimizer.get()
        if ext is not None:
            # If optimizer extension is enabled, optimize this queryset
            nodes = ext.optimize(nodes, info=info)

    return resolve(
        nodes,
        async_safe(
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
