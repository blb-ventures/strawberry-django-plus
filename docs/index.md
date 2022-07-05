# strawberry-django-plus

![Logo](./images/logo.png){ align=left }

Enhanced Strawberry integration with Django.

Built on top of [strawberry-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
integration, enhancing its overall functionality.
___

[![build status](https://img.shields.io/endpoint.svg?url=https%3A%2F%2Factions-badge.atrox.dev%2Fblb-ventures%2Fstrawberry-django-plus%2Fbadge%3Fref%3Dmain&style=flat)](https://actions-badge.atrox.dev/blb-ventures/strawberry-django-plus/goto?ref=main)
[![coverage](https://img.shields.io/codecov/c/github/blb-ventures/strawberry-django-plus.svg)](https://codecov.io/gh/blb-ventures/strawberry-django-plus)
[![downloads](https://pepy.tech/badge/strawberry-django-plus)](https://pepy.tech/project/strawberry-django-plus)
[![PyPI version](https://img.shields.io/pypi/v/strawberry-django-plus.svg)](https://pypi.org/project/strawberry-django-plus/)
![python version](https://img.shields.io/pypi/pyversions/strawberry-django-plus.svg)
![django version](https://img.shields.io/pypi/djversions/strawberry-django-plus.svg)

## Features

* [x] All supported features by `strawberry` and `strawberry-django`.
* [x] [Query optimizer extension](#query-optimizer-extension) that automatically optimizes querysets
  (using `only`/`select_related`/`prefetch_related`) to solve graphql `N+1` problems, with support
  for fragment spread, inline fragments, `@include`/`@skip` directives, prefetch merging, etc
* [x] [Django choices enums using](#django-choices-enums) support for better enum typing (requires
  [django-choices-field](https://github.com/bellini666/django-choices-field))
* [x] [Permissioned resolvers](#permissioned-resolvers) using schema directives, supporting both
  [django authentication system](https://docs.djangoproject.com/en/4.0/topics/auth/default/),
  direct and per-object permission checking for backends that implement those (e.g.
  [django-guardian](https://django-guardian.readthedocs.io/en/stable])).
* [x] [Mutations for Django](#django-mutations), with CRUD support and automatic errors validation.
* [x] [Relay support](#relay-support) for queries, connections and input mutations, all integrated with
  django types directly.
* [x] [Django Debug Toolbar integration](#django-debug-toolbar-integration) with graphiql to
  display metrics like SQL queries
* [x] Improved sync/async resolver that priorizes the model's cache to avoid have to use
  [sync_to_async](https://docs.djangoproject.com/en/4.0/topics/async/#asgiref.sync.sync_to_async)
  when not needed.
* [x] A well typed and documented API.

## Installation

Install with pip:

```shell
pip install strawberry-django-plus
```

You can now jump to the [quickstart](QuickStart.md).
