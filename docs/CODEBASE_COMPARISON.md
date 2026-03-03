# Codebase Comparison: Original vs Current

**Last Updated**: 2025-11-24

## Overview

**Original (`xml_read.py`)**: 610 lines, 26 functions  
**Current (`xml_read.py`)**: 2,675 lines, 34 functions

**Growth**: 4.4x larger codebase, 8 additional functions, complete architectural rewrite

## Function Comparison

### Identical Functions (Simple Utilities)
These small utility functions remain unchanged:
- `row_column()` - Maps pad numbers to row/column positions
- `pad_dicter()` - Creates cell dictionary
- `sequence_dicter()` - Creates sequence cell dictionary
- `find_division()` - Calculates step division (legacy, not used in Drum Rack workflow)

### Nearly Identical Functions (Minor Changes)
- `pad_params_dicter()` - Creates pad parameters dictionary
  - **Change**: `outputbus` changed from '1' to '0' (main output routing)
- `sequence_params_dicter()` - Creates sequence parameters dictionary
  - **Change**: Added `enable` parameter for sequence enable/disable
- `sequence_step_dicter()` - Creates sequence step events
  - **Change**: Enhanced with better timing calculation
- `make_song()` - Creates song structure
  - **Change**: Minor parameter adjustments
- `make_fx()` - Creates FX cells
  - **Change**: Minor parameter adjustments
- `make_master()` - Creates master settings
  - **Change**: Enhanced tempo handling

### Enhanced Functions (Same Purpose, Better Implementation)
- `read_project()` - Enhanced with error handling, version logging, and gzip support
- `track_tempo_extractor()` - Rewritten with safer navigation and fallback methods
- `save_xml()` - Enhanced with pretty-printing via `indent_xml()`

### Completely Rewritten Functions
- `sampler_extract()` - **Completely rewritten**
  - Original: Used hardcoded indices like `device[15][0][0]`
  - Current: Uses `find_element_by_tag()` and `safe_navigate()` for robust navigation
  - Added support for Live 12.2+ structure
  - Enhanced error handling with try/except blocks
  - Added WAV file header reading for accurate sample lengths
  - Added slicing mode detection and extraction
  - Added transpose extraction
  - Added playthrough mode detection
  - Added warp/sync detection

- `device_extract()` - **Completely rewritten**
  - Original: Simple device detection
  - Current: Enhanced with Drum Rack detection and better error handling
  - Added support for multiple device types

- `track_iterator()` - **Completely rewritten**
  - Original: Simple track iteration, clip-based workflow
  - Current: Enhanced with Drum Rack extraction and MIDI track handling
  - Supports multi-layer sequences (A/B/C/D sub-layers)
  - Automatic sequence mode detection (Pads/Keys/MIDI)

### New Functions (Not in Original)
- `get_wav_info()` - Reads WAV file headers for accurate sample length and sample rate
- `find_element_by_tag()` - Safe tag-based XML navigation (replaces hardcoded indices)
- `safe_navigate()` - Robust XML navigation with fallbacks and error handling
- `drum_rack_extract()` - Extracts Drum Rack pads and chains (core new feature)
- `detect_warped_stem()` - Detects warped samples and enables clip mode
- `detect_sequence_mode()` - Detects Pads/Keys/MIDI mode from routing
- `detect_note_grid_pattern()` - Detects quantised vs unquantised sequences
- `make_drum_rack_pads()` - Creates pads from Drum Rack (replaces `make_pads()`)
- `make_drum_rack_sequences()` - Creates sequences from MIDI tracks (replaces `make_sequences()`)
- `find_tempo()` - Enhanced tempo extraction with multiple fallback methods
- `find_tracks()` - Enhanced track finding with better error handling
- `indent_xml()` - Pretty-prints XML output for readability
- `extract_transpose_cents()` - Extracts transpose from Simpler Pitch section
- `_collect_slice_points()` - Collects slice points from various sources (transient, beat, region, manual)
- `extract_slicing_info()` - Comprehensive slicing information extraction

### Removed Functions (No Longer Used)
- `make_pads()` - Replaced by `make_drum_rack_pads()`
- `make_sequences()` - Replaced by `make_drum_rack_sequences()`
- `clip_extract()` - Clip-based workflow removed (now integrated into Drum Rack workflow)
- `sequence_extract()` - Replaced by Drum Rack sequence extraction
- `make_output()` - Integrated into main workflow
- `make_assets()` - Integrated into pad creation workflow
- `empty_pad()` - Integrated into pad creation
- `decrypt_params()` - No longer needed (debugging function)

## Architecture Changes

### Original Architecture (Clip-Based)
- Extracted Simplers/Samplers from individual tracks
- Extracted audio clips separately
- Simple sequence extraction (one sequence per track)
- Hardcoded XML navigation (fragile, breaks with Live version changes)
- No Drum Rack support
- Limited error handling

