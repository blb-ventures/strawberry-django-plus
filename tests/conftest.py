from django.test.client import AsyncClient, Client  # type:ignore
import pytest

from .utils import GraphQLTestClient


@pytest.fixture(params=["sync", "async", "sync_no_optimizer", "async_no_optimizer"])
def gql_client(request):
    opts = {
        "sync": (False, True),
        "async": (True, True),
        "sync_no_optimizer": (False, False),
        "async_no_optimizer": (True, False),
    }[request.param]
    yield GraphQLTestClient(Client(), is_async=opts[0], optimizer_enabled=opts[1])


@pytest.fixture(params=["async", "async_no_optimizer"])
def gql_client_async(request):
    optimizer_enabled = request.param == "async_no_optimizer"
    yield GraphQLTestClient(AsyncClient(), is_async=True, optimizer_enabled=optimizer_enabled)
