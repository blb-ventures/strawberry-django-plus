from typing import Sequence, TypeVar, Union

from typing_extensions import TypeAlias

_T = TypeVar("_T")

TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]
