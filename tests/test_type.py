import textwrap
from functools import cached_property

import strawberry
from django.db import models
from strawberry.printer import print_schema

from strawberry_django_plus import gql


class MyChoice(models.TextChoices):
    ONE = "One", "The first number"
    TWO = "Two", "The second number"
    THREE = "Three", "The third number"


class MyModel(models.Model):
    class Meta:
        app_label = "tests"

    id = models.BigAutoField(  # noqa: A003
        primary_key=True,
    )

    choice = models.CharField(choices=MyChoice.choices, default=MyChoice.ONE)

    @property
    def some_property(self) -> str:
        """Some property doc."""
        return "some_value"

    @cached_property
    def some_cached_property(self) -> str:
        """Some cached property doc."""
        return "some_value"

    @gql.model_property
    def some_model_property(self) -> str:
        """Some model property doc."""
        return "some_value"


def test_choice_field(use_generate_enums_from_choices):
    @gql.django.type(MyModel)
    class MyType:
        choice: gql.auto

    @strawberry.type
    class Query:
        my_type: MyType

    expected_representation = '''
    type MyType {
      """Some property doc."""
      choice: TestsMyModelChoiceEnum!
    }

    type Query {
      someType: MyType!
    }
    '''

    schema = strawberry.Schema(Query)
    assert print_schema(schema) == textwrap.dedent(expected_representation).strip()


def test_property():
    @gql.django.type(MyModel)
    class MyType:
        some_property: gql.auto

    @strawberry.type
    class Query:
        some_type: MyType

    expected_representation = '''
    type MyType {
      """Some property doc."""
      someProperty: String!
    }

    type Query {
      someType: MyType!
    }
    '''

    schema = strawberry.Schema(Query)
    assert print_schema(schema) == textwrap.dedent(expected_representation).strip()


def test_cached_property():
    @gql.django.type(MyModel)
    class MyType:
        some_cached_property: gql.auto

    @strawberry.type
    class Query:
        some_type: MyType

    expected_representation = '''
    type MyType {
      """Some cached property doc."""
      someCachedProperty: String!
    }

    type Query {
      someType: MyType!
    }
    '''

    schema = strawberry.Schema(Query)
    assert print_schema(schema) == textwrap.dedent(expected_representation).strip()


def test_model_property():
    @gql.django.type(MyModel)
    class MyType:
        some_model_property: gql.auto

    @strawberry.type
    class Query:
        some_type: MyType

    expected_representation = '''
    type MyType {
      """Some model property doc."""
      someModelProperty: String!
    }

    type Query {
      someType: MyType!
    }
    '''

    schema = strawberry.Schema(Query)
    assert print_schema(schema) == textwrap.dedent(expected_representation).strip()
