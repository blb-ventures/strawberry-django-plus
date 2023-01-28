import pytest

from strawberry_django_plus.relay import to_base64

from .faker import MilestoneFactory, ProjectFactory, UserFactory
from .utils import GraphQLTestClient


@pytest.mark.django_db(transaction=True)
def test_node_single_optional(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($id: GlobalID!) {
        milestone(id: $id) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    milestone = MilestoneFactory.create()
    res = gql_client.query(query, {"id": to_base64("MilestoneType", milestone.pk)})
    assert res.data == {
        "milestone": {
            "id": to_base64("MilestoneType", milestone.pk),
            "name": milestone.name,
            "project": {
                "id": to_base64("ProjectType", milestone.project.pk),
                "name": milestone.project.name,
            },
        }
    }
    #
    # The id is correct, but the type is not
    res = gql_client.query(query, {"id": to_base64("IssueType", milestone.pk)})
    assert res.data == {"milestone": None}

    res = gql_client.query(query, {"id": to_base64("MilestoneType", "9999")})
    assert res.data == {"milestone": None}


@pytest.mark.django_db(transaction=True)
def test_node_single_mandatory(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($id: GlobalID!) {
        milestoneMandatory(id: $id) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    milestone = MilestoneFactory.create()
    res = gql_client.query(query, {"id": to_base64("MilestoneType", milestone.pk)})
    assert res.data == {
        "milestoneMandatory": {
            "id": to_base64("MilestoneType", milestone.pk),
            "name": milestone.name,
            "project": {
                "id": to_base64("ProjectType", milestone.project.pk),
                "name": milestone.project.name,
            },
        }
    }

    # The id is correct, but the type is not
    res = gql_client.query(
        query,
        {"id": to_base64("IssueType", milestone.pk)},
        asserts_errors=False,
    )
    assert res.data is None
    assert res.errors == [
        {
            "message": "Issue matching query does not exist.",
            "locations": [{"line": 3, "column": 9}],
            "path": ["milestoneMandatory"],
        }
    ]

    res = gql_client.query(
        query,
        {"id": to_base64("MilestoneType", "9999")},
        asserts_errors=False,
    )
    assert res.data is None
    assert res.errors == [
        {
            "message": "Milestone matching query does not exist.",
            "locations": [{"line": 3, "column": 9}],
            "path": ["milestoneMandatory"],
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_node_multiple(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($ids: [GlobalID!]!) {
        milestones(ids: $ids) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    milestones = MilestoneFactory.create_batch(4)
    res = gql_client.query(query, {"ids": [to_base64("MilestoneType", m.pk) for m in milestones]})
    assert res.data == {
        "milestones": [
            {
                "id": to_base64("MilestoneType", m.pk),
                "name": m.name,
                "project": {
                    "id": to_base64("ProjectType", m.project.pk),
                    "name": m.project.name,
                },
            }
            for m in milestones
        ]
    }

    # The ids are correct, but the type is not
    res = gql_client.query(query, {"ids": [to_base64("IssueType", m.pk) for m in milestones]})
    assert res.data == {"milestones": []}


@pytest.mark.django_db(transaction=True)
def test_ordering(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($order: MilestoneOrder) {
        milestoneList (
          order: $order
        ) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    milestone_1 = MilestoneFactory.create(name="Foo", project__name="Proj 3")
    milestone_2 = MilestoneFactory.create(name="Bar", project__name="Proj 1")
    milestone_3 = MilestoneFactory.create(name="Zaffar", project__name="Proj 2")

    # Without ordering this should return the natural order of the database
    res = gql_client.query(query)
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_1, milestone_2, milestone_3]
        ]
    }

    res = gql_client.query(query, {"order": {"name": "ASC"}})
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_2, milestone_1, milestone_3]
        ]
    }
    res = gql_client.query(query, {"order": {"name": "DESC"}})
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_3, milestone_1, milestone_2]
        ]
    }

    res = gql_client.query(query, {"order": {"project": {"name": "ASC"}}})
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_2, milestone_3, milestone_1]
        ]
    }
    res = gql_client.query(query, {"order": {"project": {"name": "DESC"}}})
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_1, milestone_3, milestone_2]
        ]
    }


