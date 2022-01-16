import contextlib
import dataclasses
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)

from django.db import models
from django.db.models import Prefetch
from django.db.models.fields.reverse_related import (
    ManyToManyRel,
    ManyToOneRel,
    OneToOneRel,
)
from django.db.models.manager import BaseManager
from django.db.models.query import QuerySet
from graphql.type.definition import GraphQLResolveInfo, get_named_type
from strawberry.extensions.base_extension import Extension
from strawberry.schema.schema import Schema
from strawberry.types.execution import ExecutionContext
from strawberry.types.info import Info
from strawberry.types.nodes import InlineFragment, Selection, convert_selections
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.fields.types import resolve_model_field_name
from typing_extensions import TypeAlias

from .descriptors import ModelProperty
from .relay import Connection, Edge, NodeType
from .utils import resolvers
from .utils.inspect import (
    PrefetchInspector,
    get_model_fields,
    get_possible_type_definitions,
    get_selections,
)
from .utils.typing import TypeOrSequence

__all__ = [
    "OptimizerConfig",
    "DjangoOptimizerExtension",
    "OptimizerStore",
    "PrefetchType",
    "optimize",
]

_sentinel = object()
_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)

PrefetchType: TypeAlias = Union[str, Prefetch]


def _get_model_hints(
    model: Type[models.Model],
    schema: Schema,
    type_def: TypeDefinition,
    selection: Selection,
    *,
    config: Optional["OptimizerConfig"] = None,
    prefix: str = "",
    model_cache: Optional[Dict[Type[models.Model], List[Tuple[int, "OptimizerStore"]]]] = None,
    level: int = 0,
) -> "OptimizerStore":
    store = OptimizerStore()
    model_cache = model_cache or {}
    typename = schema.config.name_converter.from_object(type_def)

    # In case this is a relay field, find the selected edges/nodes, the selected fields
    # are actually inside edges -> node selection...
    if type_def.concrete_of and issubclass(type_def.concrete_of.origin, Connection):
        n_type = type_def.type_var_map[NodeType]
        n_type_def = n_type._type_definition  # type:ignore

        for edges in get_selections(selection, typename=typename).values():
            if edges.name != "edges":
                continue

            e_type = Edge._type_definition.resolve_generic(Edge[n_type])  # type:ignore
            e_typename = schema.config.name_converter.from_object(e_type._type_definition)
            for node in get_selections(edges, typename=e_typename).values():
                if node.name != "node":
                    continue

                store |= _get_model_hints(
                    model=model,
                    schema=schema,
                    type_def=n_type_def,
                    selection=node,
                    config=config,
                    prefix=prefix,
                    model_cache=model_cache,
                    level=level,
                )

        return store

    fields = {schema.config.name_converter.get_graphql_name(f): f for f in type_def.fields}
    model_fields = get_model_fields(model)

    # Make sure that the model's pk is always selected when using only
    pk = model._meta.pk
    if pk is not None:
        store.only.append(pk.attname)

    for f_selection in get_selections(selection, typename=typename).values():
        field = fields[f_selection.name]

        # Add annotations from the field if they exist
        field_store = getattr(field, "store", None)
        if field_store is not None:
            store |= field_store.with_prefix(prefix) if prefix else field_store

        # Then from the model property if one is defined
        model_attr = getattr(model, field.python_name, None)
        if model_attr is not None and isinstance(model_attr, ModelProperty):
            attr_store = model_attr.store
            store |= attr_store.with_prefix(prefix) if prefix else attr_store

        # Lastly, from the django field itself
        model_fieldname: str = getattr(field, "django_name", field.python_name)
        model_field = model_fields.get(model_fieldname, None)
        if model_field is not None:
            path = f"{prefix}{model_fieldname}"

            if isinstance(model_field, (models.ForeignKey, OneToOneRel)):
                store.only.append(path)
                store.select_related.append(path)

                # If adding a reverse relation, make sure to select its pointer to us,
                # or else this might causa a refetch from the database
                if isinstance(model_field, OneToOneRel):
                    remote_field = model_field.remote_field
                    store.only.append(f"{path}__{resolve_model_field_name(remote_field)}")

                for f_type_def in get_possible_type_definitions(field.type):
                    f_model = model_field.related_model
                    f_store = _get_model_hints(
                        f_model,
                        schema,
                        f_type_def,
                        f_selection,
                        config=config,
                        model_cache=model_cache,
                        level=level + 1,
                    )
                    model_cache.setdefault(f_model, []).append((level, f_store))
                    store |= f_store.with_prefix(f"{path}__")
            elif isinstance(model_field, (models.ManyToManyField, ManyToManyRel, ManyToOneRel)):
                f_types = list(get_possible_type_definitions(field.type))
                if len(f_types) > 1:
                    # This might be a generic foreign key. In this case, just prefetch it
                    store.prefetch_related.append(model_fieldname)
                elif len(f_types) == 1:
                    remote_field = model_field.remote_field
                    f_store = _get_model_hints(
                        remote_field.model,
                        schema,
                        f_types[0],
                        f_selection,
                        config=config,
                        model_cache=model_cache,
                        level=level + 1,
                    )
                    if (config is None or config.enable_only) and f_store.only:
                        # If adding a reverse relation, make sure to select its pointer to us,
                        # or else this might causa a refetch from the database
                        f_store.only.append(cast(str, resolve_model_field_name(remote_field)))

                    model_cache.setdefault(remote_field.model, []).append((level, f_store))

                    f_qs = f_store.apply(remote_field.model.objects.all(), config=config)
                    f_prefetch = Prefetch(path, queryset=f_qs)
                    f_prefetch._optimizer_sentinel = _sentinel  # type:ignore
                    store.prefetch_related.append(f_prefetch)
            else:
                store.only.append(path)

    # DJango keeps track of known fields. That means that if one model select_related or
    # prefetch_related another one, and later another one select_related or prefetch_related the
    # model again, if the used fields there where not optimized in this call django would have
    # to fetch those again. By mergint those with us we are making sure to avoid that
    for inner_level, inner_store in model_cache.get(model, []):
        if inner_level > level and inner_store:
            # We only want the only/select_related from this. prefetch_related is something else
            store.only.extend(inner_store.only)
            store.select_related.extend(inner_store.select_related)

    return store


