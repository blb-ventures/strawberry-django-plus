import pathlib
from typing import Iterable, List, Optional, Union

import pytest
import strawberry
from strawberry.types import Info
from typing_extensions import Self

from demo.models import Favorite
from strawberry_django_plus import relay
from strawberry_django_plus.types import OperationMessage
from tests.faker import FavoriteFactory, IssueFactory, UserFactory
from tests.utils import GraphQLTestClient


@strawberry.type
class Fruit(relay.Node):
    _id: strawberry.Private[int]
    name: str
    color: str

    @classmethod
    def resolve_id(cls, root: Self, *, info: Optional[Info] = None):
        return root._id

    @classmethod
    def resolve_nodes(
        cls,
        *,
        info: Optional[Info] = None,
        node_ids: Optional[Iterable[str]] = None,
    ):
        if node_ids is not None:
            return [fruits[nid] for nid in node_ids]

        return list(fruits.values())

    @classmethod
    def resolve_node(
        cls,
        node_id: str,
        *,
        info: Optional[Info] = None,
        required: bool = False,
    ):
        obj = fruits.get(node_id, None)
        if required and obj is None:
            raise ValueError(f"No fruit by id {node_id}")

        return obj


@strawberry.type
class CustomPaginationConnection(relay.Connection[relay.NodeType]):
    @strawberry.field
    def something(self) -> str:
        return "foobar"

    @classmethod
    def from_nodes(
        cls,
        nodes: Iterable[Fruit],
        *,
        info: Optional[Info] = None,
        total_count: Optional[int] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        first: Optional[int] = None,
        last: Optional[int] = None,
    ):
        edges_mapping = {
            relay.to_base64("fruit_name", n.name): relay.Edge(
                node=n,
                cursor=relay.to_base64("fruit_name", n.name),
            )
            for n in sorted(nodes, key=lambda f: f.name)
        }
        edges = list(edges_mapping.values())
        first_edge = edges[0] if edges else None
        last_edge = edges[-1] if edges else None

        if after is not None:
            after_edge_idx = edges.index(edges_mapping[after])
            edges = [e for e in edges if edges.index(e) > after_edge_idx]

        if before is not None:
            before_edge_idx = edges.index(edges_mapping[before])
            edges = [e for e in edges if edges.index(e) < before_edge_idx]

        if first is not None:
            edges = edges[:first]

        if last is not None:
            edges = edges[-last:]

        return cls(
            edges=edges,
            page_info=relay.PageInfo(
                start_cursor=edges[0].cursor if edges else None,
                end_cursor=edges[-1].cursor if edges else None,
                has_previous_page=first_edge is not None and bool(edges) and edges[0] != first_edge,
                has_next_page=last_edge is not None and bool(edges) and edges[-1] != last_edge,
            ),
        )


fruits = {
    str(f._id): f
    for f in [
        Fruit(_id=1, name="Banana", color="yellow"),
        Fruit(_id=2, name="Apple", color="red"),
        Fruit(_id=3, name="Pineapple", color="yellow"),
        Fruit(_id=4, name="Grape", color="purple"),
        Fruit(_id=5, name="Orange", color="orange"),
    ]
}


@strawberry.type
class Query:
    node: relay.Node = relay.node()
    nodes: List[relay.Node] = relay.node()
    fruits: relay.Connection[Fruit] = relay.connection()
    fruits_or_error: Union[relay.Connection[Fruit], OperationMessage] = relay.connection()
    fruits_custom_pagination: CustomPaginationConnection[Fruit] = relay.connection()

    @relay.connection
    def fruits_custom_resolver(
        self,
        info: Info,
        name_endswith: Optional[str] = None,
    ) -> Iterable[Fruit]:
        for f in fruits.values():
            if name_endswith is None or f.name.endswith(name_endswith):
                yield f

    @relay.connection
    def fruits_custom_resolver_returning_list(
        self,
        info: Info,
        name_endswith: Optional[str] = None,
    ) -> List[Fruit]:
        return [
            f for f in fruits.values() if name_endswith is None or f.name.endswith(name_endswith)
        ]


schema = strawberry.Schema(query=Query)


