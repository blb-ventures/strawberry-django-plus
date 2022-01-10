import pytest

from strawberry_django_plus.relay import to_base64

from .faker import IssueFactory, MilestoneFactory, ProjectFactory
from .utils import GraphQLTestClient, assert_num_queries

# Those are simple general tests. Still need to write specific ones...


@pytest.mark.django_db(transaction=True)
def test_query_forward(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery {
        issueConn {
          totalCount
          edges {
            node {
              id
              name
              milestone {
                id
                name
                project {
                  id
                  name
                }
              }
            }
          }
        }
      }
    """

    expected = []
    for p in ProjectFactory.create_batch(2):
        for m in MilestoneFactory.create_batch(2, project=p):
            for i in IssueFactory.create_batch(2, milestone=m):
                r = {
                    "id": to_base64("IssueType", i.id),
                    "name": i.name,
                    "milestone": {
                        "id": to_base64("MilestoneType", m.id),
                        "name": m.name,
                        "project": {
                            "id": to_base64("ProjectType", p.id),
                            "name": p.name,
                        },
                    },
                }
                expected.append(r)

    # FIXME: Why async is failing to track queries?
    n_queries = 2 if gql_client.optimizer_enabled else 18
    with assert_num_queries(n_queries, is_async=gql_client.is_async):
        res = gql_client.query(query)

    assert res.data == {
        "issueConn": {
            "totalCount": 8,
            "edges": [{"node": r} for r in expected],
        },
    }


@pytest.mark.django_db(transaction=True)
def test_query_forward_with_fragments(db, gql_client: GraphQLTestClient):
    query = """
      fragment issueFrag on IssueType {
          nameWithKind
          nameWithPriority
      }

      fragment milestoneFrag on MilestoneType {
        id
        project {
          name
        }
      }

      query TestQuery {
        issueConn {
          totalCount
          edges {
            node {
              id
              name
              ... issueFrag
              milestone {
                name
                project {
                  id
                  name
                }
                ... milestoneFrag
              }
              milestoneAgain: milestone {
                name
                project {
                  id
                  name
                }
                ... milestoneFrag
              }
            }
          }
        }
      }
    """

    expected = []
    for p in ProjectFactory.create_batch(3):
        for m in MilestoneFactory.create_batch(3, project=p):
            for i in IssueFactory.create_batch(3, milestone=m):
                m_res = {
                    "id": to_base64("MilestoneType", m.id),
                    "name": m.name,
                    "project": {
                        "id": to_base64("ProjectType", p.id),
                        "name": p.name,
                    },
                }
                expected.append(
                    {
                        "id": to_base64("IssueType", i.id),
                        "name": i.name,
                        "nameWithKind": f"{i.kind}: {i.name}",
                        "nameWithPriority": f"{i.kind}: {i.priority}",
                        "milestone": m_res,
                        "milestoneAgain": m_res,
                    }
                )

    # FIXME: Why async is failing to track queries?
    n_queries = 2 if gql_client.optimizer_enabled else 56
    with assert_num_queries(n_queries, is_async=gql_client.is_async):
        res = gql_client.query(query)

    assert res.data == {
        "issueConn": {
            "totalCount": 27,
            "edges": [{"node": r} for r in expected],
        },
    }


@pytest.mark.django_db(transaction=True)
def test_query_prefetch(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($node_id: GlobalID!) {
        project (id: $node_id) {
          id
          name
          milestones {
            id
            name
            project {
              id
              name
            }
            issues {
              id
              name
              milestone {
                id
                name
              }
            }
          }
        }
      }
    """

    expected = []
    for p in ProjectFactory.create_batch(2):
        p_res = {
            "id": to_base64("ProjectType", p.id),
            "name": p.name,
            "milestones": [],
        }
        expected.append(p_res)
        for m in MilestoneFactory.create_batch(2, project=p):
            m_res = {
                "id": to_base64("MilestoneType", m.id),
                "name": m.name,
                "project": {
                    "id": p_res["id"],
                    "name": p_res["name"],
                },
                "issues": [],
            }
            p_res["milestones"].append(m_res)
            for i in IssueFactory.create_batch(2, milestone=m):
                m_res["issues"].append(
                    {
                        "id": to_base64("IssueType", i.id),
                        "name": i.name,
                        "milestone": {
                            "id": m_res["id"],
                            "name": m_res["name"],
                        },
                    }
                )

    assert len(expected) == 2
    for e in expected:
        n_queries = 3 if gql_client.optimizer_enabled else 4
        with assert_num_queries(n_queries, is_async=gql_client.is_async):
            res = gql_client.query(query, {"node_id": e["id"]})

        assert res.data == {"project": e}


@pytest.mark.django_db(transaction=True)
def test_query_prefetch_with_fragments(db, gql_client: GraphQLTestClient):
    query = """
      fragment issueFrag on IssueType {
          nameWithKind
          nameWithPriority
      }

      fragment milestoneFrag on MilestoneType {
        id
        project {
          id
          name
        }
      }

      query TestQuery ($node_id: GlobalID!) {
        project (id: $node_id) {
          id
          name
          milestones {
            id
            name
            project {
              id
              name
            }
            issues {
              id
              name
              ... issueFrag
              milestone {
                ... milestoneFrag
              }
            }
            otherIssues: issues {
              id
              milestone {
                ... milestoneFrag
              }
            }
          }
        }
      }
    """

    expected = []
    for p in ProjectFactory.create_batch(3):
        p_res = {
            "id": to_base64("ProjectType", p.id),
            "name": p.name,
            "milestones": [],
        }
        expected.append(p_res)
        for m in MilestoneFactory.create_batch(3, project=p):
            m_res = {
                "id": to_base64("MilestoneType", m.id),
                "name": m.name,
                "project": {
                    "id": p_res["id"],
                    "name": p_res["name"],
                },
                "issues": [],
                "otherIssues": [],
            }
            p_res["milestones"].append(m_res)
            for i in IssueFactory.create_batch(3, milestone=m):
                m_res["issues"].append(
                    {
                        "id": to_base64("IssueType", i.id),
                        "name": i.name,
                        "nameWithKind": f"{i.kind}: {i.name}",
                        "nameWithPriority": f"{i.kind}: {i.priority}",
                        "milestone": {
                            "id": m_res["id"],
                            "project": {
                                "id": p_res["id"],
                                "name": p_res["name"],
                            },
                        },
                    }
                )
                m_res["otherIssues"].append(
                    {
                        "id": to_base64("IssueType", i.id),
                        "milestone": {
                            "id": m_res["id"],
                            "project": {
                                "id": p_res["id"],
                                "name": p_res["name"],
                            },
                        },
                    }
                )

    assert len(expected) == 3
    for e in expected:
        n_queries = 3 if gql_client.optimizer_enabled else 8
        with assert_num_queries(n_queries, is_async=gql_client.is_async):
            res = gql_client.query(query, {"node_id": e["id"]})

        assert res.data == {"project": e}


@pytest.mark.django_db(transaction=True)
def test_mutation(db, gql_client: GraphQLTestClient):
    query = """
    mutation CreateProject ($input: CreateProjectInput!) {
        createProject (input: $input) {
          name
          cost
        }
      }
    """
    with assert_num_queries(1, is_async=gql_client.is_async):
        res = gql_client.query(query, {"input": {"name": "Some Project", "cost": "12.50"}})
        assert res.data == {"createProject": {"name": "Some Project", "cost": "12.50"}}
