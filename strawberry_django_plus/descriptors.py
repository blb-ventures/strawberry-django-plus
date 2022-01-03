from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Optional,
    Type,
    TypeVar,
    Union,
    overload,
)

from django.db.models.base import Model
from django.db.models.query import Prefetch
from typing_extensions import Self

if TYPE_CHECKING:
    from .optimizer import DjangoOptimizerStore, TypeOrSequence

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_R = TypeVar("_R")


class ModelProperty(Generic[_M, _R]):
    """Model property with optimization hinting functionality."""

    name: str
    store: "DjangoOptimizerStore"

    def __init__(
        self,
        func: Callable[[_M], _R],
        *,
        cached: bool = False,
        only: Optional["TypeOrSequence[str]"] = None,
        select_related: Optional["TypeOrSequence[str]"] = None,
        prefetch_related: Optional["TypeOrSequence[Union[str, Prefetch]]"] = None,
    ):
        from .optimizer import DjangoOptimizerStore

        self.func = func
        self.cached = cached
        self.store = DjangoOptimizerStore.from_iterables(
            only=only,
            select_related=select_related,
            prefetch_related=prefetch_related,
        )

    def __set_name__(self, owner: Type[_M], name: str):
        self.name = name

    @overload
    def __get__(self, obj: _M, cls: Type[_M]) -> _R:
        ...

    @overload
    def __get__(self, obj: None, cls: Type[_M]) -> Self:
        ...

    def __get__(self, obj, cls=None):
        if obj is None:
            return self

        if not self.cached:
            return self.func(obj)

        try:
            ret = obj.__dict__[self.name]
        except KeyError:
            ret = self.func(obj)
            obj.__dict__[self.name] = ret

        return ret

    @property
    def description(self) -> Optional[str]:
        return self.func.__doc__

    @property
    def type_annotation(self) -> Union[object, str]:
        ret = self.func.__annotations__.get("return")
        if ret is None:
            raise TypeError(f"missing type annotation from {self.func}")
        return ret


@overload
def model_property(
    func: Callable[[_M], _R],
    *,
    cached: bool = False,
    only: Optional["TypeOrSequence[str]"] = None,
    select_related: Optional["TypeOrSequence[str]"] = None,
    prefetch_related: Optional["TypeOrSequence[Union[str, Prefetch]]"] = None,
) -> ModelProperty[_M, _R]:
    ...


@overload
def model_property(
    func=None,
    *,
    cached: bool = False,
    only: Optional["TypeOrSequence[str]"] = None,
    select_related: Optional["TypeOrSequence[str]"] = None,
    prefetch_related: Optional["TypeOrSequence[Union[str, Prefetch]]"] = None,
) -> Callable[[Callable[[_M], _R]], ModelProperty[_M, _R]]:
    ...


def model_property(
    func=None,
    *,
    cached: bool = False,
    only: Optional["TypeOrSequence[str]"] = None,
    select_related: Optional["TypeOrSequence[str]"] = None,
    prefetch_related: Optional["TypeOrSequence[Union[str, Prefetch]]"] = None,
) -> Any:
    def wrapper(f):
        return ModelProperty(
            f,
            cached=cached,
            only=only,
            select_related=select_related,
            prefetch_related=prefetch_related,
        )

    if func is not None:
        return wrapper(func)

    return wrapper


def model_cached_property(
    func=None,
    *,
    only: Optional["TypeOrSequence[str]"] = None,
    select_related: Optional["TypeOrSequence[str]"] = None,
    prefetch_related: Optional["TypeOrSequence[Union[str, Prefetch]]"] = None,
):
    return model_property(
        func,
        cached=True,
        only=only,
        select_related=select_related,
        prefetch_related=prefetch_related,
    )
