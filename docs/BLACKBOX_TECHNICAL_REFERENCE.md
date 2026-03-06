# Blackbox Technical Reference

This document extracts key technical information from the Blackbox User Manual 3.0 for use in the Ableton-to-Blackbox converter.

## Sequence Modes

### Pads Mode
- **Description**: In Pads Mode, the left edge of the piano roll lists the pad numbers (0-15)
- **Pitch**: In pads mode, each event's `pitch` value determines which pad is triggered (0-15)
- **Channel**: The `chan` parameter determines which pad gets triggered: `chan = 256 + pad_number`
  - Example: `chan=256` triggers pad 0, `chan=257` triggers pad 1, `chan=258` triggers pad 2, etc.
- **Note**: If you switch to/from Pads mode, any recorded events in all four patterns in that cell will be cleared

### Keys Mode
- **Description**: In Keys Mode, the piano roll has a chromatic keyboard along the left side
- **Pitch**: Pitch values represent MIDI notes (0-127)
- **Usage**: Each Keys sequence can record keys input for only one pad. This is selected using the selection grid that appears at top right in KEYS mode.
- **Channel**: Uses standard MIDI channel assignment

### MIDI Mode
- **Description**: Sends MIDI notes to external MIDI devices
- **Pitch**: Pitch values represent MIDI notes (0-127)
- **Channel**: Controlled by the sequence's `MIDIOut` parameter

## Step Length Values

The `notesteplen` parameter controls the length of time the sequence will spend on each step, in terms of fractions of a bar.

### Available Values (Non-Triplet):
- `0` = 8 Bars
- `1` = 4 Bars
- `2` = 2 Bars
- `3` = 1 Bar
- `4` = 1/2
- `6` = 1/4
- `8` = 1/8
- `10` = 1/16
- `12` = 1/32
- `14` = 1/64

### Triplet Values (Odd Numbers):
- `5` = 1/2T (triplet)
- `7` = 1/4T (triplet)
- `9` = 1/8T (triplet)
- `11` = 1/16T (triplet)
- `13` = 1/32T (triplet)

**Note**: The converter should use only non-triplet values (even numbers: 0, 1, 2, 3, 4, 6, 8, 10, 12, 14) unless specifically needed.

### Step Count
- **Range**: 1 to 256
- **Calculation**: `step_count = beats * steps_per_beat`
  - At 1/16: 1 beat = 4 steps, 1 bar (4 beats) = 16 steps
  - At 1/8: 1 beat = 2 steps, 1 bar = 8 steps
  - At 1/4: 1 beat = 1 step, 1 bar = 4 steps
  - At 1/2: 2 beats = 1 step, 1 bar = 2 steps
  - At 1 Bar: 4 beats = 1 step

**Default Strategy**: Start with 1/16 (step_len=10) and only use coarser resolutions when step_count exceeds 256.

## Sequence Parameters

### Global Sequence Parameters (apply to entire sequence cell):
- `DutyCycle`: Duration of note events (0% to 100%)
- `QuantSize`: When to start the sequence (1/64, 1/32T, 1/32, 1/16T, 1/16, 1/8T, 1/8, 1/4T, 1/4, 1/2T, 1/2, 1bar, 2bars)
- `StepMode`: Whether sequence notes are quantized to grid (ON/OFF)
- `MIDIOUT`: MIDI Channel for output (None, Ch1-Ch16)

### Per-Pattern Parameters (each pattern A-D can have different settings):
- `StepLen`: Length of time per step (see Step Length Values above)
- `StepCount`: Number of steps in sequence (1 to 256)

### Sequence Mode Parameter:
- `seqstepmode`: `1` = Pads Mode, `0` = Keys Mode (or MIDI Mode)
- `seqpadmapdest`: Pad number where this sequence is located (for pads mode)

## Sequence Events

### Display: Pads vs Keys View (Hardware Verified)

The device shows either **Pads** (pad numbers 0–15) or **Keys** (piano keyboard) in the sequence editor. This is determined by **chan** and **pitch**:

