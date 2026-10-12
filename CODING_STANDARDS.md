# Coding Standards (review)

Rules the reviewer enforces. Mechanical ones (unused imports/variables) are wired
into `ruff check`; the rest are judgement calls.

- **Test the behavior change.** Every commit that changes behavior must add or
  update a test that fails when the change is reverted. Verify by reverting the
  change, confirming the test goes red, then restoring it. A behavior change
  backed only by an already-green suite is the common failure mode.
- **Document every regex.** Each `re.compile` carries an inline comment block
  with `Matches:` / `Captures:` / `Skips:` next to the pattern.
- **Style.** `%`-formatting, ASCII output, and comments only where they explain
  non-obvious intent.
