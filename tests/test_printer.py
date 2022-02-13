import os

from strawberry_django_plus.utils.printer import print_schema


def test_printer():
    from demo.schema import schema

    schema_output = print_schema(schema).strip("\n").strip(" ")
    output = os.path.join(os.path.dirname(__file__), "data", "schema.gql")
    if not os.path.exists(output):  # pragma:nocover
        with open(output, "w") as f:
            f.write(schema_output + "\n")
    else:
        with open(output) as f:
            expected = f.read().strip("\n").strip(" ")

        assert schema_output == expected
