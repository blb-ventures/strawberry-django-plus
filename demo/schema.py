import asyncio
import datetime
import decimal
from typing import Iterable, List, Optional, Type, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from strawberry.types.info import Info
from typing_extensions import Annotated

from strawberry_django_plus import gql
from strawberry_django_plus.directives import SchemaDirectiveExtension
from strawberry_django_plus.gql import relay
from strawberry_django_plus.optimizer import DjangoOptimizerExtension
from strawberry_django_plus.permissions import (
    HasObjPerm,
    HasPerm,
    IsAuthenticated,
    IsStaff,
    IsSuperuser,
)

from .models import Issue, Milestone, Project, Tag

UserModel = cast(Type[AbstractUser], get_user_model())


@gql.django.type(UserModel)
class UserType(relay.Node):
    username: gql.auto
    email: gql.auto
    is_active: gql.auto
    is_superuser: gql.auto
    is_staff: gql.auto

    @gql.django.field(only=["first_name", "last_name"])
    def full_name(self, root: UserModel, value: str) -> str:
        return f"{root.first_name or ''} {root.last_name or ''}".strip()


@gql.django.type(Project)
class ProjectType(relay.Node):
    name: gql.auto
    status: gql.auto
    due_date: gql.auto
    milestones: List["MilestoneType"]
    cost: gql.auto = gql.django.field(directives=[IsAuthenticated()])


@gql.django.type(Milestone)
class MilestoneType(relay.Node):
    name: gql.auto
    due_date: gql.auto
    project: ProjectType
    issues: List["IssueType"]

    @gql.django.field
    async def async_field(self, value: str) -> str:
        await asyncio.sleep(0.1)
        return f"value: {value}"


@gql.django.type(Issue)
class IssueType(relay.Node):
    name: gql.auto
    milestone: MilestoneType
    priority: gql.auto
    kind: gql.auto
    name_with_priority: gql.auto
    name_with_kind: str = gql.django.field(only=["kind", "name"])
    tags: List["TagType"]


@gql.django.type(Tag)
class TagType(relay.Node):
    name: gql.auto
    issues: relay.Connection[IssueType]


@gql.django.input(Issue)
class IssueInput:
    name: gql.auto
    milestone: gql.auto
    priority: gql.auto
    kind: gql.auto
    tags: Optional[List[gql.NodeInput]]


@gql.django.partial(Issue)
class IssueInputPartial(gql.NodeInput, IssueInput):
    tags: Optional[gql.ListInput[gql.NodeInput]]


@gql.django.input(Issue)
class MilestoneIssueInput:
    name: gql.auto


@gql.django.input(Milestone)
class MilestoneInput:
    name: gql.auto
    project: gql.auto
    issues: Optional[List[MilestoneIssueInput]]


@gql.type
class Query:
    """All available queries for this schema."""

    issue: Optional[IssueType] = relay.node(description="Foobar")
    milestone: Optional[MilestoneType] = relay.node()
    project: Optional[ProjectType] = relay.node()
    tag: Optional[TagType] = relay.node()

    issue_list: List[IssueType] = gql.django.field()
    milestone_list: List[MilestoneType] = gql.django.field()
    project_list: List[ProjectType] = gql.django.field()
    tag_list: List[TagType] = gql.django.field()

    issue_conn: relay.Connection[
        gql.LazyType["IssueType", "demo.schema"]  # type:ignore # noqa:F821
    ] = relay.connection()
    milestone_conn: relay.Connection[MilestoneType] = relay.connection()
    project_conn: relay.Connection[ProjectType] = relay.connection()
    tag_conn: relay.Connection[TagType] = relay.connection()

    # Login required to resolve
    issue_login_required: IssueType = relay.node(directives=[IsAuthenticated()])
    issue_login_required_optional: Optional[IssueType] = relay.node(directives=[IsAuthenticated()])
    # Staff required to resolve
    issue_staff_required: IssueType = relay.node(directives=[IsStaff()])
    issue_staff_required_optional: Optional[IssueType] = relay.node(directives=[IsStaff()])
    # Superuser required to resolve
    issue_superuser_required: IssueType = relay.node(directives=[IsSuperuser()])
    issue_superuser_required_optional: Optional[IssueType] = relay.node(directives=[IsSuperuser()])
    # User permission on "demo.view_issue" to resolve
    issue_perm_required: IssueType = relay.node(
        directives=[HasPerm("demo.view_issue")],
    )
    issue_perm_required_optional: Optional[IssueType] = relay.node(
        directives=[HasPerm("demo.view_issue")],
    )
    issue_list_perm_required: List[IssueType] = gql.django.field(
        directives=[HasPerm("demo.view_issue")],
    )
    issue_conn_perm_required: relay.Connection[IssueType] = relay.connection(
        directives=[HasPerm("demo.view_issue")],
    )
    # User permission on the resolved object for "demo.view_issue"
    issue_obj_perm_required: IssueType = relay.node(
        directives=[HasObjPerm("demo.view_issue")],
    )
    issue_obj_perm_required_optional: Optional[IssueType] = relay.node(
        directives=[HasObjPerm("demo.view_issue")],
    )
    issue_list_obj_perm_required: List[IssueType] = gql.django.field(
        directives=[HasObjPerm("demo.view_issue")],
    )
    issue_conn_obj_perm_required: relay.Connection[IssueType] = relay.connection(
        directives=[HasObjPerm("demo.view_issue")],
    )

    @relay.connection
    def project_conn_with_resolver(self, root: str, name: str) -> Iterable[ProjectType]:
        return cast(Iterable[ProjectType], Project.objects.filter(name__contains=name))


@gql.type
class Mutation:
    """All available mutations for this schema."""

    create_issue: IssueType = gql.django.create_mutation(IssueInput)
    update_issue: IssueType = gql.django.update_mutation(IssueInputPartial)
    delete_issue: IssueType = gql.django.delete_mutation(gql.NodeInput)

    create_milestone: MilestoneType = gql.django.create_mutation(MilestoneInput)

    @gql.django.input_mutation
    def create_project(
        self,
        info: Info,
        name: str,
        cost: Annotated[decimal.Decimal, gql.argument(description="The project's cost")],
        status: Project.Status = Project.Status.ACTIVE,
        due_date: Optional[datetime.datetime] = None,
    ) -> ProjectType:
        """Create project documentation."""
        if cost > 500:
            raise ValidationError({"cost": "Cost cannot be higher than 500"})

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
        SchemaDirectiveExtension,
        DjangoOptimizerExtension,
    ],
)