@pytest.mark.django_db(transaction=True)
def test_filtering(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($filters: MilestoneFilter) {
        milestoneList (
          filters: $filters
        ) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    p = ProjectFactory.create()
    milestone_1 = MilestoneFactory.create(name="Foo", project=p)
    milestone_2 = MilestoneFactory.create(name="Bar")
    milestone_3 = MilestoneFactory.create(name="Zaffar", project=p)

    res = gql_client.query(query)
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_1, milestone_2, milestone_3]
    }

    res = gql_client.query(query, {"filters": {"name": {"contains": "ar"}}})
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_2, milestone_3]
    }

    res = gql_client.query(query, {"filters": {"project": {"id": to_base64("ProjectType", p.id)}}})
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_1, milestone_3]
    }

    res = gql_client.query(
        query,
        {
            "filters": {
                "name": {"contains": "ar"},
                "project": {"id": to_base64("ProjectType", p.id)},
            }
        },
    )
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_3]
    }


@pytest.mark.django_db(transaction=True)
def test_filtering_custom(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($filters: MilestoneFilter) {
        milestoneList (
          filters: $filters
        ) {
          id
          name
          project {
            id
            name
          }
        }
      }
    """

    p = ProjectFactory.create()
    milestone_1 = MilestoneFactory.create(name="Foo", project=p)
    milestone_2 = MilestoneFactory.create(name="Bar")
    milestone_3 = MilestoneFactory.create(name="Zaffar", project=p)

    res = gql_client.query(query)
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_1, milestone_2, milestone_3]
    }

    res = gql_client.query(query, {"filters": {"search": "ar"}})
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_2, milestone_3]
    }

    res = gql_client.query(
        query,
        {
            "filters": {
                "search": "ar",
                "project": {"id": to_base64("ProjectType", p.id)},
            }
        },
    )
    assert res.data
    assert isinstance(res.data["milestoneList"], list)
    assert {r["id"] for r in res.data["milestoneList"]} == {
        to_base64("MilestoneType", m.id) for m in [milestone_3]
    }


@pytest.mark.django_db(transaction=True)
def test_node_queryset(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($id: GlobalID!) {
        staff(id: $id) {
          id
          username
          isStaff
        }
      }
    """

    user = UserFactory.create(is_staff=False)
    res = gql_client.query(query, {"id": to_base64("StaffType", user.username)})
    assert res.data == {"staff": None}

    staff = UserFactory.create(is_staff=True)
    res = gql_client.query(query, {"id": to_base64("StaffType", staff.username)})
    assert res.data == {
        "staff": {
            "id": to_base64("StaffType", staff.username),
            "username": staff.username,
            "isStaff": True,
        }
    }


@pytest.mark.django_db(transaction=True)
def test_node_multiple_queryset(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($ids: [GlobalID!]!) {
        staffList(ids: $ids) {
          id
          username
          isStaff
        }
      }
    """

    user = UserFactory.create(is_staff=False)
    staff = UserFactory.create(is_staff=True)
    res = gql_client.query(
        query,
        {"ids": [to_base64("StaffType", user.username), to_base64("StaffType", staff.username)]},
    )
    assert res.data == {
        "staffList": [
            {
                "id": to_base64("StaffType", staff.username),
                "username": staff.username,
                "isStaff": True,
            }
        ]
    }


@pytest.mark.django_db(transaction=True)
def test_connection_queryset(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery {
        staffConn {
          edges {
            node {
              id
              username
              isStaff
            }
          }
        }
      }
    """

    UserFactory.create(is_staff=False)
    staff = UserFactory.create(is_staff=True)
    res = gql_client.query(query)
    assert res.data == {
        "staffConn": {
            "edges": [
                {
                    "node": {
                        "id": to_base64("StaffType", staff.username),
                        "username": staff.username,
                        "isStaff": True,
                    }
                }
            ]
        }
    }


@pytest.mark.django_db(transaction=True)
def test_connection_queryset_with_filter(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($first: Int = null $filters: MilestoneFilter $order: MilestoneOrder) {
        milestoneConn (
          first: $first
          filters: $filters
          order: $order
        ) {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    """

    m1 = MilestoneFactory.create(name="Foo")
    MilestoneFactory.create(name="Bar")
    m3 = MilestoneFactory.create(name="FooBar")

    res = gql_client.query(
        query,
        variables={
            "filters": {
                "name": {
                    "startsWith": "Foo",
                }
            }
        },
    )
    assert res.data == {
        "milestoneConn": {
            "edges": [
                {
                    "node": {
                        "id": to_base64("MilestoneType", m1.pk),
                        "name": "Foo",
                    }
                },
                {
                    "node": {
                        "id": to_base64("MilestoneType", m3.pk),
                        "name": "FooBar",
                    }
                },
            ]
        }
    }


@pytest.mark.django_db(transaction=True)
def test_connection_queryset_with_order(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($first: Int = null $filters: MilestoneFilter $order: MilestoneOrder) {
        milestoneConn (
          first: $first
          filters: $filters
          order: $order
        ) {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    """

    m1 = MilestoneFactory.create(name="Foo")
    m2 = MilestoneFactory.create(name="Bar")
    m3 = MilestoneFactory.create(name="FooBar")

    res = gql_client.query(
        query,
        variables={
            "order": {
                "name": "DESC",
            },
        },
    )
    assert res.data == {
        "milestoneConn": {
            "edges": [
                {
                    "node": {
                        "id": to_base64("MilestoneType", m3.pk),
                        "name": "FooBar",
                    }
                },
                {
                    "node": {
                        "id": to_base64("MilestoneType", m1.pk),
                        "name": "Foo",
                    }
                },
                {
                    "node": {
                        "id": to_base64("MilestoneType", m2.pk),
                        "name": "Bar",
                    }
                },
            ]
        }
    }


@pytest.mark.django_db(transaction=True)
def test_connection_queryset_with_filter_and_order(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($first: Int = null $filters: MilestoneFilter $order: MilestoneOrder) {
        milestoneConn (
          first: $first
          filters: $filters
          order: $order
        ) {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    """

    m1 = MilestoneFactory.create(name="Foo")
    MilestoneFactory.create(name="Bar")
    m3 = MilestoneFactory.create(name="FooBar")

    res = gql_client.query(
        query,
        variables={
            "filters": {
                "name": {
                    "startsWith": "Foo",
                }
            },
            "order": {
                "name": "DESC",
            },
        },
    )
    assert res.data == {
        "milestoneConn": {
            "edges": [
                {
                    "node": {
                        "id": to_base64("MilestoneType", m3.pk),
                        "name": "FooBar",
                    }
                },
                {
                    "node": {
                        "id": to_base64("MilestoneType", m1.pk),
                        "name": "Foo",
                    }
                },
            ]
        }
    }


@pytest.mark.django_db(transaction=True)
def test_connection_queryset_with_filter_order_and_first(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($first: Int = null $filters: MilestoneFilter $order: MilestoneOrder) {
        milestoneConn (
          first: $first
          filters: $filters
          order: $order
        ) {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    """

    MilestoneFactory.create(name="Foo")
    MilestoneFactory.create(name="Bar")
    m3 = MilestoneFactory.create(name="FooBar")

    res = gql_client.query(
        query,
        variables={
            "first": 1,
            "filters": {
                "name": {
                    "startsWith": "Foo",
                }
            },
            "order": {
                "name": "DESC",
            },
        },
    )
    assert res.data == {
        "milestoneConn": {
            "edges": [
                {
                    "node": {
                        "id": to_base64("MilestoneType", m3.pk),
                        "name": "FooBar",
                    }
                },
            ]
        }
    }
