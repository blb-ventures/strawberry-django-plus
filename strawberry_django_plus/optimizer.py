import dataclasses
from typing import (
    Any,
    Awaitable,
    Callable,
    Generator,
    Generic,
    Optional,
    Sequence,
    Set,
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
from strawberry.lazy_type import LazyType
from strawberry.schema.schema import Schema
from strawberry.type import StrawberryContainer, StrawberryType
from strawberry.types.info import Info
from strawberry.types.nodes import Selection, convert_selections
from strawberry.types.types import TypeDefinition
from strawberry.union import StrawberryUnion
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.fields.types import resolve_model_field_name
from typing_extensions import TypeAlias

from .descriptors import ModelProperty
from .relay import Connection, NodeType
from .resolvers import resolve_qs_list
from .utils import get_model_fields

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)
TypeOrSequence: TypeAlias = Union[_T, Sequence[_T]]


def _get_gql_types(
    gql_type: Union[StrawberryType, type]
) -> Generator[Union[StrawberryType, type], None, None]:
    if isinstance(gql_type, TypeDefinition):
        yield from _get_gql_types(gql_type.origin)
    elif isinstance(gql_type, LazyType):
        yield from _get_gql_types(gql_type.resolve_type())
    elif isinstance(gql_type, StrawberryContainer):
        yield from _get_gql_types(gql_type.of_type)
    elif isinstance(gql_type, StrawberryUnion):
        for t in gql_type.types:
            yield from _get_gql_types(t)
    else:
        yield gql_type


def _get_gql_type_definitions(
    gql_type: Union[StrawberryType, type]
) -> Generator[TypeDefinition, None, None]:
    for t in _get_gql_types(gql_type):
        if not isinstance(t, TypeDefinition):
            t = getattr(t, "_type_definition", None)
        if isinstance(t, TypeDefinition):
            yield t


def _ensure_set(args: Optional[TypeOrSequence[_T]]) -> Set[_T]:
    if args is None:
        return set()

    if not isinstance(args, Sequence):
        return {args}

    ret = set(args)
    assert len(ret) == len(args)

    return ret


def optimize(
    qs: Union[QuerySet[_M], BaseManager[_M]],
    *,
    info: Union[GraphQLResolveInfo, Info],
    config: Optional["DjangoOptimizerConfig"] = None,
    **kwargs,
) -> QuerySet[_M]:
    config = config or DjangoOptimizerConfig()
    optimizer = DjangoOptimizer(qs=qs, info=info, config=config, **kwargs)
    return optimizer.optimize()


@dataclasses.dataclass
class DjangoOptimizerConfig:
    enable_only: bool = dataclasses.field(default=True)
    enable_select_related: bool = dataclasses.field(default=True)
    enable_prefetch_related: bool = dataclasses.field(default=True)


@dataclasses.dataclass
class DjangoOptimizerStore:
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
        config: Optional[DjangoOptimizerConfig] = None,
    ) -> QuerySet[_M]:
        if config is None or config.enable_select_related:
            select_related = self.select_related
            if select_related:
                qs = qs.select_related(*select_related)

        if config is None or config.enable_prefetch_related:
            prefetch_related = self.prefetch_related
            if prefetch_related:
                # If there's a prefetch_related with the name of a Prefetch object,
                # replace it with the Prefetch object.
                prefetch_related = prefetch_related - {
                    p.prefetch_to  # type:ignore
                    for p in prefetch_related
                    if isinstance(p, Prefetch)
                }
                qs = qs.prefetch_related(*prefetch_related)

        if config is None or config.enable_only:
            only = self.only
            if only:
                qs = qs.only(*only)

        return qs


