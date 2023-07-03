!!! warning

    All the extra features provided by this lib were contributed and merged directly
    into the official
    [strawberry-graphql-django](https://github.com/strawberry-graphql/strawberry-graphql-django)
    lib. Since then this lib is deprecated and the official integration should be used instead.

    If you were using this lib before, check out the
    [migration guide](migration-guide#migrating-to-strawberry-django) for more information
    on how to migrate your code.

We use [poetry](https://github.com/sdispater/poetry) to manage dependencies, to
get started follow these steps:

```shell
git clone https://github.com/blb-ventures/strawberry-django-plus
cd strawberry-django-plus
poetry install
poetry run pytest
```

This will install all the dependencies (including dev ones) and run the tests.

### Pre commit

We have a configuration for
[pre-commit](https://github.com/pre-commit/pre-commit), to add the hook run the
following command:

```shell
pre-commit install
```

### Docs setup and local server:

We use Material for MkDocs, you can read the documentation [here](https://squidfunk.github.io/mkdocs-material/)

```shell
make serve-docs
```
