import abc
import dataclasses
import functools
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
    cast,
)
import uuid

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, QuerySet, Value
from graphql.type.definition import (
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLOutputType,
    GraphQLResolveInfo,
    GraphQLUnionType,
    GraphQLWrappingType,
)
import strawberry
from strawberry.django.context import StrawberryDjangoContext
from strawberry.extensions.base_extension import Extension
from strawberry.private import Private
from strawberry.schema.schema import Schema
from strawberry.schema_directive import Location
from strawberry.types.types import TypeDefinition
from strawberry.utils.await_maybe import AwaitableOrValue

from .relay import Connection, NodeType
from .utils import aio, resolvers
from .utils.inspect import get_directives, get_possible_type_definitions
from .utils.query import filter_for_user
from .utils.typing import SchemaDirective, TypeOrIterable, UserType

try:
    # Try to use the smaller/faster cache decorator if available
    _cache = functools.cache  # type:ignore
except AttributeError:
    _cache = functools.lru_cache

try:
    from .integrations.guardian import get_user_or_anonymous
except ImportError:
    # Access the user's id to force it to be loaded from the database
    get_user_or_anonymous = lambda u: (u, u.id)[0]

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)

_user_ensured_attr = "_user_ensured"
_perm_safe_marker = "_strawberry_django_perm_safe_marker"
_perm_checker_attr = "_obj_perm_checker"


def _directive(
    *,
    locations: List[Location],
    description=None,
    name=None,
) -> Callable[[_T], SchemaDirective[_T]]:
    return strawberry.schema_directive(
        locations=locations,
        description=description,
        name=name,
    )


def _default_user_has_perm(user: UserType, perm: str, obj: Any) -> bool:
    return user.has_perm(perm, obj=obj)


def _ensure_str(value: Optional[str]) -> str:
    assert value is not None
    return value


def _ensure_user(info: GraphQLResolveInfo) -> AwaitableOrValue[UserType]:
    context = cast(StrawberryDjangoContext, info.context)
    if getattr(context, _user_ensured_attr, False):
        return cast(UserType, context.request.user)

    return aio.resolve(
        cast(
            Awaitable[UserType],
            resolvers.async_unsafe(
                lambda: get_user_or_anonymous(cast(UserType, context.request.user))
            )(),
        ),
        lambda u: setattr(context, _user_ensured_attr, True) or u,
    )


@_cache
def _parse_ret_type(ret_type: GraphQLOutputType, schema: Schema):
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

    type_defs = {}
    node_type_defs = {}
    for type_ in ret_types:
        if not isinstance(type_, GraphQLObjectType):
            continue

        type_ = schema.get_type_by_name(ret_type.name)
        if type_ is None:
            continue

        for type_def in get_possible_type_definitions(type_):
            type_defs[type_def.name] = type_def
            if type_def.concrete_of and issubclass(type_def.concrete_of.origin, Connection):
                n_type = type_def.type_var_map[NodeType]
                n_type_def = cast(TypeDefinition, n_type._type_definition)  # type:ignore
                node_type_defs[type_def.name] = n_type_def

    return ReturnHandler(
        type_defs=type_defs,
        node_type_defs=node_type_defs,
        optional=optional,
        is_list=is_list,
    )


def perm_safe(result: _T) -> _T:
    """Mark a result as safe to avoid requiring test permissions again for the results."""
    # Queryset may copy itself and loose the mark
    if isinstance(result, QuerySet) and _perm_safe_marker not in result.query.annotations:
        result = result.annotate(
            **{_perm_safe_marker: Value(True)},
        )

    try:
        setattr(result, _perm_safe_marker, True)
    except AttributeError:
        if isinstance(result, Iterable):
            result = cast(_T, PermSafeIterable(result))

    return result


def is_perm_safe(result: Any) -> bool:
    """Check if the obj's perm was already checked and is safe to skip this step."""
    return getattr(result, _perm_safe_marker, False)


