---
applyTo: "**"
---
# Docstrings and comments

The line-break rule under "Key conventions" applies to every language in this repo.
The rest of this file is Python-specific.

This file is kept in step with [the same file in the FlexMeasures repo](https://github.com/FlexMeasures/flexmeasures/blob/main/.github/instructions/docstrings.instructions.md),
so that a change reads the same in both.

## Python docstrings

All public functions, methods, and classes must have docstrings. Use RST (reStructuredText) format for Sphinx compatibility.

## Standard function docstring

```python
def function_name(param1: str, param2: int) -> bool:
    """One-line summary ending with a period.

    Longer description explaining purpose and usage when needed.
    Parameter descriptions are aligned,
    and separated from the colon with at least one space.

    :param param1:      Description of param1.
    :param param2:      Description of param2.
    :returns:           Description of return value.
    :raises ValueError: When param1 is empty.
    """
```

## Key conventions

- One-line summary ends with a period and fits on one line.
- Leave one blank line between the summary and the extended description.
- Align `:param name:` descriptions using spaces (not tabs) for readability.
- Complement type hints — don't duplicate them in the docstring text.
- Use exactly one space after punctuation (no double spaces after periods).
- Use line breaks only after punctuation (this facilitates review commenting and text searching). This applies to in-line comments as well, not only docstrings; keep in-line comments short. So never wrap a docstring or comment in the middle of a phrase; each physical line must end at a comma, semicolon, colon, or period. (`E501` is ignored here, as black formats the code itself, so a long clause on one line is preferred over a mid-phrase break.)

## For code review (incl. automated reviewers)

The line-break-after-punctuation rule above is a deliberate, load-bearing convention (stable review comments and text search), not a stylistic nicety. When reviewing a change, always report a docstring or comment that wraps mid-phrase as a review comment; do not treat such a finding as low-confidence, and do not omit or suppress it.

## Comments

Comments explain *why*, not *what*. Avoid redundant comments that just restate the code. Include issue numbers or external links in TODO comments when relevant.
