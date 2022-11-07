import dataclasses
from typing import TYPE_CHECKING, Final, List, Optional

from django.conf import settings

__all__ = [
    "NAMESPACE",
    "Config",
    "config",
]

NAMESPACE: Final = "STRAWBERRY_DJANGO"


@dataclasses.dataclass
class Config:
    """Settings for this extension.

    The configs here are expected to be set on django like:

        "STRAWBERRY_DJANGO_<ATTNAME>"

    """

    RELAY_MAX_RESULTS: Optional[int] = dataclasses.field(default=100)
    REMOVE_DUPLICATED_SUFFIX: List[str] = dataclasses.field(
        default_factory=lambda: ["Input", "Partial"],
    )
    FIELDS_USE_GLOBAL_ID: bool = dataclasses.field(default=True)
    GENERATE_ENUMS_FROM_CHOICES: bool = dataclasses.field(default=False)

    # Trick type checking into thinking that we only have the defined configs
    if not TYPE_CHECKING:

        def __getattribute__(self, attr: str):
            config_name = f"{NAMESPACE}_{attr}"
            if hasattr(settings, config_name):
                return getattr(settings, config_name)
            return object.__getattribute__(self, attr)


config: Final = Config()
