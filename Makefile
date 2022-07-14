.PHONY : install test serve lint

POETRY := $(shell command -v poetry 2> /dev/null)
MKDOCS := $(shell command -v mkdocs 2> /dev/null)

all: install test serve

install:
	${POETRY} install

test:
	${POETRY} run pytest


serve-docs:
	${POETRY} install
	${MKDOCS} serve
