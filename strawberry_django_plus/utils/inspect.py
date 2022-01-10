import dataclasses
import functools
import itertools
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Generator,
    List,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from django.db import models
from django.db.models.expressions import Expression
from django.db.models.fields import Field
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.db.models.query import Prefetch, QuerySet
from django.db.models.sql.query import Query
from django.db.models.sql.where import WhereNode
from graphql.type.definition import GraphQLResolveInfo
from strawberry.django.context import StrawberryDjangoContext
from strawberry.lazy_type import LazyType
from strawberry.type import StrawberryContainer, StrawberryType, StrawberryTypeVar
from strawberry.types.info import Info
from strawberry.types.nodes import (
    FragmentSpread,
    InlineFragment,
    SelectedField,
    Selection,
)
from strawberry.types.types import TypeDefinition
from strawberry.union import StrawberryUnion
from strawberry.utils.str_converters import to_camel_case
from strawberry_django.fields.types import resolve_model_field_name

from strawberry_django_plus.utils.pyutils import (
    DictTree,
    dicttree_intersect_diff,
    dicttree_merge,
)

if TYPE_CHECKING:
    from strawberry_django_plus.optimizer import OptimizerConfig
    from strawberry_django_plus.type import StrawberryDjangoType

try:
    # Try to use the smaller/faster cache decorator if available
    _cache = functools.cache  # type:ignore
except AttributeError:
    _cache = functools.lru_cache

_T = TypeVar("_T")
_O = TypeVar("_O", bound=type)
_M = TypeVar("_M", bound=models.Model)
_R = TypeVar("_R")


@_cache
def get_model_fields(
    model: Type[models.Model],
    *,
    camel_case: bool = False,
    is_input: bool = False,
    is_filter: bool = False,
) -> Dict[str, Union[Field, ForeignObjectRel]]:
    """Get a list of model fields."""
    fields = {}
    for f in model._meta.get_fields():
        name = cast(str, resolve_model_field_name(f, is_input=is_input, is_filter=is_filter))
        if camel_case:
            name = to_camel_case(name)
        fields[name] = f
    return fields


@overload
def get_django_type(
    type_: _O,
    *,
    ensure_type: Literal[False] = ...,
) -> Optional["StrawberryDjangoType[_O, Any]"]:
    ...


@overload
def get_django_type(
    type_: _O,
    *,
    ensure_type: Literal[True],
) -> "StrawberryDjangoType[_O, Any]":
    ...


def get_django_type(type_, *, ensure_type=False):
    """Retrieve the StrawberryDjangoType from type_.

    Args:
        type_:
            The type to retrieve the django type from
        ensure_type:
            If we should ensure that the type is indeed a django type.
            If this is false, the result might be None.

    Returns:
        The retrieved StrawberryDjangoType

    Raises:
        TypeError:
            If the type ensuring fails

    """
    from strawberry_django_plus.type import StrawberryDjangoType

    django_type = getattr(type_, "_django_type", None)
    if ensure_type and not isinstance(django_type, StrawberryDjangoType):
        raise TypeError(f"{type_} does not contain a StrawberryDjangoType")

    return django_type


def get_optimizer_config(
    context: Union[GraphQLResolveInfo, Info, StrawberryDjangoContext]
) -> Optional["OptimizerConfig"]:
    """Get the django optimizer config for the current execution context.

    Args:
        info:
            The current execution info

    Returns:
        The current config or None in case the extension is not enabled

    """
    if isinstance(context, (GraphQLResolveInfo, Info)):
        context = context.context

    return getattr(context, "_django_optimizer_config", None)


def get_possible_types(
    gql_type: Union[TypeDefinition, StrawberryType, type],
    type_def: Optional[TypeDefinition] = None,
) -> Generator[type, None, None]:
    """Resolve all possible types for gql_type.

    Args:
        gql_type:
            The type to retrieve possibilities from.
        type_def:
            Optional type definition to use to resolve type vars. This is
            mostly used internally, no need to pass this.

    Yields:
        All possibilities for the type

    """
    if isinstance(gql_type, TypeDefinition):
        yield from get_possible_types(gql_type.origin, type_def=gql_type)
    elif isinstance(gql_type, LazyType):
        yield from get_possible_types(gql_type.resolve_type())
    elif isinstance(gql_type, StrawberryTypeVar) and type_def is not None:
        # Try to resolve TypeVar
        for f in type_def.fields:
            f_type = f.type
            if not isinstance(f_type, StrawberryTypeVar):
                continue

            resolved = type_def.type_var_map.get(f_type.type_var, None)
            if resolved is not None:
                yield from get_possible_types(resolved)
    elif isinstance(gql_type, StrawberryContainer):
        yield from get_possible_types(gql_type.of_type)
    elif isinstance(gql_type, StrawberryUnion):
        yield from itertools.chain.from_iterable(
            (get_possible_types(t) for t in gql_type.types),
        )
    elif isinstance(gql_type, StrawberryType):
        # Nothing to return here
        pass
    elif isinstance(gql_type, type):
        yield gql_type


def get_possible_type_definitions(
    gql_type: Union[TypeDefinition, StrawberryType, type]
) -> Generator[TypeDefinition, None, None]:
    """Resolve all possible type definitions for gql_type.

    Args:
        gql_type:
            The type to retrieve possibilities from.

    Yields:
        All possibilities for the type

    """
    if isinstance(gql_type, TypeDefinition):
        yield gql_type
        return

    for t in get_possible_types(gql_type):
        if isinstance(t, TypeDefinition):
            yield t
        elif hasattr(t, "_type_definition"):
            yield t._type_definition  # type:ignore


