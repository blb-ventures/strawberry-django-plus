import pytest

from tests.utils import GraphQLTestClient, assert_num_queries


@pytest.mark.django_db(transaction=True)
def test_input_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateProject ($input: CreateProjectInput!) {
        createProject (input: $input) {
          ... on ProjectType {
            name
            cost
          }
        }
      }
    """
    with assert_num_queries(1):
        res = gql_client.query(query, {"input": {"name": "Some Project", "cost": "12.50"}})
        assert res.data == {"createProject": {"name": "Some Project", "cost": "12.50"}}


@pytest.mark.django_db(transaction=True)
def test_input_mutation_with_errors(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateProject ($input: CreateProjectInput!) {
        createProject (input: $input) {
          ... on ProjectType {
            name
            cost
          }
          ... on OperationInfo {
            messages {
              field
              message
              kind
            }
          }
        }
      }
    """
    with assert_num_queries(0):
        res = gql_client.query(query, {"input": {"name": "Some Project", "cost": "501.50"}})
        assert res.data == {
            "createProject": {
                "messages": [
                    {
                        "field": "cost",
                        "kind": "VALIDATION",
                        "message": "Cost cannot be higher than 500",
                    }
                ]
            }
        }
