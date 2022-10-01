import contextvars
import dataclasses
import enum
import functools
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
    overload,
)

from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Model, QuerySet
from graphql.type.definition import GraphQLResolveInfo
import strawberry
from strawberry.django.context import StrawberryDjangoContext
from strawberry.private import Private
from strawberry.schema_directive import Location
from strawberry.types.info import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from typing_extensions import Self, assert_never, final

from .directives import SchemaDirectiveHelper, SchemaDirectiveWithResolver
from .relay import Connection, GlobalID
from .types import OperationInfo, OperationMessage
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

    get_user_or_anonymous = resolvers.async_safe(get_user_or_anonymous)
except ImportError:
    # Access the user's id to force it to be loaded from the database
    get_user_or_anonymous = resolvers.async_safe(lambda u: (u, u.id)[0])


_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)

_user_ensured_attr = "_user_ensured"
_perm_safe_marker = "_strawberry_django_perm_safe_marker"

_return_condition = """\
When the condition fails, the following can happen (following this priority):
1) If the return value can return an `OperationMessage`, it is returned as that type.
2) If the field is mandatory (e.g. `String!`), this will result in an error.
3) If the field is not mandatory and a list (e.g. `[String]`), an empty list will be returned.
4) If the field is not mandatory and any scalar or object (e.g. `String`), `null` will be returned.
5) If the field is a relay `Connection`, an empty connection will be returned.
"""
_desc = lambda desc: f"{desc}\n\n{_return_condition.strip()}"

perm_safe: contextvars.ContextVar[Optional[List[bool]]] = contextvars.ContextVar(
    "perm_safe",
    default=None,
)
running_checks: contextvars.ContextVar[List["HasPermDirective"]] = contextvars.ContextVar(
    "running_checks",
    default=[],
)


def init_checker(checker: "HasPermDirective"):
    checks = running_checks.get()[:]
    checks.append(checker)
    running_checks.set(checks)

    perms = perm_safe.get() or []
    perm_safe.set(perms[:])


def clear_checker():
    perm_safe.set(None)
    checks = running_checks.get()
    if checks:
        checks.clear()


def set_perm_safe(value: bool):
    ps = perm_safe.get()
    if ps is not None:
        ps.append(value)


def is_perm_safe():
    ps = perm_safe.get()
    return bool(ps and all(ps))


def filter_with_perms(qs: QuerySet[_M], info: Info) -> QuerySet[_M]:
    checks = running_checks.get()
    if not checks:
        return qs

    # Do not do anything is results are cached, the target is the the retval
    if qs._result_cache is not None:  # type:ignore
        set_perm_safe(False)
        return qs

    user = cast(StrawberryDjangoContext, info.context).request.user
    # If the user is anonymous, we can't filter object permissions for it
    if user.is_anonymous:
        set_perm_safe(False)
        return qs.none()

    for check in checks:
        if check.target != PermTarget.RETVAL:
            continue

        qs = filter_for_user(
            qs,
            cast(UserType, user),
            [p.perm for p in check.permissions],
            any_perm=check.any,
            with_superuser=check.with_superuser,
        )

    set_perm_safe(True)
    return qs


@overload
def get_with_perms(
    pk: strawberry.ID,
    info: Info,
    *,
    required: Literal[True],
    model: Type[_M],
) -> _M:
    ...


@overload
def get_with_perms(
    pk: strawberry.ID,
    info: Info,
    *,
    required: bool = ...,
    model: Type[_M],
) -> Optional[_M]:
    ...


@overload
def get_with_perms(
    pk: GlobalID,
    info: Info,
    *,
    required: Literal[True],
    model: Type[_M],
) -> _M:
    ...


@overload
def get_with_perms(
    pk: GlobalID,
    info: Info,
    *,
    required: bool = ...,
    model: Type[_M],
) -> Optional[_M]:
    ...


@overload
def get_with_perms(
    pk: GlobalID,
    info: Info,
    *,
    required: Literal[True],
) -> Any:
    ...


@overload
def get_with_perms(
    pk: GlobalID,
    info: Info,
    *,
    required: bool = ...,
) -> Optional[Any]:
    ...


def get_with_perms(pk, info, *, required=False, model=None):
    if isinstance(pk, GlobalID):
        instance = pk.resolve_node(info, required=required, ensure_type=model)
        if aio.is_awaitable(instance, info=info):
            instance = resolvers.resolve_sync(instance)
        instance = cast(models.Model, instance)
    else:
        assert model
        instance = model._default_manager.get(pk=pk)

    if instance is None:
        return None

    checks = running_checks.get()
    if not checks:
        return instance

    user = cast(StrawberryDjangoContext, info.context).request.user
    for check in checks:
        f = any if check.any else all
        checker = check.obj_perm_checker(info, cast(UserType, user))
        if not f(checker(p, instance) for p in check.permissions):
            raise PermissionDenied(check.message)

    return instance