### Current Architecture (Drum Rack-Based)
- Extracts entire Drum Rack structure
- Maps 16 chains to 16 pads (chain order-based mapping)
- Multi-layer sequence support (A/B/C/D sub-layers per pad)
- Automatic sequence mode detection (Pads/Keys/MIDI) based on routing
- Robust XML navigation with tag-based search and fallbacks
- Warped sample detection and automatic clip mode
- Quantised/unquantised sequence detection
- WAV file header reading for accurate sample lengths
- **Slicing mode support** with slice point extraction
- **Transpose extraction** from Simpler
- **Playthrough mode** detection and conversion
- **Warp sync** detection and conversion

## New Features Added

### Slicing Mode Support (Major Feature)
The current version fully supports Ableton Simpler's slicing mode:
- **Slice Detection**: Automatically detects when Simpler is in Slice mode
- **Slice Point Extraction**: Extracts slice markers from:
  - Transient detection (onset detection)
  - Beat slicing (aligned to beat grid)
  - Region slicing (user-defined regions)
  - Manual slicing (manually placed slice points)
- **Playback Settings**:
  - Play-through mode: When Simpler playback is set to "Through" or "Play Through", Blackbox playthrough is enabled
  - Sync mode: When Simpler warp is enabled, Blackbox sync is enabled
  - Transpose: Extracts and applies transpose settings from Simpler
- **Beat Count**: Automatically calculates beat count from sample length
- **Envelopes**: Uses clip-style envelope defaults for slicer pads

### Enhanced Sequence Support
- **Multi-layer sequences**: Support for A/B/C/D sub-layers (4 sequences per pad)
- **Sequence mode detection**: Automatically detects Pads/Keys/MIDI mode from Ableton routing
- **Unquantised timing**: Preserves exact MIDI note timing for unquantised sequences
- **Quantised timing**: Supports 16th note quantised sequences
- **Triplet detection**: Automatically detects and handles triplet timing

### Improved Sample Handling
- **WAV header reading**: Reads actual sample length from WAV files (not just Ableton metadata)
- **Sample rate detection**: Handles 44.1kHz and 48kHz samples correctly
- **Warped sample detection**: Automatically detects warped samples and enables clip mode
- **Beat count calculation**: Calculates beat count from sample duration and tempo

### Better Error Handling
- Comprehensive logging with different log levels
- Graceful error recovery (continues processing even if one pad fails)
- Safe XML navigation with fallbacks
- Detailed error messages for debugging

## Code Reuse Estimate

**Approximately 10-15% of original code remains:**

1. **Utility functions** (~50 lines): `row_column()`, `pad_dicter()`, `sequence_dicter()`, `find_division()`
2. **Parameter dictionaries** (~50 lines): `pad_params_dicter()`, `sequence_params_dicter()`, `sequence_step_dicter()` (with minor changes)
3. **Basic structure** (~30 lines): Some XML structure concepts, but implementation is different
4. **FX/Song/Master functions** (~50 lines): Similar structure but enhanced

**Total reused: ~180 lines out of 610 original lines (~30%)**

However, the **core logic and architecture are completely different**:
- Original: 610 lines, clip-based workflow
- Current: 2,675 lines, Drum Rack workflow with slicing support
- **Net new code: ~2,500+ lines**

## Feature Comparison Matrix

| Feature | Original | Current |
|---------|----------|---------|
| Drum Rack Support | ❌ | ✅ |
| Clip Mode Detection | ⚠️ (manual) | ✅ (automatic) |
| Slicing Mode | ❌ | ✅ |
| Multi-layer Sequences | ❌ | ✅ |
| Sequence Mode Detection | ❌ | ✅ (Pads/Keys/MIDI) |
| WAV Header Reading | ❌ | ✅ |
| Warp Detection | ❌ | ✅ |
| Transpose Extraction | ❌ | ✅ |
| Playthrough Mode | ❌ | ✅ |
| Choke Groups | ❌ | ✅ |
| Unquantised Sequences | ❌ | ✅ |
| Error Handling | ⚠️ (basic) | ✅ (comprehensive) |
| Live 12.2+ Support | ❌ | ✅ |
| Tag-based Navigation | ❌ | ✅ |
| Logging | ⚠️ (print) | ✅ (logging module) |

## Summary

While some small utility functions remain identical, the codebase has been **fundamentally rewritten**:

- ✅ **New architecture** (Drum Rack vs clip-based)
- ✅ **New features** (Slicing mode, Keys mode, MIDI mode, warped detection, transpose, playthrough, etc.)
- ✅ **Better error handling** and robustness
- ✅ **Support for newer Ableton Live versions** (12.2, 12.3+)
- ✅ **Comprehensive documentation** and workflow guides
- ✅ **4.4x codebase growth** with significant feature additions

The original code served as a **reference for XML structure and parameter names**, but the implementation is almost entirely new. The current version is production-ready and supports modern Ableton Live workflows with Drum Racks, slicing, and advanced sequence features.

## Migration Notes

If you're migrating from the original script:
1. **Project structure**: You must use Drum Racks (not individual Simplers on tracks)
2. **Template**: Use the included Ableton template for best results
3. **Slicing**: Slicing mode is now fully supported (was not in original)
4. **Sequences**: Multi-layer sequences (A/B/C/D) are now supported
5. **Error handling**: The script will continue processing even if some pads fail
