from typing import TypeVar, Union

import pytest

from strawberry_django_plus.utils.aio import resolve

_T = TypeVar("_T")


def test_resolve():
    def resolver(v):
        return v + 1

    assert resolve(1, resolver) == 2


def test_resolve_with_ensure_type():
    def resolver(v):
        return v + 1

    assert resolve(1, resolver, ensure_type=int) == 2
    assert resolve(1, resolver, ensure_type=Union[int, float]) == 2
    assert resolve(1.0, resolver, ensure_type=Union[int, float]) == 2.0
    with pytest.raises(TypeError):
        resolve(1, resolver, ensure_type=str)


async def test_resolve_async():
    def resolver(v):
        return v + 1

    async def get_value(v: _T) -> _T:
        return v

    assert (await resolve(get_value(1), resolver)) == 2


async def test_resolve_with_ensure_type_async():
    def resolver(v):
        return v + 1

    async def get_value(v: _T) -> _T:
        return v

    assert (await resolve(get_value(1), resolver, ensure_type=int)) == 2
    assert (await resolve(get_value(1), resolver, ensure_type=Union[int, float])) == 2
    assert (await resolve(get_value(1.0), resolver, ensure_type=Union[int, float])) == 2.0
    with pytest.raises(TypeError):
        await resolve(get_value(1), resolver, ensure_type=str)
