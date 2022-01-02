import dataclasses
from typing import (
    Any,
    Awaitable,
    Callable,
    Generator,
    Generic,
    List,
    Optional,
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

from .relay import Connection, NodeType
from .resolvers import resolve_qs_list
from .utils import get_model_fields

_T = TypeVar("_T", bound=models.Model)


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


def _norm_prefetch_related(pr_set: Set[Union[str, Prefetch]]) -> Set[Union[str, Prefetch]]:
    # If there's a prefetch_related with the name of a Prefetch object,
    # replace it with the Prefetch object.
    return pr_set - {p.prefetch_to for p in pr_set if isinstance(p, Prefetch)}  # type:ignore


def optimize(
    qs: Union[QuerySet[_T], BaseManager[_T]],
    *,
    info: Union[GraphQLResolveInfo, Info],
    config: Optional["DjangoOptimizerConfig"] = None,
    **kwargs,
) -> QuerySet[_T]:
    config = config or DjangoOptimizerConfig()
    optimizer = DjangoOptimizer(qs=qs, info=info, config=config, **kwargs)
    return optimizer.optimize()


@dataclasses.dataclass
class DjangoOptimizerConfig:
    enable_only: bool = dataclasses.field(default=True)
    enable_select_related: bool = dataclasses.field(default=True)
    enable_prefetch_related: bool = dataclasses.field(default=True)


@dataclasses.dataclass
class DjangoOptimizer(Generic[_T]):
    qs: Union[QuerySet[_T], BaseManager[_T]]
    info: Union[GraphQLResolveInfo, Info]
    config: DjangoOptimizerConfig = dataclasses.field(default_factory=DjangoOptimizerConfig)
    only: List[str] = dataclasses.field(default_factory=list)
    select_related: List[str] = dataclasses.field(default_factory=list)
    prefetch_related: List[Union[str, Prefetch]] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        self._info = self.info._raw_info if isinstance(self.info, Info) else self.info
        self._schema: Schema = self._info.schema._strawberry_schema  # type:ignore
        self._name_converter = self._schema.config.name_converter

    def optimize(self) -> QuerySet[_T]:
        config = self.config

        qs = self.qs
        if isinstance(qs, BaseManager):
            qs = cast(QuerySet[_T], qs.all())

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

            only, select_related, prefetch_related = self._get_model_hints(
                qs.model,
                type_def,
                selection=selection,
            )

            if config.enable_only:
                only |= set(self.only)
                if only:
                    qs = qs.only(*only)

            if config.enable_select_related:
                select_related |= set(self.select_related)
                if select_related:
                    qs = qs.select_related(*select_related)

            if config.enable_prefetch_related:
                prefetch_related |= set(self.prefetch_related)
                if prefetch_related:
                    qs = qs.prefetch_related(*_norm_prefetch_related(prefetch_related))

        return qs

    def _get_model_hints(
        self,
        model: Type[models.Model],
        type_def: TypeDefinition,
        selection: Selection,
        prefix: str = "",
    ):
        only: Set[str] = set()
        select_related: Set[str] = set()
        prefetch_related: Set[Union[str, Prefetch]] = set()

        fields = get_model_fields(model)
        gql_fields = {self._name_converter.get_graphql_name(f): f for f in type_def.fields}

        # Make sure that the model's pk is always selected when using only
        pk = model._meta.pk
        if pk is not None:
            only.add(pk.attname)

        for s in selection.selections:
            gql_field = gql_fields[s.name]
            f_name: str = getattr(gql_field, "django_name", gql_field.python_name)

            only |= {f"{prefix}{i}" for i in getattr(gql_field, "only", [])}
            select_related |= {f"{prefix}{i}" for i in getattr(gql_field, "select_related", [])}
            prefetch_related |= {  # type:ignore
                f"{prefix}{i}" if isinstance(i, str) else i
                for i in getattr(gql_field, "prefetch_related", [])
            }

            field = fields.get(f_name, None)
            if field is None:
                continue

            path = f"{prefix}{f_name}"
            if isinstance(field, (models.ForeignKey, OneToOneRel)):
                only.add(path)
                select_related.add(path)

                # If adding a reverse relation, make sure to select its pointer to us,
                # or else this might causa a refetch from the database
                if isinstance(field, OneToOneRel):
                    remote_field = field.remote_field
                    only.add(f"{path}__{resolve_model_field_name(remote_field)}")

                for f_type in _get_gql_type_definitions(gql_field.type):
                    f_model = field.related_model
                    f_only, f_select_related, f_prefetch_related = self._get_model_hints(
                        model=f_model,
                        type_def=f_type,
                        selection=s,
                        prefix=f"{path}__",
                    )
                    only |= f_only
                    select_related |= f_select_related
                    prefetch_related |= f_prefetch_related
            elif isinstance(field, (models.ManyToManyField, ManyToManyRel, ManyToOneRel)):
                f_types = list(_get_gql_type_definitions(gql_field.type))
                if len(f_types) > 1:
                    # This might be a generic foreign key. In this case, just prefetch it
                    prefetch_related.add(f_name)
                elif len(f_types) == 1:
                    remote_field = field.remote_field
                    f_type = f_types[0]
                    f_only, f_select_related, f_prefetch_related = self._get_model_hints(
                        remote_field.model,
                        f_type,
                        selection=s,
                    )
                    f_qs = remote_field.model.objects.all()
                    if self.config.enable_only and f_only:
                        # If adding a reverse relation, make sure to select its pointer to us,
                        # or else this might causa a refetch from the database
                        f_only.add(cast(str, resolve_model_field_name(remote_field)))
                        f_qs = f_qs.only(*f_only)
                    if self.config.enable_select_related and f_select_related:
                        f_qs = f_qs.select_related(*f_select_related)
                    if self.config.enable_prefetch_related and f_prefetch_related:
                        f_qs = f_qs.prefetch_related(*_norm_prefetch_related(f_prefetch_related))

                    prefetch_related.add(Prefetch(path, queryset=f_qs))
            else:
                only.add(path)

        return only, select_related, prefetch_related


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
