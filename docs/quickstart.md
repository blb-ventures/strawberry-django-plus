## Introduction

Since this lib has a long name, it does provide a shortcut called `gql` where all of
strawberry's API and ours can be accessed.

```python
from strawberry_django_plus import gql

# All strawberry's base api can be found directly on gql, like:
gql.type  # same as strawberry.type
gql.field  # same as strawberry.field
...

# The strawberry-django API and our custom implementation can be found on gql.django, like:
gql.django.type
gql.django.field
...

# We also have a custom relay implementation in here:
gql.relay
```

## How To

### Django Choices Enums

Convert choices fields into GraphQL enums by using
[Django Choices Field](https://github.com/bellini666/django-choices-field) extension.

```python
from django_choices_field import TexChoicesField

class Song(models.Model):
    class Genre(models.TextChoices):
        ROCK = "rock", "Rock'n'Roll"
        METAL = "metal", "Metal"
        OTHERS = "others", "Who Cares?"

    genre = TextChoicesField(choices_enum=Genre)
```

In that example, a new enum called `Genre` will be created and be used for queries
and mutations.

If you want to name it differently, decorate the class with `@gql.enum` with your preferred
name so that this lib will not try to register it again.

### Standard django choices enums

Convert standard django choices fields into GraphQL enums by dynamically creating an Enum class based on choices
This feature can be enable by defining `STRAWBERRY_DJANGO_GENERATE_ENUMS_FROM_CHOICES` setting to `True`

```python
class Song(models.Model):
    GENRE_CHOICES = (
        ("rock", "Rock'n'Roll"),
        ("metal", "Metal"),
        ("others", "Who Cares?"),
    )

    genre = models.CharField(choices=GENRE_CHOICES)
```

In that example, a new enum called `MyAppSongGenreEnum` will be dynamically created and be used for queries
and mutations.

Have in mind that this approach don't let you re-use the dynamically created enum elsewhere.

### Permissioned resolvers

Permissioning is done using schema directives by applying them to the fields that requires
permission checking.

For example:

```python
@strawberry.type
class SomeType:
    login_required_field: RetType = strawberry.field(
        # will check if the user is authenticated
        directives=[IsAuthenticated()],
    )
    perm_required_field: OtherType = strawberry.field(
        # will check if the user has `"some_app.some_perm"` permission
        directives=[HasPerm("some_app.some_perm")],
    )
    obj_perm_required_field: OtherType = strawberry.field(
        # will check the permission for the resolved value
        directives=[HasObjPerm("some_app.some_perm")],
    )
```

Available options are:

- `IsAuthenticated`: Checks if the user is authenticated (`user.is_autenticated`)
- `IsStaff`: Checks if the user is a staff member (`user.is_staff`)
- `IsSuperuser`: Checks if the user is a superuser (`user.is_superuser`)
- `HasPerm(perms: str, list[str], any: bool = True)`: Checks if the user has any or all of
  the given permissions (`user.has_perm(perm)`)
- `HasRootPerm(perms: str | list[str], any: bool = True)`: Checks if the user has any or all
  of the given permissions for the root of that field (`user.has_perm(perm, root)`)
- `HasObjPerm(perms: str | list[str], any: bool = True)`: Resolves the retval and then
  checks if the user has any or all of the given permissions for that specific value
  (`user.has_perm(perm, retval)`). Note that if the return value is a list, this directive
  will filter the return value, removing objects that fails the check (check below for more
  information regarding other possibilities).

There are some important notes regarding how the directives handle the return value:

- If the user passes the check, the retval is returned normally
- If the user fails the check:
  - If the return type was `Optional`, it returns `None`
  - If the return type was a `List`, it returns an empty list
  - If the return type was a relay `Connection`, it returns an empty `Connection`
  - If the field is a union with `types.OperationInfo` or `types.OperationMessage`, that type
    is returned with a kind of `PERMISSION`, explaining why the user doesn't have permission
    to resolve that field.
  - Otherwise, it raises a `PermissionError` for that resolver, which will be available at
    the result's `errors` field.

Note that since `strawberry` doesn't support resolvers for schema directives, it is necessary
to use this lib's custom extension that handles the resolution of those and any other custom
defined schema directive inherited from `strawberry_django_plus.directives.SchemaDirectiveResolver`:

```python
import strawberry
from strawberry_django_plus.directives import SchemaDirectiveExtension

schema = strawberry.Schema(
    Query,
    extensions=[
        SchemaDirectiveExtension,
        # other extensions...
    ]
)
```

### Relay Support

!!! warning

    Since version 3.0.0 the relay integration from this lib has been contributed and
    merged directly on strawberry core.
    Check its [docs](https://strawberry.rocks/docs/guides/relay) for more information on
    how to use it and/or the
    [migration guide](https://blb-ventures.github.io/strawberry-django-plus/breaking-changes/)
    to know how to migrate your code from the older implementation.

You can use the [official strawberry relay integration](https://strawberry.rocks/docs/guides/relay)
directly with django types like this:

```python
from strawberry_django_plus import gql


class Fruit(models.Model):
    ...


@gql.django.type(Fruit)
class FruitType(gql.relay.Node):
    ...


@gql.type
class Query:
    some_model_conn: gql.relay.ListConnection[FruitType] = gql.django.connection()

    @gql.django.connection(gql.relay.ListConnection[FruitType])
    def some_model_conn_with_resolver(self, root: SomeModel) -> models.QuerySet[SomeModel]:
        return SomeModel.objects.all()
```

In this example, `some_model_conn` will automatically add a resolver that
returns `SomeModel.objects.all()` for you.

You can also define your own custom resolver like `some_model_conn_with_resolver` and it
will be used instead. You can use this to filter the base `QuerySet` that will be used
for pagination. Also note that you can
[add extra arguments in that resolver](https://strawberry.rocks/docs/guides/relay#custom-connection-arguments),
and they will be included in the final field.

You can also define your own
[custom connection type](https://strawberry.rocks/docs/guides/relay#custom-connection-pagination)
to add extra fields or customize the pagination algorithm. This libs provides a custom connection
type that adds an extra field to retrieve the `totalCount` of the connection, and can be used
like this:

```python
some_model_conn: gql.django.ListConnectionWithTotalCount[FruitType] = gql.django.connection()
```