def optimize(
    qs: Union[QuerySet[_M], BaseManager[_M]],
    info: Union[GraphQLResolveInfo, Info],
    *,
    config: Optional["OptimizerConfig"] = None,
    store: Optional["OptimizerStore"] = None,
) -> QuerySet[_M]:
    """Optimize the given queryset considering the gql info.

    This will look through the gql selections, fields and model hints and apply
    `only`, `select_related` and `prefetch_related` optimizations according those
    on the `QuerySet`_.

    Note:
        This do not execute the queryset, it only optimizes it for when it is actually
        executed.

        It will also avoid doing any extra optimization if the queryset already has
        cached results in it, to avoid triggering extra queries later.

    Args:
        qs:
            The queryset to be optimized
        info:
            The current field execution info
        config:
            Optional config to use when doing the optimization
        config:
            Optional initial store to use for the optimization

    Returns:
        The optimized queryset

    .. _QuerySet:
        https://docs.djangoproject.com/en/dev/ref/models/querysets/

    """
    if isinstance(qs, BaseManager):
        qs = cast(QuerySet[_M], qs.all())

    # If the queryset already has cached results, just return it
    if qs._result_cache:  # type:ignore
        return qs

    if isinstance(info, Info):
        info = info._raw_info
    config = config or OptimizerConfig()
    store = store or OptimizerStore()
    schema = cast(Schema, info.schema._strawberry_schema)  # type:ignore

    field_name = info.field_name
    gql_type = get_named_type(info.return_type)
    strawberry_type = schema.get_type_by_name(gql_type.name)
    if strawberry_type is None:
        return qs

    for type_def in get_possible_type_definitions(strawberry_type):
        selection = next(
            (
                s
                for s in convert_selections(info, info.field_nodes)
                if not isinstance(s, InlineFragment) and s.name == field_name
            ),
            None,
        )
        if not selection:
            continue

        store |= _get_model_hints(
            qs.model,
            schema,
            type_def,
            selection,
            config=config,
        )

    # Nothing found do optimize, just skip this...
    if not store:
        return qs

    return store.apply(qs, config=config)


@dataclasses.dataclass
class OptimizerConfig:
    """Django optimization configuration.

    Attributes:
        enable_only:
            Enable `QuerySet.only` optimizations
        enable_select_related:
            Enable `QuerySet.select_related` optimizations
        enable_prefetch_related:
            Enable `QuerySet.prefetch_related` optimizations

    """

    enable_only: bool = dataclasses.field(default=True)
    enable_select_related: bool = dataclasses.field(default=True)
    enable_prefetch_related: bool = dataclasses.field(default=True)


