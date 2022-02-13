import dataclasses
import sys
from typing import Any, Dict, Iterable, Mapping, cast
import weakref

from graphql.language.directive_locations import DirectiveLocation
from graphql.language.printer import print_ast
from graphql.type.definition import (
    GraphQLArgument,
    GraphQLInputObjectType,
    is_input_object_type,
    is_object_type,
)
from graphql.type.directives import GraphQLDirective
from graphql.utilities.ast_from_value import ast_from_value
from graphql.utilities.print_schema import (
    print_block,
    print_description,
    print_directive,
    print_input_value,
    print_type,
)
from strawberry import Schema, printer
from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import UNSET, StrawberryArgument, is_unset
from strawberry.field import StrawberryField
from strawberry.private import is_private
from strawberry.schema.base import BaseSchema
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import StrawberryContainer
from strawberry.types.types import TypeDefinition

_original_print_schema = printer.print_schema
_directives = weakref.WeakKeyDictionary()
_extra_types = weakref.WeakKeyDictionary()


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
        name=name_converter.from_directive(directive),
        locations=[DirectiveLocation(loc.value) for loc in directive.locations],
        is_repeatable=False,
        args=args,
        description=directive.description,
    )
    _directives.setdefault(schema, set()).add(print_directive(d))

    return f" @{d.name}{_print_schema_directive_args(directive, args)}"


def _print_input_object(type_: GraphQLInputObjectType, schema: BaseSchema) -> str:
    strawberry_type = cast(TypeDefinition, schema.get_type_by_name(type_.name))

    fields = []
    for i, (name, field) in enumerate(type_.fields.items()):
        strawberry_field = next(
            (
                f
                for f in strawberry_type.fields
                if name == schema.config.name_converter.get_graphql_name(f)
            ),
            None,
        )
        fields.append(
            print_description(field, "  ", not i)
            + "  "
            + print_input_value(name, field)
            + printer.print_field_directives(strawberry_field, schema=schema)
        )

    return (
        print_description(type_)
        + f"input {type_.name}"
        + printer.print_type_directives(type_, schema)
        + print_block(fields)
    )


def _print_type(field, schema: BaseSchema) -> str:
    if is_object_type(field):
        return printer._print_object(field, schema)
    elif is_input_object_type(field):
        return _print_input_object(field, schema)

    return print_type(field)


def print_schema(schema: BaseSchema) -> str:
    graphql_core_schema = schema._schema  # type: ignore
    parts = [_original_print_schema(schema)]

    for type_ in _extra_types.get(schema, []):
        try:
            type_ = schema.schema_converter.from_type(type_)  # type:ignore
        except TypeError:
            continue

        if type_.name not in graphql_core_schema.type_map:
            parts.insert(0, printer._print_type(type_, schema))

    directives = _directives.get(schema)
    if directives:
        parts.insert(0, "\n\n".join(sorted(directives)))

    return "\n\n".join(parts)


printer.print_schema_directive = _print_schema_directive
printer.print_schema = print_schema
printer._print_type = _print_type
