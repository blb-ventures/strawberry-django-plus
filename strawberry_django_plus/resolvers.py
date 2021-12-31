import functools
import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    List,
    Literal,
    Optional,
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

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_R = TypeVar("_R")
_P = ParamSpec("_P")


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]],
    *,
    get_list: Literal[False] = ...,
    get_one: Literal[False] = ...,
) -> Callable[_P, AwaitableOrValue[QuerySet[_M]]]:
    ...


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]],
    *,
    get_list: Literal[True],
) -> Callable[_P, AwaitableOrValue[List[_M]]]:
    ...


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]],
    *,
    get_one: Literal[True],
) -> Callable[_P, AwaitableOrValue[_M]]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_list: Literal[False] = ...,
    get_one: Literal[False] = ...,
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]]],
    Callable[_P, AwaitableOrValue[QuerySet[_M]]],
]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_list: Literal[True],
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]]],
    Callable[_P, AwaitableOrValue[List[_M]]],
]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_one: Literal[True],
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_M], QuerySet[_M]]]]],
    Callable[_P, AwaitableOrValue[_M]],
]:
    ...


def qs_resolver(f=None, *, get_list=False, get_one=False):  # type:ignore
    def resolver(qs):
        if isinstance(qs, BaseManager):
            qs = qs.all()

        if get_one:
            return qs.get()

        # This is what QuerySet does internally to fetch results. After this,
        # iterating over the queryset should be async safe
        qs._fetch_all()  # type:ignore
        if get_list:
            # _fetch_all caches the result to this list
            return qs._result_cache  # type:ignore

        return qs

    async_resolver = sync_to_async(resolver, thread_sensitive=True)

    async def awaitable_resolver(qs):
        qs = await qs
        return await async_resolver(qs)

    def make_resolver(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            qs = func(*args, **kwargs)

            if inspect.isawaitable(qs):
                return awaitable_resolver(qs)
            elif is_async():
                return async_resolver(qs)

            return resolver(qs)

        return wrapper

    if f is not None:
        return make_resolver(f)

    return make_resolver


@overload
def callable_resolver(
    f: Callable[_P, _R],
    *,
    thread_sensitive: bool = True,
) -> Callable[_P, AwaitableOrValue[_R]]:
    ...


@overload
def callable_resolver(
    f=None,
    *,
    thread_sensitive: bool = True,
) -> Callable[[Callable[_P, _R]], Callable[_P, AwaitableOrValue[_R]]]:
    ...


def callable_resolver(f=None, *, thread_sensitive=True):
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


resolve_qs = qs_resolver(lambda qs: qs)
resolve_qs_list = qs_resolver(lambda qs: qs, get_list=True)
resolve_qs_one = qs_resolver(lambda qs: qs, get_one=True)
resolve_callable = callable_resolver(lambda f, *args, **kwargs: f(*args, **kwargs))


def resolve_result(
    res: Any,
    info: Info,
    *,
    resolve_callable_func: Optional[Callable[[Callable[_P, _R]], AwaitableOrValue[_R]]] = None,
    resolve_qs_func: Optional[
        Callable[
            [Union[BaseManager[_M], QuerySet[_M]]],
            AwaitableOrValue[Union[QuerySet[_M], List[_M], _M]],
        ]
    ] = None,
) -> AwaitableOrValue[Any]:
    if isinstance(res, (BaseManager, QuerySet)):
        config = getattr(info.context, "_django_optimizer_config", None)
        if config is not None:
            from .optimizer import optimize

            # If optimizer extension is enabled, optimize this queryset
            res = optimize(res, info=info, config=config)

        if resolve_qs_func is None:
            resolve_qs_func = resolve_qs

        return resolve_qs_func(res)
    elif callable(res):
        if resolve_callable_func is None:
            resolve_callable_func = resolve_callable

        return resolve_result(
            resolve_callable_func(res),
            info,
            resolve_callable_func=resolve_callable_func,
            resolve_qs_func=resolve_qs_func,
        )
    elif info._raw_info.is_awaitable(res):
        return resolve_result_async(
            cast(Awaitable[Any], res),
            info,
            resolve_callable_func=resolve_callable_func,
            resolve_qs_func=resolve_qs_func,
        )

    return res


async def resolve_result_async(
    res: Awaitable[Any],
    info: Info,
    *,
    resolve_callable_func: Optional[Callable[[Callable[_P, _R]], AwaitableOrValue[_R]]] = None,
    resolve_qs_func: Optional[
        Callable[
            [Union[BaseManager[_M], QuerySet[_M]]],
            AwaitableOrValue[Union[QuerySet[_M], List[_M], _M]],
        ]
    ] = None,
) -> AwaitableOrValue[Any]:
    return resolve_result(
        await res,
        info,
        resolve_callable_func=resolve_callable_func,
        resolve_qs_func=resolve_qs_func,
    )
