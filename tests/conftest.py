from django.test.client import AsyncClient, Client  # type:ignore
import pytest

from .utils import GraphQLTestClientAsync, GraphQLTestClientSync


@pytest.fixture(params=["sync", "async"])
def gql_client(request):
    client = {
        "sync": GraphQLTestClientSync,
        "async": GraphQLTestClientAsync,
    }[request.param]
    yield client(Client())


@pytest.fixture
def gql_client_async(request):
    yield GraphQLTestClientAsync(AsyncClient())
