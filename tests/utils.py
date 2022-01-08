import contextlib
from typing import Dict, Mapping, Optional

from django.test.client import Client
from strawberry.test import BaseGraphQLTestClient
from strawberry.test.client import Response

from strawberry_django_plus.optimizer import DjangoOptimizerExtension


class GraphQLTestClient(BaseGraphQLTestClient):
    def __init__(self, client: Client, *, is_async: bool = False, optimizer_enabled: bool = True):
        super().__init__(client=client)

        self.is_async = is_async
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
        variables: Optional[Dict[str, Mapping]] = None,
        headers: Optional[Dict[str, object]] = None,
        asserts_errors: Optional[bool] = True,
        files: Optional[Dict[str, object]] = None,
    ) -> Response:
        body = self._build_body(query, variables, files)
        resp = self.request(body, headers, files)
        data = self._decode(resp, type="multipart" if files else "json")

        response = Response(
            errors=data.get("errors"),
            data=data.get("data"),
            extensions=data.get("extensions"),
        )
        if asserts_errors:
            assert response.errors is None

        return response

    async def aquery(
        self,
        query: str,
        variables: Optional[Dict[str, Mapping]] = None,
        headers: Optional[Dict[str, object]] = None,
        asserts_errors: Optional[bool] = True,
        files: Optional[Dict[str, object]] = None,
    ) -> Response:
        body = self._build_body(query, variables, files)

        resp = await self.request(body, headers, files)
        data = self._decode(resp, type="multipart" if files else "json")

        response = Response(
            errors=data.get("errors"),
            data=data.get("data"),
            extensions=data.get("extensions"),
        )
        if asserts_errors:
            assert response.errors is None

        return response
