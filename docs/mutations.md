!!! warning

    All the extra features provided by this lib were contributed and merged directly
    into the official
    [strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
    lib. Since then this lib is deprecated and the official integration should be used instead.

    If you were using this lib before, check out the
    [migration guide](migration-guide#migrating-to-strawberry-django) for more information
    on how to migrate your code.

This library provides 3 CUD mutations for streamlining common create/update/delete operations and reducing boilerplate code.
There is also a facility for creating custom mutations with automatic `ValidationError` support.

## CUD mutations

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

@gql.django.input(SomeModel)
class SomeModelInput:
    name: gql.auto


@gql.django.partial(SomeModel)
class SomeModelInputPartial(gql.NodeInput):
    name: gql.auto

@gql.type
class Mutation:
    create_model: SomeModelType = gql.django.create_mutation(SomeModelInput)
    update_model: SomeModelType = gql.django.update_mutation(SomeModelInputPartial)
    delete_model: SomeModelType = gql.django.delete_mutation(gql.NodeInput)
```

## Extending build in CUD mutations

There might be the need to perform some pre or post validation before running the built-in mutations. A common use case is for example setting a model field based on the current request context.

As the syntax is not completely straightforward at the moment an example is listed as follows.

```python

from django.conf import settings
from django.db import models

# Django Model
class Asset(models.Model):
    name = models.TextField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)

```

The strawberry code uses a relay implementation but the concept should also work in a non-relay context.

```python
from strawberry_django_plus.mutations import resolvers

@gql.django.type(Asset)
class AssetNode(gql.relay.Node):
    name: gql.auto
    owner: UserNode

@gql.django.partial(Asset)
class UpdateAssetInput(gql.NodeInput):
    name: gql.auto

@gql.type
class ModelMutation:

    @gql.mutation
    def update_asset(self, info: Info, input: UpdateAssetInput) -> ModelNode:
        data = vars(input)
        node_id: gql.relay.GlobalID = data.pop('id')
        asset: Asset = node_id.resolve_node(info, ensure_type=Asset)

        if asset.owner != info.context.request.user:
            raise PermissionError("You can only modify objects you own.")

        return resolvers.update(info, asset, resolvers.parse_input(info, data))
```

Important to note is that the input has to be converted via `vars` call. The concept is taken from the built-in mutation. You then need to call the `resolvers.update` function to mutate the model instance. The main benefit is that you keep all the validation and update logic from the built-in mutation.

## Custom model mutations

It is possible to create custom model mutations with `gql.django.input_mutation`, which will
automatically convert the arguments to an input type and mark the return value as a union
between the type annotation and `types.OperationInfo`. The latter will be returned if
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
