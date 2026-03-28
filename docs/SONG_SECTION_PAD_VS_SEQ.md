# Song sections: pad block vs seq block (UI vs playback)

## Symptom

A sequence can **appear** in song mode (e.g. pattern **A** on the grid for a section) but **not actually start or keep playing** when the song advances to that section.

This is not always a converter bug: it often reflects how the **Blackbox** applies two different **sceneitem** groups in the preset XML, and how **Ableton Live** is authored (**Seq** tracks vs **Pads** track).

## Two independent sceneitem groups

In each song **section** (`<cell type="section">`), the `<sequence>` contains `type="sceneitem"` entries in two ranges (see `BLACKBOX_TECHNICAL_REFERENCE.md` § Song Mode):

| XML `chan` range | Role in song mode | Typical source in **this** converter |
|------------------|-------------------|--------------------------------------|
| 0–15 (pad grid)  | Which **pad/slot** shows ON / Keep / OFF and **which sub-layer (A/B/…)** | **Seq** tracks: arrangement clips named `A`, `B`, …, `Keep`, etc. (`extract_pad_sections` → `pad_conds`) |
| 256–271          | **Seq 1–16** arm/hold in song mode | **Pads** track: MIDI notes `36+N−1` for Seq N, and clip names like `Keep 1, 2, 3` (`extract_seq_sections` → `seq_conds`) |

The firmware treats these **separately**. Showing pattern **A** on the pad grid does not, by itself, guarantee that the matching **seq channel** row was fired for that section.

## Why the converter does not merge them

`make_song_from_sections` builds the **seq block** only from `seq_conds` (Pads track). It does **not** copy `pad_conds` (Seq tracks) into the seq block when the Pads track left that slot at **off**.

That is intentional for regression tests: e.g. **Connection Error** golden `preset.xml` section **“2 Main”** has **`cond="1"`** on a **pad** channel and **all seq channels `cond="0"`** for that section. Auto-filling seq sceneitems from Seq-track clips would change that shape and break structural compare, and not every project needs seq-block rows for every armed pad.

## Fixing the Live project

For any section where a sequence **must** start or be held in song mode:

1. **Seq** tracks: arrangement clips with the right **`A` / `B` / …** (and **`Keep`** where needed) so the **pad block** matches what you want to **see**.
2. **Pads** track: in the same timeline region, either  
   - a MIDI note **`36 + (seq_index)`** with `seq_index` 0-based (i.e. Seq **1** → note **36**, Seq **2** → **37**, …), and/or  
   - a clip whose **name** follows the **Keep** convention so the right seq indices get `cond=2` on the **seq block**.

If the **`Keep`** clip name omits a seq number (or uses the wrong list), you can get a mismatch: the grid still reflects **Seq** track clips while **playback** does not carry or arm that seq in the seq block.

## Debugging converter output

When investigating “pad armed but no seq sceneitem”, run conversion with **DEBUG** logging enabled. For each section where arrangement arms a pad (`cond >= 1`) but **every** `silayer` for that seq index in `seq_conds` is still **0**, `xml_read.py` logs a **debug** line naming the section and the MIDI note / Keep expectation.

Ensure the process logger level is `DEBUG` (e.g. set the root logger or `xml_read` logger accordingly for your run).

## Related code

- `make_song_from_sections` — builds both sceneitem blocks from merged section dicts.
- `extract_pad_sections` — Seq tracks → `pad_conds`.
- `extract_seq_sections` — Pads track → `seq_conds`.
