# strawberry-django-plus

[![build status](https://img.shields.io/endpoint.svg?url=https%3A%2F%2Factions-badge.atrox.dev%2Fblb-ventures%2Fstrawberry-django-plus%2Fbadge%3Fref%3Dmain&style=flat)](https://actions-badge.atrox.dev/blb-ventures/strawberry-django-plus/goto?ref=main)
[![coverage](https://img.shields.io/codecov/c/github/blb-ventures/strawberry-django-plus.svg)](https://codecov.io/gh/blb-ventures/strawberry-django-plus)
[![downloads](https://pepy.tech/badge/strawberry-django-plus)](https://pepy.tech/project/strawberry-django-plus)
[![PyPI version](https://img.shields.io/pypi/v/strawberry-django-plus.svg)](https://pypi.org/project/strawberry-django-plus/)
![python version](https://img.shields.io/pypi/pyversions/strawberry-django-plus.svg)
![django version](https://img.shields.io/pypi/djversions/strawberry-django-plus.svg)

Enhanced Strawberry integration with Django.

Built on top of [strawberry-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
integration, enhancing its overall functionality.

Check the [docs](https://blb-ventures.github.io/strawberry-django-plus/)
for information on how to use this lib.

## Features

- All supported features by `strawberry` and `strawberry-django`, with proper typing and
  documentation.
- [Query optimizer extension](https://blb-ventures.github.io/strawberry-django-plus/query-optimizer/)
  that automatically optimizes querysets
  (using `only`/`select_related`/`prefetch_related`) to solve graphql `N+1` problems, with support
  for fragment spread, inline fragments, `@include`/`@skip` directives, prefetch merging, etc
- [Django choices enums using](https://blb-ventures.github.io/strawberry-django-plus/quickstart/#django-choices-enums)
  support for better enum typing (requires
  [django-choices-field](https://github.com/bellini666/django-choices-field))
- [Permissioned resolvers](https://blb-ventures.github.io/strawberry-django-plus/quickstart/#permissioned-resolvers)
  using schema directives, supporting both
  [django authentication system](https://docs.djangoproject.com/en/4.0/topics/auth/default/),
  direct and per-object permission checking for backends that implement those (e.g.
  [django-guardian](https://django-guardian.readthedocs.io/en/stable/)).
- [Mutations for Django](https://blb-ventures.github.io/strawberry-django-plus/mutations/),
  with CRUD support and automatic errors validation.
- [Relay support](https://blb-ventures.github.io/strawberry-django-plus/quickstart/#relay-support)
  for queries, connections and input mutations, using the official strawberry's
  [relay integration](https://strawberry.rocks/docs/guides/relay)
- [Django Debug Toolbar integration](https://blb-ventures.github.io/strawberry-django-plus/debug-toolbar/)
  with graphiql to display metrics like SQL queries
- Improved sync/async resolver that priorizes the model's cache to avoid have to use
  [sync_to_async](https://docs.djangoproject.com/en/4.0/topics/async/#asgiref.sync.sync_to_async)
  when not needed.

## Installation

```shell
pip install strawberry-django-plus
```

## Licensing

The code in this project is licensed under MIT license. See [LICENSE](./LICENSE)
for more information.

## Stats

![Recent Activity](https://images.repography.com/23718985/blb-ventures/strawberry-django-plus/recent-activity/bf7c25def67510b494ac7981e0f4082c.svg)