@dataclasses.dataclass
class AuthDirective(SchemaDirectiveWithResolver):
    """Base auth directive definition."""

    has_resolver: ClassVar = True

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
                    **kwargs,
                ),
                info=info,
            )

        return self.resolve_for_user(
            helper,
            resolver,
            root,
            info,
            cast(UserType, user),
            **kwargs,
        )

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        **kwargs,
    ):
        raise NotImplementedError

    def resolve_retval(
        self,
        helper: SchemaDirectiveHelper,
        root: Any,
        info: GraphQLResolveInfo,
        retval: Any,
        auth_ok: AwaitableOrValue[bool],
    ):
        # If this is not bool, assume async. Avoid is_awaitable since it is slow
        if not isinstance(auth_ok, bool):
            return aio.resolve_async(
                auth_ok,
                functools.partial(self.resolve_retval, helper, root, info, retval),
            )

        # Make sure any chained resolvers will not try to validate the result again
        clear_checker()

        if auth_ok:
            if callable(retval):
                retval = retval()
            return retval

        for p in helper.ret_possibilities:
            if p.type_def and issubclass(p.type_def.origin, OperationMessage):
                return p.type_def.origin(
                    kind=OperationMessage.Kind.PERMISSION,
                    message=self.message,
                    field=info.field_name,
                )
            elif p.type_def and issubclass(p.type_def.origin, OperationInfo):
                return p.type_def.origin(
                    messages=[
                        OperationMessage(
                            kind=OperationMessage.Kind.PERMISSION,
                            message=self.message,
                            field=info.field_name,
                        )
                    ],
                )

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
        raise PermissionDenied(self.message)


@dataclasses.dataclass
class ConditionDirective(AuthDirective):
    """Base directive for condition checking."""

    priority: ClassVar = 99
    message: Private[str] = dataclasses.field(default="User does not have permission.")

    def __init_subclass__(cls) -> None:
        for attr in ["__hash__", "__eq__"]:
            if attr not in cls.__dict__:
                setattr(cls, attr, getattr(cls, attr))
        return super().__init_subclass__()

    def __hash__(self):
        return hash(self.__class__)

    def __eq__(self, other: Self):
        return self.__class__ == other.__class__

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        **kwargs,
    ):
        return self.resolve_retval(
            helper,
            root,
            info,
            resolver,
            self.check_condition(root, info, user, **kwargs),
        )

    def check_condition(
        self, root: Any, info: GraphQLResolveInfo, user: UserType, **kwargs
    ) -> bool:
        raise NotImplementedError


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc("Can only be resolved by authenticated users."),
)
@final
class IsAuthenticated(ConditionDirective):
    """Mark a field as only resolvable by authenticated users."""

    message: Private[str] = dataclasses.field(default="User is not authenticated.")

    def check_condition(
        self, root: Any, info: GraphQLResolveInfo, user: UserType, **kwargs
    ) -> bool:
        return user.is_authenticated and user.is_active


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc("Can only be resolved by staff users."),
)
@final
class IsStaff(ConditionDirective):
    """Mark a field as only resolvable by staff users."""

    message: Private[str] = dataclasses.field(default="User is not a staff member.")

    def check_condition(
        self, root: Any, info: GraphQLResolveInfo, user: UserType, **kwargs
    ) -> bool:
        return user.is_authenticated and user.is_staff


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc("Can only be resolved by superuser users."),
)
@final
class IsSuperuser(ConditionDirective):
    """Mark a field as only resolvable by superuser users."""

    message: Private[str] = dataclasses.field(default="User is not a superuser.")

    def check_condition(
        self, root: Any, info: GraphQLResolveInfo, user: UserType, **kwargs
    ) -> bool:
        return user.is_authenticated and user.is_superuser


