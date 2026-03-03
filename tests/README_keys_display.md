# Keys vs Pads Display Logic Test

## Logic

Multiple distinct pitches on the **same chan** (same pad) → Keys display on Blackbox.
Different chans (different pads) → Pads display (drum pattern).

## Test Preset: `keys_display_logic_test.xml`

| Col | Case | Notes | Expected |
|-----|------|-------|----------|
| 0 | A | chan 256, pitches [0,0] | OK |
| 1 | B | chan 257, pitches [36,37,40] | Keys risk |
| 2 | C | chan 256,257,258 each pitch 0 (multi-pad) | OK |
| 3 | D | chan 259, pitch [13] | OK |
| 4 | E | chan 256 pitch 36, chan 257 pitch 38 (kick+snare) | OK |

## Run

```bash
python tests/verify_pads_display.py tests/keys_display_logic_test.xml
```

Exit 0 = all OK. Exit 1 = at least one Keys risk found.

Use on converter output:
```bash
python tests/verify_pads_display.py "path/to/preset.xml"
```