def test_schema():
    schema_output = str(schema).strip("\n").strip(" ")
    output = pathlib.Path(__file__).parent / "data" / "relay_schema.gql"
    if not output.exists():
        with output.open("w") as f:
            f.write(schema_output + "\n")

    with output.open() as f:
        expected = f.read().strip("\n").strip(" ")

    assert schema_output == expected


def test_query_node():
    result = schema.execute_sync(
        """
        query TestQuery ($id: GlobalID!) {
            node (id: $id) {
                ... on Node {
                    id
                }
                ... on Fruit {
                    name
                    color
                }
            }
        }
        """,
        variable_values={
            "id": relay.to_base64("Fruit", 2),
        },
    )
    assert result.errors is None
    assert result.data == {
        "node": {
            "id": relay.to_base64("Fruit", 2),
            "color": "red",
            "name": "Apple",
        },
    }


def test_query_nodes():
    result = schema.execute_sync(
        """
        query TestQuery ($ids: [GlobalID!]!) {
            nodes (ids: $ids) {
                ... on Node {
                    id
                }
                ... on Fruit {
                    name
                    color
                }
            }
        }
        """,
        variable_values={
            "ids": [relay.to_base64("Fruit", 2), relay.to_base64("Fruit", 4)],
        },
    )
    assert result.errors is None
    assert result.data == {
        "nodes": [
            {
                "id": relay.to_base64("Fruit", 2),
                "name": "Apple",
                "color": "red",
            },
            {
                "id": relay.to_base64("Fruit", 4),
                "name": "Grape",
                "color": "purple",
            },
        ],
    }


fruits_query = """
query TestQuery (
    $first: Int = null
    $last: Int = null
    $before: String = null,
    $after: String = null,
) {{
    {} (
        first: $first
        last: $last
        before: $before
        after: $after
    ) {{
        pageInfo {{
            hasNextPage
            hasPreviousPage
            startCursor
            endCursor
        }}
        edges {{
            cursor
            node {{
                id
                name
                color
            }}
        }}
    }}
}}
"""


