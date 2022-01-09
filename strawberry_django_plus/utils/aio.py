from typing import Awaitable, Callable, Optional, Type, TypeVar, overload

from graphql.pyutils.is_awaitable import is_awaitable as _is_awaitable
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import TypeGuard

__all__ = [
    "is_awaitable",
    "resolve",
    "resolve_async",
]

_T = TypeVar("_T")
_R = TypeVar("_R")


def is_awaitable(
    value: AwaitableOrValue[_T],
    *,
    info: Optional[Info] = None,
) -> "TypeGuard[Awaitable[_T], _T]":
    """Check if the given value is awaitable.

    Args:
        value:
            The value to check for awaitability
        info:
            Optional gql execution info. If present, will use its implementation
            of `is_awaitable`, which might have some optimizations. Otherwise
            will fallback to `inspect.is_awaitable`

    Returns:
        `True` if the value is awaitable, `False` otherwise

    """
    if info is not None:
        return info._raw_info.is_awaitable(value)
    return _is_awaitable(value)


async def resolve_async(
    value: Awaitable[_T],
    resolver: Callable[[_T], AwaitableOrValue[_R]],
    *,
    ensure_type: Optional[Type[_R]] = None,
    info: Optional[Info] = None,
) -> _R:
    """Call resolver with the awaited value's response.

    Args:
        value:
            The value to be awaited
        resolver:
            The resolver to be called after the value was awaited.
        ensure_type:
            Optional type to ensure that the retval is an instance of it.

    Returns:
        An `Awaitable` with the return value of `resolver(await value)`

    Raises:
        TypeError: If ensure_type was passed and the return value
        is not an instance of it (checked using `instance(retval, ensyure_type)`).

    """
    ret = resolver(await value)
    while is_awaitable(ret, info=info):
        ret = await ret
    if ensure_type is not None and not isinstance(ret, ensure_type):
        raise TypeError(f"{ensure_type} expected, found {repr(ret)}")

    return ret


@overload
def resolve(
    value: _T,
    resolver: Callable[[_T], _R],
    *,
    ensure_type: Optional[Type[_R]] = None,
) -> _R:
    ...


@overload
def resolve(
    value: AwaitableOrValue[_T],
    resolver: Callable[[_T], Awaitable[_R]],
    *,
    ensure_type: Optional[Type[_R]] = None,
    info: Optional[Info] = None,
) -> AwaitableOrValue[_R]:
    ...


@overload
def resolve(
    value: AwaitableOrValue[_T],
    resolver: Callable[[_T], _R],
    *,
    ensure_type: Optional[Type[_R]] = None,
    info: Optional[Info] = None,
) -> AwaitableOrValue[_R]:
    ...


def resolve(value, resolver, *, ensure_type=None, info=None):
    """Call resolver with the value's response.

    Args:
        value:
            The value to be passed to the resolver.
        info:
            The resolver to be called after the value was awaited.
        ensure_type:
            Optional type to ensure that the retval is an instance of it.
        info:
            Optional gql execution info. If present, will use its implementation
            of `is_awaitable`, which might have some optimizations. Otherwise
            will fallback to `inspect.is_awaitable`

    Returns:
        If the value is not awaitable, it will simply return `resolver(value)`.
        Otherwise, an `Awaitable` will be returned that, when awaited will return
        the return value of `resolver(await value)`.

    Raises:
        TypeError: If ensure_type was passed and the return value
        is not an instance of it (checked using `instance(retval, ensyure_type)`).

    """
    if is_awaitable(value, info=info):
        return resolve_async(value, resolver, info=info, ensure_type=ensure_type)

    ret = resolver(value)
    if ensure_type is not None and not isinstance(ret, ensure_type):
        raise TypeError(f"{ensure_type} expected, found {repr(ret)}")

    return ret