- **chan 0–15**: Always Keys layout (MIDI channel range)
- **chan 256–271**: Pads layout *unless* pitch is in MIDI range
  - **pitch 0–15**: Pads display (pad-like values)
  - **pitch 36+** (MIDI notes): Keys display — pitch overrides chan

**For Pads display**: Use `chan = 256 + pad` and `pitch = 0` (or 0–15). **Never** use raw MIDI pitch values (36, 37, 40, 60, etc.) when targeting Pads mode — the device will show Keys.

*Verified with chan_pitch_hw_test preset, March 2025.*

### Event Attributes:
- `step`: Step position in the sequence (0-based)
- `chan`: Channel number
  - **Pads Mode**: `chan = 256 + pad_number` (e.g., 256 for pad 0, 257 for pad 1)
  - **Keys/MIDI Mode**: Standard MIDI channel
- `type`: Event type (typically "note")
- `pitch`: Pitch value
  - **Pads Mode**: Use `0` (or 0–15) for Pads display. MIDI pitches (36+) force Keys display.
  - **Keys/MIDI Mode**: MIDI note number (0-127)
- `strtks`: Start time in ticks (1 beat = 3840 ticks)
- `lencount`: Length in step counts
- `lentks`: Length in ticks

## Pad Layout

The Blackbox has **16 pads** arranged in a 4x4 grid:

```
┌────┬────┬────┬────┐
│ 0  │ 1  │ 2  │ 3  │  Row 0
├────┼────┼────┼────┤
│ 4  │ 5  │ 6  │ 7  │  Row 1
├────┼────┼────┼────┤
│ 8  │ 9  │ 10 │ 11 │  Row 2
├────┼────┼────┼────┤
│ 12 │ 13 │ 14 │ 15 │  Row 3
└────┴────┴────┴────┘
Col:  0    1    2    3
```

**Note**: There are NO extra pads beyond the 16-pad grid (no pads 16-19).

## Pad Parameters

### Cell Mode
- `cellmode`: `0` = Sample mode, `1` = Clip mode
- Clip mode is used for synchronized loops and quantized playback

### Loop Mode
- `loopmode`: `0` = None, `1` = Forward (looping enabled)
- Used for both sample and clip mode pads

### Beat Count
- `beatcount`: Number of beats in the clip file (1-512, or Auto)
- Used for synchronizing playback to current song BPM

### Envelope Settings
Default values for clip mode pads:
- `envattack`: `0` (0% attack)
- `envdecay`: `1000` (100% decay)
- `envsus`: `1000` (100% sustain)
- `envrel`: `200` (20% release)

### Output Bus
- `outputbus`: `0` = Main output (Out1), `1` = Out2, `2` = Out3

### Choke Groups
- `chokegrp`: `0` = No choke (ExclX), `1-4` = Exclusion groups A-D

## MIDI Note Mappings

