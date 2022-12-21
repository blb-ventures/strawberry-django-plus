import asyncio
import datetime
import decimal
from typing import Iterable, List, Optional, Type, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Prefetch
from django.db.models.query import QuerySet
from strawberry.types.info import Info
from typing_extensions import Annotated

from strawberry_django_plus import gql
from strawberry_django_plus.directives import SchemaDirectiveExtension
from strawberry_django_plus.gql import relay
from strawberry_django_plus.mutations import resolvers
from strawberry_django_plus.optimizer import DjangoOptimizerExtension
from strawberry_django_plus.permissions import (
    HasObjPerm,
    HasPerm,
    IsAuthenticated,
    IsStaff,
    IsSuperuser,
)

from .models import Assignee, Issue, Milestone, Project, Quiz, Tag

UserModel = cast(Type[AbstractUser], get_user_model())


@gql.django.type(UserModel)
class UserType(relay.Node):
    username: gql.auto
    email: gql.auto
    is_active: gql.auto
    is_superuser: gql.auto
    is_staff: gql.auto

    id_attr = "username"

    @gql.django.field(only=["first_name", "last_name"])
    def full_name(self, root: AbstractUser) -> str:
        return f"{root.first_name or ''} {root.last_name or ''}".strip()


@gql.django.type(UserModel)
class StaffType(relay.Node):
    username: gql.auto
    email: gql.auto
    is_active: gql.auto
    is_superuser: gql.auto
    is_staff: gql.auto

    id_attr = "username"

    @classmethod
    def get_queryset(cls, queryset: QuerySet[AbstractUser], info: Info) -> QuerySet[AbstractUser]:
        return queryset.filter(is_staff=True)


@gql.django.type(Project)
class ProjectType(relay.Node):
    name: gql.auto
    status: gql.auto
    due_date: gql.auto
    milestones: List["MilestoneType"]
    cost: gql.auto = gql.django.field(directives=[IsAuthenticated()])


@gql.django.filter(Milestone, lookups=True)
class MilestoneFilter:
    name: gql.auto
    project: gql.auto
    search: Optional[str]

    def filter_search(self, queryset: QuerySet[Milestone]):
        return queryset.filter(name__contains=self.search)


@gql.django.order(Project)
class ProjectOrder:
    id: gql.auto  # noqa:A003
    name: gql.auto


@gql.django.order(Milestone)
class MilestoneOrder:
    name: gql.auto
    project: Optional[ProjectOrder]


@gql.django.type(Milestone, filters=MilestoneFilter, order=MilestoneOrder)
class MilestoneType(relay.Node):
    name: gql.auto
    due_date: gql.auto
    project: ProjectType
    issues: List["IssueType"]

    @gql.django.field(
        prefetch_related=[
            lambda info: Prefetch(
                "issues",
                queryset=Issue.objects.filter(
                    Exists(
                        Assignee.objects.filter(
                            issue=OuterRef("pk"),
                            user_id=info.context.request.user.id,
                        )
                    )
                ),
                to_attr="_my_issues",
            )
        ],
    )
    def my_issues(self) -> List["IssueType"]:
        return self._my_issues  # type:ignore

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
    issue_assignees: List["AssigneeType"]


@gql.django.type(Tag)
class TagType(relay.Node):
    name: gql.auto
    issues: relay.Connection[IssueType]


@gql.django.type(Quiz)
class QuizType(relay.Node):
    title: gql.auto
    sequence: gql.auto


@gql.django.partial(Tag)
class TagInputPartial(gql.NodeInputPartial):
    name: gql.auto


@gql.django.input(Issue)
class IssueInput:
    name: gql.auto
    milestone: "MilestoneInputPartial"
    priority: gql.auto
    kind: gql.auto
    tags: Optional[List[gql.NodeInput]]


@gql.django.type(Assignee)
class AssigneeType(relay.Node):
    user: UserType
    owner: gql.auto


@gql.django.partial(Assignee)
class IssueAssigneeInputPartial(gql.NodeInputPartial):
    user: gql.auto
    owner: gql.auto


@gql.input
class AssigneeThroughInputPartial:
    owner: Optional[bool] = gql.UNSET


@gql.django.partial(UserModel)
class AssigneeInputPartial(gql.NodeInputPartial):
    through_defaults: Optional[AssigneeThroughInputPartial] = gql.UNSET


@gql.django.partial(Issue)
class IssueInputPartial(gql.NodeInput, IssueInput):
    tags: Optional[gql.ListInput[TagInputPartial]]
    assignees: Optional[gql.ListInput[AssigneeInputPartial]]
    issue_assignees: Optional[gql.ListInput[IssueAssigneeInputPartial]]


@gql.django.input(Issue)
class MilestoneIssueInput:
    name: gql.auto


@gql.django.partial(Project)
class ProjectInputPartial(gql.NodeInputPartial):
    name: gql.auto
    milestones: Optional[List["MilestoneInputPartial"]]


