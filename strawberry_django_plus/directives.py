from collections import defaultdict
import dataclasses
import functools
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    DefaultDict,
    Dict,
    Generic,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)

from graphql.type.definition import (
    GraphQLEnumType,
    GraphQLInterfaceType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLResolveInfo,
    GraphQLScalarType,
    GraphQLUnionType,
    GraphQLWrappingType,
)
from strawberry.extensions.base_extension import Extension
from strawberry.field import StrawberryField
from strawberry.private import Private
from strawberry.schema.schema import Schema
from strawberry.schema_directive import Location, StrawberrySchemaDirective
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Self, TypeAlias

from .utils.inspect import get_possible_type_definitions

try:
    # Try to use the smaller/faster cache decorator if available
    _cache = functools.cache  # type:ignore
except AttributeError:
    _cache = functools.lru_cache

_T = TypeVar("_T", bound="SchemaDirectiveResolver")
Origin: TypeAlias = Union[TypeDefinition, StrawberryField]

_origin_cache: DefaultDict[Origin, List["SchemaDirectiveResolver"]] = defaultdict(list)


# FIXME: This is here to help with typing
@dataclasses.dataclass
class SchemaDirective(Generic[_T], StrawberrySchemaDirective):
    wrap: Type[_T]
    instance: Optional[_T] = dataclasses.field(init=False)

    if TYPE_CHECKING:

        def __call__(self, *args, **kwargs) -> Self:
            ...


@dataclasses.dataclass
@functools.total_ordering
class SchemaDirectiveResolver:
    """Base schema directive resolver definition."""

    has_resolver: ClassVar[bool] = False
    priority: ClassVar[int] = 0

    origin: Private[Optional[Origin]] = dataclasses.field(init=False)

    def __post_init__(self):
        self.origin = None

    def __lt__(self, other: "SchemaDirectiveResolver"):
        return self.priority < other.priority

    def __le__(self, other: "SchemaDirectiveResolver"):
        return self.priority <= other.priority

    def __gt__(self, other: "SchemaDirectiveResolver"):
        return self.priority > other.priority

    def __ge__(self, other: "SchemaDirectiveResolver"):
        return self.priority >= other.priority

    @classmethod
    @_cache
    def for_origin(cls, origin: Origin) -> List[Self]:
        directives = [d for d in _origin_cache[origin] if isinstance(d, cls)]
        if isinstance(origin, StrawberryField):
            for type_def in get_possible_type_definitions(origin.type):
                for d in _origin_cache[type_def]:
                    if isinstance(d, cls) and d not in directives:
                        directives.append(d)

        directives.sort(reverse=True)
        return directives

    def register(self, origin: Origin):
        assert self.origin is None
        self.origin = origin
        _origin_cache[origin].append(self)

    def resolve(
        self,
        helper: "SchemaDirectiveHelper",
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ) -> AwaitableOrValue[Any]:
        raise NotImplementedError


def schema_directive(
    *,
    locations: List[Location],
    description: Optional[str] = None,
    name: Optional[str] = None,
):
    def _wrap(cls: Type[_T]) -> SchemaDirective[_T]:
        if isinstance(cls, StrawberrySchemaDirective):
            cls = cls.wrap

        return SchemaDirective(
            python_name=cls.__name__,
            wrap=dataclasses.dataclass(cls),
            graphql_name=name,
            locations=locations,
            description=description,
        )

    return _wrap


@dataclasses.dataclass
class SchemaDirectiveHelperReturnType:
    ret_type: Union[GraphQLObjectType, GraphQLInterfaceType, GraphQLScalarType, GraphQLEnumType]
    type_def: Optional[TypeDefinition] = dataclasses.field(default=None)


@dataclasses.dataclass
class SchemaDirectiveHelper:
    directives: List[SchemaDirectiveResolver]
    ret_possibilities: List[SchemaDirectiveHelperReturnType]
    optional: bool
    is_list: bool


class SchemaDirectiveExtension(Extension):
    """Execute schema directives."""

    _helper_cache: ClassVar[Dict[Tuple[str, str], SchemaDirectiveHelper]] = {}

    def resolve(
        self,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ) -> AwaitableOrValue[Any]:
        helper = self._get_directives(info)

        for d in helper.directives:
            _next = functools.partial(d.resolve, helper, _next)

        return _next(root, info, *args, **kwargs)

    def _get_directives(self, info: GraphQLResolveInfo):
        type_name = info.parent_type.name
        field_name = info.field_name
        key = (type_name, field_name)

        directives = self._helper_cache.get(key)
        if directives is not None:
            return directives

        schema = cast(
            Schema,
            info.schema._strawberry_schema,  # type:ignore
        )

        type_def = schema.get_type_by_name(type_name)
        if isinstance(type_def, TypeDefinition):
            field = next(
                (
                    f
                    for f in type_def.fields
                    if field_name == schema.config.name_converter.get_graphql_name(f)
                ),
                None,
            )
        else:
            field = None

        found_directives = _origin_cache[field][:] if field is not None else []

        ret_type = info.return_type
        if isinstance(ret_type, GraphQLNonNull):
            ret_type = ret_type.of_type
            optional = False
        else:
            optional = True

        is_list = isinstance(ret_type, GraphQLList)
        while isinstance(ret_type, GraphQLWrappingType):
            ret_type = ret_type.of_type

        if isinstance(ret_type, GraphQLUnionType):
            ret_types = cast(List[GraphQLObjectType], ret_type.types)
        else:
            ret_types = [ret_type]

        ret_possibilities = []
        for type_ in ret_types:
            t = schema.get_type_by_name(ret_type.name)
            if t is None:
                continue

            for type_def in get_possible_type_definitions(t):
                found_directives.extend(_origin_cache[type_def])
                ret_possibilities.append(
                    SchemaDirectiveHelperReturnType(
                        ret_type=type_,
                        type_def=type_def,
                    )
                )

        # Keep directives sorted by order of priority and avoid duplicates
        directives = []
        for d in reversed(found_directives):
            if d.has_resolver and d not in directives:
                directives.append(d)

        helper = SchemaDirectiveHelper(
            directives=directives,
            ret_possibilities=ret_possibilities,
            optional=optional,
            is_list=is_list,
        )
        self._helper_cache[key] = helper

        return helper
