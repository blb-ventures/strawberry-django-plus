import functools
import inspect
from typing import Callable, List, Literal, TypeVar, Union, overload

from asgiref.sync import sync_to_async
from django.db.models import Model, QuerySet
from django.db.models.manager import BaseManager
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import ParamSpec

from .utils import is_async

_T = TypeVar("_T", bound=Model)
_R = TypeVar("_R")
_P = ParamSpec("_P")


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]],
    *,
    get_list: Literal[False] = ...,
    get_one: Literal[False] = ...,
) -> Callable[_P, AwaitableOrValue[QuerySet[_T]]]:
    ...


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]],
    *,
    get_list: Literal[True],
) -> Callable[_P, AwaitableOrValue[List[_T]]]:
    ...


@overload
def qs_resolver(
    f: Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]],
    *,
    get_one: Literal[True],
) -> Callable[_P, AwaitableOrValue[_T]]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_list: Literal[False] = ...,
    get_one: Literal[False] = ...,
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]]],
    Callable[_P, AwaitableOrValue[QuerySet[_T]]],
]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_list: Literal[True],
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]]],
    Callable[_P, AwaitableOrValue[List[_T]]],
]:
    ...


@overload
def qs_resolver(
    f=None,
    *,
    get_one: Literal[True],
) -> Callable[
    [Callable[_P, AwaitableOrValue[Union[BaseManager[_T], QuerySet[_T]]]]],
    Callable[_P, AwaitableOrValue[_T]],
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
