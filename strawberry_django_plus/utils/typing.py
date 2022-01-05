from typing import Sequence, TypeVar, Union

from django.db.models.base import Model
from typing_extensions import TypeAlias

_M = TypeVar("_M", bound=Model)
_T = TypeVar("_T")

TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]
