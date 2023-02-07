import pathlib

from strawberry.printer import print_schema


def test_printer():
    from demo.schema import schema

    schema_output = print_schema(schema).strip("\n").strip(" ")
    output = pathlib.Path(__file__).parent / "data" / "schema.gql"
    if not output.exists():
        with output.open("w") as f:
            f.write(schema_output + "\n")

    with output.open() as f:
        expected = f.read().strip("\n").strip(" ")

    assert schema_output == expected
