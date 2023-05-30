from typing import Dict, Tuple, Type, Union, cast

import pytest
from django.test.client import AsyncClient  # type: ignore
from django.test.client import Client

from strawberry_django_plus.optimizer import DjangoOptimizerExtension
from tests.utils import GraphQLTestClient


@pytest.fixture(params=["sync", "async", "sync_no_optimizer", "async_no_optimizer"])
def gql_client(request):
    client, path, with_optimizer = cast(
        Dict[str, Tuple[Union[Type[Client], Type[AsyncClient]], str, bool]],
        {
            "sync": (Client, "/graphql/", True),
            "async": (AsyncClient, "/graphql_async/", True),
            "sync_no_optimizer": (Client, "/graphql/", False),
            "async_no_optimizer": (AsyncClient, "/graphql_async/", False),
        },
    )[request.param]
    token = DjangoOptimizerExtension.enabled.set(with_optimizer)
    with GraphQLTestClient(path, client()) as c:
        yield c
    DjangoOptimizerExtension.enabled.reset(token)


@pytest.fixture(autouse=False)
def use_generate_enums_from_choices(settings):
    settings.STRAWBERRY_DJANGO_GENERATE_ENUMS_FROM_CHOICES = True