@dataclasses.dataclass
class DjangoOptimizer(Generic[_M]):
    qs: Union[QuerySet[_M], BaseManager[_M]]
    info: Union[GraphQLResolveInfo, Info]
    config: DjangoOptimizerConfig = dataclasses.field(default_factory=DjangoOptimizerConfig)
    store: DjangoOptimizerStore = dataclasses.field(default_factory=DjangoOptimizerStore)

    def __post_init__(self):
        self._info = self.info._raw_info if isinstance(self.info, Info) else self.info
        self._schema: Schema = self._info.schema._strawberry_schema  # type:ignore
        self._name_converter = self._schema.config.name_converter

    def optimize(self) -> QuerySet[_M]:
        store = self.store

        qs = self.qs
        if isinstance(qs, BaseManager):
            qs = cast(QuerySet[_M], qs.all())

        # If the queryset already has cached results, just return it
        if qs._result_cache:  # type:ignore
            return qs

        gql_type = get_named_type(self._info.return_type)
        type_def = self._schema.get_type_by_name(gql_type.name)

        # TODO: This should never be scalar/enum. But what to do for unions?
        if isinstance(type_def, TypeDefinition):
            selection = convert_selections(self._info, self.info.field_nodes[:1])[0]

            concrete_type_def = type_def.concrete_of
            concrete_type = concrete_type_def and concrete_type_def.origin
            if concrete_type and issubclass(concrete_type, Connection):
                try:
                    edges = next(s for s in selection.selections if s.name == "edges")
                    node = next(s for s in edges.selections if s.name == "node")
                except StopIteration:
                    pass
                else:
                    selection = node
                    type_def = type_def.type_var_map[NodeType]._type_definition  # type:ignore
                    assert type_def

            store |= self._get_model_hints(qs.model, type_def, selection=selection)
            qs = store.apply(qs, self.config)

        return qs

    def _get_model_hints(
        self,
        model: Type[models.Model],
        type_def: TypeDefinition,
        selection: Selection,
        prefix: str = "",
    ):
        store = DjangoOptimizerStore()

        fields = get_model_fields(model)
        gql_fields = {self._name_converter.get_graphql_name(f): f for f in type_def.fields}

        # Make sure that the model's pk is always selected when using only
        pk = model._meta.pk
        if pk is not None:
            store.only.add(pk.attname)

        for s in selection.selections:
            gql_field = gql_fields[s.name]

            # Add annotations from the field if they exist
            field_store = getattr(gql_field, "store", None)
            if field_store is not None:
                store |= field_store.with_prefix(prefix) if prefix else field_store

            # Then from the model property if one is defined
            model_attr = getattr(model, gql_field.python_name, None)
            if model_attr is not None and isinstance(model_attr, ModelProperty):
                attr_store = model_attr.store
                store |= attr_store.with_prefix(prefix) if prefix else attr_store

            # Lastly, from the django field itself
            field_name: str = getattr(gql_field, "django_name", gql_field.python_name)
            field = fields.get(field_name, None)
            if field is None:
                continue

            path = f"{prefix}{field_name}"
            if isinstance(field, (models.ForeignKey, OneToOneRel)):
                store.only.add(path)
                store.select_related.add(path)

                # If adding a reverse relation, make sure to select its pointer to us,
                # or else this might causa a refetch from the database
                if isinstance(field, OneToOneRel):
                    remote_field = field.remote_field
                    store.only.add(f"{path}__{resolve_model_field_name(remote_field)}")

                for f_type in _get_gql_type_definitions(gql_field.type):
                    f_model = field.related_model
                    store |= self._get_model_hints(
                        model=f_model,
                        type_def=f_type,
                        selection=s,
                        prefix=f"{path}__",
                    )
            elif isinstance(field, (models.ManyToManyField, ManyToManyRel, ManyToOneRel)):
                f_types = list(_get_gql_type_definitions(gql_field.type))
                if len(f_types) > 1:
                    # This might be a generic foreign key. In this case, just prefetch it
                    store.prefetch_related.add(field_name)
                elif len(f_types) == 1:
                    remote_field = field.remote_field
                    f_type = f_types[0]
                    f_store = self._get_model_hints(remote_field.model, f_type, selection=s)
                    if self.config.enable_only and f_store.only:
                        # If adding a reverse relation, make sure to select its pointer to us,
                        # or else this might causa a refetch from the database
                        f_store.only.add(cast(str, resolve_model_field_name(remote_field)))

                    f_qs = f_store.apply(remote_field.model.objects.all(), self.config)
                    store.prefetch_related.add(Prefetch(path, queryset=f_qs))
            else:
                store.only.add(path)

        return store

    def _get_field_hints(
        self,
        model: Type[models.Model],
        type_def: TypeDefinition,
        selection: Selection,
        prefix: str = "",
    ):
        pass


class DjangoOptimizerExtension(Extension):
    def __init__(
        self,
        enable_only_optimization: bool = True,
        enable_select_related_optimization: bool = True,
        enable_prefetch_related_optimization: bool = True,
    ):
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
    ) -> AwaitableOrValue[object]:
        return self._resolver(_next(root, info, *args, **kwargs), info)

    def _resolver(self, ret: object, info: GraphQLResolveInfo):
        if isinstance(ret, (QuerySet, BaseManager)):
            # This will get optimized below
            ret = DjangoOptimizer(qs=ret, info=info, config=self._config)

        if isinstance(ret, DjangoOptimizer):
            return resolve_qs_list(ret.optimize())
        elif info.is_awaitable(ret):
            return self._async_resolver(cast(Awaitable, ret), info)

        return ret

    async def _async_resolver(self, ret: Awaitable, info: GraphQLResolveInfo):
        return self._resolver(await ret, info)
