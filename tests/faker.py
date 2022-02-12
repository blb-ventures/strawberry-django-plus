from typing import Generic, List, TypeVar

import factory

from demo.models import Issue, Milestone, Project, Tag

_T = TypeVar("_T")


class _BaseFactory(Generic[_T], factory.django.DjangoModelFactory):
    @classmethod
    def create(cls, **kwargs) -> _T:
        return super().create(**kwargs)

    @classmethod
    def create_batch(cls, size: int, **kwargs) -> List[_T]:
        return super().create_batch(size, **kwargs)


class ProjectFactory(_BaseFactory[Project]):
    class Meta:
        model = Project

    name = factory.Sequence(lambda n: f"Project {n}")
    due_date = factory.Faker("future_date")


class MilestoneFactory(_BaseFactory[Milestone]):
    class Meta:
        model = Milestone

    name = factory.Sequence(lambda n: f"Milestone {n}")
    due_date = factory.Faker("future_date")
    project = factory.SubFactory(ProjectFactory)


class IssueFactory(_BaseFactory[Issue]):
    class Meta:
        model = Issue

    name = factory.Sequence(lambda n: f"Issue {n}")
    kind = factory.Iterator(Issue.Kind)
    milestone = factory.SubFactory(MilestoneFactory)
    priority = factory.Faker("pyint", min_value=0, max_value=5)


class TagFactory(_BaseFactory[Tag]):
    class Meta:
        model = Tag

    name = factory.Sequence(lambda n: f"Tag {n}")
