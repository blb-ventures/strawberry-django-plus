import asyncio
import datetime
import decimal
from typing import Iterable, List, Optional, cast

from typing_extensions import Annotated

from strawberry_django_plus import gql
from strawberry_django_plus.gql import relay
from strawberry_django_plus.optimizer import DjangoOptimizerExtension

from .models import Issue, Milestone, Project


@gql.django.type(Project)
class ProjectType(relay.Node):
    name: gql.auto
    status: gql.auto
    due_date: gql.auto
    cost: gql.auto
    milestones: List["MilestoneType"]
    milestones_conn: "relay.Connection[MilestoneType]" = relay.connection()


@gql.django.type(Milestone)
class MilestoneType(relay.Node):
    name: gql.auto
    due_date: gql.auto
    project: ProjectType
    issues: List["IssueType"]

    @gql.field
    async def async_field(self, value: str) -> str:
        await asyncio.sleep(0.1)
        return f"value: {value}"


@gql.django.type(Issue)
class IssueType(relay.Node):
    name: gql.auto
    milestone: MilestoneType
    name_with_priority: gql.auto
    name_with_kind: str = gql.django.field(only=["kind", "name"])


@gql.type
class Query:
    """All available queries for this schema."""

    issue: Optional[IssueType] = relay.node(description="FOobar")
    milestone: Optional[MilestoneType] = relay.node()
    project: Optional[ProjectType] = relay.node()

    issue_list: List[IssueType] = gql.django.field()
    milestone_list: List[MilestoneType] = gql.django.field()
    project_list: List[ProjectType] = gql.django.field()

    issue_conn: relay.Connection[IssueType] = relay.connection()
    milestone_conn: relay.Connection[MilestoneType] = relay.connection()
    project_conn: relay.Connection[ProjectType] = relay.connection()

    @relay.connection
    def project_conn_with_resolver(self, name: str) -> Iterable[ProjectType]:
        return cast(Iterable[ProjectType], Project.objects.filter(name__contains=name))


@gql.type
class Mutation:
    """All available mutations for this schema."""

    @gql.django.input_mutation
    def create_project(
        self,
        name: str,
        cost: Annotated[decimal.Decimal, gql.argument(description="The project's cost")],
        status: Project.Status = Project.Status.ACTIVE,
        due_date: Optional[datetime.datetime] = None,
    ) -> ProjectType:
        """Create project documentation"""
        return cast(
            ProjectType,
            Project.objects.create(
                name=name,
                cost=cost,
                status=status or Project.Status.ACTIVE,
                due_date=due_date,
            ),
        )


schema = gql.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        DjangoOptimizerExtension(),
    ],
)
