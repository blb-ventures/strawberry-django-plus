.PHONY : install test serve deploy-docs

POETRY := $(shell command -v poetry 2> /dev/null)
MKDOCS := $(shell command -v mkdocs 2> /dev/null)

all: install test serve


install:
	${POETRY} install

test:
	${POETRY} run pytest


serve:
	${POETRY} install --extras "docs"
	${MKDOCS} serve

deploy-docs:
	${POETRY} install --extras "docs"
	${MKDOCS} gh-deploy --force