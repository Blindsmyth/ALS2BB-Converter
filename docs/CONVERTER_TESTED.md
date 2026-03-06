# Converter: tested output and confirmed features

This file records **which converter output has been human-tested** and **which features are confirmed to work** on which commit. Use it to trace a known-good version when debugging regressions.

- **Untested:** Push + convert does *not* mean tested. Commits may be marked `[OUTPUT NOT HUMAN-TESTED]` until the user tests.
- **After user tests:** Update this file and (optionally) add a commit or tag like `[OUTPUT HUMAN-TESTED]` or `tested-YYYY-MM-DD`.

## Confirmed features (commit → what was verified)

| Commit     | What was tested / confirmed |
|-----------|-----------------------------|
| 8c33d36   | Song mode section layout (pads/seq ON per section) matches expected; Connection Error project. |
| *(add rows when user confirms a version)* | |

## Last tested commit

- **Last commit confirmed by user:** 8c33d36 *(update when you have tested a newer build)*
