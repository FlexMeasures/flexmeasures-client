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


def merge_updates(
    updates: dict | None,
    keyword_updates: dict,
    kebab_case_keywords: bool = False,
) -> dict:
    """Combine fields to update, given as a dict and/or as keyword arguments.

    Dict keys are sent as given, since they can already be spelled the way the API expects.
    Keyword names are Python identifiers, so with ``kebab_case_keywords`` their underscores become hyphens,
    for endpoints whose fields are kebab-case (``depends_on`` becomes ``depends-on``).
    Field names are not checked otherwise, so that fields the server makes editable later need no client release.

    :raises ValueError: if no field is given, or if a field is given both ways.
    """
    merged = dict(updates or {})
    for name, value in keyword_updates.items():
        key = name.replace("_", "-") if kebab_case_keywords else name
        if key in merged:
            raise ValueError(
                f"Field {key!r} is given both in the updates dict and as a keyword argument."
            )
        merged[key] = value
    if not merged:
        raise ValueError("No fields to update were given.")
    return merged
