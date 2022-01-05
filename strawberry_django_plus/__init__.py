from strawberry import auto
from strawberry_django import types as _types
from strawberry_django.fields import types as _ftypes

# Monkey patch strawberry_django to use strawberry's auto
_ftypes.auto = auto
_types.auto = auto  # type:ignore
