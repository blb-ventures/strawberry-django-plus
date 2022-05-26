import functools
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from graphql.pyutils.is_awaitable import is_awaitable as _is_awaitable
from graphql.type.definition import GraphQLResolveInfo
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import ParamSpec, TypeGuard

__all__ = [
    "is_awaitable",
    "resolve",
    "resolve_async",
]

_T = TypeVar("_T")
_P = ParamSpec("_P")
_R = TypeVar("_R")
_E = TypeVar("_E")


def is_awaitable(
    value: AwaitableOrValue[_T],
    *,
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> TypeGuard[Awaitable[_T]]:
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
        if isinstance(info, Info):
            info = info._raw_info
        return info.is_awaitable(value)
    return _is_awaitable(value)


async def resolve_async(
    value: Awaitable[_T],
    resolver: Callable[[_T], AwaitableOrValue[_R]],
    *,
    ensure_type: Optional[Type[_R]] = None,
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
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
        ret = await cast(Awaitable, ret)

    if ensure_type is not None and not isinstance(ret, ensure_type):
        raise TypeError(f"{ensure_type} expected, found {repr(ret)}")

    # FIXME: Remove cast once pyright resolves the negative TypeGuard form
    ret = cast(_R, ret)

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
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> AwaitableOrValue[_R]:
    ...


@overload
def resolve(
    value: AwaitableOrValue[_T],
    resolver: Callable[[_T], _R],
    *,
    ensure_type: Optional[Type[_R]] = None,
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
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


@overload
def resolver(
    func: Callable[_P, AwaitableOrValue[_R]],
    *,
    on_result: Callable[[_R], _T],
    on_error: Callable[[Exception], _E],
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> Callable[_P, AwaitableOrValue[Union[_T, _E]]]:
    ...


@overload
def resolver(
    func: Callable[_P, AwaitableOrValue[_R]],
    *,
    on_result: Callable[[_R], _T],
    on_error: None = ...,
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> Callable[_P, AwaitableOrValue[_T]]:
    ...


@overload
def resolver(
    func: Callable[_P, AwaitableOrValue[_R]],
    *,
    on_result: None = ...,
    on_error: Callable[[Exception], _E],
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> Callable[_P, AwaitableOrValue[Union[_R, _E]]]:
    ...


@overload
def resolver(
    func: Callable[_P, AwaitableOrValue[_R]],
    *,
    on_result: None = ...,
    on_error: None = ...,
    info: Optional[Union[Info, GraphQLResolveInfo]] = None,
) -> Callable[_P, AwaitableOrValue[_R]]:
    ...


def resolver(func, *, on_result=None, on_error=None, info=None) -> Any:
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        try:
            retval = func(*args, **kwargs)
        except Exception as e:
            if on_error is None:
                raise

            retval = on_error(e)
            if isinstance(retval, BaseException):
                raise retval

        if is_awaitable(retval, info=info):

            async def resolve():
                try:
                    resolved = await retval
                except Exception as exc:
                    if on_error is not None:
                        exc = on_error(exc)

                    if not isinstance(exc, BaseException):
                        return exc

                    raise exc
                else:
                    if on_result is not None:
                        resolved = on_result(resolved)

                    return resolved

            return resolve()

        if on_result is not None:
            retval = on_result(retval)

        return retval

    return wrapped
