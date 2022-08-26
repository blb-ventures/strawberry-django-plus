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