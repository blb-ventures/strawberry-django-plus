import pytest
from strawberry.relay import to_base64

from .faker import MilestoneFactory
from .utils import GraphQLTestClient


@pytest.mark.django_db(transaction=True)
def test_ordering(db, gql_client: GraphQLTestClient):
    query = """
      query TestQuery ($order: MilestoneOrder $filters: MilestoneFilter) {
        milestoneList (
          order: $order
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

    milestone_1 = MilestoneFactory.create(name="Foo")
    milestone_2 = MilestoneFactory.create(name="Bar")
    milestone_3 = MilestoneFactory.create(name="Bin")

    res = gql_client.query(query)
    assert res.data == {
        "milestoneList": [
            {
                "id": to_base64("MilestoneType", i.pk),
                "name": i.name,
                "project": {"id": to_base64("ProjectType", i.project.pk), "name": i.project.name},
            }
            for i in [milestone_1, milestone_2, milestone_3]
        ],
    }
