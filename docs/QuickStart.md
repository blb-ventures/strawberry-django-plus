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

### Django mutations

This lib provides 3 CRUD mutations for create/update/delete operations, and also a facility
for creating custom mutations with automatic `ValidationError` support.

## CRUD mutations

- `gql.django.create_mutation`: Will create the model using the data from the given input,
  returning a `types.OperationInfo` if it fails with all raised `ValidationError` data.
- `gql.django.update_mutation`: Will update the model using the data from the given input,
  returning a `types.OperationInfo` if it fails with all raised `ValidationError` data.
- `gql.django.delete_mutation`: Will delete the model using the id from the given input,
  returning a `types.OperationInfo` if it fails with all raised `ValidationError` data.

A simple complete example would be:

```python
from strawberry_django_plus import gql

@gql.django.type(SomeModel)
class SomeModelType(gql.Node):
    name: gql.auto

@gql.django.input(SomeModelType)
class SomeModelInput:
    name: gql.auto


@gql.django.partial(SomeModelType)
class SomeModelInputPartial(gql.NodeInput):
    name: gql.auto

@gql.type
class Mutation:
    create_model: SomeModelType = gql.django.create_mutation(SomeModelInput)
    update_model: SomeModelType = gql.django.update_mutation(SomeModelInputPartial)
    delete_model: SomeModelType = gql.django.delete_mutation(gql.NodeInput)
```

## Custom model mutations

It is possible to create custom model mutations with `gql.django.input_mutation`, which will
automatically convert the arguments to a input type and mark the return value as a union
between the type annotation and `types.OperationInfo`. The later will be returned if
the resolver raises `ValidationError`.

For example:

```python
from django.core.exceptions import ValidationError
from strawberry_django_plus import gql

@gql.type
class Mutation:
    @gql.django.input_mutation
    def set_model_name(self, info, id: GlobalID, name: str) -> ModelType:
        obj = id.resolve_node(info)
        if obj.some_field == "some_value":
            raise ValidationError("Cannot update obj with some_value")

        obj.name = name
        obj.save()
        return obj
```

### Relay Support

We have a custom [relay spec](https://relay.dev/docs/guides/graphql-server-specification/)
implementation. It is not tied to Django at all to allow its usage with other types.

It provides types and fields for node and connection querying. For example:

```python
# schema.py
from strawberry_django_plus import gql
from strawberry_django_plus.gql import relay

@gql.type
class Fruit(relay.Node):
    name: str

    def resolve_node(cls, node_id, info, required=False):
        ...

    def resolve_nodes(cls, node_id, info, node_ids=False):
        ...


@gql.type
class Query:
    fruit: Optional[Fruit] = relay.node()
    fruits_connection: relay.Connection[Fruit] = relay.connection()

    @relay.connection
    def fruits_connection_filtered(self, name_startswith: str) -> Iterable[Fruit]:
        # Note that this resolver is special. It should not resolve the connection, but
        # the iterable of nodes itself. Thus, any arguments defined here will be appended
        # to the query, and the pagination of the iterable returned here will be
        # automatically handled.
        ...
```

Will generate a schema like this:

```gql
interface Node {
  id: GlobalID!
}

type Fruit implements Node {
  id: GlobalID!
  name: String!
}

type FruitEdge implements Node {
  cursor: String!
  node: Fruit
}

type FruitConnection {
  edges: [ShipEdge!]!
  pageInfo: PageInfo!
}

type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}

type Query {
  fruit(id: GlobalID!): Fruit
  fruits_connection(
    before: String
    after: String
    first: Int
    last: Int
  ): FruitConnection
  fruits_connection_filtered(
    before: String
    after: String
    first: Int
    last: Int
    nameStartswith: String!
  ): FruitConnection
}
```

It is expected that types implementing the `Node` interface define some methods, like
`resolve_nodes` and `resolve_node`. By default the `id` field is used for the node id.
This is customizable with the `id_attr` attribute. Take a look at
[the documentation](/strawberry_django_plus/relay.py) for more information.

Also note that Django fields created with `@gql.django.type` automatically implements
all of the required methods when the type inherits from `Node`.
By default the `pk` field is used for the node id (overwrites the default `id` field) but
can also be customized with the `id_attr`attribute.

This module also exposes a mutation that converts all of its arguments to a single input.
For example:

```python
@gql.type
class Mutation:
    @relay.input_mutation
    def create_fruit(name: str) -> Fruit:
        ....
```

Will generate those types:

```gql
input CreateFruitInput {
  name: String!
}

type Mutation {
  createFruit(input: CreateFruitInput!): Fruit
}
```