@pytest.mark.parametrize(
    "query_attr",
    ["fruits", "fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection(query_attr: str):
    result = schema.execute_sync(
        fruits_query.format(query_attr),
        variable_values={},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjA=",
                    "node": {
                        "id": relay.to_base64("Fruit", 1),
                        "color": "yellow",
                        "name": "Banana",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjQ=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": False,
                "startCursor": relay.to_base64("arrayconnection", "0"),
                "endCursor": relay.to_base64("arrayconnection", "4"),
            },
        },
    }


def test_query_connection_union():
    result = schema.execute_sync(
        """
        query TestQuery {
            fruitsOrError {
                ... on FruitConnection {
                    edges {
                        node {
                            id
                        }
                    }
                }
            }
        }
        """,
    )
    assert result.errors is None
    assert result.data == {
        "fruitsOrError": {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjA=",
                    "node": {
                        "id": relay.to_base64("Fruit", 1),
                        "color": "yellow",
                        "name": "Banana",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjQ=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": False,
                "startCursor": relay.to_base64("arrayconnection", "0"),
                "endCursor": relay.to_base64("arrayconnection", "4"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruits", "fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_filtering_first(query_attr: str):
    result = schema.execute_sync(
        fruits_query.format(query_attr),
        variable_values={"first": 2},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjA=",
                    "node": {
                        "id": relay.to_base64("Fruit", 1),
                        "color": "yellow",
                        "name": "Banana",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": False,
                "startCursor": relay.to_base64("arrayconnection", "0"),
                "endCursor": relay.to_base64("arrayconnection", "1"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruits", "fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_filtering_first_with_after(query_attr: str):
    result = schema.execute_sync(
        fruits_query.format(query_attr),
        variable_values={"first": 2, "after": relay.to_base64("arrayconnection", "1")},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "2"),
                "endCursor": relay.to_base64("arrayconnection", "3"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruits", "fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_filtering_last(query_attr: str):
    result = schema.execute_sync(
        fruits_query.format(query_attr),
        variable_values={"last": 2},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjQ=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "3"),
                "endCursor": relay.to_base64("arrayconnection", "4"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruits", "fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_filtering_last_with_before(query_attr: str):
    result = schema.execute_sync(
        fruits_query.format(query_attr),
        variable_values={"last": 2, "before": relay.to_base64("arrayconnection", "4")},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "2"),
                "endCursor": relay.to_base64("arrayconnection", "3"),
            },
        },
    }


fruits_custom_query = """
query TestQuery (
    $first: Int = null
    $last: Int = null
    $before: String = null,
    $after: String = null,
) {
    fruitsCustomPagination (
        first: $first
        last: $last
        before: $before
        after: $after
    ) {
        something
        pageInfo {
            hasNextPage
            hasPreviousPage
            startCursor
            endCursor
        }
        edges {
            cursor
            node {
                id
                name
                color
            }
        }
    }
}
"""


def test_query_custom_connection():
    result = schema.execute_sync(
        fruits_custom_query,
        variable_values={},
    )
    assert result.errors is None
    assert result.data == {
        "fruitsCustomPagination": {
            "something": "foobar",
            "edges": [
                {
                    "cursor": "ZnJ1aXRfbmFtZTpBcHBsZQ==",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpCYW5hbmE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 1),
                        "color": "yellow",
                        "name": "Banana",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpHcmFwZQ==",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpPcmFuZ2U=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpQaW5lYXBwbGU=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
            ],
            "pageInfo": {
                "startCursor": relay.to_base64("fruit_name", "Apple"),
                "endCursor": relay.to_base64("fruit_name", "Pineapple"),
                "hasNextPage": False,
                "hasPreviousPage": False,
            },
        },
    }


def test_query_custom_connection_filtering_first():
    result = schema.execute_sync(
        fruits_custom_query,
        variable_values={"first": 2},
    )
    assert result.errors is None
    assert result.data == {
        "fruitsCustomPagination": {
            "something": "foobar",
            "edges": [
                {
                    "cursor": "ZnJ1aXRfbmFtZTpBcHBsZQ==",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpCYW5hbmE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 1),
                        "color": "yellow",
                        "name": "Banana",
                    },
                },
            ],
            "pageInfo": {
                "startCursor": relay.to_base64("fruit_name", "Apple"),
                "endCursor": relay.to_base64("fruit_name", "Banana"),
                "hasNextPage": True,
                "hasPreviousPage": False,
            },
        },
    }


def test_query_custom_connection_filtering_first_with_after():
    result = schema.execute_sync(
        fruits_custom_query,
        variable_values={"first": 2, "after": relay.to_base64("fruit_name", "Banana")},
    )
    assert result.errors is None
    assert result.data == {
        "fruitsCustomPagination": {
            "something": "foobar",
            "edges": [
                {
                    "cursor": "ZnJ1aXRfbmFtZTpHcmFwZQ==",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpPcmFuZ2U=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("fruit_name", "Grape"),
                "endCursor": relay.to_base64("fruit_name", "Orange"),
            },
        },
    }


def test_query_custom_connection_filtering_last():
    result = schema.execute_sync(
        fruits_custom_query,
        variable_values={"last": 2},
    )
    assert result.errors is None
    assert result.data == {
        "fruitsCustomPagination": {
            "something": "foobar",
            "edges": [
                {
                    "cursor": "ZnJ1aXRfbmFtZTpPcmFuZ2U=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpQaW5lYXBwbGU=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("fruit_name", "Orange"),
                "endCursor": relay.to_base64("fruit_name", "Pineapple"),
            },
        },
    }


def test_query_custom_connection_filtering_first_with_before():
    result = schema.execute_sync(
        fruits_custom_query,
        variable_values={"last": 2, "before": relay.to_base64("fruit_name", "Pineapple")},
    )
    assert result.errors is None
    assert result.data == {
        "fruitsCustomPagination": {
            "something": "foobar",
            "edges": [
                {
                    "cursor": "ZnJ1aXRfbmFtZTpHcmFwZQ==",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "ZnJ1aXRfbmFtZTpPcmFuZ2U=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("fruit_name", "Grape"),
                "endCursor": relay.to_base64("fruit_name", "Orange"),
            },
        },
    }


fruits_query_custom_resolver = """
query TestQuery (
    $first: Int = null
    $last: Int = null
    $before: String = null,
    $after: String = null,
    $nameEndswith: String = null
) {{
    {} (
        first: $first
        last: $last
        before: $before
        after: $after
        nameEndswith: $nameEndswith
    ) {{
        pageInfo {{
            hasNextPage
            hasPreviousPage
            startCursor
            endCursor
        }}
        edges {{
            cursor
            node {{
                id
                name
                color
            }}
        }}
    }}
}}
"""


@pytest.mark.parametrize(
    "query_attr",
    ["fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_custom_resolver(query_attr: str):
    result = schema.execute_sync(
        fruits_query_custom_resolver.format(query_attr),
        variable_values={"nameEndswith": "e"},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjA=",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": False,
                "startCursor": relay.to_base64("arrayconnection", "0"),
                "endCursor": relay.to_base64("arrayconnection", "3"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_custom_resolver_filtering_first(query_attr: str):
    result = schema.execute_sync(
        fruits_query_custom_resolver.format(query_attr),
        variable_values={"first": 2, "nameEndswith": "e"},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjA=",
                    "node": {
                        "id": relay.to_base64("Fruit", 2),
                        "color": "red",
                        "name": "Apple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": False,
                "startCursor": relay.to_base64("arrayconnection", "0"),
                "endCursor": relay.to_base64("arrayconnection", "1"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_custom_resolver_filtering_first_with_after(query_attr: str):
    result = schema.execute_sync(
        fruits_query_custom_resolver.format(query_attr),
        variable_values={
            "first": 2,
            "after": relay.to_base64("arrayconnection", "1"),
            "nameEndswith": "e",
        },
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "2"),
                "endCursor": relay.to_base64("arrayconnection", "3"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_custom_resolver_filtering_last(query_attr: str):
    result = schema.execute_sync(
        fruits_query_custom_resolver.format(query_attr),
        variable_values={"last": 2, "nameEndswith": "e"},
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjM=",
                    "node": {
                        "id": relay.to_base64("Fruit", 5),
                        "color": "orange",
                        "name": "Orange",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "2"),
                "endCursor": relay.to_base64("arrayconnection", "3"),
            },
        },
    }


@pytest.mark.parametrize(
    "query_attr",
    ["fruitsCustomResolver", "fruitsCustomResolverReturningList"],
)
def test_query_connection_custom_resolver_filtering_last_with_before(query_attr: str):
    result = schema.execute_sync(
        fruits_query_custom_resolver.format(query_attr),
        variable_values={
            "last": 2,
            "before": relay.to_base64("arrayconnection", "3"),
            "nameEndswith": "e",
        },
    )
    assert result.errors is None
    assert result.data == {
        query_attr: {
            "edges": [
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjE=",
                    "node": {
                        "id": relay.to_base64("Fruit", 3),
                        "color": "yellow",
                        "name": "Pineapple",
                    },
                },
                {
                    "cursor": "YXJyYXljb25uZWN0aW9uOjI=",
                    "node": {
                        "id": relay.to_base64("Fruit", 4),
                        "color": "purple",
                        "name": "Grape",
                    },
                },
            ],
            "pageInfo": {
                "hasNextPage": True,
                "hasPreviousPage": True,
                "startCursor": relay.to_base64("arrayconnection", "1"),
                "endCursor": relay.to_base64("arrayconnection", "2"),
            },
        },
    }


# Test Relay Connection with ForeignKey and ManyToMany relationships
@pytest.mark.django_db(transaction=True)
def test_query_connection_get_queryset(db, gql_client: GraphQLTestClient):
    query = """
    query Issue ($id: GlobalID!){
        issue(id: $id) {
            favoriteSet {
                edges { node {
                    name
                    user { id }
                } }
            }
        }
    }
    """

    user_a = UserFactory.create()
    user_b = UserFactory.create()

    issue = IssueFactory.create()

    favorite_a1 = FavoriteFactory.create(user=user_a, issue=issue)
    favorite_a2 = FavoriteFactory.create(user=user_a, issue=issue)
    FavoriteFactory.create(user=user_b, issue=issue)

    with gql_client.login(user_a):
        res = gql_client.query(query, {"id": relay.to_base64("IssueType", issue.pk)})
        assert res.data
        assert isinstance(res.data["issue"], dict)
        nodes = [i["node"] for i in res.data["issue"]["favoriteSet"]["edges"]]
        assert nodes == [
            {
                "name": favorite_a1.name,
                "user": {"id": relay.to_base64("UserType", user_a.username)},
            },
            {
                "name": favorite_a2.name,
                "user": {"id": relay.to_base64("UserType", user_a.username)},
            },
        ]

    assert Favorite.objects.all().count() == 3
