import inspect
from typing import Any, Awaitable, Callable, Coroutine, Union, cast

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware


@sync_and_async_middleware
def user_warmup_middleware(
    get_response: Callable[[HttpRequest], Union[HttpResponse, Coroutine[Any, Any, HttpResponse]]],
):
    if inspect.iscoroutinefunction(get_response):

        async def middleware(request):  # type: ignore
            # Warm up user object in sync context
            await sync_to_async(getattr)(request.user, "is_anonymous")
            return await cast(Awaitable, get_response(request))

    else:

        def middleware(request):
            # Warm up user object in sync context
            request.user.is_anonymous  # noqa: B018
            return get_response(request)

    return middleware
