import dataclasses
import enum
import functools
from typing import (
    Any,
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

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model, QuerySet, Value
from graphql.type.definition import GraphQLResolveInfo
import strawberry
from strawberry.django.context import StrawberryDjangoContext
from strawberry.private import Private
from strawberry.schema_directive import Location
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Self

from .directives import SchemaDirectiveHelper, SchemaDirectiveResolver, schema_directive
from .relay import Connection
from .utils import aio, resolvers
from .utils.query import filter_for_user
from .utils.typing import UserType

try:
    # Try to use the smaller/faster cache decorator if available
    _cache = functools.cache  # type:ignore
except AttributeError:
    _cache = functools.lru_cache

try:
    from .integrations.guardian import get_user_or_anonymous

    get_user_or_anonymous = resolvers.async_unsafe(get_user_or_anonymous)
except ImportError:
    # Access the user's id to force it to be loaded from the database
    get_user_or_anonymous = resolvers.async_unsafe(lambda u: (u, u.id)[0])


_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)

_user_ensured_attr = "_user_ensured"
_perm_safe_marker = "_strawberry_django_perm_safe_marker"


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
class AuthDirective(SchemaDirectiveResolver):
    """Base auth directive definition."""

    has_resolver: ClassVar[Private[bool]] = True

    @property
    def message(self) -> str:
        return "User is not authorized."

    def resolve(
        self,
        helper: SchemaDirectiveHelper,
        _next: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        *args,
        **kwargs,
    ):
        context = cast(StrawberryDjangoContext, info.context)
        resolver = functools.partial(_next, root, info, *args, **kwargs)

        user = cast(UserType, context.request.user)
        if not getattr(context, _user_ensured_attr, False):
            return aio.resolve(
                cast(UserType, get_user_or_anonymous(user)),
                functools.partial(
                    self.resolve_for_user,
                    helper,
                    resolver,
                    root,
                    info,
                ),
                info=info,
            )

        return self.resolve_for_user(
            helper,
            resolver,
            root,
            info,
            cast(UserType, user),
        )

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        raise NotImplementedError

    def resolve_retval(
        self,
        helper: SchemaDirectiveHelper,
        retval: Any,
        auth_ok: AwaitableOrValue[bool],
    ):
        # If this is not bool, assume async. Avoid is_awaitable since it is slow
        if not isinstance(auth_ok, bool):
            return aio.resolve_async(
                auth_ok, functools.partial(self.resolve_retval, helper, retval)
            )

        if auth_ok:
            if callable(retval):
                retval = retval()
            return retval

        # If the field is optional, return null
        if helper.optional:
            return None

        # If it is a list, return an empty list
        if helper.is_list:
            return []

        # If it is a Connection, try to return an empty connection, but only if
        # it is the only possibility available...
        if len(helper.ret_possibilities) == 1:
            type_def = helper.ret_possibilities[0].type_def
            if (
                type_def
                and type_def.concrete_of
                and issubclass(type_def.concrete_of.origin, Connection)
            ):
                return type_def.origin.from_nodes([], total_count=0)

        # In last case, raise an error
        raise PermissionError(self.message)


@schema_directive(locations=[Location.FIELD_DEFINITION])
class LoginRequired(AuthDirective):
    """Mark a field as only resolvable by authenticated users."""

    message: Private[str] = dataclasses.field(default="User is not authenticated.")

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return self.resolve_retval(
            helper,
            resolver,
            bool(user.is_active and user.is_authenticated),
        )


@schema_directive(locations=[Location.FIELD_DEFINITION])
class StaffRequired(AuthDirective):
    """Mark a field as only resolvable by staff users."""

    message: Private[str] = dataclasses.field(default="User is not a staff member.")

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return self.resolve_retval(
            helper,
            resolver,
            bool(user.is_active and user.is_staff),
        )


@schema_directive(locations=[Location.FIELD_DEFINITION])
class SuperuserRequired(AuthDirective):
    """Mark a field as only resolvable by superuser users."""

    message: Private[str] = dataclasses.field(default="User is not a superuser.")

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        return self.resolve_retval(
            helper,
            resolver,
            bool(user.is_active and user.is_superuser),
        )


@strawberry.input
@dataclasses.dataclass(eq=True, order=True, frozen=True)
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


class PermTarget(enum.Enum):
    """Permission location."""

    ROOT = "root"
    RETVAL = "retval"


