import pytest

from demo.models import Issue
from strawberry_django_plus.relay import from_base64, to_base64
from tests.faker import IssueFactory, MilestoneFactory, TagFactory
from tests.utils import GraphQLTestClient, assert_num_queries


@pytest.mark.django_db(transaction=True)
def test_input_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateProject ($input: CreateProjectInput!) {
        createProject (input: $input) {
          ... on ProjectType {
            name
            cost
            dueDate
          }
        }
      }
    """
    with assert_num_queries(1):
        res = gql_client.query(
            query,
            {
                "input": {
                    "name": "Some Project",
                    "cost": "12.50",
                    "dueDate": "2030-01-01",
                }
            },
        )
        assert res.data == {
            "createProject": {
                "name": "Some Project",
                # This is set, but will not be displayed because the user is not authenticated
                "cost": None,
                "dueDate": "2030-01-01T00:00:00",
            }
        }


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


@pytest.mark.django_db(transaction=True)
def test_input_create_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateIssue ($input: IssueInput!) {
      createIssue (input: $input) {
        __typename
        ... on OperationInfo {
          messages {
            kind
            field
            message
          }
        }
        ... on IssueType {
          id
          name
          milestone {
            id
            name
          }
          priority
          kind
          tags {
            id
            name
          }
        }
      }
    }
    """
    milestone = MilestoneFactory.create()
    tags = TagFactory.create_batch(4)
    res = gql_client.query(
        query,
        {
            "input": {
                "name": "Some Issue",
                "milestone": {"id": to_base64("MilestoneType", milestone.pk)},
                "priority": 5,
                "kind": Issue.Kind.FEATURE.value,
                "tags": [{"id": to_base64("TagType", t.pk)} for t in tags],
            }
        },
    )
    assert res.data and isinstance(res.data["createIssue"], dict)

    typename, pk = from_base64(res.data["createIssue"].pop("id"))
    assert typename == "IssueType"
    assert {frozenset(t.items()) for t in res.data["createIssue"].pop("tags")} == {
        frozenset({"id": to_base64("TagType", t.pk), "name": t.name}.items()) for t in tags
    }

    assert res.data == {
        "createIssue": {
            "__typename": "IssueType",
            "name": "Some Issue",
            "milestone": {
                "id": to_base64("MilestoneType", milestone.pk),
                "name": milestone.name,
            },
            "priority": 5,
            "kind": "f",
        }
    }
    issue = Issue.objects.get(pk=pk)
    assert issue.name == "Some Issue"
    assert issue.priority == 5
    assert issue.kind == Issue.Kind.FEATURE
    assert issue.milestone == milestone
    assert set(issue.tags.all()) == set(tags)


@pytest.mark.django_db(transaction=True)
def test_input_update_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateIssue ($input: IssueInputPartial!) {
      updateIssue (input: $input) {
        __typename
        ... on OperationInfo {
          messages {
            kind
            field
            message
          }
        }
        ... on IssueType {
          id
          name
          milestone {
            id
            name
          }
          priority
          kind
          tags {
            id
            name
          }
        }
      }
    }
    """
    issue = IssueFactory.create(
        name="Old name",
        milestone=MilestoneFactory.create(),
        priority=0,
        kind=Issue.Kind.BUG,
    )
    tags = TagFactory.create_batch(4)
    issue.tags.set(tags)

    milestone = MilestoneFactory.create()
    add_tags = TagFactory.create_batch(2)
    remove_tags = tags[:2]

    res = gql_client.query(
        query,
        {
            "input": {
                "id": to_base64("IssueType", issue.pk),
                "name": "New name",
                "milestone": {"id": to_base64("MilestoneType", milestone.pk)},
                "priority": 5,
                "kind": Issue.Kind.FEATURE.value,
                "tags": {
                    "add": [{"id": to_base64("TagType", t.pk)} for t in add_tags],
                    "remove": [{"id": to_base64("TagType", t.pk)} for t in remove_tags],
                },
            }
        },
    )
    assert res.data and isinstance(res.data["updateIssue"], dict)

    expected_tags = tags + add_tags
    for removed in remove_tags:
        expected_tags.remove(removed)

    print("xxx", res.data)
    assert {frozenset(t.items()) for t in res.data["updateIssue"].pop("tags")} == {
        frozenset({"id": to_base64("TagType", t.pk), "name": t.name}.items()) for t in expected_tags
    }

    assert res.data == {
        "updateIssue": {
            "__typename": "IssueType",
            "id": to_base64("IssueType", issue.pk),
            "name": "New name",
            "milestone": {
                "id": to_base64("MilestoneType", milestone.pk),
                "name": milestone.name,
            },
            "priority": 5,
            "kind": "f",
        }
    }

    issue.refresh_from_db()
    assert issue.name == "New name"
    assert issue.priority == 5
    assert issue.kind == Issue.Kind.FEATURE
    assert issue.milestone == milestone
    assert set(issue.tags.all()) == set(expected_tags)


@pytest.mark.django_db(transaction=True)
def test_input_delete_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation DeleteIssue ($input: NodeInput!) {
      deleteIssue (input: $input) {
        __typename
        ... on OperationInfo {
          messages {
            kind
            field
            message
          }
        }
        ... on IssueType {
          id
          name
          milestone {
            id
            name
          }
          priority
          kind
        }
      }
    }
    """
    issue = IssueFactory.create()
    assert issue.milestone
    assert issue.kind

    res = gql_client.query(
        query,
        {
            "input": {
                "id": to_base64("IssueType", issue.pk),
            }
        },
    )
    assert res.data and isinstance(res.data["deleteIssue"], dict)
    assert res.data == {
        "deleteIssue": {
            "__typename": "IssueType",
            "id": to_base64("IssueType", issue.pk),
            "name": issue.name,
            "milestone": {
                "id": to_base64("MilestoneType", issue.milestone.pk),
                "name": issue.milestone.name,
            },
            "priority": issue.priority,
            "kind": issue.kind.value,  # type:ignore
        }
    }

    with pytest.raises(Issue.DoesNotExist):
        Issue.objects.get(pk=issue.pk)