@strawberry.input(description="Permission definition for schema directives.")
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
class HasPermDirective(AuthDirective):
    """Permission directive."""

    target: ClassVar[Optional[PermTarget]]

    # FIXME: This is to allow passing a str or list[str] to perms, but still print
    perms: strawberry.Private[Union[List[str], str]]

    permissions: List[PermDefinition] = strawberry.field(
        description="Required perms to access this resource.",
        default_factory=list,
    )
    any: bool = strawberry.field(  # noqa:A003
        description="If any or all perms listed are required.",
        default=True,
    )
    message: Private[str] = dataclasses.field(
        default="You don't have permission to access this resource.",
    )
    perm_checker: Private[
        Callable[[GraphQLResolveInfo, UserType], Callable[[PermDefinition], bool]]
    ] = dataclasses.field(
        default=lambda info, user: lambda perm: (
            user.has_perm(perm.perm)
            if perm.permission
            else user.has_module_perms(cast(str, perm.resource))
        ),
    )
    obj_perm_checker: Private[
        Callable[[GraphQLResolveInfo, UserType], Callable[[PermDefinition, Any], bool]]
    ] = dataclasses.field(
        default=lambda info, user: lambda perm, obj: (
            # Check global perms first, then object specific
            user.has_perm(perm.perm)
            or user.has_perm(perm.perm, obj=obj)
        ),
    )
    with_anonymous: Private[bool] = dataclasses.field(default=True)
    with_superuser: Private[bool] = dataclasses.field(default=False)

    def __post_init__(self):
        super().__post_init__()

        perms = self.perms
        if isinstance(perms, str):
            perms = [perms]
        if isinstance(perms, Iterable):
            perms = [PermDefinition.from_perm(p) if isinstance(p, str) else p for p in perms]

        if not len(self.perms):
            raise TypeError(f"At least one perm is required for {self!r}")

        assert all(isinstance(p, PermDefinition) for p in perms)
        self.permissions = perms

    def __init_subclass__(cls) -> None:
        for attr in ["__hash__", "__eq__"]:
            if attr not in cls.__dict__:
                setattr(cls, attr, getattr(cls, attr))
        return super().__init_subclass__()

    def __hash__(self):
        return hash(
            (
                self.target,
                frozenset(self.perms),
                self.any,
                self.perm_checker,
                self.obj_perm_checker,
            )
        )

    def __eq__(self, other: Self):
        return (
            self.__class__ == other.__class__
            and self.target == other.target
            and set(self.perms) == set(other.perms)
            and self.any == other.any
            and self.message == other.message
            and self.perm_checker == other.perm_checker
            and self.obj_perm_checker == other.obj_perm_checker
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

    def resolve_for_user(
        self,
        helper: SchemaDirectiveHelper,
        resolver: Callable,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        **kwargs,
    ):
        if self.with_superuser and user.is_active and user.is_superuser:
            return self.resolve_retval(helper, root, info, resolver, True)
        if self.with_anonymous and user.is_anonymous:
            return self.resolve_retval(helper, root, info, resolver, False)

        cache = self.get_cache(info, user)

        if self.target is None:
            has_perm = cache.get(self)
            if has_perm is None:
                has_perm = self._has_perm_safe(root, info, user)
            return self.resolve_retval(helper, root, info, resolver, has_perm)
        elif self.target == PermTarget.ROOT:
            has_perm = cache.get((self, root))
            if has_perm is None:
                has_perm = self._has_obj_perm_safe(root, info, user, root)
            return self.resolve_retval(helper, root, info, resolver, has_perm)
        elif self.target == PermTarget.RETVAL:
            init_checker(self)

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
            assert_never(self.target)  # noqa:R503

    @resolvers.async_safe
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
        checker = self.perm_checker(info, user)
        has_perm = f(checker(p) for p in self.permissions)
        cache[self] = has_perm

        return has_perm

    @resolvers.async_safe
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
        checker = self.obj_perm_checker(info, user)
        has_perm = f(checker(p, obj) for p in self.permissions)

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
        if is_perm_safe():
            return self.resolve_retval(helper, root, info, obj, True)

        if isinstance(obj, Iterable):
            return self._resolve_iterable_perms_safe(helper, root, info, user, obj)

        cache = self.get_cache(info, user)
        has_perm = cache.get((self, obj))
        if has_perm is None:
            has_perm = self._has_obj_perm_safe(root, info, user, obj)

        return self.resolve_retval(helper, root, info, obj, has_perm)

    @resolvers.async_safe
    def _resolve_iterable_perms_safe(
        self,
        helper: SchemaDirectiveHelper,
        root: Any,
        info: GraphQLResolveInfo,
        user: UserType,
        objs: Iterable[Any],
    ) -> Any:
        if is_perm_safe():
            return self.resolve_retval(helper, root, info, objs, True)

        cache = self.get_cache(info, user)
        f = any if self.any else all
        checker = self.obj_perm_checker(info, user)

        def _check_obj(obj):
            key = (self, obj)
            if key in cache:
                return cache[key]

            has_perm = f(checker(p, obj) for p in self.permissions)
            cache[key] = has_perm

            return has_perm

        objs = [obj for obj in objs if _check_obj(obj)]
        return self.resolve_retval(helper, root, info, objs, True)


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc("Will check if the user has any/all permissions to resolve this."),
)
@final
class HasPerm(HasPermDirective):
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

    priority: ClassVar = 55
    target: ClassVar = None


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc(
        "Will check if the user has any/all permissions for the parent "
        "of this field to resolve this."
    ),
)
@final
class HasRootPerm(HasPermDirective):
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

    priority: ClassVar = 50
    target: ClassVar = PermTarget.ROOT


@strawberry.schema_directive(
    locations=[Location.FIELD_DEFINITION],
    description=_desc(
        "Will check if the user has any/all permissions for the resolved "
        "value of this field before returning it."
    ),
)
@final
class HasObjPerm(HasPermDirective):
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

    target: ClassVar = PermTarget.RETVAL
