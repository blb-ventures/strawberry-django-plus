from django.conf.urls import url
from strawberry.django.views import AsyncGraphQLView, GraphQLView

from .schema import schema

urlpatterns = [
    url(r"^graphql/", GraphQLView.as_view(schema=schema)),
    url(r"^graphql_async/", AsyncGraphQLView.as_view(schema=schema)),
]
