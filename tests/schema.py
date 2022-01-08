from typing import Iterable, List, Optional

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
    def foo(self, name: str) -> str:
        return "xxx"


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


schema = gql.Schema(
    Query,
    extensions=[
        DjangoOptimizerExtension(),
    ],
)
