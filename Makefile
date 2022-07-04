.PHONY : install test serve deploy-docs

POETRY := $(shell command -v poetry 2> /dev/null)
MKDOCS := $(shell command -v mkdocs 2> /dev/null)

all: install test serve

install:
	${POETRY} install

test:
	${POETRY} run pytest


serve:
	poetry install --extras "docs"
	${MKDOCS} serve

deploy-docs:
	python3 -m pip install poetry
	poetry install -E "docs"
	poetry run pip install mkdocs # for some reason it is not installed by poetry
	mkdocs gh-deploy --force