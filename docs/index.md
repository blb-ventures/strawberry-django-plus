# strawberry-django-plus

![Logo](./images/logo.png){ align=left }

Enhanced Strawberry integration with Django.

Built on top of [strawberry-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
integration, enhancing its overall functionality.

---

[![build status](https://img.shields.io/endpoint.svg?url=https%3A%2F%2Factions-badge.atrox.dev%2Fblb-ventures%2Fstrawberry-django-plus%2Fbadge%3Fref%3Dmain&style=flat)](https://actions-badge.atrox.dev/blb-ventures/strawberry-django-plus/goto?ref=main)
[![coverage](https://img.shields.io/codecov/c/github/blb-ventures/strawberry-django-plus.svg)](https://codecov.io/gh/blb-ventures/strawberry-django-plus)
[![downloads](https://pepy.tech/badge/strawberry-django-plus)](https://pepy.tech/project/strawberry-django-plus)
[![PyPI version](https://img.shields.io/pypi/v/strawberry-django-plus.svg)](https://pypi.org/project/strawberry-django-plus/)
![python version](https://img.shields.io/pypi/pyversions/strawberry-django-plus.svg)
![django version](https://img.shields.io/pypi/djversions/strawberry-django-plus.svg)

!!! warning

    All the extra features provided by this lib were contributed and merged directly
    into the official
    [strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
    lib. Since then this lib is deprecated and the official integration should be used instead
    and development will continue there!

    If you were using this lib before, check out the
    [migration guide](migration-guide#migrating-to-strawberry-django) for more information
    on how to migrate your code.

## Features

- [x] All supported features by `strawberry` and `strawberry-django`.
- [x] [Query optimizer extension](query-optimizer/)
      that automatically optimizes querysets
      (using `only`/`select_related`/`prefetch_related`) to solve graphql `N+1` problems, with support
      for fragment spread, inline fragments, `@include`/`@skip` directives, prefetch merging, etc
- [x] [Django choices enums using](quickstart/#django-choices-enums)
      support for better enum typing (requires
      [django-choices-field](https://github.com/bellini666/django-choices-field))
- [x] [Permissioned resolvers](quickstart/#permissioned-resolvers)
      using schema directives, supporting both
      [django authentication system](https://docs.djangoproject.com/en/4.0/topics/auth/default/),
      direct and per-object permission checking for backends that implement those (e.g.
      [django-guardian](https://django-guardian.readthedocs.io/en/stable)).
- [x] [Mutations for Django](mutations/),
      with CRUD support and automatic errors validation.
- [x] [Relay support](quickstart/#relay-support)
      for queries, connections and input mutations, all integrated with django types directly.
- [x] [Django Debug Toolbar integration](debug-toolbar/) with graphiql to
      display metrics like SQL queries
- [x] Improved sync/async resolver that priorizes the model's cache to avoid have to use
      [sync_to_async](https://docs.djangoproject.com/en/4.0/topics/async/#asgiref.sync.sync_to_async)
      when not needed.
- [x] A well typed and documented API.

## Installation

Install with pip:

```shell
pip install strawberry-django-plus
```

## How to

You can now jump to the [quickstart](quickstart.md) to learn how to use this lib.
