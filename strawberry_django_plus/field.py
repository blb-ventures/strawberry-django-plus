import dataclasses
from functools import cached_property
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    overload,
)

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import QuerySet
from django.db.models.fields.related_descriptors import (
    ForwardManyToOneDescriptor,
    ReverseManyToOneDescriptor,
    ReverseOneToOneDescriptor,
)
from django.db.models.fields.reverse_related import ManyToManyRel, ManyToOneRel
from django.db.models.query_utils import DeferredAttribute
import strawberry
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument, is_unset
from strawberry.permission import BasePermission
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.types.fields.resolver import StrawberryResolver
from strawberry.types.info import Info
from strawberry.union import StrawberryUnion
from strawberry_django.arguments import argument
from strawberry_django.fields.field import (
    StrawberryDjangoField as _StrawberryDjangoField,
)
from strawberry_django.fields.types import (
    get_model_field,
    is_auto,
    is_optional,
    resolve_model_field_name,
)
from strawberry_django.utils import is_similar_django_type, unwrap_type
from typing_extensions import Annotated, Self

from . import relay
from .descriptors import ModelProperty
from .optimizer import OptimizerStore, PrefetchType
from .permissions import filter_with_perms
from .types import resolve_model_field_type
from .utils import resolvers
from .utils.typing import TypeOrSequence

if TYPE_CHECKING:
    from .type import StrawberryDjangoType

__all__ = [
    "StrawberryDjangoField",
    "field",
]

_T = TypeVar("_T")
_M = TypeVar("_M", bound=models.Model)


