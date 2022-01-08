from django.test.client import AsyncClient, Client  # type:ignore
import pytest

from .utils import GraphQLTestClient


@pytest.fixture(params=["sync", "async", "sync_no_optimizer", "async_no_optimizer"])
def gql_client(request):
    opts = {
        "sync": (Client, True),
        "async": (AsyncClient, True),
        "sync_no_optimizer": (Client, False),
        "async_no_optimizer": (AsyncClient, False),
    }[request.param]
    yield GraphQLTestClient(opts[0](), optimizer_enabled=opts[1])
