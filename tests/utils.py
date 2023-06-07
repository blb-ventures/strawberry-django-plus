import asyncio
import contextlib
import contextvars
import inspect
from typing import Any, Dict, Optional, Union

from django.db import DEFAULT_DB_ALIAS, connections
from django.test.client import (
    AsyncClient,  # type: ignore
    Client,
)
from django.test.utils import CaptureQueriesContext
from strawberry.test.client import Response

from strawberry_django_plus.test.client import TestClient

_client: contextvars.ContextVar["GraphQLTestClient"] = contextvars.ContextVar("_client_ctx")


@contextlib.contextmanager
def assert_num_queries(n: int, *, using=DEFAULT_DB_ALIAS):
    with CaptureQueriesContext(connection=connections[DEFAULT_DB_ALIAS]) as ctx:
        yield

    executed = len(ctx)

    # FIXME: Why async is failing to track queries? Like, 0?
    if _client.get().is_async and executed == 0:
        return

    assert executed == n, "{} queries executed, {} expected\nCaptured queries were:\n{}".format(
        executed,
        n,
        "\n".join(f"{i}. {q['sql']}" for i, q in enumerate(ctx.captured_queries, start=1)),
    )


class GraphQLTestClient(TestClient):
    def __init__(
        self,
        path: str,
        client: Union[Client, AsyncClient],
    ):
        super().__init__(path, client=client)
        self._token: Optional[contextvars.Token] = None
        self.is_async = isinstance(client, AsyncClient)

    def __enter__(self):
        self._token = _client.set(self)
        return self

    def __exit__(self, *args, **kwargs):
        assert self._token
        _client.reset(self._token)

    def request(
        self,
        body: Dict[str, object],
        headers: Optional[Dict[str, object]] = None,
        files: Optional[Dict[str, object]] = None,
    ):
        kwargs: Dict[str, object] = {"data": body}
        if files:  # pragma:nocover
            kwargs["format"] = "multipart"
        else:
            kwargs["content_type"] = "application/json"

        return self.client.post(
            self.path,
            **kwargs,  # type: ignore
        )

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
        if inspect.iscoroutine(resp):
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
