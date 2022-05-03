import dataclasses
import sys
from typing import Any, Dict, cast
import weakref

from graphql.language.directive_locations import DirectiveLocation
from graphql.language.printer import print_ast
from graphql.type.definition import GraphQLArgument
from graphql.type.directives import GraphQLDirective
from graphql.utilities.ast_from_value import ast_from_value
from graphql.utilities.print_schema import print_directive
from strawberry import Schema, printer
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument
from strawberry.directive import StrawberryDirective
from strawberry.field import StrawberryField
from strawberry.private import is_private
from strawberry.schema.base import BaseSchema
from strawberry.type import StrawberryContainer

_original_print_schema = printer.print_schema
_directives = weakref.WeakKeyDictionary()
_extra_types = weakref.WeakKeyDictionary()


def _serialize_dataclasses(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [_serialize_dataclasses(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_dataclasses(v) for k, v in value.items()}

    return value


def _print_schema_directive_arg(directive: Any, name: str, arg: GraphQLArgument):
    value = getattr(directive, name, UNSET)
    if value is UNSET:
        return None

    ast = ast_from_value(_serialize_dataclasses(value), arg.type)
    return ast and f"{name}: {print_ast(ast)}"


def _print_schema_directive_args(directive: Any, args: Dict[str, GraphQLArgument]):
    printed = [
        p for name, arg in args.items() if (p := _print_schema_directive_arg(directive, name, arg))
    ]
    return f'({", ".join(printed)})' if printed else ""


def _print_schema_directive(directive: Any, schema: Schema) -> str:
    cls = directive.__class__
    strawberry_directive = cast(StrawberryDirective, cls.__strawberry_directive__)
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

        f_type = StrawberryAnnotation(field.type, namespace=module.__dict__).resolve()
        while isinstance(f_type, StrawberryContainer):
            f_type = f_type.of_type
        _extra_types.setdefault(schema, set()).add(f_type)

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
        name=name_converter.from_directive(strawberry_directive),
        locations=[DirectiveLocation(loc.value) for loc in strawberry_directive.locations],
        is_repeatable=False,
        args=args,
        description=strawberry_directive.description,
    )
    _directives.setdefault(schema, set()).add(print_directive(d))

    return f" @{d.name}{_print_schema_directive_args(directive, args)}"


def print_schema(schema: BaseSchema) -> str:
    graphql_core_schema = schema._schema  # type: ignore
    parts = [_original_print_schema(schema)]

    extra_parts = []
    for type_ in _extra_types.get(schema, []):
        try:
            type_ = schema.schema_converter.from_type(type_)  # type:ignore
        except TypeError:
            continue

        if type_.name not in graphql_core_schema.type_map:
            extra_parts.append(printer._print_type(type_, schema))

    extra_parts.sort()
    directives = _directives.get(schema)
    if directives:
        extra_parts.insert(0, "\n\n".join(sorted(directives)))

    return "\n\n".join(extra_parts + parts)


printer.print_schema_directive = _print_schema_directive
printer.print_schema = print_schema
