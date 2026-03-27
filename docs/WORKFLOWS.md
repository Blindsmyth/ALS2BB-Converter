# Ableton to Blackbox Converter - Complete Workflows Guide

## Overview

This document describes all workflows supported by the converter, from project setup to final Blackbox preset.

## Table of Contents

1. [Drum Rack Workflow](#drum-rack-workflow)
2. [Sequence Modes](#sequence-modes)
3. [Sample Handling](#sample-handling)
4. [Pad Mapping](#pad-mapping)
5. [Timing and Quantization](#timing-and-quantization)
6. [Warped Samples and Clip Mode](#warped-samples-and-clip-mode)

---

## Drum Rack Workflow

### Overview

The converter automatically detects and processes Drum Rack projects, mapping 16 Drum Rack pads to 16 Blackbox pads with full sequence support.

### Project Structure

```
Track 1: MIDI Track with Drum Rack
  ├─ Chain 0 → Pad 0: Simpler with sample
  ├─ Chain 1 → Pad 1: Simpler with sample
  ├─ ...
  └─ Chain 15 → Pad 15: Simpler with sample

Track 2-17: MIDI Tracks (for sequences)
  ├─ Clip Slot 0: MIDI Clip for sub-layer A
  ├─ Clip Slot 1: MIDI Clip for sub-layer B
  ├─ Clip Slot 2: MIDI Clip for sub-layer C
  └─ Clip Slot 3: MIDI Clip for sub-layer D
```

### Key Features

- **16 Pads**: Direct 1:1 mapping from Drum Rack chains to Blackbox pads
- **Chain Order Mapping**: Pads are mapped by chain order (Chain 0 → Pad 0, etc.)
- **Choke Groups**: Automatically extracted and mapped (Ableton 0-4 → Blackbox 0-4)
- **Warp Detection**: Automatically detects warped samples and enables clip mode
- **Multi-layer Sequences**: Supports up to 4 sub-layers (A/B/C/D) per pad

### Important: Chain Order vs MIDI Notes

**The converter uses CHAIN ORDER, not MIDI note mapping.**

Before converting:
1. Open your Drum Rack in Ableton
2. Arrange the **chains** (right side list) to match your desired pad order:
   - Chain 0 = Pad 1 (bottom-left)
   - Chain 1 = Pad 2
   - Chain 2 = Pad 3
   - Chain 3 = Pad 4 (bottom-right)
   - Chain 4 = Pad 5
   - ... and so on
3. Save your project
4. Run the converter

---

## Sequence Modes

The converter automatically detects and handles three sequence modes based on MIDI track routing.

### 1. Pads Mode (Default)

**When**: MIDI track routed to `TrackIn` (internal routing)

**Behavior**:
- Sequence triggers multiple pads
- `seqstepmode="1"` (Pads mode)
- `chan` = `256 + pad_number` for each note
- `pitch` = `0` (not used in Pads mode)
- `seqpadmapdest` = sequence location pad

**Use Case**: Drum patterns, multi-pad sequences

### 2. Keys Mode

**When**: MIDI track routed to a specific drum rack pad (e.g., `DeviceIn.0.B40`)

**Behavior**:
- Sequence plays melodic content on one pad
- For **quantised** sequences: `seqstepmode="1"`, `chan=256+target_pad`, `pitch=MIDI note`
- For **unquantised** sequences: `seqstepmode="0"`, `chan=256`, `pitch=MIDI note`
- `seqpadmapdest` = target pad number

**Use Case**: Basslines, melodic sequences routed to specific pads

### 3. MIDI Mode

**When**: MIDI track routed to external MIDI device (e.g., `External.Dev:IAC Driver`)

**Behavior**:
- Sequence sends MIDI to external devices
- `seqstepmode="0"` (MIDI mode)
- `chan` = MIDI channel (0-15)
- `pitch` = MIDI note (0-127)
- `midioutchan` = MIDI output channel
- `seqpadmapdest` = `0` (no pad destination)

**Use Case**: Controlling external synthesizers, MIDI hardware

---

## Sample Handling

### Automatic Sample Copying

By default, the converter:
1. Reads sample paths from the `.als` file
2. Copies all samples to the output folder
3. Updates references in `preset.xml` to use relative paths

**Samples can be anywhere** on your computer - the converter finds and copies them automatically.

### Manual Mode (`-m` flag)

Use `-m` flag to skip automatic copying:
```bash
python3 code/xml_read.py -i project.als -o output -m
```

**Use when**:
- Samples are missing or moved
- Testing conversion without copying files
- Project from different computer

### Sample Format

- **Supported**: `.wav` files only (Blackbox requirement)
- **Not Supported**: `.aif`, `.mp3`, `.flac`, `.ogg`

Convert non-WAV samples in Ableton before converting.

---

## Pad Mapping

### Blackbox Pad Layout

**Pads are numbered 1-16, starting from bottom-left:**

```
Row 0 (top):     13  14  15  16
Row 1:           9   10  11  12
Row 2:           5   6   7   8
Row 3 (bottom):  1   2   3   4
```

### Drum Rack Mapping

- **Chain 0** → **Pad 1** (bottom-left, row 3, col 0)
- **Chain 1** → **Pad 2** (row 3, col 1)
- **Chain 2** → **Pad 3** (row 3, col 2)
- **Chain 3** → **Pad 4** (bottom-right, row 3, col 3)
- **Chain 4** → **Pad 5** (row 2, col 0)
- ... and so on
- **Chain 15** → **Pad 16** (top-right, row 0, col 3)

**Important**: 
- Mapping is by chain order, not MIDI note. Arrange chains in Ableton before converting.
- The code uses 0-15 internally, but Blackbox displays pads as 1-16.

### Sequence Location

Sequences are placed at pads matching their MIDI track index:
- **Track 2** (first MIDI track) → Sequence at **Pad 1** (bottom-left)
- **Track 3** → Sequence at **Pad 2**
- **Track 4** → Sequence at **Pad 3**
- ... and so on

This is independent of which pad the sequence plays (controlled by `seqpadmapdest`).

---

## Timing and Quantization

### Quantised Sequences

**Detection**: Notes align to a grid (≥95% alignment)

**Timing**:
- `strtks` = `time_val * 3840` (3840 ticks per beat)
- `lentks` = `960` (constant)
- `lencount` = step-based length (≥1)
- `step` = `int(time_val * steps_per_beat)`

**Step Length Detection**:
- Default: `step_len=10` (1/16 notes = 4 steps per beat)
- 1/32 notes: `step_len=14` (8 steps per beat) - only if needed
- Triplets: `step_len=11` (1/16T) or `step_len=9` (1/8T) - if detected

### Unquantised Sequences

**Detection**: Notes don't align to grid (<95% alignment) OR mixed triplets + straight notes

**Timing**:
- `strtks` = `time_val * 960` (960 ticks per beat - finer resolution)
- `lentks` = `240` (constant)
- `lencount` = `0` (CRITICAL: must be 0 for unquantised)
- `step` = `floor(strtks / 960)` (beat position)

**Step Length**: Defaults to `step_len=10` (1/16) for display purposes

### Step Length Rules

1. **Default to 1/16**: Always start with `step_len=10` unless notes require finer resolution
2. **1/32 only when needed**: Only use `step_len=14` if notes fall on odd 32nd positions
3. **Triplets**: Use triplet `step_len` if triplets detected and NOT mixed with straight notes
4. **Mixed patterns**: If triplets AND straight notes are present (>30% each), treat as unquantised

---

## Warped Samples and Clip Mode

### Automatic Detection

The converter automatically detects warped samples by checking:
- `SampleWarpProperties/IsWarped` = `"true"`
- `SampleWarpProperties/WarpMode` > `0`

### Clip Mode Behavior

**Warped samples** → **Clip Mode** (`cellmode="1"`):
- Automatically enabled for any warped sample
- `loopmode="1"` (loop enabled)
- `beatcount` = extracted from warp markers or calculated from duration

### Beta branch: Clip mode + transient `<slices>` (`beta/clip-transients`)

On **`main`**, warped Simplers in **Classic** playback do **not** use auto-onset data as slice markers (that data would incorrectly force **slicer** mode).

On **`beta/clip-transients`**, the same pads stay in **clip mode** (`cellmode="1"`), but Ableton's **`InitialSlicePointsFromOnsets`** are also written into the preset as `<slice pos="..."/>` entries so Blackbox clip playback can use transients where supported. If there are more than **128** onsets, the list is reduced with a **density-priority** rule (keeps sparse boundaries and prefers dense transient clusters).

**Simpler slicing mode** (manual / beat / region slices, or unwarped transients when onset editing applies) still maps to **slicer** mode (`cellmode="2"`) and is unchanged.

**Unwarped samples** → **Sampler Mode** (`cellmode="0"`):
- One-shot or sampler mode
- `loopmode="0"` (no auto-loop)
- `beatcount` = calculated from sample duration (for display)

### Beat Count Calculation

- **Warped samples**: Uses `LoopLength` from warp markers if available
- **Unwarped samples**: Calculated from sample duration and project tempo
- Formula: `beats = (duration_seconds * tempo) / 60`

---

## Complete Conversion Workflow

### Step 1: Prepare Your Ableton Project

1. **Set up Drum Rack**:
   - Create Drum Rack on Track 1
   - Add Simplers to chains 0-15
   - Arrange chains in desired pad order
   - Set choke groups if needed

2. **Create MIDI Sequences**:
   - Add MIDI tracks (Track 2+)
   - Create MIDI clips in clip slots 0-3 (for sub-layers A-D)
   - Route tracks appropriately:
     - To `TrackIn` for Pads mode
     - To specific pad for Keys mode
     - To external device for MIDI mode

3. **Check Samples**:
   - Ensure all samples load in Ableton
   - Convert non-WAV samples to WAV
   - Use "Collect All and Save" if needed

### Step 2: Run Conversion

```bash
python3 code/xml_read.py -i "project.als" -o "output_folder"
```

**Options**:
- `-v`: Verbose logging
- `-m`: Manual mode (don't copy samples)
- `-V VERSION`: Specify version name for output folder

### Step 3: Verify Output

1. Check `preset.xml` was created
2. Verify all samples were copied (unless using `-m`)
3. Check conversion logs for warnings
4. Test on Blackbox

### Step 4: Transfer to Blackbox

1. Copy entire output folder to Blackbox SD card
2. Load preset in Blackbox
3. Test all pads and sequences
4. Adjust settings if needed

---

## Troubleshooting

### Samples Not Found

**Problem**: Converter can't find sample files

**Solutions**:
- Use `-m` flag and copy samples manually
- Check verbose output (`-v`) to see expected paths
- Use "Collect All and Save" in Ableton
- Ensure external drives are mounted

### Wrong Pad Mapping

**Problem**: Samples appear on wrong pads

**Solution**: Re-arrange chains in Drum Rack to match desired order, then re-convert

### Sequences Too Fast/Slow

**Problem**: Timing issues with sequences

**Check**:
- Is sequence quantised or unquantised? (check logs)
- Are notes aligned to grid in Ableton?
- Verify `step_len` and `step_count` in output

### Warped Samples Not in Clip Mode

**Problem**: Warped samples stay in sampler mode

**Check**:
- Is `IsWarped="true"` in Ableton XML?
- Check conversion logs for warp detection messages
- Verify `cellmode="1"` in output XML

---

## Best Practices

### Project Organization

1. **Use clear track names** in Ableton
2. **Organize chains** in desired pad order
3. **Consolidate samples** before converting
4. **Test incrementally** - start with simple projects

### Conversion

1. **Always use verbose mode first** (`-v`) to check for issues
2. **Review logs** for warnings
3. **Test on Blackbox** before assuming it works
4. **Keep original Ableton project** as backup

### Blackbox Setup

1. **Load preset** and test all pads
2. **Check sequence routing** matches expectations
3. **Adjust levels** if needed
4. **Save as new preset** after tweaks

---

*For detailed technical information, see:*
- `docs/SEQUENCE_TIMING_WORKFLOW.md` - Detailed timing and quantization rules
- `docs/BLACKBOX_TECHNICAL_REFERENCE.md` - Blackbox XML structure reference

