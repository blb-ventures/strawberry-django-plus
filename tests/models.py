from typing import TYPE_CHECKING, Optional

from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class Project(models.Model):
    milestones: "RelatedManager[Milestone]"

    id = models.BigAutoField(  # noqa: A003
        verbose_name="ID",
        primary_key=True,
    )
    name = models.CharField(
        max_length=255,
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        default=None,
    )
    cost = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )


class Milestone(models.Model):
    issues: "RelatedManager[Issue]"
    comment_set: "RelatedManager[MilestoneComment]"

    id = models.BigAutoField(  # noqa: A003
        verbose_name="ID",
        primary_key=True,
    )
    name = models.CharField(
        max_length=255,
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        default=None,
    )
    project_id: int
    project = models.ForeignKey[Project](
        Project,
        on_delete=models.CASCADE,
        related_name="milestones",
        related_query_name="milestone",
    )


class Issue(models.Model):
    comments: "RelatedManager[Issue]"

    class Kind(models.TextChoices):
        BUG = "b", "Bug"
        FEATURE = "f", "Feature"

    id = models.BigAutoField(  # noqa: A003
        verbose_name="ID",
        primary_key=True,
    )
    name = models.CharField(
        max_length=255,
    )
    kind = models.CharField(
        verbose_name="kind",
        help_text="the kind of the issue",
        choices=Kind.choices,
        max_length=max(len(k.value) for k in Kind),
        default=None,
        blank=True,
        null=True,
    )
    priority = models.IntegerField(
        default=0,
    )
    milestone_id: Optional[int]
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.SET_NULL,
        related_name="issues",
        related_query_name="issue",
        null=True,
        blank=True,
        default=None,
    )


class IssueComment(models.Model):
    id = models.BigAutoField(  # noqa: A003
        verbose_name="ID",
        primary_key=True,
    )
    issue_id: int
    issue = models.ForeignKey(
        Issue,
        on_delete=models.CASCADE,
        related_name="comments",
        related_query_name="comments",
    )
    comment = models.CharField(
        max_length=255,
    )


class MilestoneComment(models.Model):
    id = models.BigAutoField(  # noqa: A003
        verbose_name="ID",
        primary_key=True,
    )
    text = models.CharField(
        max_length=255,
    )
    milestone_id: int
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.CASCADE,
    )
