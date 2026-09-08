"""Small shared utilities for the FlexMeasures client."""

from __future__ import annotations

import warnings
from collections.abc import Iterable

DeprecatedParameterAlias = tuple[str, object | None, str, object]


def apply_deprecated_parameter_aliases(
    instance: object,
    aliases: Iterable[DeprecatedParameterAlias],
) -> None:
    """Apply deprecated constructor arguments to their replacement attributes.

    Each alias contains the deprecated name and supplied value followed by the
    replacement name and its default value. ``None`` means that the deprecated
    argument was not supplied. Supplying both names with a non-default value is
    rejected because their precedence would otherwise be ambiguous.
    """
    for deprecated_name, deprecated_value, replacement_name, default in aliases:
        if deprecated_value is None:
            continue

        if getattr(instance, replacement_name) != default:
            raise TypeError(
                f"Pass either {replacement_name} or the deprecated "
                f"{deprecated_name}, not both."
            )

        warnings.warn(
            f"{deprecated_name} is deprecated; use {replacement_name} instead.",
            DeprecationWarning,
            stacklevel=4,
        )
        setattr(instance, replacement_name, deprecated_value)
