# Converter testing workflow (two-repo)

This is a short summary of how we test converter changes. The full workflow is in the Cursor rule **converter-testing-workflow** (`.cursor/rules/converter-testing-workflow.mdc` in the workspace that has the converter).

## Summary

- **Two repos**: (1) Converter repo (this repo – code only). (2) **Conversion output repo** (separate) – one folder per song (e.g. `Connection Error/`) with latest preset + samples.
- **Link**: Every output commit must include **`converter_link.txt`** in the song folder (converter repo URL + commit hash) and the converter commit hash in the output repo commit message. So we can always “go back” to the exact code that produced a given output.
- **Flow**: Work on a **branch** → commit with clear intentions + `[OUTPUT NOT HUMAN-TESTED]` → run conversion → copy output into output repo song folder + write `converter_link.txt` → commit and push output repo (message references converter hash) → push converter branch → **user tests on hardware** and reports **issues** → we fix and repeat → **merge to main** only when everything is fixed.
- **Go back**: From output repo, open `converter_link.txt` or the commit message to get the converter hash; in converter repo run `git checkout <hash>`. From converter repo, search output repo history for that hash and check out the matching output commit.

See the Cursor rule for the full steps and conventions.
