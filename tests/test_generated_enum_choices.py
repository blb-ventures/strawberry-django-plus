import textwrap

import pytest
import strawberry
from django.db import models
from strawberry.printer import print_schema

from strawberry_django_plus import gql


class FavoriteNumber(models.TextChoices):
    ONE = "ONE", "The first number"
    TWO = "TWO", "The second number"
    THREE = "THREE", "The third number"


class UserFavorites(models.Model):
    class Meta:
        app_label = "tests"

    id = models.BigAutoField(  # noqa: A003
        primary_key=True,
    )

    favorite_number = models.CharField(choices=FavoriteNumber.choices, default=FavoriteNumber.ONE)


def make_schema():
    """Shared schema for testing type and query of generated enums."""

    @gql.django.type(UserFavorites)
    class UserFavoritesType:
        favorite_number: gql.auto

    @strawberry.type
    class Query:
        user_favorites: UserFavoritesType

    return strawberry.Schema(Query)


@pytest.mark.usefixtures("_use_generate_enums_from_choices")
def test_schema():
    schema = make_schema()

    expected_representation = '''
    type Query {
      userFavorites: UserFavoritesType!
    }

    """user favorites | favorite number"""
    enum TestsUserFavoritesFavoriteNumberEnum {
      """The first number"""
      ONE

      """The second number"""
      TWO

      """The third number"""
      THREE
    }

    type UserFavoritesType {
      favoriteNumber: TestsUserFavoritesFavoriteNumberEnum!
    }
    '''

    assert print_schema(schema) == textwrap.dedent(expected_representation).strip()


@pytest.mark.usefixtures("_use_generate_enums_from_choices")
def test_enum_data_query():
    schema = make_schema()

    res = schema.execute_sync(
        """
      query TestsUserFavoritesFavoriteNumberEnumQuery {
        enumData: __type(name: "TestsUserFavoritesFavoriteNumberEnum") {
          name
          enumValues {
            name
            description
          }
        }
      }
      """,
    )

    assert res.data == {
        "enumData": {
            "name": "TestsUserFavoritesFavoriteNumberEnum",
            "enumValues": [
                {"description": "The first number", "name": "ONE"},
                {"description": "The second number", "name": "TWO"},
                {"description": "The third number", "name": "THREE"},
            ],
        },
    }
