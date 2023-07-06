## Migrating to Strawberry Django

All the extra features provided by this lib were contributed and merged directly
into the official
[strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
lib. Since then this lib is deprecated and the official integration should be used instead.

Follow these steps to migrate your existing code:

### 1) Required dependencies

Make sure you have `strawberry-graphql-django>=0.10.0` in your dependencies.
After the migration is complete you can safely remove `strawberry-django-plus` from them.

### 2) Replace your `gql.*` aliases

The `gql.*` alias should be replaces by their correct counterpart. For example:

- `gql.type` -> `strawberry.type`
- `gql.field` -> `strawberry.field`
- `gql.django.type` -> `strawberry.django.type` (or `strawberry_django.type`)
- `gql.django.field` -> `strawberry.django.field` (or `strawberry_django.field`)

### 3) Relay API adjustments

The relay integration was from `v3.0` in this lib was ported "as is" to
strawberry django, meaning that the `gql.*` step adjustment will also
adjust the relay APIs.

In case you are migrating from `v2.x`, check the `v3.0.0` migration guide below.
You don't need to upgrade to `v3.0.0` first, but you can use it to help adjusting
your relay code.

If you were not using the relay integration, you can skip this step.

### 4) Mutation API adjustments

There are some differences to be aware for the mutations API:

1. `strawberry_django.mutation` and `strawberry_django.input_mutation` changed the
   `handle_django_errors` argument default value from `True` to `False`. If you want
   the old behaviour for all mutations without having to modify the argument by hand,
   you can set the `"MUTATIONS_DEFAULT_HANDLE_ERRORS": True` in your
   [strawberry django settings](https://strawberry-graphql.github.io/strawberry-graphql-django/guide/settings/)
2. CUD mutations are now based on strawberry django's ones. You should rename your
   `create_mutation`/`update_mutation`/`delete_mutation` calls to
   `create`/`update`/`delete` respectively.
3. CUD mutations from strawberry django define the input field argument's name to
   `data` by default. You can change it to `input` (this lib's argument name) by passing
   `argument_name="input"` to the mutation. If you want that name for all mutations
   regardless, you can set the `"MUTATIONS_DEFAULT_ARGUMENT_NAME": "input"` in your
   [strawberry django settings](https://strawberry-graphql.github.io/strawberry-graphql-django/guide/settings/)

### 5) Permissions refactored to use Field Extensions

Permission checking used to require including a "Schema Directive Extension" in your
schema's extensions. That is not required anymore since the new implementation
is based on the official "Field Extensions" support from strawberry.

Most extensions have the same name, except for `HasRootPerm` and `HasSourcePerm`
that were renamed like this:

- `HasRootPerm` -> `HasSourcePerm`
- `HasObjPerm` -> `HasRetvalPerm`

To migrate, all you need to do is change the directive your were previously
inserting in your field with the related extension. For example, the following code:

```python
import strawberry
from strawberry_django_plus import gql
from strawberry_django_plus.permissions import IsAuthenticated, HasObjPerm
from strawberry_django_plus.directives import SchemaDirectiveExtension

@gql.type
class Query:
    fruit: Fruit = gql.django.field(directives=[IsAuthenticated()])
    fruit2: Fruit = gql.django.field(directives=[HasObjPerm("can_view_fruit")])

schema = strawberry.schema(
    query=Query,
    extensions=[
        SchemaDirectiveExtension,
    ],
)
```

Can be migrated to:

```python
import strawberry
from strawberry_django.permissions import IsAuthenticated, HasRetvalPerm

@strawberry.type
class Query:
    fruit: Fruit = strawberry.django.field(extensions=[IsAuthenticated()])
    fruit2: Fruit = strawberry.django.field(extensions=[HasRetvalPerm("can_view_fruit")])

schema = strawberry.schema(
    query=Query,
)
```

### 6) Types/Fields description from model's docstring and field's `help_text`

The ability to retrieve types/fields description directly from the model's
docstring and/or field's `help_text` is available on strawberry django,
but it is an opt-in feature.

To enable those, you can set the `"FIELD_DESCRIPTION_FROM_HELP_TEXT": True` and
`"TYPE_DESCRIPTION_FROM_MODEL_DOCSTRING": True` in your
[strawberry django settings](https://strawberry-graphql.github.io/strawberry-graphql-django/guide/settings/)

### 7) Enjoy! 😊

If you followed all those steps correctly, your code should be working just like
it was before.

Be sure to check
[strawberry django's documentation page](https://strawberry-graphql.github.io/strawberry-graphql-django/)
to know more about all the feature it provides.

Also, if you find any issues during your migration, be sure to open an issue at its repository.

Don't forget you can also reach us in our [discord page](https://strawberry.rocks/discord).

## Version 3.0.0

### Debug toolbar integration moved to strawberry-graphql-django

The debug-toolbar-integration was merged on the official
[strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
and should be used from there.

If you were using the integration before, you need to change your `MIDDLEWARE`
settings from:

```python
MIDDLEWARE = [
    ...
    "strawberry_django_plus.middlewares.debug_toolbar.DebugToolbarMiddleware",
    ...
]
```

to:

```python
MIDDLEWARE = [
    ...
    "strawberry_django.middlewares.debug_toolbar.DebugToolbarMiddleware",
    ...
]
```

Also make sure that you have `"strawberry_django"` added to your `INSTALLED_APPS`
settings.

### Relay integration moved to strawberry core

The relay integration from this lib has been contributed and merged directly
on strawberry core.

It works almost the same as the one in this lib with a few differences. Be sure
to check the [strawberry's relay docs](https://strawberry.rocks/docs/guides/relay)
to know more about it.

strawberry-django-plus has been updated to use that new official integration instead
of the one provided by us, which has also been removed. If you were using it, there
are a few adjustments that you need to do:

- Change your `relay.Connection[SomeType]` annotations to either
  `relay.ListConnection[SomeType]` or `gql.django.ListConnectionWithTotalCount`.

`relay.Connection` is now an abstract class which you can inherit from it to implement
your own pagination algorithm.

`relay.ListConnection` is a limit/offset implementation of `relay.Connection` that
works the same way as this lib's one used to work, except for the fact that it
doesn't include a `totalCount` field by default. For that reason we are providing
a new `gql.django.ListConnectionWithTotalCount` which builds on top
`relay.ListConnection` and includes a `totalCount` field, meaning it will actually
produce the same schema and functionality as the old `Connection`.

- All fields annotated with a `Connection` needs to define be set to a relay field,
  and any resolver decorated with `@relay.connection` should define the connection
  type it returns.

For example, you can migrate this code:

```python
@gql.type
class Query:
    some_conn: relay.Connection[SomeType]
    some_django_conn: relay.Connection[SomeDjangoType] = gql.django.connection()

    @gql.django.connection
    def other_django_conn(self) -> Iterable[SomeDjangoType]:
        return SomeDjangoModel.objects.all()
```

By changing it to:

```python
@gql.type
class Query:
    some_conn: relay.Connection[SomeType] = relay.connection(resolver=some_resolver)
    some_django_conn: relay.ListConnection[SomeDjangoType] = gql.django.connection()

    @gql.django.connection(relay.ListConnection[SomeDjangoType])
    def other_django_conn(self) -> Iterable[SomeDjangoModel]:
        return SomeDjangoModel.objects.all()
```

Note that the `other_django_conn` resolver's return type don't need to be set
to an `Iterable[SomeDjangoType]`, because the connection type now is retrieved
from the connection decorator. This means you can remove some useless `casts`
for type checkers in those functions.

- All connection fields should define a resolver

The new official integration enforces a resolver that returns an iterable/generator
of items for all connection fields. This means that you either need to pass a
`resolver` argument to `relay.connection()` or use it as a decorator on top
of a resolver.

When using `gql.django.connection()`, strawberry-django-plus will create a default
resolver for you in case you didn't use it as a decorator on top of one. That
default resolver works the same way as before, by returning a queryset of
the model's queryset (e.g. `SomeDjangoModel.objects.all()`).

- All `Node` implemented types should define a `relay.NodeID` annotation

The field that will be used to generate the `GlobalID` value for the type is
now identified by annotating it with `relay.NodeID`. For example:

```python
@strawberry.type
class SomeType:
    my_id: relay.NodeID[int]
```

For django types, if you don't define any `relay.NodeID` annotation it will
automatically use the model's primary key (the `pk` attr) as it. You can change
it by annotation a different field instead. For example:

```python
from django.db import models
from strabwerry import relay
from strawberry_django_plus import gql


class SomeDjangoModel(models.Model):
    code = models.CharField(max_length=10, unique=True)


@gql.django.type(SomeDjangoModel)
class SomeDjangoType(relay.Node):
    ...


@gql.django.type(SomeDjangoModel)
class SomeDjangoTypeWithCodeAsGlobalId(relay.Node):
    code: relay.NodeID[str]
```

In this example, `SomeDjangoType.id` will generate its value from the model's
primary key, but `SomeDjangoTypeWithCodeAsGlobalId` will generate it from `code`.

Note that the field annotated as `relay.NodeID` is private, meaning it will not
be exposed
in the final schema (it is only used to generate the `id`).
