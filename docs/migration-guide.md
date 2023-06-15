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