### Global In Channel Note Mappings:
- **Pads**: Notes 36-51 (C2-D#3) → Pads 0-15
- **Sequences**: Notes 52-67 (E3-G4) → Sequences 0-15
- **Song Scenes**: Notes 2-33 (D-1-A1) → Scenes 0-31

### Per-Pad MIDI In:
- Each pad can have its own `MIDIIn` channel specified
- When set, the pad responds to MIDI notes on that channel for chromatic playback

## Sequence Recording

### Recording Behavior:
- Sequences record to the length specified in sequence parameters, then loop back
- When recording, incoming notes are added to the sequence (additive)
- Previously existing notes are not removed unless you Clear first
- For a sequence to loop seamlessly, the `QuantSize` must be less than or equal to the length of the sequence

### Pattern Management:
- Each sequence cell has 4 patterns (A, B, C, D)
- All patterns in a cell have the same sequence mode (Keys/Pads/MIDI)
- Each pattern can have different `StepLen` and `StepCount`
- Only one pattern per sequence may play or be edited at a time

## Important Notes

1. **Pads Mode vs Keys Mode**:
   - In Pads Mode, `pitch` should always be `0`
   - The `chan` parameter (256 + pad_number) determines which pad is triggered
   - In Keys Mode, `pitch` represents the MIDI note number

2. **Step Length Default**:
   - Default to 1/16 (`notesteplen=10`) for highest resolution
   - Only use coarser resolutions when step_count exceeds 256

3. **Sequence Length**:
   - Length is determined by `StepCount * StepLen`
   - 1 bar = 4 beats
   - At 1/16: 1 bar = 16 steps
   - Maximum `StepCount` is 256

4. **Pad Numbering**:
   - Pads are numbered 0-15
   - Formula: `pad_number = row * 4 + column`
   - Row 0, Col 0 = Pad 0
   - Row 0, Col 1 = Pad 1
   - Row 3, Col 3 = Pad 15

5. **Sequence Events in Pads Mode**:
   - All events should have `pitch="0"`
   - `chan` should be `256 + pad_number` to trigger the correct pad
   - Example: To trigger pad 1, use `chan="257"` and `pitch="0"`

---

## Song Mode (Preset XML)

**Note:** In song mode, “chan” means section state (which pad/seq is ON or Keep). This is separate from sequence *note* events, where chan 256+ selects the pad in Pads mode.

Song mode is stored in **layer 2** cells with `type="section"`. Each section cell has a `<sequence>` containing **sceneitem** events (not note events). Channel numbers in this context mean something different from sequence note events.

### Section cells
- `<cell layer="2" type="section">` — one cell per song section (e.g. "0 Intro", "1 Beat").
- `params.sectionrepeats` — how many times the section repeats.
- The section’s `<sequence>` lists `<seqevent type="sceneitem">` entries that define which pads and sequences are ON, OFF, or Keep in that section.

### Channel mapping in song mode (sceneitem events)

| Chan in XML | Meaning (human) | Use |
|-------------|------------------|-----|
| (no `chan` attribute) | **Pad 1** | First pad state (silayer 0 or 1). |
| `chan="0"` … `chan="15"` | **Pad 1 … Pad 16** | Pad states: chan 0 = Pad 1, chan 3 = Pad 4, chan 15 = Pad 16. |
| `chan="256"` … `chan="271"` | **Seq 1 … Seq 16** | Sequence states: chan 256 = Seq 1, chan 259 = Seq 4, chan 271 = Seq 16. |

So in song mode:
- **Pads** are addressed by chan **0–15** (or no chan = Pad 1).
- **Seqs** are addressed by chan **256–271** (Seq 1–16).

This is the opposite of sequence *note* events, where chan 256+ is used to trigger pads in Pads mode.

### Sceneitem attributes
- `type="sceneitem"` — marks the event as a scene/section state, not a note.
- `chan` — as in the table above (omit for Pad 1).
- `silayer` — sub-layer:
  - `0` = main pad/seq (layer A).
  - `1` = layer B (e.g. Pad 15 layer B, Pad 16 layer B).
  - `4` = special “scene” block (e.g. no chan + silayer 4, or chan 15 silayer 4) used in some section layouts.
- `cond` — state:
  - `0` = off.
  - `1` = on (trigger in this section).
  - `2` = keep (continue from previous section).

### Layout variants
- **Standard layout**: Pad 1 (no chan), Pads 2–16 (chan 1–15), then Seq 1–16 (chan 256–271).
- **Alt layout** (e.g. “1 Beat”, “2 Main”): Pads as chan 1–14 or 1–15, then Seq 2–16 as chan 257–271, then a silayer 4 block (no chan cond 2, chan 256 cond 0, optionally chan 15 silayer 4 cond 0).

### Pad vs seq in song mode
- Pad state (chan 0–15) and seq state (chan 256–271) are separate.
- When a pad has **ON** (cond 1) in silayer 0, that pad “carries” the seq: the corresponding seq is not output (cleared). **Keep** (cond 2) does not clear the seq.
- Pads come from the “Pads” track in the converter; seqs come from the Seq tracks. They are written to different chan ranges in the preset XML as above.

