# Based on https://github.com/flavors/django-graphiql-debug-toolbar

import collections
import json
from typing import Optional
import weakref

from debug_toolbar.middleware import DebugToolbarMiddleware as _DebugToolbarMiddleware
from debug_toolbar.middleware import _HTML_TYPES
from debug_toolbar.middleware import show_toolbar
from debug_toolbar.panels.templates import TemplatesPanel
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

# FIXME: This is breaking async views when it tries to render the user
# without being in an async safe context. How to properly handle this?
TemplatesPanel._store_template_info = lambda *args, **kwargs: None


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