@dataclasses.dataclass
class PermDirective(AuthDirective):
    """Permission directive."""

    target: ClassVar[Private[Optional[PermTarget]]] = None

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

    def __hash__(self):
        return hash(
            (
                self.__class__,
                frozenset(self.perms),
                self.any,
            )
        )

    def __eq__(self, other: Self):
        return (
            self.__class__ == other.__class__
            and set(self.perms) == set(other.perms)
            and self.any == other.any
            and self.message == other.message
            and self.with_anonymous == other.with_anonymous
            and self.with_superuser == other.with_superuser
        )

    def get_cache(
        self,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> Dict[Union[Self, Tuple[Self, Any]], bool]:
        cache_key = f"_{self.__class__.__name__}_cache"

        cache = getattr(user, cache_key, None)
        if cache is not None:
            return cache

        cache = {}
        setattr(user, cache_key, cache)
        return cache

    def get_queryset(
        self,
        qs: QuerySet[_M],
        user: UserType,
        *,
        ctype: ContentType = None,
    ) -> QuerySet[_M]:
        # Do not do anything is results are cached, the target is the the retval
        if qs._result_cache or self.target != PermTarget.RETVAL:  # type:ignore
            return qs

        # If the user is anonymous, we can't filter object permissions for it
        if user.is_anonymous:
            return qs.none()

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

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ):
        if self.with_superuser and user.is_active and user.is_superuser:
            return self.resolve_retval(helper, resolver, True)
        if self.with_anonymous and user.is_anonymous:
            return self.resolve_retval(helper, resolver, False)

        cache = self.get_cache(info, user)

        if self.target is None:
            has_perm = cache.get(self)
            if has_perm is None:
                has_perm = self._has_perm_safe(root, info, user)
            return self.resolve_retval(helper, resolver, has_perm)
        elif self.target == PermTarget.ROOT:
            has_perm = cache.get((self, root))
            if has_perm is None:
                has_perm = self._has_obj_perm_safe(root, info, user, root)
            return self.resolve_retval(helper, resolver, has_perm)
        elif self.target == PermTarget.RETVAL:
            ret = resolver()
            if ret is None:
                # Retval is None, just return it
                return None

            # Avoid is_awaitable as much as we can
            if not isinstance(ret, (list, Model, QuerySet)) and aio.is_awaitable(ret, info=info):
                return aio.resolve_async(
                    ret,
                    functools.partial(self._resolve_obj_perms, helper, root, info, user),
                )
            return self._resolve_obj_perms(helper, root, info, user, ret)
        else:
            raise AssertionError(f"Unknown target {self.target!r}")

    @resolvers.async_unsafe
    def _has_perm_safe(
        self,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
    ) -> bool:
        cache = self.get_cache(info, user)

        # Maybe the result ended up in the cache in the meantime
        if self in cache:
            return cache[self]

        f = any if self.any else all
        has_perm = f(
            (
                # Check for perm if permission is defined, otherwise check for module
                user.has_perm(p.perm)
                if p.permission
                else user.has_module_perms(cast(str, p.resource))
            )
            for p in self.perms
        )
        cache[self] = has_perm

        return has_perm

    @resolvers.async_unsafe
    def _has_obj_perm_safe(
        self,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        obj: Any,
    ) -> bool:
        cache = self.get_cache(info, user)

        # Maybe the result ended up in the cache in the meantime
        key = (self, obj)
        if key in cache:
            return cache[key]

        f = any if self.any else all
        has_perm = f(is_perm_safe(root) or user.has_perm(p.perm, obj=root) for p in self.perms)
        cache[key] = has_perm

        return has_perm

    def _resolve_obj_perms(
        self,
        helper: SchemaDirectiveHelper,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        obj: Any,
    ) -> Any:
        # If retval is already perm safe, just return it
        if is_perm_safe(obj):
            return self.resolve_retval(helper, obj, True)

        if not isinstance(obj, Iterable):
            cache = self.get_cache(info, user)
            has_perm = cache.get((self, obj))
            if has_perm is not None:
                return self.resolve_retval(helper, obj, has_perm)

        return self._resolve_obj_perms_safe(helper, root, info, user, obj)

    @resolvers.async_unsafe
    def _resolve_obj_perms_safe(
        self,
        helper: SchemaDirectiveHelper,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        obj: Any,
    ) -> Any:
        cache = self.get_cache(info, user)
        f = any if self.any else all

        def _check_obj(obj):
            key = (self, obj)
            has_perm = cache.get(key)
            if has_perm is not None:
                return has_perm

            has_perm = f(is_perm_safe(obj) or user.has_perm(p.perm, obj) for p in self.perms)
            cache[key] = has_perm
            return has_perm

        if isinstance(obj, Iterable):
            return self.resolve_retval(helper, [i for i in obj if _check_obj(i)], True)

        return self.resolve_retval(helper, obj, _check_obj(obj))


@schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class PermRequired(PermDirective):
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


@schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class ObjPermRequired(PermDirective):
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

    target: ClassVar[Private[PermTarget]] = PermTarget.RETVAL


@schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class RootPermRequired(PermDirective):
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

    target: ClassVar[Private[PermTarget]] = PermTarget.ROOT
