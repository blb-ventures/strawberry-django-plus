import pytest

from strawberry_django_plus.relay import to_base64

from .faker import MilestoneFactory, ProjectFactory
from .utils import GraphQLTestClient


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
    milestone_3 = MilestoneFactory.create(name="Zar", project__name="Proj 2")

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
    milestone_3 = MilestoneFactory.create(name="Zar", project=p)

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