class StrawberryDjangoField(_StrawberryDjangoField):
    """A strawberry field for django attributes.

    Do not instantiate this directly. Instead, use `@field` decorator.

    """

    store: OptimizerStore

    def __init__(self, *args, **kwargs):
        self.store = OptimizerStore.with_hints(
            only=kwargs.pop("only", None),
            select_related=kwargs.pop("select_related", None),
            prefetch_related=kwargs.pop("prefetch_related", None),
        )
        super().__init__(*args, **kwargs)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        is_node = isinstance(unwrap_type(self.type), relay.Node)
        return [
            (
                (
                    argument("ids", List[relay.GlobalID], is_optional=is_optional)
                    if self.is_list
                    else argument("id", relay.GlobalID, is_optional=is_optional)
                )
                if is_node
                and arg.python_name == "pk"
                and arg.type_annotation.annotation == strawberry.ID
                else arg
            )
            for arg in super().arguments
        ]

    @cached_property
    def model(self) -> Type[models.Model]:
        model = self.django_model
        if model:
            return model

        origin = self.origin_django_type or getattr(self.origin, "_django_type", None)
        model = origin and origin.model

        if isinstance(self.type, StrawberryUnion):
            mlist = []
            for t in self.type.types:
                dj_type = getattr(t, "_django_type", None)
                if dj_type:
                    mlist.append(dj_type.model)
                else:
                    model = getattr(t, "model", None)
                    if model:
                        mlist.append(model)
            assert len(mlist) == 1
            model = mlist[0]

        return cast(Type[models.Model], model)

    @cached_property
    def safe_resolver(self):
        resolver = self.base_resolver
        assert resolver
        if not resolver.is_async:
            resolver = resolvers.async_safe(resolver)
        return resolver

    @classmethod
    def from_django_type(
        cls,
        django_type: "StrawberryDjangoType",
        name: str,
        *,
        type_annotation: Optional[StrawberryAnnotation] = None,
    ) -> Self:
        origin = django_type.origin

        attr = getattr(origin, name, UNSET)
        if is_unset(attr):
            attr = getattr(cls, "__dataclass_fields__", {}).get(name, UNSET)
        if attr is dataclasses.MISSING:
            attr = UNSET

        if type_annotation:
            try:
                type_origin = get_origin(type_annotation.annotation)
                is_connection = issubclass(type_origin, relay.Connection) if type_origin else False
            except Exception:
                is_connection = False
        else:
            is_connection = False

        if isinstance(attr, cls) and not attr.origin_django_type:
            field = cast(Self, attr)
        elif is_connection or isinstance(attr, relay.ConnectionField):
            field = attr
            if not isinstance(field, relay.Connection):
                field = relay.connection()

            field = cast(Self, field)

            # FIXME: Improve this...
            if not field.base_resolver:

                def conn_resolver(root):
                    return getattr(root, name).all()

                field.base_resolver = StrawberryResolver(conn_resolver)
                if type_annotation is not None:
                    field.type_annotation = type_annotation
        elif isinstance(attr, dataclasses.Field):
            default = getattr(attr, "default", UNSET)
            if default is dataclasses.MISSING:
                default = UNSET

            default_factory = getattr(attr, "default_factory", UNSET)
            if default_factory is dataclasses.MISSING:
                default_factory = UNSET

            if type_annotation is None:
                type_annotation = getattr(attr, "type_annotation", None)
            if type_annotation is None:
                type_annotation = StrawberryAnnotation(attr.type)

            field = cls(
                django_name=getattr(attr, "django_name", attr.name),
                graphql_name=getattr(attr, "graphql_name", None),
                origin=getattr(attr, "origin", None),
                is_subscription=getattr(attr, "is_subscription", False),
                description=getattr(attr, "description", None),
                base_resolver=getattr(attr, "base_resolver", None),
                permission_classes=getattr(attr, "permission_classes", ()),
                default=default,
                default_factory=default_factory,
                deprecation_reason=getattr(attr, "deprecation_reason", None),
                directives=getattr(attr, "directives", ()),
                type_annotation=type_annotation,
            )
        elif isinstance(attr, StrawberryResolver):
            field = cls(base_resolver=attr)
        elif callable(attr):
            field = cast(Self, cls()(attr))
        else:
            field = cls(default=attr)

        field.python_name = name
        # store origin django type for further usage
        if name in origin.__dict__.get("__annotations__", {}):
            field.origin_django_type = django_type

        # annotation of field is used as a class type
        if type_annotation is not None:
            field.type_annotation = type_annotation
            field.is_auto = is_auto(field.type_annotation)

        # resolve the django_name and check if it is relation field. django_name
        # is used to access the field data in resolvers
        try:
            model_field = get_model_field(
                django_type.model, getattr(field, "django_name", None) or name
            )
        except FieldDoesNotExist:
            model_attr = getattr(django_type.model, name, None)
            if model_attr is not None and isinstance(model_attr, ModelProperty):
                if field.is_auto:
                    annotation = model_attr.type_annotation
                    if get_origin(annotation) is Annotated:
                        annotation = get_args(annotation)[0]
                    field.type_annotation = StrawberryAnnotation(annotation)
                    field.is_auto = is_auto(field.type_annotation)

                if field.description is None:
                    field.description = model_attr.description
            elif field.django_name or field.is_auto:
                raise  # field should exist, reraise caught exception
        else:
            field.is_relation = model_field.is_relation
            field.django_name = resolve_model_field_name(
                model_field,
                is_input=django_type.is_input,
                is_filter=django_type.is_filter,
            )

            # change relation field type to auto if field is inherited from another
            # type. for example if field is inherited from output type but we are
            # configuring field for input type
            if field.is_relation and not is_similar_django_type(
                django_type, field.origin_django_type
            ):
                field.is_auto = True

            # resolve type of auto field
            if field.is_auto:
                field.type_annotation = StrawberryAnnotation(
                    resolve_model_field_type(
                        model_field,
                        django_type,
                        is_input=django_type.is_input,
                        is_partial=django_type.is_partial,
                    )
                )

            if field.description is None:
                description = (
                    model_field.field.help_text
                    if isinstance(model_field, (ManyToOneRel, ManyToManyRel))
                    else model_field.help_text
                )
                if description:
                    field.description = str(description)

        # FIXME: How to properly workaround this for mutations?
        if django_type.is_input and is_unset(field.default_value):
            field.default = UNSET

        return field

    def get_result(
        self,
        source: Optional[models.Model],
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Union[Awaitable[Any], Any]:
        if self.base_resolver is not None:
            result = self.resolver(source, info, args, kwargs)
        elif source is None:
            result = self.model._default_manager.all()
        else:
            # Small optimization to async resolvers avoid having to call it in an sync_to_async
            # context if the value is already cached, since it will not hit the db anymore
            attname = self.django_name or self.python_name
            attr = getattr(source.__class__, attname, None)
            try:
                if isinstance(attr, ModelProperty):
                    result = source.__dict__[attr.name]
                elif isinstance(attr, DeferredAttribute):
                    # If the value is cached, retrieve it with getattr because
                    # some fields wrap values at that time (e.g. FileField).
                    # If this next like fails, it will raise KeyError and get
                    # us out of the loop before we can do getattr
                    source.__dict__[attr.field.attname]
                    result = getattr(source, attr.field.attname)
                elif isinstance(attr, ForwardManyToOneDescriptor):
                    # This will raise KeyError if it is not cached
                    result = attr.field.get_cached_value(source)  # type:ignore
                elif isinstance(attr, ReverseOneToOneDescriptor):
                    # This will raise KeyError if it is not cached
                    result = attr.related.get_cached_value(source)
                elif isinstance(attr, ReverseManyToOneDescriptor):
                    # This returns a queryset, it is async safe
                    result = getattr(source, attname)
                else:
                    raise KeyError
            except KeyError:
                result = resolvers.getattr_async_safe(source, attname)

        if self.is_list:
            qs_resolver = lambda qs: self.get_queryset_as_list(qs, info, **kwargs)
        else:
            qs_resolver = lambda qs: self.get_queryset_one(qs, info, **kwargs)

        return resolvers.resolve_result(result, info=info, qs_resolver=qs_resolver)

    def resolver(
        self,
        source: Any,
        info: Info,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        return self.safe_resolver(*args, **kwargs)

    @resolvers.async_safe
    def get_queryset_as_list(self, qs: QuerySet[_M], info: Info, **kwargs) -> List[_M]:
        if not self.base_resolver:
            nodes: Optional[List[relay.GlobalID]] = kwargs.get("ids")
            if isinstance(nodes, list):
                if nodes:
                    assert {n.resolve_type(info) for n in nodes} == {unwrap_type(self.type)}
                qs = qs.filter(pk__in=[n.node_id for n in nodes])

        return list(self.get_queryset(filter_with_perms(qs, info), info, **kwargs))

    @resolvers.async_safe
    def get_queryset_one(self, qs: QuerySet[_M], info: Info, **kwargs) -> Optional[_M]:
        try:
            qs = self.get_queryset(qs, info, **kwargs)
            if not self.base_resolver:
                node = kwargs.get("id")
                if isinstance(node, relay.GlobalID):
                    assert node.resolve_type(info) == unwrap_type(self.type)
                    qs = qs.filter(pk=node.node_id)
            return qs.get()
        except self.model.DoesNotExist:
            if not self.is_optional:
                raise

        return None


@overload
def field(
    *,
    resolver: Callable[[], _T],
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[False] = False,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
) -> _T:
    ...


@overload
def field(
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    init: Literal[True] = True,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
) -> Any:
    ...


@overload
def field(
    resolver: Union[StrawberryResolver, Callable, staticmethod, classmethod],
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
) -> StrawberryDjangoField:
    ...


def field(
    resolver=None,
    *,
    name: Optional[str] = None,
    field_name: Optional[str] = None,
    filters: Any = UNSET,
    is_subscription: bool = False,
    description: Optional[str] = None,
    permission_classes: Optional[List[Type[BasePermission]]] = None,
    deprecation_reason: Optional[str] = None,
    default: Any = UNSET,
    default_factory: Union[Callable, object] = UNSET,
    directives: Optional[Sequence[StrawberrySchemaDirective]] = (),
    only: Optional[TypeOrSequence[str]] = None,
    select_related: Optional[TypeOrSequence[str]] = None,
    prefetch_related: Optional[TypeOrSequence[PrefetchType]] = None,
    # This init parameter is used by pyright to determine whether this field
    # is added in the constructor or not. It is not used to change
    # any behavior at the moment.
    init=None,
) -> Any:
    """Annotate a method or property as a Django GraphQL field.

    Examples:
        It can be used both as decorator and as a normal function:

        >>> @gql.type
        >>> class X:
        ...     field_abc: str = gql.django.field(description="ABC")
        ...     @gql.django.field(description="ABC")
        ...
        ...     def field_with_resolver(self) -> str:
        ...         return "abc"

    """
    f = StrawberryDjangoField(
        python_name=None,
        django_name=field_name,
        graphql_name=name,
        type_annotation=None,
        description=description,
        is_subscription=is_subscription,
        permission_classes=permission_classes or [],
        deprecation_reason=deprecation_reason,
        default=default,
        default_factory=default_factory,
        directives=directives,
        filters=filters,
        only=only,
        select_related=select_related,
        prefetch_related=prefetch_related,
    )
    if resolver:
        f = f(resolver)
    return f
