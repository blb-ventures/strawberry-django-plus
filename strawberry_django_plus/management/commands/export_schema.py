import pathlib
import sys

from django.core.management.base import BaseCommand, CommandError
from strawberry import Schema
from strawberry.printer import print_schema
from strawberry.utils.importer import import_module_symbol


class Command(BaseCommand):
    help = "Export the graphql schema"  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument("schema", nargs=1, type=str, help="The schema location")
        parser.add_argument("--path", nargs="?", type=str, help="Optional path to export")
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Exit with a non-zero status if schema changes are missing and don't actually write"
                " them"
            ),
        )

    def handle(self, schema, path, check, **kwargs):
        try:
            schema_symbol = import_module_symbol(schema[0], default_symbol_name="schema")
        except (ImportError, AttributeError) as e:
            raise CommandError(str(e)) from e

        if not isinstance(schema_symbol, Schema):
            raise CommandError("The `schema` must be an instance of strawberry.Schema")

        schema_output = print_schema(schema_symbol)
        if check:
            if not path:
                raise CommandError("--path must be specified when using --check")
            try:
                with pathlib.Path(path).open() as f:
                    existing_schema = f.read()
            except (FileNotFoundError, PermissionError) as e:
                raise CommandError(str(e)) from e
            if schema_output != existing_schema:
                raise CommandError("GraphQL schema has changes")
            print("Schema file is up to date")  # noqa: T201
        elif path:
            with pathlib.Path(path).open("w") as f:
                f.write(schema_output)
        else:
            sys.stdout.write(schema_output)
