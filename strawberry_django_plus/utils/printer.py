import dataclasses
import sys
from typing import Any, Dict, Iterable, Mapping
import weakref

from graphql.language.directive_locations import DirectiveLocation
from graphql.language.printer import print_ast
from graphql.type.definition import GraphQLArgument
from graphql.type.directives import GraphQLDirective
from graphql.utilities.ast_from_value import ast_from_value
from graphql.utilities.print_schema import print_directive
from strawberry import Schema, printer
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument, is_unset
from strawberry.field import StrawberryField
from strawberry.private import is_private
from strawberry.schema.base import BaseSchema
from strawberry.schema_directive import StrawberrySchemaDirective

_original_print_schema = printer.print_schema
_directives = weakref.WeakKeyDictionary()


def _normalize_dataclasses(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Iterable):
        return [_normalize_dataclasses(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _normalize_dataclasses(v) for k, v in value.items()}

    return value


def _print_schema_directive_arg(
    directive: StrawberrySchemaDirective,
    name: str,
    arg: GraphQLArgument,
):
    value = getattr(directive.instance, name, UNSET)
    if is_unset(value):
        return ""

    ast = ast_from_value(_normalize_dataclasses(value), arg.type)
    return ast and f"{name}: {print_ast(ast)}"


def _print_schema_directive_args(
    directive: StrawberrySchemaDirective,
    args: Dict[str, GraphQLArgument],
):
    printed = []
    for name, arg in args.items():
        p = _print_schema_directive_arg(directive, name, arg)
        if p:
            printed.append(p)

    if not printed:
        return ""

    return f'({", ".join(printed)})'


def _print_schema_directive(directive: StrawberrySchemaDirective, schema: Schema) -> str:
    cls = directive.wrap
    name_converter = schema.config.name_converter
    schema_converter = schema.schema_converter

    module = sys.modules[cls.__module__]

    _args: Dict[str, StrawberryArgument] = {}
    for field in dataclasses.fields(cls):
        if is_private(field.type):
            continue

        if isinstance(field, StrawberryField):
            default = field.default
        else:
            default = getattr(cls, field.name, UNSET)

        if default == dataclasses.MISSING:
            default = UNSET

        arg = StrawberryArgument(
            python_name=field.name,
            graphql_name=None,
            type_annotation=StrawberryAnnotation(
                annotation=field.type,
                namespace=module.__dict__,
            ),
            default=default,
        )
        _args[name_converter.from_argument(arg)] = arg

    args = {k: schema_converter.from_argument(v) for k, v in _args.items()}
    d = GraphQLDirective(
        name=name_converter.from_directive(directive),
        locations=[DirectiveLocation(loc.value) for loc in directive.locations],
        is_repeatable=False,
        args=args,
        description=directive.description,
    )
    _directives.setdefault(schema, set()).add(print_directive(d))

    return f" @{d.name}{_print_schema_directive_args(directive, args)}"


def print_schema(schema: BaseSchema) -> str:
    parts = [_original_print_schema(schema)]
    directives = _directives.get(schema)
    if directives:
        parts.insert(0, "\n\n".join(directives))
    return "\n\n".join(parts)


printer.print_schema_directive = _print_schema_directive
printer.print_schema = print_schema