@dataclasses.dataclass
class OptimizerStore:
    """Django optimization store.

    Attributes:
        only:
            Set of values to optimize using `QuerySet.only`
        selected:
            Set of values to optimize using `QuerySet.select_related`
        prefetch_related:
            Set of values to optimize using `QuerySet.prefetch_related`

    """

    only: List[str] = dataclasses.field(default_factory=list)
    select_related: List[str] = dataclasses.field(default_factory=list)
    prefetch_related: List[PrefetchType] = dataclasses.field(default_factory=list)

    def __bool__(self):
        return any([self.only, self.select_related, self.prefetch_related])

    def __ior__(self, other: "OptimizerStore"):
        self.only.extend(other.only)
        self.select_related.extend(other.select_related)
        self.prefetch_related.extend(other.prefetch_related)
        return self

    def __or__(self, other: "OptimizerStore"):
        return self.copy().__ior__(other)

    def copy(self):
        return self.__class__(
            only=self.only[:],
            select_related=self.select_related[:],
            prefetch_related=self.prefetch_related[:],
        )

    @classmethod
    def with_hints(
        cls,
        *,
        only: Optional[TypeOrSequence[str]] = None,
        select_related: Optional[TypeOrSequence[str]] = None,
        prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    ):
        return cls(
            only=[only] if isinstance(only, str) else list(only or []),
            select_related=(
                [select_related] if isinstance(select_related, str) else list(select_related or [])
            ),
            prefetch_related=(
                [prefetch_related]
                if isinstance(prefetch_related, (str, Prefetch))
                else list(prefetch_related or [])
            ),
        )

    def with_prefix(self, prefix: str):
        prefetch_related = []
        for p in self.prefetch_related:
            if isinstance(p, str):
                prefetch_related.append(f"{prefix}{p}")
            elif isinstance(p, Prefetch):
                p.add_prefix(prefix)
                prefetch_related.append(p)
            else:  # pragma:nocover
                raise AssertionError(f"Unexpected prefetch type {repr(p)}")

        return self.__class__(
            only=[f"{prefix}{i}" for i in self.only],
            select_related=[f"{prefix}{i}" for i in self.select_related],
            prefetch_related=prefetch_related,
        )

    def apply(
        self,
        qs: QuerySet[_M],
        *,
        config: Optional[OptimizerConfig] = None,
    ) -> QuerySet[_M]:
        config = config or OptimizerConfig()

        if config.enable_prefetch_related and self.prefetch_related:
            # Add all str at the same time to make it easier to handle Prefetch below
            to_prefetch: Dict[str, PrefetchType] = {
                p: p for p in self.prefetch_related if isinstance(p, str)
            }

            for p in self.prefetch_related:
                # Already added above
                if isinstance(p, str):
                    continue

                path = cast(str, p.prefetch_to)  # type:ignore
                existing = to_prefetch.get(path)
                # The simplest case. The prefetch doesn't exist or is a string.
                # In this case, just replace it.
                if not existing or isinstance(existing, str):
                    to_prefetch[path] = p
                    continue

                p1 = PrefetchInspector(existing)
                p2 = PrefetchInspector(p)
                if getattr(existing, "_sentinel", None) is _sentinel:
                    ret = p1.merge(p2, allow_unsafe_ops=True)
                elif getattr(p, "_sentinel", None) is _sentinel:
                    ret = p2.merge(p1, allow_unsafe_ops=True)
                else:
                    # The order here doesn't matter
                    ret = p1.merge(p2)

                to_prefetch[path] = ret.prefetch

            qs = qs.prefetch_related(*to_prefetch.values())

        if config.enable_select_related and self.select_related:
            qs = qs.select_related(*self.select_related)

        if config.enable_only and self.only:
            qs = qs.only(*self.only)

        return qs


class DjangoOptimizerExtension(Extension):
    """Automatically optimize returned querysets from internal resolvers.

    Attributes:
        enable_only_optimization:
            Enable `QuerySet.only` optimizations
        enable_select_related_optimization:
            Enable `QuerySet.select_related` optimizations
        enable_prefetch_related_optimization:
            Enable `QuerySet.prefetch_related` optimizations

    Examples:
        Add the following to your schema configuration.

        >>> import strawberry
        >>> from strawberry_django_plus.optimizer import DjangoOptimizerExtension
        ...
        >>> schema = strawberry.Schema(
        ...     Query,
        ...     extensions=[
        ...         DjangoOptimizerExtension(),
        ...     ]
        ... )

    """

    _disabled: ClassVar[bool] = False

    def __init__(
        self,
        *,
        enable_only_optimization: bool = True,
        enable_select_related_optimization: bool = True,
        enable_prefetch_related_optimization: bool = True,
        execution_context: ExecutionContext = None,
    ):
        super().__init__(execution_context=execution_context)  # type:ignore
        self._config = OptimizerConfig(
            enable_only=enable_only_optimization,
            enable_select_related=enable_select_related_optimization,
            enable_prefetch_related=enable_prefetch_related_optimization,
        )

    @classmethod
    @contextlib.contextmanager
    def disable(cls):
        cls._disabled = True
        yield
        cls._disabled = False

    def on_request_start(self) -> AwaitableOrValue[None]:
        if self._disabled:
            return

        self.execution_context.context._django_optimizer_config = self._config

    def resolve(
        self,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ) -> AwaitableOrValue[Any]:
        ret = _next(root, info, *args, **kwargs)

        if self._disabled:
            return ret

        if isinstance(ret, (BaseManager, QuerySet)):
            if isinstance(ret, BaseManager):
                ret = ret.all()
            if not ret._result_cache:  # type:ignore
                return resolvers.resolve_qs(optimize(qs=ret, info=info, config=self._config))

        return ret
