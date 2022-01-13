# Based on https://github.com/flavors/django-graphiql-debug-toolbar

import collections
import json
from typing import Optional
import weakref

from debug_toolbar.middleware import DebugToolbarMiddleware as _DebugToolbarMiddleware
from debug_toolbar.middleware import _HTML_TYPES
from debug_toolbar.middleware import show_toolbar
from debug_toolbar.panels.sql import panel, tracking
from debug_toolbar.toolbar import DebugToolbar
from django.core.serializers.json import DjangoJSONEncoder
from django.http.request import HttpRequest
from django.http.response import HttpResponse
from django.template.loader import render_to_string
from django.utils.encoding import force_str
from strawberry.django.views import BaseView

_store_cache = weakref.WeakKeyDictionary()
_original_store = DebugToolbar.store


def _store(toolbar: DebugToolbar):
    _original_store(toolbar)
    _store_cache[toolbar.request] = toolbar.store_id


def _get_payload(request: HttpRequest, response: HttpResponse):
    store_id = _store_cache.get(request)
    if not store_id:
        return None

    toolbar: Optional[DebugToolbar] = DebugToolbar.fetch(store_id)
    if not toolbar:
        return None

    content = force_str(response.content, encoding=response.charset)
    payload = json.loads(content, object_pairs_hook=collections.OrderedDict)
    payload["debugToolbar"] = collections.OrderedDict([("panels", collections.OrderedDict())])
    payload["debugToolbar"]["storeId"] = toolbar.store_id

    for p in reversed(toolbar.enabled_panels):
        if p.panel_id == "TemplatesPanel":
            continue

        if p.has_content:
            title = p.title
        else:
            title = None

        sub = p.nav_subtitle
        payload["debugToolbar"]["panels"][p.panel_id] = {
            "title": title() if callable(title) else title,
            "subtitle": sub() if callable(sub) else sub,
        }

    return payload


DebugToolbar.store = _store  # type:ignore


def _wrap_cursor(connection, panel):
    c = type(connection)
    if hasattr(c, "_djdt_cursor"):
        return None

    c._djdt_cursor = c.cursor
    c._djdt_chunked_cursor = c.chunked_cursor

    def cursor(*args, **kwargs):
        return tracking.state.Wrapper(c._djdt_cursor(*args, **kwargs), args[0], panel)

    def chunked_cursor(*args, **kwargs):
        cursor = c._djdt_chunked_cursor(*args, **kwargs)
        if not isinstance(cursor, tracking.BaseCursorWrapper):
            return tracking.state.Wrapper(cursor, args[0], panel)
        return cursor

    c.cursor = cursor
    c.chunked_cursor = chunked_cursor

    return cursor


def _unwrap_cursor(connection):
    c = type(connection)
    if not hasattr(c, "_djdt_cursor"):
        return

    c.cursor = c._djdt_cursor
    c.chunked_cursor = c._djdt_chunked_cursor
    del c._djdt_cursor
    del c._djdt_chunked_cursor


# Patch wrap_cursor/unwrap_cursor so that they work with async views
# Are there any drawbacks to this?
tracking.wrap_cursor = _wrap_cursor
tracking.unwrap_cursor = _unwrap_cursor
panel.wrap_cursor = _wrap_cursor
panel.unwrap_cursor = _unwrap_cursor


class DebugToolbarMiddleware(_DebugToolbarMiddleware):
    sync_capable = True
    async_capable = True

    def __call__(self, request: HttpRequest):
        response = super().__call__(request)

        if not show_toolbar(request) or DebugToolbar.is_toolbar_request(request):
            return response

        content_type = response.get("Content-Type", "").split(";")[0]
        is_html = content_type in _HTML_TYPES
        is_graphiql = getattr(request, "_is_graphiql", False)

        if is_html and is_graphiql and response.status_code == 200:
            template = render_to_string("strawberry_django_plus/debug_toolbar.html")
            response.write(template)
            if "Content-Length" in response:
                response["Content-Length"] = len(response.content)

        if is_html or not is_graphiql or content_type != "application/json":
            return response

        payload = _get_payload(request, response)
        if payload is None:
            return response

        response.content = json.dumps(payload, cls=DjangoJSONEncoder)
        if "Content-Length" in response:
            response["Content-Length"] = len(response.content)

        return response

    def process_view(self, request: HttpRequest, view_func, *args, **kwargs):
        view = getattr(view_func, "view_class", None)
        request._is_graphiql = bool(view and issubclass(view, BaseView))  # type:ignore
