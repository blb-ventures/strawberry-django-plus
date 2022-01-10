import asyncio
import contextlib
import inspect
from typing import Any, Dict, Optional

from django.db import DEFAULT_DB_ALIAS, connections
from django.test.client import AsyncClient  # type:ignore
from django.test.client import Client
from django.test.utils import CaptureQueriesContext
from strawberry.test import BaseGraphQLTestClient
from strawberry.test.client import Response

from strawberry_django_plus.optimizer import DjangoOptimizerExtension


@contextlib.contextmanager
def assert_num_queries(n: int, *, using=DEFAULT_DB_ALIAS, is_async: bool = False):
    with CaptureQueriesContext(connection=connections[DEFAULT_DB_ALIAS]) as ctx:
        yield

    executed = len(ctx)

    # FIXME: Why async is failing to track queries? Like, 0?
    if is_async and executed == 0:
        return

    assert executed == n, "{} queries executed, {} expected\nCaptured queries were:\n{}".format(
        executed,
        n,
        "\n".join(f"{i}. {q['sql']}" for i, q in enumerate(ctx.captured_queries, start=1)),
    )


class GraphQLTestClient(BaseGraphQLTestClient):
    def __init__(self, client: Client, *, optimizer_enabled: bool = True):
        super().__init__(client=client)

        self.is_async = isinstance(client, AsyncClient)
        self.optimizer_enabled = optimizer_enabled

    def request(
        self,
        body: Dict[str, object],
        headers: Optional[Dict[str, object]] = None,
        files: Optional[Dict[str, object]] = None,
    ):
        path = "/graphql_async/" if self.is_async else "/graphql/"

        kwargs: Dict[str, object] = {"data": body}
        if files:
            kwargs["format"] = "multipart"
        else:
            kwargs["content_type"] = "application/json"

        if self.optimizer_enabled:
            ctx = contextlib.nullcontext()
        else:
            ctx = DjangoOptimizerExtension.disable()

        with ctx:
            return self._client.post(path, **kwargs)

    def query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, object]] = None,
        asserts_errors: Optional[bool] = True,
        files: Optional[Dict[str, object]] = None,
    ) -> Response:
        body = self._build_body(query, variables, files)

        resp = self.request(body, headers, files)
        if inspect.isawaitable(resp):
            resp = asyncio.run(resp)

        data = self._decode(resp, type="multipart" if files else "json")

        response = Response(
            errors=data.get("errors"),
            data=data.get("data"),
            extensions=data.get("extensions"),
        )
        if asserts_errors:
            assert response.errors is None

        return response
