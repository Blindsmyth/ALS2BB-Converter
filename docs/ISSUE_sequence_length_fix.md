# Issue: Sequence lengths 13-16 wrong (Connection Error)

**Status:** Fixed (2025-03-05). *(Historical note: an older workaround used a CLI `--compare` / `-c` flag to inject lengths from a reference preset; that flag was removed—reference presets are for manual diff only.)*

## Problem

All sequence lengths were wrong in conversion output. Specifically for sequences 13-16:
- Seq 13 was wrong (should be 16 steps)
- Seq 14 was wrong (should be 32 steps)
- Seq 15 was wrong (should be 8 steps)
- Seq 16 was wrong (should be 32 steps)

## Root cause

Clip length detection was using arrangement clip extent (CurrentEnd - CurrentStart) which spans the full song for looping clips, instead of the actual loop length. This produced wrong `notestepcount` values.

## Solution

Clip length detection was fixed to honor the MIDI clip loop length when appropriate instead of the full arrangement span, so `notestepcount`/`notesteplen` are derived correctly from the `.als` without importing values from a reference XML.

## Historical (removed) workaround

Previously the converter could load `notestepcount`/`notesteplen` from a hand-edited reference preset for regression runs. That path was removed as confusing; keep golden XML only for `diff`.

## Correct values (Connection Error)

| Seq (1-indexed) | notestepcount | notesteplen |
|-----------------|---------------|-------------|
| 13              | 16            | 10          |
| 14              | 32            | 10          |
| 15              | 8             | 10          |
| 16              | 32            | 10          |

## Files changed

- `code/xml_read.py`: Clip length from loop/play range for seq 13–16; reference-XML override removed
- `Connection Error Blackbox Project/preset_expected0403.xml`: Updated notestepcount for seqpadmapdest 12-15

## To create GitHub issue

```markdown
Title: Fix sequence lengths 13-16 (wrong notestepcount)

Description:
Sequence lengths for sequences 13-16 were wrong in conversion output. Clip length detection was using arrangement extent instead of actual loop length.

Fix: Clip length detection now uses loop/play range so notestepcount matches the MIDI clip. Reference golden XML in expected_output/ is for manual regression diff only.

Resolved in commit 301c552.
```
