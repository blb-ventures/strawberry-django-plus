from collections import defaultdict
import dataclasses
import functools
from typing import (
    Any,
    Callable,
    ClassVar,
    DefaultDict,
    Dict,
    List,
    Optional,
    Tuple,
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
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import TypeAlias

try:
    # Try to use the smaller/faster cache decorator if available
    _cache = functools.cache  # type:ignore
except AttributeError:
    _cache = functools.lru_cache

Origin: TypeAlias = Union[TypeDefinition, StrawberryField]

_origin_cache: DefaultDict[Origin, List["SchemaDirectiveWithResolver"]] = defaultdict(list)


@dataclasses.dataclass
@functools.total_ordering
class SchemaDirectiveWithResolver:
    """Base schema directive resolver definition."""

    priority: ClassVar[int] = 0
    origin: Private[Optional[Origin]] = dataclasses.field(init=False)

    def __post_init__(self):
        self.origin = None

    def __lt__(self, other: "SchemaDirectiveWithResolver"):
        return self.priority < other.priority

    def __le__(self, other: "SchemaDirectiveWithResolver"):
        return self.priority <= other.priority

    def __gt__(self, other: "SchemaDirectiveWithResolver"):
        return self.priority > other.priority

    def __ge__(self, other: "SchemaDirectiveWithResolver"):
        return self.priority >= other.priority

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


@dataclasses.dataclass
class SchemaDirectiveHelperReturnType:
    ret_type: Union[GraphQLObjectType, GraphQLInterfaceType, GraphQLScalarType, GraphQLEnumType]
    type_def: Optional[TypeDefinition] = dataclasses.field(default=None)


@dataclasses.dataclass
class SchemaDirectiveHelper:
    directives: List[SchemaDirectiveWithResolver]
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
        # Avoid circular references
        from .utils.inspect import get_possible_type_definitions

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
            if isinstance(d, SchemaDirectiveWithResolver) and d not in directives:
                directives.append(d)

        helper = SchemaDirectiveHelper(
            directives=directives,
            ret_possibilities=ret_possibilities,
            optional=optional,
            is_list=is_list,
        )
        self._helper_cache[key] = helper

        return helper