@gql.django.input(Milestone)
class MilestoneInput:
    name: gql.auto
    project: ProjectInputPartial
    issues: Optional[List[MilestoneIssueInput]]


@gql.django.partial(Milestone)
class MilestoneInputPartial(gql.NodeInputPartial):
    name: gql.auto


@gql.type
class Query:
    """All available queries for this schema."""

    node: Optional[gql.Node] = gql.django.node()

    issue: Optional[IssueType] = gql.django.node(description="Foobar")
    milestone: Optional[Annotated["MilestoneType", gql.lazy("demo.schema")]] = gql.django.node()
    milestone_mandatory: MilestoneType = gql.django.node()
    milestones: List[MilestoneType] = gql.django.node()
    project: Optional[ProjectType] = gql.django.node()
    project_login_required: Optional[ProjectType] = gql.django.node(directives=[IsAuthenticated()])
    tag: Optional[TagType] = gql.django.node()
    staff: Optional[StaffType] = gql.django.node()
    staff_list: List[StaffType] = gql.django.node()

    issue_list: List[IssueType] = gql.django.field()
    milestone_list: List[MilestoneType] = gql.django.field(
        order=MilestoneOrder,
        filters=MilestoneFilter,
        pagination=True,
    )
    project_list: List[ProjectType] = gql.django.field()
    tag_list: List[TagType] = gql.django.field()

    issue_conn: relay.Connection[
        gql.LazyType["IssueType", "demo.schema"]  # type:ignore # noqa:F821
    ] = gql.django.connection()
    milestone_conn: relay.Connection[MilestoneType] = gql.django.connection()

    project_conn: relay.Connection[ProjectType] = gql.django.connection()
    tag_conn: relay.Connection[TagType] = gql.django.connection()
    staff_conn: relay.Connection[StaffType] = gql.django.connection()

    # Login required to resolve
    issue_login_required: IssueType = gql.django.node(directives=[IsAuthenticated()])
    issue_login_required_optional: Optional[IssueType] = gql.django.node(
        directives=[IsAuthenticated()]
    )
    # Staff required to resolve
    issue_staff_required: IssueType = gql.django.node(directives=[IsStaff()])
    issue_staff_required_optional: Optional[IssueType] = gql.django.node(directives=[IsStaff()])
    # Superuser required to resolve
    issue_superuser_required: IssueType = gql.django.node(directives=[IsSuperuser()])
    issue_superuser_required_optional: Optional[IssueType] = gql.django.node(
        directives=[IsSuperuser()]
    )
    # User permission on "demo.view_issue" to resolve
    issue_perm_required: IssueType = gql.django.node(
        directives=[HasPerm(perms=["demo.view_issue"])],
    )
    issue_perm_required_optional: Optional[IssueType] = gql.django.node(
        directives=[HasPerm(perms=["demo.view_issue"])],
    )
    issue_list_perm_required: List[IssueType] = gql.django.field(
        directives=[HasPerm(perms=["demo.view_issue"])],
    )
    issue_conn_perm_required: relay.Connection[IssueType] = gql.django.connection(
        directives=[HasPerm(perms=["demo.view_issue"])],
    )
    # User permission on the resolved object for "demo.view_issue"
    issue_obj_perm_required: IssueType = gql.django.node(
        directives=[HasObjPerm(perms=["demo.view_issue"])],
    )
    issue_obj_perm_required_optional: Optional[IssueType] = gql.django.node(
        directives=[HasObjPerm(perms=["demo.view_issue"])],
    )
    issue_list_obj_perm_required: List[IssueType] = gql.django.field(
        directives=[HasObjPerm(perms=["demo.view_issue"])],
    )
    issue_conn_obj_perm_required: relay.Connection[IssueType] = gql.django.connection(
        directives=[HasObjPerm(perms=["demo.view_issue"])],
    )

    @gql.django.field
    def me(self, info: Info) -> Optional[UserType]:
        user = info.context.request.user
        if not user.is_authenticated:
            return None

        return cast(UserType, user)

    @gql.django.connection
    def project_conn_with_resolver(self, root: str, name: str) -> Iterable[ProjectType]:
        return cast(Iterable[ProjectType], Project.objects.filter(name__contains=name))


@gql.type
class Mutation:
    """All available mutations for this schema."""

    create_issue: IssueType = gql.django.create_mutation(IssueInput)
    update_issue: IssueType = gql.django.update_mutation(IssueInputPartial)
    delete_issue: IssueType = gql.django.delete_mutation(gql.NodeInput)

    update_project: ProjectType = gql.django.update_mutation(ProjectInputPartial)

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

    @gql.django.input_mutation
    def create_quiz(self, info: Info, title: str, full_clean_options: bool = False) -> QuizType:
        return cast(
            QuizType,
            resolvers.create(
                info,
                Quiz,
                {"title": title},
                full_clean={"exclude": ["sequence"]} if full_clean_options else True,
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