def get_selections(
    selection: Selection,
    *,
    typename: Optional[str] = None,
) -> Dict[str, SelectedField]:
    """Resolve subselections considering fragments.

    Args:
        selection:
            The selection to retrieve subselections from
        typename:
            Only resolve fragments for that typename

    Yields:
        All possibilities for the type

    """
    # Because of the way graphql spreads fragments,
    # later selections should replace previous ones
    ret: Dict[str, SelectedField] = {}

    for s in selection.selections:
        if isinstance(s, SelectedField):
            # @include(if: <bool>)
            include = s.directives.get("include")
            if include and not include["if"]:
                continue

            # @skip(if: <bool>)
            skip = s.directives.get("skip")
            if skip and skip["if"]:
                continue

            ret[s.alias or s.name] = s
        elif isinstance(s, (FragmentSpread, InlineFragment)):
            if typename is not None and s.type_condition != typename:
                continue

            for f_name, f in get_selections(s, typename=typename).items():
                existing = ret.get(f_name)
                if existing is not None:
                    f.selections = list(
                        {
                            **get_selections(existing),
                            **get_selections(f),
                        }.values()
                    )
                ret[f.name] = f
        else:  # pragma:nocover
            raise AssertionError(f"Unknown selection type {repr(s)}")

    return ret


@dataclasses.dataclass(eq=True)
class PrefetchInspector:
    """Prefetch hints."""

    prefetch: Prefetch
    qs: QuerySet = dataclasses.field(init=False, compare=False)
    query: Query = dataclasses.field(init=False, compare=False)

    def __post_init__(self):
        self.qs = cast(QuerySet, self.prefetch.queryset)  # type:ignore
        self.query = self.qs.query

    @property
    def only(self) -> Optional[FrozenSet[str]]:
        if self.query.deferred_loading[1]:
            return None
        return frozenset(self.query.deferred_loading[0])

    @only.setter
    def only(self, value=Optional[Sequence[str]]):
        value = frozenset(v for v in (value or []) if v is not None)
        self.query.deferred_loading = (value, len(value) == 0)

    @property
    def defer(self) -> Optional[FrozenSet[str]]:
        if not self.query.deferred_loading[1]:
            return None
        return frozenset(self.query.deferred_loading[0])

    @defer.setter
    def defer(self, value=Optional[Sequence[str]]):
        value = frozenset(v for v in (value or []) if v is not None)
        self.query.deferred_loading = (value, True)

    @property
    def select_related(self) -> Optional[DictTree]:
        if not isinstance(self.query.select_related, dict):
            return None
        return self.query.select_related

    @select_related.setter
    def select_related(self, value=Optional[DictTree]):
        self.query.select_related = value or {}

    @property
    def prefetch_related(self) -> List[Union[Prefetch, str]]:
        return list(self.qs._prefetch_related_lookups)  # type:ignore

    @prefetch_related.setter
    def prefetch_related(self, value=Optional[Sequence[Union[Prefetch, str]]]):
        self.query.select_related = tuple(value or [])  # type:ignore

    @property
    def annotations(self) -> Dict[str, Expression]:
        return self.query.annotations

    @annotations.setter
    def annotations(self, value=Optional[Dict[str, Expression]]):
        self.query.annotations = value or {}  # type:ignore

    @property
    def extra(self) -> DictTree:
        return self.query.extra

    @extra.setter
    def extra(self, value=Optional[DictTree]):
        self.query.extra = value or {}  # type:ignore

    @property
    def where(self) -> WhereNode:
        return self.query.where  # type:ignore

    @where.setter
    def where(self, value=Optional[WhereNode]):
        self.query.where = value or WhereNode()  # type:ignore

    def merge(self, other: "PrefetchInspector", allow_unsafe_ops=False):
        if not allow_unsafe_ops and self.where != other.where:
            raise ValueError(
                "Tried to prefetch 2 queries with different filters to the "
                "same attribute. Use `to_attr` in this case..."
            )

        # Merge select_related
        self.select_related = dicttree_merge(self.select_related or {}, other.select_related or {})

        # Merge only/deferred
        if not allow_unsafe_ops and (self.defer is None) != (other.defer is None):
            raise ValueError(
                "Tried to prefetch 2 queries with with different deferred "
                "operations. Use only `only` or `deferred`, not both..."
            )
        if self.only and other.only:
            self.only = self.only | other.only
        elif self.defer and other.defer:
            self.defer = self.defer | other.defer
        else:
            # One has defer, the other only. In this case, defer nothing
            self.defer = frozenset()

        # Merge annotations
        s_annotations = self.annotations
        o_annotations = other.annotations
        if not allow_unsafe_ops:
            for k in set(s_annotations) & set(o_annotations):
                if s_annotations[k] != o_annotations[k]:
                    raise ValueError("Tried to prefetch 2 queries with overlapping annotations.")
        self.annotations = {**s_annotations, **o_annotations}

        # Merge extra
        s_extra = self.extra
        o_extra = other.extra
        if not allow_unsafe_ops and dicttree_intersect_diff(s_extra, o_extra):
            raise ValueError("Tried to prefetch 2 queries with overlapping extras.")
        self.extra = {**s_extra, **o_extra}

        prefetch_related: Dict[str, Union[str, Prefetch]] = {}
        for p in itertools.chain(self.prefetch_related, other.prefetch_related):
            if isinstance(p, str):
                if p not in prefetch_related:
                    prefetch_related[p] = p
                continue

            path = p.prefetch_to  # type:ignore
            existing = prefetch_related.get(path)
            if not existing or isinstance(existing, str):
                prefetch_related[path] = p
                continue

            inspector = PrefetchInspector(existing).merge(PrefetchInspector(p))
            prefetch_related[path] = inspector.prefetch

        self.prefetch_related = prefetch_related

        return self
