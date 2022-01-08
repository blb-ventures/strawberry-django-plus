from typing import Dict, Mapping, Optional

from strawberry.test import BaseGraphQLTestClient
from strawberry.test.client import Response


class GraphQLTestClient(BaseGraphQLTestClient):
    is_async: bool
    path: str

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

    def request(
        self,
        body: Dict[str, object],
        headers: Optional[Dict[str, object]] = None,
        files: Optional[Dict[str, object]] = None,
    ):
        assert self.path

        if files:
            return self._client.post(
                self.path,
                data=body,
                format="multipart",
                # headers=headers,
            )
        else:
            return self._client.post(
                self.path,
                data=body,
                content_type="application/json",
                # headers=headers,
            )


class GraphQLTestClientSync(GraphQLTestClient):
    path = "/graphql/"


class GraphQLTestClientAsync(GraphQLTestClient):
    path = "/graphql_async/"
