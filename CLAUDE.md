# FlexMeasures Client — conventions for coding agents

The repo's conventions for agents live in [`.github/agents/copilot.md`](.github/agents/copilot.md)
and in [`.github/instructions/`](.github/instructions/) as `*.instructions.md` files,
their `applyTo:` frontmatter mapping them to file globs.
They are written for GitHub Copilot, but they are the source of truth for **any** agent working here:
read the ones relevant to the files you touch, and follow them.

Most-missed rules, called out so they are not forgotten:

- **Give each pull request a milestone**: the first release that will ship it.
  Re-check it before merging, and move it to the patch release when backporting.
  See [`milestones.instructions.md`](.github/instructions/milestones.instructions.md).
- **Add a changelog entry** under **Unreleased** for changes users notice,
  in `CHANGELOG.rst`, or in `CHANGELOG-S2.rst` for the S2 integration.
- **Docstrings and comments break lines only after punctuation** — never wrap in the middle of a phrase.
  Each physical line ends at a comma, semicolon, colon, or period.
  `E501` is ignored, so prefer a long clause on one line over a mid-phrase break.
  This keeps review comments and text search stable.
  See [`docstrings.instructions.md`](.github/instructions/docstrings.instructions.md).
