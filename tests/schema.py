from typing import Iterable, List, Optional

from typing_extensions import Annotated

from strawberry_django_plus import gql
from strawberry_django_plus.gql import relay
from strawberry_django_plus.optimizer import DjangoOptimizerExtension

from .models import Issue, Milestone, Project


@gql.django.type(Project)
class ProjectType(relay.Node[Project]):
    name: gql.auto
    due_date: gql.auto
    cost: gql.auto
    milestones: List["MilestoneType"]
    milestones_conn: "relay.Connection[MilestoneType]" = relay.connection()


@gql.django.type(Milestone)
class MilestoneType(relay.Node[Milestone]):
    name: gql.auto
    due_date: gql.auto
    project: ProjectType
    issues: List["IssueType"]

    @gql.field
    async def async_field(self, value: str) -> str:
        return f"value: {value}"


@gql.django.type(Issue)
class IssueType(relay.Node[Issue]):
    name: gql.auto
    milestone: MilestoneType
    name_with_priority: gql.auto
    name_with_kind: str = gql.django.field(only=["kind", "name"])


@gql.type
class Query:
    issue: Optional[IssueType] = relay.node()
    milestone: Optional[MilestoneType] = relay.node()
    project: Optional[ProjectType] = relay.node()

    issue_list: List[IssueType] = gql.django.field()
    milestone_list: List[MilestoneType] = gql.django.field()
    project_list: List[ProjectType] = gql.django.field()

    issue_conn: relay.Connection[IssueType] = relay.connection()
    milestone_conn: relay.Connection[MilestoneType] = relay.connection()
    project_conn: relay.Connection[ProjectType] = relay.connection()

    @relay.connection
    def project_conn_with_resolver(self, name: str) -> Iterable[Project]:
        return Project.objects.filter(name__contains=name)


@gql.type
class Mutation:
    @relay.input_mutation
    def test_mutation(
        self,
        name: Annotated[str, gql.argument(description="foobar")],
        abc: int,
    ) -> Optional[Project]:
        """Test mutation doc"""
        return Project.objects.first()


schema = gql.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        DjangoOptimizerExtension(),
    ],
)