class PermSafeIterable(Generic[_T]):
    """Helper to mark a base iterable as perm safe (e.g. `list`)"""

    def __init__(self, iterable: Iterable[_T]):
        super().__init__()
        setattr(self, _perm_safe_marker, True)
        self.iterable = iterable

    def __iter__(self) -> Iterator[_T]:
        return iter(self.iterable)


@dataclasses.dataclass
class ReturnHandler:
    type_defs: Dict[str, TypeDefinition]
    node_type_defs: Dict[str, TypeDefinition]
    optional: bool
    is_list: bool

    def __post_init__(self):
        assert len(self.node_type_defs) <= len(self.type_defs)

    def can_handle(self, type_name: str) -> bool:
        return len(self.type_defs) == 1 and (
            type_name in self.type_defs or type_name in self.node_type_defs
        )

    def resolve(self, retval: Any, ok: bool, *, message: str) -> Any:
        if ok:
            if callable(retval):
                retval = retval()
            return retval

        if self.optional:
            return None
        elif self.is_list:
            return []
        elif len(self.type_defs) == 1 and len(self.node_type_defs) == 1:
            type_def = next(iter(self.type_defs.values()))
            return type_def.origin.from_nodes([], total_count=0)

        # In last case, raise an error
        raise PermissionError(message)


@dataclasses.dataclass(eq=True, unsafe_hash=True)
class Requires(abc.ABC):
    """Base requires definition."""

    key: Private[uuid.UUID] = dataclasses.field(
        default_factory=uuid.uuid4,
        init=False,
        compare=False,
    )
    class_key: Private[str] = dataclasses.field(default="", init=False)
    type_name: Private[Optional[str]] = dataclasses.field(default=None, init=False)

    def __post_init__(self):
        self.class_key = self.__class__.__name__

    def __call__(
        self,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ) -> AwaitableOrValue[Any]:
        schema = cast(
            Schema,
            info.schema._strawberry_schema,  # type:ignore
        )
        ret_handler = _parse_ret_type(info.return_type, schema)
        if self.type_name is not None and not ret_handler.can_handle(self.type_name):
            return _next(root, info, *args, **kwargs)

        user = _ensure_user(info)
        partial = functools.partial(_next, root, info, *args, **kwargs)

        if aio.is_awaitable(user, info=info):
            return aio.resolve_async(
                cast(Awaitable[UserType], user),
                functools.partial(self.resolve, ret_handler, partial, root, info),
            )

        return self.resolve(ret_handler, partial, root, info, cast(UserType, user))

    @abc.abstractmethod
    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> AwaitableOrValue[Any]:
        """Resolve next, checking the required permissions for the user."""
        raise NotImplementedError


@_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class LoginRequired(Requires):
    """Defines that the given field/type can only be accessed by authenticated users."""

    message: Private[str] = dataclasses.field(default="User is not authenticated.")

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return ret_handler.resolve(
            _next,
            user.is_active and user.is_authenticated,
            message=self.message,
        )


@_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class StaffRequired(Requires):
    """Defines that the given field/type can only be accessed by staff users."""

    message: Private[str] = dataclasses.field(default="User is not a staff member.")

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return ret_handler.resolve(
            _next,
            user.is_active and user.is_staff,
            message=self.message,
        )


@_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class SuperuserRequired(Requires):
    """Defines that the given field/type can only be accessed by superusers."""

    message: Private[str] = dataclasses.field(default="User is not a superuser.")

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return ret_handler.resolve(
            _next,
            user.is_active and user.is_superuser,
            message=self.message,
        )


@strawberry.type
class PermDefinition:
    """Permission definition.

    Attributes:
        resource:
            The resource to which we are requiring permission.
        permission:
            The permission itself

    """

    resource: Optional[str] = strawberry.field(
        description=(
            "The resource to which we are requiring permission. If this is "
            "empty that means that we are checking the permission directly."
        ),
    )
    permission: Optional[str] = strawberry.field(
        description=(
            "The permission itself. If this is empty that means that we "
            "are checking for any permission for the given resource."
        ),
    )

    @classmethod
    def from_perm(cls, perm: str):
        parts = perm.split(".")
        if len(parts) != 2:
            raise TypeError(
                "Permissions need to be defined as `app_label.perm`, `app_label.` or `.perm`"
            )
        return cls(
            resource=parts[0].strip() or None,
            permission=parts[1].strip() or None,
        )

    @property
    def perm(self):
        return f"{self.resource or ''}.{self.permission or ''}".strip(".")


