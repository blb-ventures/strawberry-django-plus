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
from django.db.models.fields.reverse_related import ManyToManyRel, ManyToOneRel
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

from ._relay import Connection, NodeType
from .resolvers import qs_resolver
from .utils import get_model_fields

_T = TypeVar("_T")
_get_list = qs_resolver(lambda qs: qs, get_list=True)


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


@dataclasses.dataclass
class DjangoOptimizerConfig:
    enable_only: bool = dataclasses.field(default=True)
    enable_select_related: bool = dataclasses.field(default=True)
    enable_prefetch_related: bool = dataclasses.field(default=True)


@dataclasses.dataclass
class DjangoOptimizer(Generic[_T]):
    info: Union[GraphQLResolveInfo, Info]
    qs: Union[models.QuerySet, models.manager.Manager]
    qs_resolver: Optional[Callable[[models.QuerySet], _T]] = dataclasses.field(default=None)
    only: List[str] = dataclasses.field(default_factory=list)
    select_related: List[str] = dataclasses.field(default_factory=list)
    prefetch_related: List[Union[str, Prefetch]] = dataclasses.field(default_factory=list)
    config: DjangoOptimizerConfig = dataclasses.field(default_factory=DjangoOptimizerConfig)

    def __post_init__(self):
        self._info = self.info._raw_info if isinstance(self.info, Info) else self.info
        self._schema: Schema = self._info.schema._strawberry_schema  # type:ignore
        self._name_converter = self._schema.config.name_converter

    def optimize(self) -> Union[models.QuerySet, _T]:
        config = self.config

        qs = self.qs
        if isinstance(qs, models.manager.BaseManager):
            qs = cast(models.QuerySet, qs.all())

        # If the queryset already has cached results, just return it
        if qs._result_cache:  # type:ignore
            return self._get_qs(qs)

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
                    # If optimizing only, make sure that our pk is always selected
                    only.add(qs.model._meta.pk.attname)
                    qs = qs.only(*only)

            if config.enable_select_related:
                select_related |= set(self.select_related)
                if select_related:
                    qs = qs.select_related(*select_related)

            if config.enable_prefetch_related:
                prefetch_related |= set(self.prefetch_related)
                if prefetch_related:
                    qs = qs.prefetch_related(*_norm_prefetch_related(prefetch_related))

        return self._get_qs(qs)

    def _get_qs(self, qs):
        if self.qs_resolver:
            return self.qs_resolver(qs)
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

        select_related = set()
        for s in selection.selections:
            gql_field = gql_fields[s.name]
            f_name: str = getattr(gql_field, "django_name", gql_field.python_name)
            selected_field = fields.get(f_name, None)
            if selected_field is None:
                continue

            path = f"{prefix}{f_name}"
            if isinstance(selected_field, models.ForeignKey):
                only.add(path)
                select_related.add(path)

                for f_type in _get_gql_type_definitions(gql_field.type):
                    f_model = selected_field.related_model
                    f_only, f_select_related, f_prefetch_related = self._get_model_hints(
                        model=f_model,
                        type_def=f_type,
                        selection=s,
                        prefix=f"{path}__",
                    )
                    only |= f_only
                    # If optimizing only, make sure that our pk is always selected
                    only.add(f_model._meta.pk.attname)
                    select_related |= f_select_related
                    prefetch_related |= f_prefetch_related
            elif isinstance(selected_field, (models.ManyToManyField, ManyToManyRel, ManyToOneRel)):
                selected_model = cast(Type[models.Model], selected_field.model)
                selected_pk = selected_model._meta.pk
                assert selected_pk
                # Make sure the object's pk is on only list, or else Django will need to
                # refetch the object again just to get it...
                only.add(f"{prefix}{selected_pk.name}")

                f_types = list(_get_gql_type_definitions(gql_field.type))
                if len(f_types) > 1:
                    # TODO: What to do in this case? Can this ever happen?
                    prefetch_related.add(f_name)
                elif len(f_types) == 1:
                    f_type = f_types[0]
                    f_model = selected_field.remote_field.model
                    f_only, f_select_related, f_prefetch_related = self._get_model_hints(
                        f_model,
                        f_type,
                        selection=s,
                    )
                    f_qs = f_model.objects.all()
                    if self.config.enable_only and f_only:
                        # If optimizing only, make sure that our pk is always selected
                        f_only.add(selected_field.remote_field.name)
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
        return self._resolver(
            _next(root, info, *args, **kwargs),
            info,
        )

    def _resolver(self, ret: object, info: GraphQLResolveInfo):
        if isinstance(ret, (models.QuerySet, models.manager.BaseManager)):
            ret = DjangoOptimizer(
                info=info,
                config=self._config,
                qs=ret,
                qs_resolver=_get_list,
            )

        if isinstance(ret, DjangoOptimizer):
            return self._resolver(ret.optimize(), info)
        elif info.is_awaitable(ret):
            return self._async_resolver(cast(Awaitable, ret), info)

        return ret

    async def _async_resolver(self, ret: Awaitable, info: GraphQLResolveInfo):
        return self._resolver(await ret, info)
