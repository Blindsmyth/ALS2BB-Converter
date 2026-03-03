# Output Verification Workflow

## Why This Matters

Physical testing on the Blackbox requires ejecting the SD card and loading the preset. Each iteration is costly. **Always verify output XML before declaring a fix complete**—especially when fixing sequence mode (Keys vs Pads) issues.

## Pads Mode vs Keys Mode in the XML

The device display (Pads = pad numbers 0–15 vs Keys = piano roll) is determined by **chan** and **pitch** (hardware verified):

| Condition | Display |
|-----------|---------|
| `chan` 0–15 | Always Keys |
| `chan` 256–271 + `pitch` 0–15 | Pads |
| `chan` 256–271 + `pitch` 36+ (MIDI) | Keys (pitch overrides) |

### The Critical Rule

For Pads display: use `chan = 256 + pad` and `pitch = 0` (or 0–15). **Never** use MIDI pitch values (36, 37, 40, 60, etc.) — they force Keys display even with Pads chan.

### How to Verify in the Output XML

1. Open the generated `preset.xml`.
2. Find `type="noteseq"` cells for the sequence grid (layer="1").
3. For each cell with notes, inspect `<seqevent>` elements in its `<sequence>`:
   - **Pads OK**: All `pitch` values are the same (e.g. all `pitch="0"` or all `pitch="13"`).
   - **Keys BUG**: Different `pitch` values (e.g. 36, 37, 40, 38) → device will show Keys mode.

### Example: Good (Pads)

```xml
<cell row="3" column="1" layer="1" seqsublayer="0" type="noteseq">
    <params seqpadmapdest="13" seqstepmode="1" ... />
    <sequence>
        <seqevent step="0" chan="269" type="note" pitch="0" ... />
        <seqevent step="0" chan="269" type="note" pitch="0" ... />
    </sequence>
</cell>
```

All notes have `pitch="0"` → Pads display.

### Example: Bad (Keys)

```xml
<cell row="3" column="1" layer="1" type="noteseq">
    <sequence>
        <seqevent pitch="36" ... />
        <seqevent pitch="37" ... />
        <seqevent pitch="40" ... />
    </sequence>
</cell>
```

Multiple different pitches (36, 37, 40) → Keys display → wrong for drum pad sequences.

## When to Run Verification

1. **When fixing sequence mode bugs** (e.g. "seq 13–16 showing as Keys"): Run conversion, then inspect the output XML for the affected cells (row/column from track index) and confirm single pitch per cell.
2. **Before merging or pushing**: If an expected preset exists, run with `-c <path>`. Also spot-check noteseq cells for pitch consistency when the fix touches sequence logic.
3. **After any change to** `make_drum_rack_sequences`, `event_pitch`, `event_chan`, or note filtering.

## Checklist for Sequence Mode Fixes

- [ ] Run conversion
- [ ] Open output `preset.xml`
- [ ] Locate noteseq cells for sequences in question (e.g. row 3 col 1–3 for pads 13–15)
- [ ] Confirm all seqevents in each cell share the same `pitch` value
- [ ] If any cell has mixed pitches (36, 37, 40, …), the fix is incomplete—do not merge

## Reference: Cell Location to Pad Mapping

- `row` and `column` map to pad index: pad = row*4 + column
- Pads 13, 14, 15 → row=3, col=1/2/3
- See `docs/BLACKBOX_TECHNICAL_REFERENCE.md` for chan (256+pad) and seqstepmode details.