@dataclasses.dataclass
class BasePermRequired(Requires):
    """Base class for defining permissions requirement."""

    perms: List[PermDefinition] = strawberry.field(
        description="Required perms to access this resource.",
    )
    any: bool = strawberry.field(  # noqa:A003
        description="If any or all perms listed are required.",
        default=True,
    )
    message: Private[str] = dataclasses.field(
        default="You don't have permission to access this resource.",
    )
    with_anonymous: Private[bool] = dataclasses.field(default=True)
    with_superuser: Private[bool] = dataclasses.field(default=False)

    def __post_init__(self):
        super().__post_init__()

        if not len(self.perms):
            raise TypeError(f"At least one perm is required for {self!r}")

        perms = self.perms
        if isinstance(perms, str):
            perms = [perms]
        if isinstance(perms, list):
            perms = [PermDefinition.from_perm(p) if isinstance(p, str) else p for p in perms]
        self.perms = perms

    def get_cache(
        self,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> Dict[Union[uuid.UUID, Tuple[uuid.UUID, Any]], bool]:
        cache_key = f"_{self.__class__.__name__}_cache"

        cache = getattr(user, cache_key, None)
        if cache is not None:
            return cache

        cache = {}
        setattr(user, cache_key, cache)
        return cache

    def filter_queryset(
        self,
        qs: QuerySet[_M],
        user: UserType,
        *,
        ctype: ContentType = None,
    ) -> QuerySet[_M]:
        # If results are already prefetched, we don't want to cause a refetch
        # Let the resolver exclude the results later...
        if qs._result_cache:  # type:ignore
            return qs

        # We can't filter results for anonymous user. If we are not optimizing
        # with_anonymous, return the qs without marking it as perm_safe, so this
        # will trigger a check for each result. Otherwise filter_for_user will
        # handle it its way.
        if not self.with_anonymous and user.is_anonymous:
            return qs

        return perm_safe(
            filter_for_user(
                qs,
                user,
                [p.perm for p in self.perms],
                any_perm=self.any,
                ctype=ctype,
                with_superuser=self.with_superuser,
            )
        )

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        if self.with_superuser and user.is_active and user.is_superuser:
            return ret_handler.resolve(_next, True, message=self.message)
        if self.with_anonymous and user.is_anonymous:
            return ret_handler.resolve(_next, False, message=self.message)

        cache = self.get_cache(info, user)
        if self.key in cache:
            return ret_handler.resolve(_next, cache[self.key], message=self.message)

        return self._resolve_safe(ret_handler, _next, root, info, user)

    @resolvers.async_unsafe
    def _resolve_safe(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> bool:
        cache = self.get_cache(info, user)

        # Maybe the result ended up in the cache in the meantime
        if self.key in cache:
            return ret_handler.resolve(_next, cache[self.key], message=self.message)

        f = any if self.any else all
        has_perm = f(
            (
                # Check for perm if permission is defined, otherwise check for module
                user.has_perm(p.perm)
                if p.permission
                else user.has_module_perms(_ensure_str(p.resource))
            )
            for p in self.perms
        )
        cache[self.key] = has_perm

        return ret_handler.resolve(_next, has_perm, message=self.message)


@_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class PermRequired(BasePermRequired):
    """Defines permissions required to access the given object/field.

    Given a `resource` name, the user can access the decorated object/field
    if he has any of the permissions defined in this directive.

    Examples:
        To indicate that a mutation can only be done by someone who
        has "product.add_product" perm in the django system:

        >>> @strawberry.type
        ... class Query:
        ...     @strawberry.mutation(directives=[PermRequired("product.add_product")])
        ...     def create_product(self, name: str) -> ProductType:
        ...         ...

    Attributes:
        perms:
            Perms required to access this resource.
        any:
            If any perm or all perms are required to resolve the object/field.
        with_anonymous:
            If we should optimize the permissions check and consider an anonymous
            user as not having any permissions. This is true by default, which means
            that anonymous users will not trigger has_perm checks.
        with_superuser:
            If we should optimize the permissions check and consider a superuser
            as having permissions foe everything. This is false by default to avoid
            returning unexpected results. Setting this to true will avoid triggering
            has_perm checks.

    """


@_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class ObjPermRequired(BasePermRequired):
    """Defines permissions required to access the given object/field at object level.

    Given a `resource` name, the user can access the decorated object/field
    if he has any of the permissions defined in this directive.

    Note that this depends on resolving the object to check the permissions
    specifically for that object, unlike `PermRequired` which checks it before resolving.

    Examples:
        To indicate that a field that returns a `ProductType` can only be accessed
        by someone who has "product.view_product"
        has "product.view_product" perm in the django system:

        >>> @strawberry.type
        ... class SomeType:
        ...     product: ProductType = strawberry.field(
        ...         directives=[ObjPermRequired(".add_product")],
        ...     )

    Attributes:
        perms:
            Perms required to access this resource.
        any:
            If any perm or all perms are required to resolve the object/field.
        checker:
            An optional callable to check if the user can access that object.
            By default it resolves using `user.has_perm(perm, retval)`
        with_anonymous:
            If we should optimize the permissions check and consider an anonymous
            user as not having any permissions. This is true by default, which means
            that anonymous users will not trigger has_perm checks.
        with_superuser:
            If we should optimize the permissions check and consider a superuser
            as having permissions foe everything. This is false by default to avoid
            returning unexpected results. Setting this to true will avoid triggering
            has_perm checks.
            Note that lists and relay connections filter the results based on the
            existence of the exact permissions for the user, which means that when
            this is true, no results would be filtered, which might not be the
            expected result.

    """

    checker: Private[Optional[Callable[[UserType, str, Any], bool]]] = dataclasses.field(
        default=None
    )

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        if self.with_superuser and user.is_active and user.is_superuser:
            return ret_handler.resolve(_next, True, message=self.message)
        if self.with_anonymous and user.is_anonymous:
            return ret_handler.resolve(_next, False, message=self.message)

        retval = _next()
        if retval is None:
            # Retval is None, its fine to return it no matter what
            return None

        # Avoid is_awaitable as much as we can
        if not isinstance(retval, (list, Model, QuerySet)) and aio.is_awaitable(retval, info=info):
            return aio.resolve_async(
                retval,
                functools.partial(self._resolve_retval, ret_handler, root, info, user),
            )

        return self._resolve_retval(ret_handler, root, info, user, retval)

    def _resolve_retval(
        self,
        ret_handler: ReturnHandler,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        retval: TypeOrIterable[_T],
    ) -> AwaitableOrValue[TypeOrIterable[_T]]:
        # If retval is already perm safe, just return it
        if is_perm_safe(retval):
            return ret_handler.resolve(retval, True, message=self.message)

        if not isinstance(retval, Iterable):
            cache = self.get_cache(info, user)
            has_perm = cache.get((self.key, retval))
            if has_perm is not None:
                return ret_handler.resolve(retval, has_perm, message=self.message)

        return self._resolve_retval_safe(ret_handler, root, info, user, retval)

    @resolvers.async_unsafe
    def _resolve_retval_safe(
        self,
        ret_handler: ReturnHandler,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        retval: TypeOrIterable[_T],
    ) -> TypeOrIterable[_T]:
        cache = self.get_cache(info, user)
        checker = self.checker or _default_user_has_perm
        f = any if self.any else all

        def _check_obj(obj):
            key = (self.key, obj)
            has_perm = cache.get(key)
            if has_perm is not None:
                return has_perm

            has_perm = f(is_perm_safe(obj) or checker(user, p.perm, obj) for p in self.perms)
            cache[key] = has_perm
            return has_perm

        if isinstance(retval, Iterable):
            return ret_handler.resolve(
                [obj for obj in retval if _check_obj(obj)],
                True,
                message=self.message,
            )

        return ret_handler.resolve(retval, _check_obj(retval), message=self.message)


@_directive(locations=[Location.FIELD_DEFINITION])
class RootPermRequired(BasePermRequired):
    """Defines permissions required to access the given field at object level.

    This will check the permissions for the root object to access the given field.

    Unlike `ObjPermRequired`, this uses the root value (the object where the field
    is defined) to resolve the field, which means that this cannot be used for root
    queries and types.

    Examples:
        To indicate that a field inside a `ProductType` can only be accessed if
        the user has "product.view_field" in it in the django system:

        >>> @gql.django.type(Product)
        ... class ProdyctType:
        ...     some_field: str = strawberry.field(
        ...         directives=[RootPermRequired(".add_product")],
        ...     )

    Attributes:
        perms:
            Perms required to access this resource.
        any:
            If any perm or all perms are required to resolve the object/field.
        checker:
            An optional callable to check if the user can access that object.
            By default it resolves using `user.has_perm(perm, retval)`
        with_anonymous:
            If we should optimize the permissions check and consider an anonymous
            user as not having any permissions. This is true by default, which means
            that anonymous users will not trigger has_perm checks.
        with_superuser:
            If we should optimize the permissions check and consider a superuser
            as having permissions foe everything. This is false by default to avoid
            returning unexpected results. Setting this to true will avoid triggering
            has_perm checks.

    """

    checker: Private[Optional[Callable[[UserType, str, Any], bool]]] = dataclasses.field(
        default=None
    )

    def resolve(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        if self.with_superuser and user.is_active and user.is_superuser:
            return ret_handler.resolve(_next, True, message=self.message)
        if self.with_anonymous and user.is_anonymous:
            return ret_handler.resolve(_next, False, message=self.message)

        cache = self.get_cache(info, user)
        key = (self.key, root)
        if key in cache:
            return ret_handler.resolve(_next, cache[key], message=self.message)

        return self._resolve_safe(ret_handler, _next, root, info, user)

    @resolvers.async_unsafe
    def _resolve_safe(
        self,
        ret_handler: ReturnHandler,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> bool:
        cache = self.get_cache(info, user)
        key = (self.key, root)

        # Maybe the result ended up in the cache in the meantime
        if key in cache:
            return ret_handler.resolve(_next, cache[key], message=self.message)

        f = any if self.any else all
        checker = self.checker or _default_user_has_perm
        has_perm = f(is_perm_safe(root) or checker(user, p.perm, root) for p in self.perms)
        cache[key] = has_perm

        return ret_handler.resolve(_next, has_perm, message=self.message)


class DjangoPermissionsExtension(Extension):
    """Per field, model and object permissions for resolvers."""

    _cache: ClassVar[Dict[Tuple[str, str], List[Requires]]] = {}

    def resolve(
        self,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ) -> AwaitableOrValue[Any]:
        typename = info.path.typename
        assert typename

        cache_key = (typename, info.field_name)
        requires = self._cache.get(cache_key)
        if requires is None:
            schema = cast(
                Schema,
                info.schema._strawberry_schema,  # type:ignore
            )
            fields_or_types = []

            type_ = schema.get_type_by_name(typename)
            if type_ is not None:
                for type_def in get_possible_type_definitions(type_):
                    field = next(
                        (
                            f
                            for f in type_def.fields
                            if info.field_name == schema.config.name_converter.get_graphql_name(f)
                        ),
                    )
                    fields_or_types.append(field)

            ret_handler = _parse_ret_type(info.return_type, schema)
            fields_or_types.extend(ret_handler.type_defs.values())
            fields_or_types.extend(ret_handler.node_type_defs.values())

            requires = get_directives(fields_or_types, instanceof=Requires)
            self._cache[cache_key] = requires

        if requires:
            for r in reversed(requires):
                _next = functools.partial(r, _next)

        return _next(root, info, *args, **kwargs)
