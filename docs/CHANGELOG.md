# Changelog

## 2026-03

### Documentation
- **`docs/SONG_SECTION_PAD_VS_SEQ.md`** — Song sections: pad block (chan 0–15) vs seq block (chan 256–271), why the grid can show pattern A while playback does not arm that sequence, and how Seq vs Pads tracks map in the converter. Cross-linked from **`docs/BLACKBOX_TECHNICAL_REFERENCE.md`** (Song Mode); corrected the inverted “which Live track drives which block” sentence.

### Converter
- **`code/xml_read.py`** — DEBUG-only log when a section arms the pad block from Seq-track arrangement but the Pads track contributed no seq-block sceneitem for that seq index (helps diagnose song-mode UI vs playback mismatches without changing XML output).

## Version 0.3 (November 2024)

### Major Improvements
- ✅ **Ableton Live 12 Compatibility**: Full support for Ableton Live 12.x file format
- ✅ **Backward Compatibility**: Still works with Ableton Live 10 and 11
- ✅ **Tag-Based XML Navigation**: Replaced hardcoded array indices with robust tag-based element finding
- ✅ **Comprehensive Error Handling**: Better error messages and graceful degradation
- ✅ **Detailed Logging**: See exactly what the script is doing at each step
- ✅ **Python 3.7+ Support**: Works with Python 3.7 through 3.12+

### Technical Changes

#### XML Navigation Overhaul
The original script used hardcoded array indices like `root[0][i][19][7][14]` which breaks when Ableton changes their file format. The new version uses tag-based navigation:

```python
# Old (brittle):
tempo = child[-1][6][25][1].attrib['Value']

# New (robust):
device_chain = find_element_by_tag(maintrack, 'DeviceChain')
mixer = find_element_by_tag(device_chain, 'Mixer')
tempo_elem = find_element_by_tag(mixer, 'Tempo')
manual = find_element_by_tag(tempo_elem, 'Manual')
tempo = manual.attrib['Value']
```

#### Ableton 12 Specific Changes
- Handles `MainTrack` (Live 12) vs `MasterTrack` (Live 10/11)
- Updated tempo extraction path
- Improved device detection
- Better handling of clip and sequence structures

#### Improved Error Handling
- All extraction functions return `None` on failure instead of crashing
- Detailed logging shows where extraction failed
- Script continues even if some tracks fail to extract
- Safe navigation with `safe_navigate()` helper function

### Known Limitations
- Simpler/Sampler extraction still needs improvement for complex multisample setups
- Audio clip detection works but may need sample rate adjustments
- MIDI clip extraction is partially implemented
- Grouped instruments are not yet supported

## Version 0.2 (May 2023)
- Initial public release by Maximilian Karlander
- Support for Ableton Live 10
- Basic Simpler/Sampler extraction
- MIDI sequence extraction
- Audio clip as loop pads

## Future Roadmap
- [ ] Full Simpler/Sampler parameter extraction for Live 12
- [ ] Support for grouped instruments
- [ ] Extract send/return effects settings
- [ ] Better envelope parameter conversion
- [ ] Support for other samplers (Kontakt, etc.)
- [ ] GUI version for non-technical users


