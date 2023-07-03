!!! warning

    All the extra features provided by this lib were contributed and merged directly
    into the official
    [strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
    lib. Since then this lib is deprecated and the official integration should be used instead.

    If you were using this lib before, check out the
    [migration guide](migration-guide#migrating-to-strawberry-django) for more information
    on how to migrate your code.

!!! tip

    Since version 3.0.0 this feature was removed from this lib due to it being merged on
    [strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django),
    and should now be used from there.
    Check its [docs](https://strawberry-graphql.github.io/strawberry-graphql-django/guides/debug-toolbar/)
    for more information.

This integration provides integration between the
[Django Debug Toolbar](https://github.com/jazzband/django-debug-toolbar) and
`strawberry`, allowing it to display stats like `SQL Queries`, `CPU Time`, `Cache Hits`, etc
for queries and mutations done inside the [graphiql page](https://github.com/graphql/graphiql).

To use it, make sure you have the
[Django Debug Toolbar](https://github.com/jazzband/django-debug-toolbar) installed
and configured, then change its middleware settings from:

```python
MIDDLEWARE = [
    ...
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    ...
]
```

To:

```python
MIDDLEWARE = [
    ...
    "strawberry_django_plus.middlewares.debug_toolbar.DebugToolbarMiddleware",
    ...
]
```

Finally, ensure app `"strawberry_django_plus"` is added to your `INSTALLED_APPS` in Django settings.
