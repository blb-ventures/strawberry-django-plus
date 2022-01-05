import dataclasses
from typing import Any, Callable, Optional, Sequence, Set, Type, TypeVar, Union, cast

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
from strawberry.types.info import Info
from strawberry.types.nodes import Selection, convert_selections
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.fields.types import resolve_model_field_name

from .descriptors import ModelProperty
from .relay import Connection, NodeType
from .utils import resolvers
from .utils.inspect import get_model_fields, get_possible_type_definitions
from .utils.typing import TypeOrSequence

__all__ = [
    "DjangoOptimizerConfig",
    "DjangoOptimizerExtension",
    "DjangoOptimizerStore",
    "optimize",
]

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)


def _ensure_set(args: Optional[TypeOrSequence[_T]]) -> Set[_T]:
    if args is None:
        return set()

    if not isinstance(args, Sequence):
        return {args}

    ret = set(args)
    assert len(ret) == len(args)

    return ret


def _get_model_hints(
    model: Type[models.Model],
    schema: Schema,
    type_def: TypeDefinition,
    selection: Selection,
    *,
    config: Optional["DjangoOptimizerConfig"] = None,
    prefix: str = "",
) -> "DjangoOptimizerStore":
    store = DjangoOptimizerStore()

    fields = {schema.config.name_converter.get_graphql_name(f): f for f in type_def.fields}
    model_fields = get_model_fields(model)

    # Make sure that the model's pk is always selected when using only
    pk = model._meta.pk
    if pk is not None:
        store.only.add(pk.attname)

    for field_selection in selection.selections:
        field = fields[field_selection.name]

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
                store.only.add(path)
                store.select_related.add(path)

                # If adding a reverse relation, make sure to select its pointer to us,
                # or else this might causa a refetch from the database
                if isinstance(model_field, OneToOneRel):
                    remote_field = model_field.remote_field
                    store.only.add(f"{path}__{resolve_model_field_name(remote_field)}")

                for f_type_def in get_possible_type_definitions(field.type):
                    f_model = model_field.related_model
                    store |= _get_model_hints(
                        f_model,
                        schema,
                        f_type_def,
                        field_selection,
                        config=config,
                        prefix=f"{path}__",
                    )
            elif isinstance(model_field, (models.ManyToManyField, ManyToManyRel, ManyToOneRel)):
                f_types = list(get_possible_type_definitions(field.type))
                if len(f_types) > 1:
                    # This might be a generic foreign key. In this case, just prefetch it
                    store.prefetch_related.add(model_fieldname)
                elif len(f_types) == 1:
                    remote_field = model_field.remote_field
                    f_store = _get_model_hints(
                        remote_field.model,
                        schema,
                        f_types[0],
                        field_selection,
                        config=config,
                    )
                    if (config is None or config.enable_only) and f_store.only:
                        # If adding a reverse relation, make sure to select its pointer to us,
                        # or else this might causa a refetch from the database
                        f_store.only.add(cast(str, resolve_model_field_name(remote_field)))

                    f_qs = f_store.apply(remote_field.model.objects.all(), config=config)
                    store.prefetch_related.add(Prefetch(path, queryset=f_qs))
            else:
                store.only.add(path)

    return store


def optimize(
    qs: Union[QuerySet[_M], BaseManager[_M]],
    info: Union[GraphQLResolveInfo, Info],
    *,
    config: Optional["DjangoOptimizerConfig"] = None,
    store: Optional["DjangoOptimizerStore"] = None,
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
    config = config or DjangoOptimizerConfig()
    store = store or DjangoOptimizerStore()
    schema = cast(Schema, info.schema._strawberry_schema)  # type:ignore

    gql_type = get_named_type(info.return_type)
    strawberry_type = schema.get_type_by_name(gql_type.name)
    if strawberry_type is None:
        return qs

    for type_def in get_possible_type_definitions(strawberry_type):
        selection = convert_selections(info, info.field_nodes[:1])[0]

        concrete_type_def = type_def.concrete_of
        concrete_type = concrete_type_def and concrete_type_def.origin
        if concrete_type and issubclass(concrete_type, Connection):
            edges = next((s for s in selection.selections if s.name == "edges"), None)
            node = edges and next((s for s in edges.selections if s.name == "node"), None)
            if node:
                selection = node
                type_def = type_def.type_var_map[NodeType]._type_definition  # type:ignore
                assert type_def

        store |= _get_model_hints(
            qs.model,
            schema,
            type_def,
            selection,
            config=config,
        )

    return store.apply(qs, config=config)


@dataclasses.dataclass
class DjangoOptimizerConfig:
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
class DjangoOptimizerStore:
    """Django optimization store.

    Attributes:
        only:
            Set of values to optimize using `QuerySet.only`
        selected:
            Set of values to optimize using `QuerySet.select_related`
        prefetch_related:
            Set of values to optimize using `QuerySet.prefetch_related`

    """

    only: Set[str] = dataclasses.field(default_factory=set)
    select_related: Set[str] = dataclasses.field(default_factory=set)
    prefetch_related: Set[Union[str, Prefetch]] = dataclasses.field(default_factory=set)

    def __or__(self, other: "DjangoOptimizerStore"):
        return self.__class__(
            only=self.only.copy(),
            select_related=self.select_related.copy(),
            prefetch_related=self.prefetch_related.copy(),
        ).__ior__(other)

    def __ior__(self, other: "DjangoOptimizerStore"):
        self.only |= other.only
        self.select_related |= other.select_related
        self.prefetch_related |= other.prefetch_related
        return self

    @classmethod
    def from_iterables(
        cls,
        only: Optional[TypeOrSequence[str]] = None,
        select_related: Optional[TypeOrSequence[str]] = None,
        prefetch_related: Optional[TypeOrSequence[Union[str, Prefetch]]] = None,
    ):
        return cls(
            only=_ensure_set(only),
            select_related=_ensure_set(select_related),
            prefetch_related=_ensure_set(prefetch_related),
        )

    def with_prefix(self, prefix: str):
        return self.__class__(
            only={f"{prefix}{i}" for i in self.only},
            select_related={f"{prefix}{i}" for i in self.select_related},
            prefetch_related={
                f"{prefix}{i}" if isinstance(i, str) else i for i in self.prefetch_related
            },
        )

    def apply(
        self,
        qs: QuerySet[_M],
        *,
        config: Optional[DjangoOptimizerConfig] = None,
    ) -> QuerySet[_M]:
        if config is None or config.enable_select_related and self.select_related:
            qs = qs.select_related(*self.select_related)

        if (config is None or config.enable_prefetch_related) and self.prefetch_related:
            # If there's a prefetch_related with the name of a Prefetch object,
            # replace it with the Prefetch object.
            prefetch_related = self.prefetch_related - {
                p.prefetch_to  # type:ignore
                for p in self.prefetch_related
                if isinstance(p, Prefetch)
            }
            qs = qs.prefetch_related(*prefetch_related)

        if (config is None or config.enable_only) and self.only:
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

    def __init__(
        self,
        enable_only_optimization: bool = True,
        enable_select_related_optimization: bool = True,
        enable_prefetch_related_optimization: bool = True,
    ):
        """ """
        self._config = DjangoOptimizerConfig(
            enable_only=enable_only_optimization,
            enable_select_related=enable_select_related_optimization,
            enable_prefetch_related=enable_prefetch_related_optimization,
        )

    def on_request_start(self) -> AwaitableOrValue[None]:
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

        if isinstance(ret, (QuerySet, BaseManager)):
            return resolvers.resolve_qs(optimize(qs=ret, info=info, config=self._config))

        return ret
