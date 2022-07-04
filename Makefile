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
	poetry export -f requirements.txt --output requirements.txt --without-hashes
	python3 -m pip install -r requirements.txt
	python3 -m pip install mkdocs-material-extensions
	python3 -m pip install mkdocs # for some reason it is not installed by poetry
	python3 -m mkdocs gh-deploy --force
	rm requirements.txt