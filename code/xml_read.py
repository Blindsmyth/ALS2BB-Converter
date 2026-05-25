#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ableton Live Drum Rack to 1010music Blackbox converter
Version 0.3 - Drum Rack Workflow

Converts Ableton Live projects with Drum Racks to Blackbox presets.

Based on the original work by Maximilian Karlander (pro424)
Original: https://forum.1010music.com/forum/products/blackbox/support-blackbox/43727-python-script-converting-an-ableton-live-project-to-blackbox-xml

Requirements:
- Track 1: Drum Rack with up to 16 Simplers
- Tracks 2-17: MIDI tracks for sequences (optional)

Features:
- 16-pad drum rack mapping
- Choke group support
- Warped stem detection
- Multi-layer sequences (A/B/C/D)
- Unquantised MIDI timing support
- Compatible with Ableton Live 10, 11, and 12

CREDITS:
- Original author: Maximilian Karlander (pro424) - Blackbox XML format reverse-engineering
- Enhanced by: Simon Schmidt (2024-2025) - Ableton 12 compatibility, Drum Rack workflow
"""
import argparse
from argparse import RawTextHelpFormatter
import xml.etree.ElementTree as ET
import math
import gzip
import os
import shutil
import logging
import sys
import struct
import subprocess
import re

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# #region agent log
import json as _json_dbg
import time as _time_dbg
_DEBUG_LOG_PATH = '/Users/simon/Dropbox/Blackbox Stuff/ableton_blackbox/.cursor/debug-870236.log'
_DEBUG_SESSION_ID = '870236'

def _dbg(location, message, data=None, hypothesis=None):
    try:
        payload = {
            'sessionId': _DEBUG_SESSION_ID,
            'timestamp': int(_time_dbg.time() * 1000),
            'location': location,
            'message': message,
        }
        if data is not None:
            payload['data'] = data
        if hypothesis is not None:
            payload['hypothesisId'] = hypothesis
        with open(_DEBUG_LOG_PATH, 'a') as _f:
            _f.write(_json_dbg.dumps(payload) + '\n')
    except Exception:
        pass
# #endregion

# Beta branch (beta/clip-transients): max slice markers embedded on clip-mode (warped) cells
CLIP_TRANSIENT_SLICE_MAX_BETA = 128


def _parse_slice_point_container(container):
    """Parse Ableton slice point container to seconds and/or sample indices."""
    seconds = []
    samples = []
    for point in list(container):
        attrib = point.attrib
        if 'TimeInSeconds' in attrib:
            try:
                seconds.append(float(attrib['TimeInSeconds']))
                continue
            except ValueError:
                pass
        if 'Time' in attrib:
            try:
                seconds.append(float(attrib['Time']))
                continue
            except ValueError:
                pass
        for key in ('SampleIndex', 'Samples', 'Sample'):
            if key in attrib:
                try:
                    samples.append(int(round(float(attrib[key]))))
                    break
                except ValueError:
                    pass
    return seconds, samples


def limit_slice_positions_density_priority(positions, max_count, samlen_int=None):
    """
    Reduce slice onset count while preferring regions where onsets are dense (short gaps).
    Always keeps the first and last onset from the input list; fills remaining slots with
    highest-scoring interiors.

    Used for beta clip-mode transient embeds (hardware / MIDI practical limit).

    Does NOT inject an artificial slice at sample 0: if Ableton provided no onset at the
    start (Frozen_Git-style), omitting pos 0 avoids a bogus first slice; if Live saved a
    real slice at 0, it remains in the input and is kept like any other onset.
    """
    if max_count < 1:
        return []
    uniq = sorted(set(int(max(0, p)) for p in positions))
    if not uniq:
        return []
    if samlen_int is not None and samlen_int > 0:
        uniq = [min(p, samlen_int) for p in uniq]
        uniq = sorted(set(uniq))
    if len(uniq) <= max_count:
        return uniq
    first, last = uniq[0], uniq[-1]
    inner = uniq[1:-1]
    if not inner:
        return uniq[:max_count]
    budget = max_count - 2
    if budget <= 0:
        return [first] if max_count == 1 else [first, last]
    full = uniq
    scored = []
    for i in range(1, len(full) - 1):
        p = full[i]
        d_prev = max(full[i] - full[i - 1], 1)
        d_next = max(full[i + 1] - full[i], 1)
        score = (1.0 / d_prev) + (1.0 / d_next)
        scored.append((score, i, p))
    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen = {first, last}
    for j in range(min(budget, len(scored))):
        chosen.add(scored[j][2])
    out = sorted(chosen)
    if samlen_int is not None and samlen_int > 0:
        out = [min(p, samlen_int) for p in out]
        out = sorted(set(out))
    return out


def _dedupe_keys_quantised_same_step_strtks(events):
    """
    Collapse stacked MIDI notes that quantise to the same step/strtks (Keys mode).

    Live sometimes writes two hits on one triplet grid slot; hand-trimmed BB presets keep a
    single row (lowest MIDI pitch wins). Matches Digital Waterfall fixed preset shape.
    """
    if len(events) < 2:
        return events
    best = {}
    for e in events:
        key = (int(e.get('step', 0)), int(e.get('strtks', 0)))
        cur = best.get(key)
        if cur is None or int(e.get('pitch', 127)) < int(cur.get('pitch', 127)):
            best[key] = e
    return sorted(best.values(), key=lambda x: (
        int(x.get('strtks', 0)),
        int(x.get('pitch', 0)),
        int(str(x.get('chan', 0))),
    ))


def get_wav_info(filepath):
    """
    Read WAV file header and return sample information.
    Returns dict with {'sample_length_samples': int, 'sample_rate': int, 'duration_seconds': float}
    Returns None if file doesn't exist or isn't a valid WAV.
    """
    try:
        if not os.path.exists(filepath):
            return None
            
        with open(filepath, 'rb') as f:
            # Read RIFF header
            riff = f.read(4)
            if riff != b'RIFF':
                return None
            
            # Skip file size (4 bytes)
            f.read(4)
            
            # Check for WAVE format
            wave = f.read(4)
            if wave != b'WAVE':
                return None
            
            # Find fmt chunk
            while True:
                chunk_id = f.read(4)
                if not chunk_id:
                    return None
                    
                chunk_size = struct.unpack('<I', f.read(4))[0]
                
                if chunk_id == b'fmt ':
                    # Read fmt chunk
                    audio_format = struct.unpack('<H', f.read(2))[0]  # 1 = PCM
                    num_channels = struct.unpack('<H', f.read(2))[0]
                    sample_rate = struct.unpack('<I', f.read(4))[0]
                    byte_rate = struct.unpack('<I', f.read(4))[0]
                    block_align = struct.unpack('<H', f.read(2))[0]
                    bits_per_sample = struct.unpack('<H', f.read(2))[0]
                    
                    # Skip rest of fmt chunk
                    if chunk_size > 16:
                        f.read(chunk_size - 16)
                    
                    # Find data chunk
                    while True:
                        data_chunk_id = f.read(4)
                        if not data_chunk_id:
                            return None
                        data_chunk_size = struct.unpack('<I', f.read(4))[0]
                        
                        if data_chunk_id == b'data':
                            # Calculate sample length
                            bytes_per_sample = bits_per_sample // 8
                            total_samples = data_chunk_size // (num_channels * bytes_per_sample)
                            duration_seconds = total_samples / sample_rate
                            
                            return {
                                'sample_length_samples': total_samples,
                                'sample_rate': sample_rate,
                                'duration_seconds': duration_seconds,
                                'num_channels': num_channels,
                                'bits_per_sample': bits_per_sample
                            }
                        else:
                            # Skip this chunk
                            f.read(data_chunk_size)
                else:
                    # Skip this chunk
                    f.read(chunk_size)
                    
    except Exception as e:
        logger.debug(f"Error reading WAV file {filepath}: {e}")
        return None


def _sf_normalized_peak_clips(np, block):
    """True if magnitude exceeds 1.0 (normalized float PCM as returned by ``soundfile``)."""
    if block is None or block.size == 0:
        return False
    return float(np.max(np.abs(block))) > 1.0 + 1e-6


def _gain_wav_via_soundfile_numpy(src, dst, linear_gain):
    try:
        import numpy as np
        import soundfile as sf  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False

    info = sf.info(src)
    data, samplerate = sf.read(src, always_2d=True, dtype='float64')

    subtype = getattr(info, 'subtype', None)
    clipped = False
    if linear_gain != 1.0 and data.size:
        pre_peak = float(np.max(np.abs(data))) * linear_gain
        clipped = clipped or pre_peak > 1.0 + 1e-6

    scaled = np.multiply(data, linear_gain, out=data, casting='unsafe')
    if linear_gain != 1.0 and scaled.size:
        clipped = clipped or _sf_normalized_peak_clips(np, scaled)

    sf.write(dst, scaled, samplerate, subtype=subtype, format=getattr(info, 'format', 'WAV'))

    if clipped:
        logger.warning(f'Potential clipping after Utility gain bake: {os.path.basename(dst)}')
    return True


def _gain_wav_via_wave_pcm16(src, dst, linear_gain):
    """
    Multiply 16‑bit PCM WAV channels by ``linear_gain`` (pure Python fallback when NumPy absent).
    """
    try:
        import wave  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False

    wav_info = get_wav_info(src)
    if not wav_info or int(wav_info.get('bits_per_sample', 0)) != 16:
        return False

    with wave.open(src, 'rb') as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()

    if sw != 2:
        return False

    with open(src, 'rb') as f:
        riff = f.read(4)
        if riff != b'RIFF':
            return False
        f.read(4)
        wave_id = f.read(4)
        if wave_id != b'WAVE':
            return False

        data_offset = None
        data_sz = None
        while True:
            cid = f.read(4)
            if not cid or len(cid) < 4:
                break
            sz_bytes = f.read(4)
            if len(sz_bytes) < 4:
                break
            chunk_size = struct.unpack('<I', sz_bytes)[0]
            if cid == b'data':
                data_offset = f.tell()
                data_sz = chunk_size
                break
            f.seek(chunk_size, 1)
            if chunk_size % 2:
                f.seek(1, 1)

    if data_offset is None:
        return False

    with open(src, 'rb') as f:
        f.seek(data_offset)
        pcm = f.read(data_sz)

    clipped = False
    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None:
        arr = np.frombuffer(pcm, dtype=np.int16)
        if n_ch > 1:
            try:
                arr = arr.reshape(-1, n_ch)
            except ValueError:
                return False
        prod = np.multiply(arr.astype(np.float64, copy=False), linear_gain)
        if np.any(np.abs(prod) > 32767.0 + 1e-9):
            clipped = True
        pcm_out = np.clip(np.rint(prod), -32768, 32767).astype(np.int16).tobytes()
    else:
        import array as _array
        samp = _array.array('h')
        samp.frombytes(pcm)
        if n_ch <= 0 or len(samp) % n_ch != 0:
            return False

        scaled = _array.array('h')
        for iv in samp:
            fv = float(iv) * linear_gain
            r = int(math.floor(fv + 0.5)) if fv >= 0 else int(math.ceil(fv - 0.5))
            if r > 32767 or r < -32768:
                clipped = True
            if r > 32767:
                r = 32767
            elif r < -32768:
                r = -32768
            scaled.append(r)
        pcm_out = scaled.tobytes()

    with wave.open(dst, 'wb') as out_w:
        out_w.setnchannels(n_ch)
        out_w.setsampwidth(sw)
        out_w.setframerate(sr)
        out_w.writeframes(pcm_out)

    if clipped:
        logger.warning(f'PCM16 clipping after Utility gain bake: {os.path.basename(dst)}')
    return True


def _gain_wav_via_ffmpeg(src, dst, linear_gain):
    exe = shutil.which('ffmpeg')
    if not exe:
        return False
    if linear_gain <= 0:
        return False
    gain_db = 20.0 * math.log10(max(linear_gain, 1e-20))
    cmd = [
        exe, '-nostdin', '-hide_banner', '-loglevel', 'warning', '-y',
        '-i', src, '-af', f'volume={gain_db}dB', dst,
    ]
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, OSError):
        return False

    if linear_gain > 1.0 + 1e-6:
        logger.warning(
            'Utility gain bake used ffmpeg boost — check exported WAVs for clipping: '
            f'{os.path.basename(dst)}'
        )
    return True


def copy_wav_with_master_gain(src_path: str, dest_path: str, linear_gain: float):
    """
    Write ``dest_path`` with every sample multiplied by ``linear_gain``.

    Prefer ``soundfile`` + NumPy when installed (preserves WAV subtype/format),
    falling back to 16‑bit PCM via ``wave`` + NumPy, then ``ffmpeg`` volume in dB.
    Raises ``RuntimeError`` if conversion is not supported on this machine.
    """
    if linear_gain <= 0 or not math.isfinite(linear_gain):
        raise ValueError('linear_gain must be a finite positive number')

    if abs(linear_gain - 1.0) <= 1e-15:
        shutil.copy2(src_path, dest_path)
        return

    if _gain_wav_via_soundfile_numpy(src_path, dest_path, linear_gain):
        return

    logger.info('soundfile+numpy unavailable or unsuitable — trying PCM16 WAV fallback...')
    if _gain_wav_via_wave_pcm16(src_path, dest_path, linear_gain):
        return

    logger.info('PCM16 wave fallback not applicable — trying ffmpeg...')
    if _gain_wav_via_ffmpeg(src_path, dest_path, linear_gain):
        return

    raise RuntimeError(
        'Utility gain bake needs soundfile+NumPy for this WAV '
        '(or 16‑bit PCM in the WAV + stdlib/array, or ffmpeg on PATH)'
    )


def read_project(file):
    """Read and parse an Ableton Live .als file (handles both gzipped and plain XML)."""
    try:
        # Try to open as gzipped file first
        file_content = gzip.open(file, 'rb')
        tree = ET.parse(file_content)
        root = tree.getroot()
        
        # Log Ableton version info
        version_info = root.attrib
        logger.info(f"Ableton version: {version_info.get('Creator', 'Unknown')}")
        logger.info(f"Major version: {version_info.get('MajorVersion', 'Unknown')}")
        logger.info(f"Minor version: {version_info.get('MinorVersion', 'Unknown')}")
        
        return root
    except OSError as e:
        # If not gzipped, try as regular XML
        if 'Not a gzipped file' in str(e):
            logger.info('File is not gzipped, reading as plain XML')
            tree = ET.parse(file)
            root = tree.getroot()
            
            # Log Ableton version info
            version_info = root.attrib
            logger.info(f"Ableton version: {version_info.get('Creator', 'Unknown')}")
            logger.info(f"Major version: {version_info.get('MajorVersion', 'Unknown')}")
            logger.info(f"Minor version: {version_info.get('MinorVersion', 'Unknown')}")
            
            return root
        else:
            raise
    except Exception as e:
        logger.error(f"Failed to read project file: {e}")
        raise


def find_element_by_tag(parent, tag):
    """Find first child element with given tag."""
    for child in parent:
        if child.tag == tag:
            return child
    return None


def extract_first_midi_note_from_track(midi_track):
    """
    Extract the first MIDI note played in any clip of this MIDI track.
    Used for Keys mode to determine which pad the sequence targets.
    
    Returns:
        int: MIDI note number, or None if no notes found
    """
    try:
        device_chain = find_element_by_tag(midi_track, 'DeviceChain')
        if not device_chain:
            return None
        
        main_sequencer = find_element_by_tag(device_chain, 'MainSequencer')
        if not main_sequencer:
            return None
        
        clip_slot_list = find_element_by_tag(main_sequencer, 'ClipSlotList')
        if not clip_slot_list:
            return None
        
        # Check all clip slots for a MIDI clip
        for clip_slot in list(clip_slot_list):
            if len(clip_slot) > 1:
                clip_slot_value = clip_slot[1]
                if len(clip_slot_value) > 0:
                    midi_clip = clip_slot_value[0][0]
                    notes = midi_clip.find('.//Notes/KeyTracks')
                    if notes and len(notes) > 0:
                        # Get first key track
                        key_track = notes[0]
                        midi_key = find_element_by_tag(key_track, 'MidiKey')
                        if midi_key and 'Value' in midi_key.attrib:
                            return int(midi_key.attrib['Value'])
        
        return None
        
    except Exception as e:
        logger.debug(f'  Error extracting MIDI note: {e}')
        return None


def detect_sequence_mode(midi_track):
    """
    Detect the sequence mode for a MIDI track based on its routing.
    
    Returns:
        tuple: (mode, target)
        - mode: 'Keys', 'MIDI', or 'Pads'
        - target: For Keys mode: branch_id (int), For MIDI mode: channel (int), For Pads mode: None
    
    Routing patterns (examples from Ableton 12):
        - Keys Mode: MidiOut/Track.XX/DeviceIn.Y.BZ (routed to specific drum rack pad via Branch Id Z)
        - Pads Mode (default): MidiOut/Track.XX/DeviceIn.Y.0 or MidiOut/Track.XX/TrackIn or MidiOut/None
        - MIDI Mode: MidiOut/External.Dev:DeviceName/Channel (routed to external MIDI)
    """
    try:
        # Find MidiOutputRouting/Target element (search recursively)
        midi_output_routing = midi_track.find('.//MidiOutputRouting')
        if midi_output_routing is None:
            logger.debug('  No MidiOutputRouting found, defaulting to Pads mode')
            return 'Pads', None
        
        target_elem = find_element_by_tag(midi_output_routing, 'Target')
        if target_elem is None or 'Value' not in target_elem.attrib:
            logger.debug('  No routing Target found, defaulting to Pads mode')
            return 'Pads', None
        
        target = target_elem.attrib['Value']
        logger.debug(f'  Routing target: {target}')
        
        # Check for Keys mode: MidiOut/Track.XX/DeviceIn.Y.BZ,W.V
        # Example: MidiOut/Track.34/DeviceIn.0.B40,0.0
        # The BZ part (e.g., B40) is the Branch Id that maps to a specific pad
        if '/DeviceIn.' in target:
            # Extract the DeviceIn part
            device_part = target.split('/DeviceIn.')[-1]
            
            # Parse format: 0.B40,0.0  (keys) or 0.0 (pads-to-entire-drum-rack)
            parts = device_part.split('.')
            if len(parts) >= 2:
                second_part = parts[1]
                
                # Check if second part contains 'B' followed by a number (Branch Id)
                if 'B' in second_part:
                    branch_part = second_part.split(',')[0]  # Get part before comma
                    if branch_part.startswith('B'):
                        try:
                            branch_id = int(branch_part[1:])  # Remove 'B' prefix and convert to int
                            logger.info(f'  Detected Keys mode → Branch Id {branch_id}')
                            return 'Keys', branch_id
                        except ValueError:
                            logger.warning(f'  Could not parse branch id from: {branch_part}')
                # No 'B' branch id → routing to whole drum rack, treat as Pads mode
                logger.info('  Detected Pads mode (DeviceIn without branch id)')
                return 'Pads', None
        
        # Check for MIDI mode: MidiOut/External.Dev:
        if '/External.Dev:' in target or '/External/' in target:
            # Extract MIDI channel if present (usually ends with /Channel)
            channel = 0  # Default to channel 0 (Ch1)
            if '/' in target:
                parts = target.split('/')
                if parts[-1].isdigit():
                    channel = int(parts[-1])
            logger.info(f'  Detected MIDI mode → Channel {channel}')
            return 'MIDI', channel
        
        # Default to Pads mode (TrackIn, None, or any other non-explicit routing)
        logger.debug('  Defaulting to Pads mode')
        return 'Pads', None
        
    except Exception as e:
        logger.warning(f'  Error detecting sequence mode: {e}, defaulting to Pads mode')
        return 'Pads', None


def find_tempo(root):
    """
    Find tempo in the project using tag-based navigation.
    Works with both Ableton 10 (MasterTrack) and 12 (MainTrack).
    """
    try:
        liveset = root[0]
        
        # Try MainTrack first (Live 12+)
        maintrack = find_element_by_tag(liveset, 'MainTrack')
        
        # Fall back to MasterTrack (Live 10/11)
        if maintrack is None:
            maintrack = find_element_by_tag(liveset, 'MasterTrack')
        
        if maintrack is None:
            logger.warning("Could not find MainTrack or MasterTrack")
            return '120'  # Default tempo
        
        # Navigate: MainTrack -> DeviceChain -> Mixer -> Tempo -> Manual
        device_chain = find_element_by_tag(maintrack, 'DeviceChain')
        if device_chain is None:
            logger.warning("Could not find DeviceChain")
            return '120'
        
        mixer = find_element_by_tag(device_chain, 'Mixer')
        if mixer is None:
            logger.warning("Could not find Mixer")
            return '120'
        
        tempo_elem = find_element_by_tag(mixer, 'Tempo')
        if tempo_elem is None:
            logger.warning("Could not find Tempo element")
            return '120'
        
        manual = find_element_by_tag(tempo_elem, 'Manual')
        if manual is None or 'Value' not in manual.attrib:
            logger.warning("Could not find Manual tempo value")
            return '120'
        
        tempo = manual.attrib['Value']
        logger.info(f"Found tempo: {tempo} BPM")
        return tempo
        
    except Exception as e:
        logger.warning(f"Error finding tempo: {e}. Using default 120 BPM")
        return '120'


def _xml_local_tag(elem):
    """Strip XML namespace prefix from Element.tag, if present."""
    tag = getattr(elem, 'tag', '')
    if not isinstance(tag, str):
        return ''
    if tag and tag[0] == '{':
        return tag.partition('}')[-1]
    return tag


def _nested_devices_under_master_track(master_track):
    """Return the <Devices> element under Master/Main track device chain (Live 11/12-style nesting)."""
    device_chain = find_element_by_tag(master_track, 'DeviceChain')
    if device_chain is None:
        return None
    nested_chain = find_element_by_tag(device_chain, 'DeviceChain')
    if nested_chain is None:
        return None
    return find_element_by_tag(nested_chain, 'Devices')


def _utility_device_engaged(device):
    """
    Return False if Ableton Utility (StereoGain) is bypass/off.
    Missing <On> defaults to engaged.
    """
    on_el = None
    for ch in device:
        if _xml_local_tag(ch) == 'On':
            on_el = ch
            break
    if on_el is None:
        return True
    manual = find_element_by_tag(on_el, 'Manual')
    if manual is None or 'Value' not in manual.attrib:
        return True
    raw = manual.attrib['Value'].strip().lower()
    if raw in ('false', '0', 'no', 'off'):
        return False
    return True


def _effective_name_under_name_block(device_elem):
    """Return EffectiveName/@Value inside a <Name>...</Name> subtree, if present."""
    for el in device_elem.iter():
        if _xml_local_tag(el) != 'Name':
            continue
        for ch in el:
            if _xml_local_tag(ch) != 'EffectiveName':
                continue
            v = ch.attrib.get('Value')
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def extract_master_utility_gain_db(root, log_on_match=True):
    """
    Read the Gain value from the first engaged Ableton Utility on the master/output chain.

    Live persists Utility as the native device tag ``StereoGain`` with ``Gain/Manual@Value``.
    That value is a **linear amplitude** multiplier (unity = ``1.0``, +3 dB ≈ ``1.4125``).

    Converts to **dB**: ``20 * log10(linear)``.

    ``log_on_match``: when False, suppress INFO for a successful read (caller may log separately).

    Returns ``None`` if no Utility/StereoGain is found, Gain is unreadable, the device is
    bypassed, or linear gain is non-positive.

    Scope: MainTrack (Live 12+) or MasterTrack fallback only.
    Only the **first** matching Utility along the master's device chain is used.
    """
    try:
        liveset = root[0]
    except (IndexError, TypeError):
        return None

    main_track = find_element_by_tag(liveset, 'MainTrack')
    if main_track is None:
        main_track = find_element_by_tag(liveset, 'MasterTrack')
    if main_track is None:
        logger.debug('extract_master_utility_gain_db: no MainTrack/MasterTrack')
        return None

    devices_elem = _nested_devices_under_master_track(main_track)
    if devices_elem is None:
        logger.debug('extract_master_utility_gain_db: nested Devices missing on master chain')
        return None

    for device in devices_elem:
        is_utility_like = False
        if _xml_local_tag(device) == 'StereoGain':
            is_utility_like = True
        else:
            en = _effective_name_under_name_block(device)
            if en is not None and en.strip().lower() == 'utility':
                is_utility_like = True
        if not is_utility_like:
            continue
        if not _utility_device_engaged(device):
            if log_on_match:
                logger.info('Master Utility (StereoGain) is bypassed; not applying Utility gain.')
            else:
                logger.debug('Master Utility (StereoGain) is bypassed; skipping device.')
            continue

        gain_node = None
        for ch in device:
            if _xml_local_tag(ch) == 'Gain':
                gain_node = ch
                break
        if gain_node is None:
            logger.warning('extract_master_utility_gain_db: Utility lacks Gain subtree')
            return None

        manual = find_element_by_tag(gain_node, 'Manual')
        if manual is None or 'Value' not in manual.attrib:
            logger.warning('extract_master_utility_gain_db: Utility Gain has no Manual@Value')
            return None

        try:
            linear = float(str(manual.attrib['Value']).strip())
        except (ValueError, TypeError):
            logger.warning('extract_master_utility_gain_db: could not parse linear gain')
            return None

        if linear <= 0.0:
            logger.warning('extract_master_utility_gain_db: non-positive linear gain — skipping Utility bake')
            return None

        if not math.isfinite(linear):
            return None

        db = 20.0 * math.log10(linear)
        if log_on_match:
            logger.info(f'Master Utility gain: {db:.6f} dB (linear {linear:.8f})')
        return db

    return None


def find_tracks(root):
    """Find the Tracks element in the project."""
    try:
        liveset = root[0]
        tracks = find_element_by_tag(liveset, 'Tracks')
        
        if tracks is None:
            logger.error("Could not find Tracks element")
            return None
        
        logger.info(f"Found {len(tracks)} tracks")
        return tracks
        
    except Exception as e:
        logger.error(f"Error finding tracks: {e}")
        return None


def track_tempo_extractor(root):
    """Extract tracks and tempo from the project."""
    tracks = find_tracks(root)
    tempo = find_tempo(root)
    
    if tracks is None:
        raise ValueError("Could not extract tracks from project")
    
    return tracks, tempo


def device_extract(track, track_count):
    """Extract device information from a track."""
    device_dict = {}
    logger.info(f'Track {track_count}, track type: {track.tag}')
    
    try:
        device_chain = find_element_by_tag(track, 'DeviceChain')
        if device_chain is None:
            return device_dict, track.tag
        
        # Find the nested DeviceChain
        nested_chain = find_element_by_tag(device_chain, 'DeviceChain')
        if nested_chain is None:
            return device_dict, track.tag
        
        # Find Devices element
        devices_elem = find_element_by_tag(nested_chain, 'Devices')
        if devices_elem is None:
            return device_dict, track.tag
        
        # Extract individual devices
        count = 1
        for device in devices_elem:
            logger.info(f'  Device {count}: {device.tag}')
            count += 1
            device_dict[device.tag] = device
            
    except Exception as e:
        logger.warning(f"Error extracting devices from track {track_count}: {e}")
    
    return device_dict, track.tag


def safe_navigate(element, path_description, *indices_or_tags):
    """
    Safely navigate XML tree using indices or tags.
    Returns None if path doesn't exist.
    
    Args:
        element: Starting element
        path_description: Description for error messages
        *indices_or_tags: Mix of integer indices or string tag names
    """
    current = element
    path = []
    
    try:
        for item in indices_or_tags:
            if isinstance(item, int):
                if len(current) <= item:
                    logger.warning(f"Path {path_description}: Index {item} out of range (length {len(current)})")
                    return None
                current = current[item]
                path.append(f"[{item}]")
            elif isinstance(item, str):
                found = find_element_by_tag(current, item)
                if found is None:
                    logger.warning(f"Path {path_description}: Tag '{item}' not found")
                    return None
                current = found
                path.append(f"<{item}>")
        return current
    except Exception as e:
        logger.warning(f"Error navigating {path_description} at {''.join(path)}: {e}")
        return None


def drum_rack_extract(drum_rack_device):
    """
    Extract all Simplers from a DrumGroupDevice.
    Returns a list of pad info: [{'blackbox_pad': 0-15, 'simpler': device, 'midi_note': 36-51, 'choke_group': 0-16, 'branch_id': X, 'name': '...'}, ...]
    """
    pad_list = []
    
    try:
        # Find Branches element
        branches = find_element_by_tag(drum_rack_device, 'Branches')
        if not branches:
            logger.warning("DrumGroupDevice has no Branches element")
            return pad_list
        
        # Extract pads from branches
        # Map branch/chain index to Blackbox pad
        # Default: Use chain order as pad order (Branch 0 = Pad 0, etc.)
        # Blackbox has 16 pads in a 4x4 grid
        max_pads = 16
        for branch_index in range(min(len(branches), max_pads)):
            branch = branches[branch_index]
            
            # Extract branch Id attribute (used for Keys mode routing)
            branch_id = branch.attrib.get('Id', None)
            if branch_id:
                try:
                    branch_id = int(branch_id)
                except ValueError:
                    branch_id = None
            
            pad_info = {
                'blackbox_pad': branch_index,  # Use chain index as pad index by default (0-15)
                'simpler': None,
                'midi_note': None,
                'choke_group': 0,
                'branch_id': branch_id,  # Store branch Id for Keys mode pad mapping
                'name': '',
                'is_empty': True
            }
            
            # Extract BranchInfo for MIDI note and choke group
            branch_info = find_element_by_tag(branch, 'BranchInfo')
            if branch_info:
                receiving_note = find_element_by_tag(branch_info, 'ReceivingNote')
                # IMPORTANT: use `is not None`, NOT `if receiving_note` — ElementTree evaluates
                # leaf nodes (no children) as falsy even when they have attributes.
                if receiving_note is not None and 'Value' in receiving_note.attrib:
                    midi_note = int(receiving_note.attrib['Value'])
                    pad_info['midi_note'] = midi_note
                
                choke_group = find_element_by_tag(branch_info, 'ChokeGroup')
                logger.debug(f'  Branch {branch_index}: ChokeGroup element found: {choke_group is not None}')
                if choke_group is not None:
                    logger.debug(f'    ChokeGroup attribs: {choke_group.attrib}')
                    if 'Value' in choke_group.attrib:
                        # Ableton choke group mapping to Blackbox excl groups:
                        # Ableton 0 or -1 (no choke) → Blackbox 0 (excl group X)
                        # Ableton 1-4 → Blackbox 1-4 (excl groups A-D)
                        ableton_choke = int(choke_group.attrib['Value'])
                        logger.debug(f'    Ableton choke value: {ableton_choke}')
                        if ableton_choke <= 0:
                            pad_info['choke_group'] = 0  # No choke / excl group X
                        elif ableton_choke >= 1 and ableton_choke <= 4:
                            pad_info['choke_group'] = ableton_choke  # Direct mapping for groups 1-4 (A-D)
                            logger.debug(f'    → Mapped to Blackbox choke group: {ableton_choke}')
                        else:
                            # If Ableton has choke groups > 4, cap at 4 (excl group D)
                            pad_info['choke_group'] = min(ableton_choke, 4)
                            logger.debug(f'    → Capped to Blackbox choke group: {pad_info["choke_group"]}')
                    else:
                        logger.debug(f'    ChokeGroup has no Value attribute')
                else:
                    logger.debug(f'  Branch {branch_index}: No ChokeGroup element found in BranchInfo')
            
            # Extract Name
            name_elem = find_element_by_tag(branch, 'Name')
            if name_elem and 'Value' in name_elem.attrib:
                pad_info['name'] = name_elem.attrib['Value']
            
            # Extract Simpler device from DeviceChain
            dev_chain = find_element_by_tag(branch, 'DeviceChain')
            if dev_chain:
                # Check for MidiToAudioDeviceChain (Ableton 12.3+)
                midi_to_audio = find_element_by_tag(dev_chain, 'MidiToAudioDeviceChain')
                if midi_to_audio:
                    devices_elem = find_element_by_tag(midi_to_audio, 'Devices')
                else:
                    # Fallback to direct Devices (older structure)
                    devices_elem = find_element_by_tag(dev_chain, 'Devices')
                
                if devices_elem:
                    simpler = find_element_by_tag(devices_elem, 'OriginalSimpler')
                    if simpler:
                        pad_info['simpler'] = simpler
                        pad_info['is_empty'] = False
                        # Use the Simpler's UserName (preset name) if set
                        un_elem = find_element_by_tag(simpler, 'UserName')
                        if un_elem is not None:
                            preset_name = un_elem.attrib.get('Value', '').strip()
                            if preset_name:
                                pad_info['preset_name'] = preset_name
            
            # Only add pads that have a valid pad number
            if pad_info['blackbox_pad'] is not None:
                pad_list.append(pad_info)
                
                # Log pad mapping
                choke_label = {0: 'X (none)', 1: 'A', 2: 'B', 3: 'C', 4: 'D'}.get(pad_info["choke_group"], str(pad_info["choke_group"]))
                logger.info(f'  Chain {branch_index} → Pad {pad_info["blackbox_pad"]}: MIDI {pad_info["midi_note"]}, Choke: {choke_label}, Has Simpler: {not pad_info["is_empty"]}')
            else:
                logger.debug(f'  Chain {branch_index}: Skipped (no valid pad number)')
        
        # Keep pads in chain order (don't sort - preserve the order they appear in Ableton)
        # This ensures the visual layout matches what the user sees in Ableton
        
        logger.info(f'Extracted {len(pad_list)} drum rack pads')
        return pad_list
        
    except Exception as e:
        logger.error(f"Error extracting drum rack: {e}")
        import traceback
        traceback.print_exc()
        return pad_list


def detect_warped_stem(device):
    """
    Detect if a Simpler contains a warped stem sample.
    Returns dict with {'is_warped': bool, 'beat_count': int, 'loop_length_bars': float, 'trigger_mode': str}
    """
    result = {
        'is_warped': False,
        'beat_count': 0,
        'loop_length_bars': 0.0,
        'trigger_mode': 'gate'  # default
    }
    
    logger.debug('detect_warped_stem: Starting detection')
    try:
        # Extract trigger mode from Simpler (1-shot vs classic)
        # TriggerMode: 0=Gate, 1=Trigger(1-shot), 2=Toggle
        player = find_element_by_tag(device, 'Player')
        if player:
            trigger_mode_elem = find_element_by_tag(player, 'TriggerMode')
            if trigger_mode_elem and 'Value' in trigger_mode_elem.attrib:
                trigger_val = int(trigger_mode_elem.attrib['Value'])
                if trigger_val == 0:
                    result['trigger_mode'] = 'gate'
                elif trigger_val == 1:
                    result['trigger_mode'] = 'trigger'
                elif trigger_val == 2:
                    result['trigger_mode'] = 'toggle'
        
            # Check for warp properties in the sample
            # Use the player variable already found above (line 571)
            # No need to re-fetch it with safe_navigate
            multi_sample_map = find_element_by_tag(player, 'MultiSampleMap')
            if not multi_sample_map:
                logger.debug('  detect_warped_stem: No MultiSampleMap found')
                return result
            
            sample_parts = find_element_by_tag(multi_sample_map, 'SampleParts')
            if not sample_parts or len(sample_parts) == 0:
                logger.debug('  detect_warped_stem: No SampleParts found')
                return result
            
            part = sample_parts[0]
            logger.debug(f'  detect_warped_stem: Found SamplePart')
            
            # Check for SampleWarpProperties
            warp_props = find_element_by_tag(part, 'SampleWarpProperties')
            if warp_props:
                logger.debug('  detect_warped_stem: Found SampleWarpProperties!')
                logger.debug(f'  detect_warped_stem: SampleWarpProperties children: {[c.tag for c in warp_props]}')
                
                # Check both WarpMode and IsWarped flag
                warp_mode = find_element_by_tag(warp_props, 'WarpMode')
                is_warped_elem = find_element_by_tag(warp_props, 'IsWarped')
                logger.debug(f'  detect_warped_stem: IsWarped element found: {is_warped_elem is not None}')
                
                warp_mode_val = 0
                is_warped_val = False
                
                # CRITICAL FIX: ElementTree elements with no children/text are falsy even if they exist
                # Must check "is not None" instead of truthiness
                if warp_mode is not None and 'Value' in warp_mode.attrib:
                    warp_mode_val = int(warp_mode.attrib['Value'])
                    logger.info(f'  detect_warped_stem: WarpMode = {warp_mode_val}')
                
                # CRITICAL FIX: ElementTree elements with no children/text are falsy even if they exist
                # Must check "is not None" instead of truthiness
                if is_warped_elem is not None and 'Value' in is_warped_elem.attrib:
                    is_warped_str = is_warped_elem.attrib['Value']
                    is_warped_val = is_warped_str.lower() == 'true'
                    logger.info(f'  detect_warped_stem: IsWarped element found: attrib={is_warped_elem.attrib}, Value="{is_warped_str}", lower="{is_warped_str.lower()}", bool={is_warped_val}')
                else:
                    if is_warped_elem is not None:
                        logger.warning(f'  detect_warped_stem: IsWarped element found but no Value attribute: {is_warped_elem.attrib}')
                    else:
                        logger.debug(f'  detect_warped_stem: IsWarped element not found')
                
                # Try to extract loop length from LoopLength element
                # First check in SampleWarpProperties
                loop_length_elem = find_element_by_tag(warp_props, 'LoopLength')
                if not loop_length_elem:
                    # Try in part level
                    loop_length_elem = find_element_by_tag(part, 'LoopLength')
                
                if loop_length_elem and 'Value' in loop_length_elem.attrib:
                    loop_length_beats = float(loop_length_elem.attrib['Value'])
                    result['loop_length_bars'] = loop_length_beats / 4.0
                    result['beat_count'] = int(loop_length_beats)
                    logger.debug(f'  detect_warped_stem: Found LoopLength: {loop_length_beats} beats')
                
                # Always extract sample duration from SampleRef (for beat calculation)
                sample_ref = find_element_by_tag(part, 'SampleRef')
                logger.debug(f'  detect_warped_stem: Looking for SampleRef... found: {sample_ref is not None}')
                if sample_ref:
                    default_duration_elem = find_element_by_tag(sample_ref, 'DefaultDuration')
                    default_sample_rate_elem = find_element_by_tag(sample_ref, 'DefaultSampleRate')
                    logger.debug(f'  detect_warped_stem: DefaultDuration: {default_duration_elem is not None}, DefaultSampleRate: {default_sample_rate_elem is not None}')
                    logger.debug(f'  detect_warped_stem: Bool test - dur: {bool(default_duration_elem)}, rate: {bool(default_sample_rate_elem)}, and: {bool(default_duration_elem and default_sample_rate_elem)}')
                    
                    if default_duration_elem is not None and default_sample_rate_elem is not None:
                        logger.debug(f'  detect_warped_stem: About to extract duration values...')
                        try:
                            dur_val = default_duration_elem.attrib.get('Value')
                            rate_val = default_sample_rate_elem.attrib.get('Value')
                            logger.debug(f'  detect_warped_stem: Raw values - Duration: {dur_val}, SampleRate: {rate_val}')
                            
                            duration_samples = float(dur_val) if dur_val else 0
                            sample_rate = float(rate_val) if rate_val else 48000
                            logger.debug(f'  detect_warped_stem: Extracted values: {duration_samples} samples @ {sample_rate}Hz')
                            
                            if duration_samples > 0 and sample_rate > 0:
                                duration_seconds = duration_samples / sample_rate
                                result['sample_duration_seconds'] = duration_seconds
                                logger.info(f'  ✓ Sample duration: {duration_seconds:.2f}s = {duration_samples} samples @ {sample_rate}Hz')
                        except (ValueError, TypeError) as e:
                            logger.warning(f'  detect_warped_stem: Error extracting duration: {e}')
                
                # Sample is warped if WarpMode > 0 OR IsWarped = true
                if warp_mode_val > 0 or is_warped_val:
                    result['is_warped'] = True
                    logger.info(f'  ✓ Sample IS WARPED (WarpMode={warp_mode_val}, IsWarped={is_warped_val})')
                else:
                    logger.info(f'  ✗ Sample NOT warped (WarpMode={warp_mode_val}, IsWarped={is_warped_val})')
            else:
                logger.debug('  detect_warped_stem: No SampleWarpProperties found')
        
        # If we can't determine from warp properties, check if it's a long sample
        # (stems are typically longer than one-shots)
        # This is a heuristic fallback
        
        return result
        
    except Exception as e:
        logger.warning(f"Error detecting warped stem: {e}")
        return result


def extract_transpose_cents(device):
    """
    Extract Simpler transpose (in cents) from the Pitch section.
    Returns int cents (semitones * 100).
    """
    try:
        pitch_section = find_element_by_tag(device, 'Pitch')
        if pitch_section is None:
            return 0
        
        transpose_key = find_element_by_tag(pitch_section, 'TransposeKey')
        if transpose_key is None:
            return 0
        
        manual = find_element_by_tag(transpose_key, 'Manual')
        if manual is None:
            return 0
        
        value = manual.attrib.get('Value')
        if value is None:
            return 0
        
        transpose_semitones = float(value)
        return int(round(transpose_semitones * 100))
    except Exception as e:
        logger.debug(f'  extract_transpose_cents failed: {e}')
        return 0


def _simpler_globals_playback_mode(device):
    """
    Simpler Globals/PlaybackMode (Live 11/12):
      0 = Classic, 2 = Slice (Slicer). Other values are left to legacy rules downstream.
    """
    if device is None:
        return None
    globals_el = find_element_by_tag(device, 'Globals')
    if globals_el is None:
        return None
    pm = find_element_by_tag(globals_el, 'PlaybackMode')
    if pm is None or 'Value' not in pm.attrib:
        return None
    try:
        return int(pm.attrib['Value'])
    except (ValueError, TypeError):
        return None


def _collect_slice_points(sample_part, simpler_playback_mode=None):
    """
    Locate slice definitions inside a Simpler sample part.
    Returns tuple: (source_tag, seconds_list, samples_list)

    simpler_playback_mode: from _simpler_globals_playback_mode (0=Classic, 2=Slice).
    Classic must NOT use transient UserOnsets as slicer slices; Slice mode must still
    get InitialSlicePointsFromOnsets when warped (see Frozen_Git pad 1 vs pad 0).
    """
    slice_tags = [
        'ManualSlicePoints',
        'SlicePoints',
        'RegionSlicePoints',
        'BeatSlicePoints',
        'InitialSlicePointsFromOnsets'
    ]
    
    # Check whether the sample is warped. If IsWarped=true the Simpler is in Clip/Warp
    # mode (not Slice mode), so auto-detected onset points should NOT trigger slice mode.
    warp_props = find_element_by_tag(sample_part, 'SampleWarpProperties')
    is_warped = False
    if warp_props is not None:
        iw_elem = find_element_by_tag(warp_props, 'IsWarped')
        if iw_elem is not None and iw_elem.attrib.get('Value', 'false').lower() == 'true':
            is_warped = True

    for tag in slice_tags:
        container = find_element_by_tag(sample_part, tag)
        if container is not None and len(container):
            # InitialSlicePointsFromOnsets are auto-computed for all samples. Only treat as
            # slicer slices when in Live Slice mode (2), or when unwarped + user edited onsets.
            if tag == 'InitialSlicePointsFromOnsets':
                if simpler_playback_mode == 2:
                    pass  # Slice (Slicer): use onset grid even if warped / not editable
                else:
                    if is_warped:
                        continue
                    editable_el = find_element_by_tag(sample_part, 'AreSlicesFromOnsetsEditable')
                    if editable_el is None or editable_el.attrib.get('Value', 'false').lower() != 'true':
                        continue
            seconds, samples = _parse_slice_point_container(container)
            if seconds or samples:
                return tag, seconds, samples
    
    # UserOnsets: only in Slice mode, or legacy (mode unknown) when not warped — never Classic (0)
    if warp_props is not None:
        use_user_onsets = False
        if simpler_playback_mode == 2:
            use_user_onsets = True
        elif simpler_playback_mode == 0:
            use_user_onsets = False
        elif simpler_playback_mode is None and not is_warped:
            use_user_onsets = True
        if use_user_onsets:
            user_onsets = warp_props.find('.//UserOnsets')
            if user_onsets is not None and len(user_onsets):
                seconds, _samples = _parse_slice_point_container(user_onsets)
                if seconds:
                    return 'UserOnsets', seconds, []
    return None, [], []


def _collect_all_slice_points_for_embed(sample_part, default_sample_rate):
    """
    Union all Ableton slice/onset containers — regardless of Simpler PlaybackMode.
    Used for optional <slices> when cellmode is sample/clip (not full slicer).
    """
    if sample_part is None:
        return []
    slice_tags = [
        'ManualSlicePoints',
        'SlicePoints',
        'RegionSlicePoints',
        'BeatSlicePoints',
        'InitialSlicePointsFromOnsets',
    ]
    positions = []
    for tag in slice_tags:
        container = find_element_by_tag(sample_part, tag)
        if container is None or not len(container):
            continue
        seconds, samples = _parse_slice_point_container(container)
        for p in samples:
            try:
                positions.append(int(p))
            except (TypeError, ValueError):
                continue
        if seconds and default_sample_rate:
            try:
                rate = float(default_sample_rate)
                positions.extend(int(round(s * rate)) for s in seconds)
            except (TypeError, ValueError):
                pass
    warp_props = find_element_by_tag(sample_part, 'SampleWarpProperties')
    if warp_props is not None:
        user_onsets = warp_props.find('.//UserOnsets')
        if user_onsets is not None and len(user_onsets):
            seconds, samples = _parse_slice_point_container(user_onsets)
            for p in samples:
                try:
                    positions.append(int(p))
                except (TypeError, ValueError):
                    continue
            if seconds and default_sample_rate:
                try:
                    rate = float(default_sample_rate)
                    positions.extend(int(round(s * rate)) for s in seconds)
                except (TypeError, ValueError):
                    pass
    return sorted(set(max(0, int(p)) for p in positions))


def extract_slicing_info(device, sample_part):
    """
    Gather slicing-related metadata from a Simpler device.
    Returns dict with slice positions (samples), beat grid, playback mode, etc.
    """
    info = {
        'has_slices': False,
        'slice_positions_samples': [],
        'slice_times_seconds': [],
        'slice_source': None,
        'default_sample_rate': None,
        'default_duration_samples': None,
        'slicing_style': None,
        'slicing_playback_mode': None,
        'playthrough': False,
        'beat_grid': None,
        'transpose_cents': extract_transpose_cents(device),
        # Beta: warped classic clip — onset positions for <slices> without forcing slicer mode
        'clip_transient_embed_samples': [],
        # All onset/slice markers (any Live mode); for optional <slices> when not Slicer cellmode
        'non_slicer_slice_samples': [],
    }
    
    if device is None or sample_part is None:
        return info
    
    try:
        simpler_pb = _simpler_globals_playback_mode(device)
        info['simpler_playback_mode'] = simpler_pb

        sample_ref = find_element_by_tag(sample_part, 'SampleRef')
        if sample_ref is not None:
            duration_elem = find_element_by_tag(sample_ref, 'DefaultDuration')
            if duration_elem is not None and 'Value' in duration_elem.attrib:
                try:
                    info['default_duration_samples'] = int(round(float(duration_elem.attrib['Value'])))
                except ValueError:
                    pass
            
            rate_elem = find_element_by_tag(sample_ref, 'DefaultSampleRate')
            if rate_elem is not None and 'Value' in rate_elem.attrib:
                try:
                    info['default_sample_rate'] = float(rate_elem.attrib['Value'])
                except ValueError:
                    pass

        # Beta: extract Auto Onsets for warped samples (Classic + Warp → clip mode + transient embed)
        warp_props_embed = find_element_by_tag(sample_part, 'SampleWarpProperties')
        is_warped_embed = False
        if warp_props_embed is not None:
            iw_embed = find_element_by_tag(warp_props_embed, 'IsWarped')
            if iw_embed is not None and iw_embed.attrib.get('Value', 'false').lower() == 'true':
                is_warped_embed = True
        # Classic (0) and Slice (2) do not use warped auto-onset embed here — Classic stays sample;
        # Slice uses real slice positions from _collect_slice_points.
        if is_warped_embed and simpler_pb not in (0, 2):
            onsets_container = find_element_by_tag(sample_part, 'InitialSlicePointsFromOnsets')
            if onsets_container is not None and len(onsets_container):
                sec_emb, smp_emb = _parse_slice_point_container(onsets_container)
                embed_pts = list(smp_emb)
                if sec_emb and info['default_sample_rate']:
                    embed_pts.extend(
                        int(round(s * float(info['default_sample_rate']))) for s in sec_emb
                    )
                info['clip_transient_embed_samples'] = sorted(
                    set(max(0, int(p)) for p in embed_pts)
                )
        
        # Record slicing parameters stored on the sample part
        style_elem = find_element_by_tag(sample_part, 'SlicingStyle')
        if style_elem is not None and 'Value' in style_elem.attrib:
            info['slicing_style'] = style_elem.attrib['Value']
        
        beat_grid_elem = find_element_by_tag(sample_part, 'SlicingBeatGrid')
        if beat_grid_elem is not None and 'Value' in beat_grid_elem.attrib:
            info['beat_grid'] = beat_grid_elem.attrib['Value']
        
        source_tag, seconds, samples = _collect_slice_points(sample_part, simpler_playback_mode=simpler_pb)
        if source_tag:
            info['slice_source'] = source_tag
            info['has_slices'] = True
            if seconds:
                info['slice_times_seconds'] = seconds
            if samples:
                info['slice_positions_samples'].extend(samples)
            if seconds and info['default_sample_rate']:
                positions = [int(round(sec * info['default_sample_rate'])) for sec in seconds]
                info['slice_positions_samples'].extend(positions)
        
        # Read slicing playback mode (Mono/Poly/Thru)
        simpler_slicing = find_element_by_tag(device, 'SimplerSlicing')
        if simpler_slicing is not None:
            playback_mode_elem = find_element_by_tag(simpler_slicing, 'PlaybackMode')
            if playback_mode_elem is not None and 'Value' in playback_mode_elem.attrib:
                try:
                    playback_mode = int(playback_mode_elem.attrib['Value'])
                except ValueError:
                    playback_mode = None
                info['slicing_playback_mode'] = playback_mode
                info['playthrough'] = playback_mode == 2
        
        if info['slice_positions_samples']:
            info['slice_positions_samples'] = sorted(
                set(max(0, int(pos)) for pos in info['slice_positions_samples']))

        # Informative / optional embed: every slice container Live saved, even in Classic / non-Slicer
        if not info['has_slices']:
            info['non_slicer_slice_samples'] = _collect_all_slice_points_for_embed(
                sample_part, info.get('default_sample_rate'))
            nss = info['non_slicer_slice_samples']
            if not nss or (len(nss) == 1 and nss[0] == 0):
                info['non_slicer_slice_samples'] = []
        
        return info
    except Exception as e:
        logger.debug(f'  extract_slicing_info failed: {e}')
        return info


def sampler_extract(device):
    """Extract sampler/simpler parameters with robust error handling."""
    params = {}
    
    try:
        # Live 12.2+ structure: Use tag-based navigation for Player element
        # Try Live 12.2+ path first (works for 12.2, 12.3+)
        player = find_element_by_tag(device, 'Player')
        sample_map = None
        
        if player:
            multi_sample_map = find_element_by_tag(player, 'MultiSampleMap')
            if multi_sample_map:
                sample_parts = find_element_by_tag(multi_sample_map, 'SampleParts')
                if sample_parts and len(sample_parts) > 0:
                    # In Live 12.2+, SampleParts contains MultiSamplePart elements
                    # We'll iterate through SampleParts (which is the container)
                    sample_map = sample_parts
                    logger.info("  Found Live 12.2+ structure")
        
        # Fallback to older Live 10/11 structure
        if sample_map is None:
            sample_map = safe_navigate(device, "MultiSampleMap", 15, 0, 0)
            if sample_map:
                logger.info("  Found Live 10/11 structure")
        
        if sample_map is None:
            logger.warning("Could not find sample map in device")
            return None
        
        # Extract multisample parts
        sample_names = []
        filepaths = []
        rootkeys = []
        keyrangemins = []
        keyrangemaxs = []
        
        # Check if this is Live 12.2+ structure (SampleParts contains MultiSamplePart)
        if sample_map.tag == 'SampleParts':
            # Live 12.2+ structure - iterate through all children
            for i in range(len(sample_map)):
                part = sample_map[i]
                
                # Skip if not a MultiSamplePart
                if part.tag != 'MultiSamplePart':
                    continue
                
                # Find SampleRef in this part using tag-based search
                sample_ref = None
                for child in part:
                    if child.tag == 'SampleRef':
                        sample_ref = child
                        logger.info(f'  Found SampleRef in MultiSamplePart[{i}]')
                        break
                
                if sample_ref is None:
                    logger.warning(f'  No SampleRef found in MultiSamplePart[{i}]')
                    continue
                
                # Live 12.2+ uses FileRef with Path element
                file_ref = None
                for child in sample_ref:
                    if child.tag == 'FileRef':
                        file_ref = child
                        logger.info(f'  Found FileRef')
                        break
                
                if file_ref:
                    # Look for Path element using tag-based search (prefer absolute Path over RelativePath)
                    path_elem = None
                    rel_path_elem = None
                    for child in file_ref:
                        if child.tag == 'Path':
                            if 'Value' in child.attrib:
                                path_elem = child
                                logger.info(f'  Found Path element')
                        elif child.tag == 'RelativePath':
                            if 'Value' in child.attrib:
                                rel_path_elem = child
                                logger.info(f'  Found RelativePath element')
                    
                    if path_elem is not None:
                        logger.info(f'  Entering path_elem block')
                        try:
                            full_path = path_elem.attrib['Value']
                            logger.info(f'  Got path value: {full_path[:50]}...')
                            filepaths.append(full_path)
                            sample_name = full_path.split('/')[-1]
                            sample_names.append(sample_name)
                            logger.info(f'  Found sample: {sample_name}')
                            logger.info(f'  Full path: {full_path}')
                        except Exception as e:
                            logger.warning(f'  Error extracting Path value: {e}')
                            import traceback
                            traceback.print_exc()
                            path_elem = None  # Reset so we can try RelativePath
                    
                    if path_elem is None and rel_path_elem is not None:
                        try:
                            rel_path_value = rel_path_elem.attrib['Value']
                            filepaths.append(rel_path_value)
                            sample_name = rel_path_value.split('/')[-1]
                            sample_names.append(sample_name)
                            logger.info(f'  Found sample (relative): {sample_name}')
                            logger.info(f'  Relative path: {rel_path_value}')
                        except Exception as e:
                            logger.warning(f'  Error extracting RelativePath value: {e}')
                    
                    if path_elem is None and rel_path_elem is None:
                        logger.warning(f'  Could not find Path or RelativePath in FileRef')
                        continue
                    
                    if len(filepaths) == 0:
                        continue
                else:
                    logger.warning(f'  No FileRef found in SampleRef')
                    continue
                
                # Get root key for Live 12.2+
                root_key = safe_navigate(part, "RootKey", 8)
                if root_key and 'Value' in root_key.attrib:
                    rootkeys.append(root_key.attrib['Value'])
                    logger.info(f'  Root key: {root_key.attrib["Value"]}')
                else:
                    rootkeys.append('60')  # Default middle C
                
                # Get key range for Live 12.2+
                key_range = safe_navigate(part, "KeyRange", 5)
                if key_range:
                    key_min = safe_navigate(key_range, "KeyRangeMin", 0)
                    key_max = safe_navigate(key_range, "KeyRangeMax", 1)
                    
                    if key_min and 'Value' in key_min.attrib:
                        keyrangemins.append(key_min.attrib['Value'])
                    else:
                        keyrangemins.append('0')
                    
                    if key_max and 'Value' in key_max.attrib:
                        keyrangemaxs.append(key_max.attrib['Value'])
                    else:
                        keyrangemaxs.append('127')
                    
                    logger.info(f'  Key range: {keyrangemins[-1]} - {keyrangemaxs[-1]}')
                else:
                    keyrangemins.append('0')
                    keyrangemaxs.append('127')
        else:
            # Live 10/11 structure (old path)
            for i in range(len(sample_map)):
                part = sample_map[i]
                
                # Try to find sample reference
                sample_ref = safe_navigate(part, "SampleRef", 18, 0)
                if sample_ref is None:
                    continue
                
                # Get file name
                file_ref = safe_navigate(sample_ref, "FileName", 3)
                if file_ref and 'Value' in file_ref.attrib:
                    sample_name = file_ref.attrib['Value']
                    sample_names.append(sample_name)
                else:
                    continue
                
                # Get file path
                path_hint = safe_navigate(sample_ref, "PathHint", 7, 0)
                if path_hint:
                    filepath = ''
                    for k in range(len(path_hint)):
                        if 'Dir' in path_hint[k].attrib:
                            filepath = filepath + '/' + path_hint[k].attrib['Dir']
                    filepaths.append(filepath + '/' + sample_name)
                else:
                    filepaths.append(sample_name)
            
            # Get root key
            root_key = safe_navigate(part, "RootKey", 8)
            if root_key and 'Value' in root_key.attrib:
                rootkeys.append(root_key.attrib['Value'])
                logger.info(f'  Root key: {root_key.attrib["Value"]}')
            else:
                rootkeys.append('60')  # Default middle C
            
            # Get key range
            key_range = safe_navigate(part, "KeyRange", 5)
            if key_range:
                key_min = safe_navigate(key_range, "KeyRangeMin", 0)
                key_max = safe_navigate(key_range, "KeyRangeMax", 1)
                
                if key_min and 'Value' in key_min.attrib:
                    keyrangemins.append(key_min.attrib['Value'])
                else:
                    keyrangemins.append('0')
                
                if key_max and 'Value' in key_max.attrib:
                    keyrangemaxs.append(key_max.attrib['Value'])
                else:
                    keyrangemaxs.append('127')
                
                logger.info(f'  Key range: {keyrangemins[-1]} - {keyrangemaxs[-1]}')
            else:
                keyrangemins.append('0')
                keyrangemaxs.append('127')
        
        if not filepaths:
            logger.warning("No samples found in device")
            return None
        
        params['filepath'] = filepaths
        params['rootkey'] = rootkeys
        params['keyrangemin'] = keyrangemins
        params['keyrangemax'] = keyrangemaxs
        
        if not filepaths:
            logger.warning("No samples found in device")
            return None
        
        params['filepath'] = filepaths
        params['rootkey'] = rootkeys
        params['keyrangemin'] = keyrangemins
        params['keyrangemax'] = keyrangemaxs
        
        logger.info(f'  Sample names: {sample_names}')
        logger.info(f'  Sample filepaths: {filepaths}')
        
        # Store with both key names for compatibility
        params['filepaths'] = filepaths
        params['rootkeys'] = rootkeys
        params['keyrangemins'] = keyrangemins
        params['keyrangemaxs'] = keyrangemaxs
        
        # Find sample start and end points
        if sample_map.tag == 'SampleParts' and len(sample_map) > 0:
            # Live 12.2+ structure
            first_part = sample_map[0]
        else:
            first_part = sample_map[0]
        
        # Extract slicing metadata + transpose
        slicing_info = extract_slicing_info(device, first_part)
        params['slicing'] = slicing_info
        params['transpose_cents'] = slicing_info.get('transpose_cents', 0)
        
        sample_start = None
        sample_end = None
        for child in first_part:
            if child.tag == 'SampleStart' and 'Value' in child.attrib:
                sample_start = child
            if child.tag == 'SampleEnd' and 'Value' in child.attrib:
                sample_end = child
        
        if sample_start:
            params['sample_start'] = sample_start.attrib['Value']
        else:
            params['sample_start'] = '0'
        
        if sample_end:
            params['sample_end'] = sample_end.attrib['Value']
        else:
            params['sample_end'] = '44100'  # Default 1 second at 44.1kHz
        
        logger.info(f'  Play start: {params["sample_start"]}')
        logger.info(f'  Play end: {params["sample_end"]}')
        
        # Find loop settings
        loop_on = None
        loop_start = None
        loop_end = None
        
        for child in first_part:
            if child.tag == 'LoopOn' and 'Value' in child.attrib:
                loop_on = child
            if child.tag == 'LoopStart' and 'Value' in child.attrib:
                loop_start = child
            if child.tag == 'LoopEnd' and 'Value' in child.attrib:
                loop_end = child
        
        if loop_on:
            params['loop_on'] = loop_on.attrib['Value']
        else:
            params['loop_on'] = '0'  # Default off
        
        if loop_start:
            params['loop_start'] = loop_start.attrib['Value']
        else:
            params['loop_start'] = '0'
        
        if loop_end:
            params['loop_end'] = loop_end.attrib['Value']
        else:
            params['loop_end'] = params.get('sample_end', '44100')
        
        logger.info(f'  Loop On: {params["loop_on"]}')
        logger.info(f'  Loop Start: {params["loop_start"]}')
        logger.info(f'  Loop End: {params["loop_end"]}')
        
        # Find envelope settings
        # Try to locate the amplitude envelope
        envelope = safe_navigate(device, "VolumeEnvelope", 19, 8)
        
        if envelope:
            attack = safe_navigate(envelope, "Attack", 0, 1)
            decay = safe_navigate(envelope, "Decay", 3, 1)
            sustain = safe_navigate(envelope, "Sustain", 6, 1)
            release = safe_navigate(envelope, "Release", 7, 1)
            
            params['attack'] = attack.attrib.get('Value', '1') if attack else '1'
            params['decay'] = decay.attrib.get('Value', '300') if decay else '300'
            params['sustain'] = sustain.attrib.get('Value', '1') if sustain else '1'
            params['release'] = release.attrib.get('Value', '200') if release else '200'
        else:
            # Default envelope values
            params['attack'] = '1'
            params['decay'] = '300'
            params['sustain'] = '1'
            params['release'] = '200'
        
        logger.info(f'  Vol Env Attack: {round(float(params["attack"]))} ms')
        logger.info(f'  Vol Env Decay: {round(float(params["decay"]))} ms')
        logger.info(f'  Vol Env Sustain: {round(8.6859*math.log(max(float(params["sustain"]), 0.001)))} dB')
        logger.info(f'  Vol Env Release: {round(float(params["release"]))} ms')
        
        return params
        
    except Exception as e:
        logger.error(f"Error extracting sampler parameters: {e}")
        import traceback
        traceback.print_exc()
        return None


def track_iterator(tracks):
    """
    Extract drum rack and MIDI tracks from Ableton project.
    Returns: (pad_list, midi_tracks)
    
    Expected structure:
    - Track 1: Drum Rack with 16 pads
    - Tracks 2-17: MIDI tracks for sequences
    """
    if len(tracks) == 0:
        logger.error('No tracks found in project')
        return [], []
    
    # First track should contain DrumGroupDevice
    first_track = tracks[0]
    devices, track_type = device_extract(first_track, 1)
    
    if 'DrumGroupDevice' not in devices:
        logger.error('ERROR: No DrumGroupDevice found in first track!')
        logger.error('This script requires a Drum Rack in the first track.')
        logger.error('Please set up your project with:')
        logger.error('  - Track 1: Drum Rack with up to 16 Simplers')
        logger.error('  - Tracks 2-17: MIDI tracks for sequences')
        return [], []
    
    logger.info('='*60)
    logger.info('DRUM RACK DETECTED')
    logger.info('='*60)
    logger.warning('NOTE: Pad mapping uses CHAIN ORDER, not MIDI notes!')
    logger.warning('Ensure chains are ordered correctly in Ableton before converting.')
    logger.warning('Chain 0 → Pad 0, Chain 1 → Pad 1, etc.')
    logger.info('='*60)
    
    # Extract drum rack pads
    drum_rack = devices['DrumGroupDevice']
    pad_list = drum_rack_extract(drum_rack)
    
    def _get_track_name(t):
        name_elem = find_element_by_tag(t, 'Name')
        if name_elem is not None:
            effective = find_element_by_tag(name_elem, 'EffectiveName') or find_element_by_tag(name_elem, 'UserName')
            return (effective.attrib.get('Value', '') or '') if effective is not None else (name_elem.attrib.get('Value', '') or '')
        return ''

    def _get_track_group_id(t):
        """Return the TrackGroupId value for a track, or '-1' if not in any group."""
        tg_elem = find_element_by_tag(t, 'TrackGroupId')
        if tg_elem is not None:
            return tg_elem.attrib.get('Value', '-1')
        return '-1'

    # In ALS, GroupTracks and MidiTracks are SIBLINGS in the parent Tracks element.
    # A MidiTrack's TrackGroupId points to the Id of its parent GroupTrack.
    # Find the "Seq" GroupTrack by name, then collect only MidiTracks with a matching TrackGroupId.
    seq_group_id = None
    for t in tracks:
        if t.tag == 'GroupTrack':
            gname = _get_track_name(t).lower()
            if 'seq' in gname:
                seq_group_id = t.attrib.get('Id', None)
                logger.info(f'Found Seq GroupTrack: "{_get_track_name(t)}" (Id={seq_group_id})')
                break

    if seq_group_id is not None:
        candidates = [t for t in tracks if t.tag == 'MidiTrack' and _get_track_group_id(t) == seq_group_id]
    else:
        # Fallback: no Seq group found — gather all MidiTracks except the drum rack (first one).
        logger.warning('No "Seq" GroupTrack found; falling back to all MidiTracks (may pick up wrong tracks).')
        all_midi = [t for t in tracks if t.tag == 'MidiTrack']
        candidates = all_midi[1:]  # skip track 0 (drum rack)

    midi_tracks = []
    midi_track_info = []
    for track in candidates:
        track_name = _get_track_name(track)
        midi_tracks.append(track)
        midi_track_info.append((track, track_name, len(midi_tracks) - 1))
        logger.info(f'  Found MIDI track (name="{track_name}") for sequence (midi_tracks index {len(midi_tracks)-1})')
        if len(midi_tracks) >= 16:
            break
    logger.info(f'Extracted {len(pad_list)} drum pads and {len(midi_tracks)} MIDI tracks')
    return pad_list, midi_tracks, midi_track_info


# Helper functions for generating Blackbox XML

def row_column(pad):
    rc_dict = {0:[0,0], 1:[0,1], 2:[0,2], 3:[0,3],
               4:[1,0], 5:[1,1], 6:[1,2], 7:[1,3],
               8:[2,0], 9:[2,1], 10:[2,2], 11:[2,3],
               12:[3,0], 13:[3,1], 14:[3,2], 15:[3,3],
               16:[0,4], 17:[1,4], 18:[2,4], 19:[3,4]}
    rc = rc_dict.get(int(pad), [0, 0])
    row = rc[0]
    column = rc[1]
    return (row, column)

def _project_label_from_input_path(input_path):
    """
    Derive a human project label from the input ALS path.
    Example:
      ".../Connection Error Blackbox Project/Connection Error Blackbox Song Mode.als"
      -> "Connection Error"
    """
    if not input_path:
        return ''

    parent_name = os.path.basename(os.path.dirname(input_path)).strip()
    if parent_name:
        parent_name = re.sub(r'\s+Blackbox\s+Project$', '', parent_name, flags=re.IGNORECASE).strip()
        if parent_name:
            return parent_name

    base = os.path.splitext(os.path.basename(input_path))[0].strip()
    if not base:
        return ''
    base = re.sub(r'\s+Blackbox(\s+Song\s+Mode)?(\s+no\s+group)?$', '', base, flags=re.IGNORECASE).strip()
    return base


def _append_project_suffix(filename, project_label):
    """Append [Project Name] before extension for exported sample files."""
    if not filename or not project_label:
        return filename

    safe_label = re.sub(r'[\\/:*?"<>|]', '_', project_label).strip()
    if not safe_label:
        return filename

    stem, ext = os.path.splitext(filename)
    if not ext:
        ext = '.wav'
    if stem.endswith(f'[{safe_label}]'):
        return stem + ext
    return f'{stem}[{safe_label}]{ext}'


def pad_dicter(row, column, filename, type):
    cell_dict = {'row':str(row), 'column':str(column), 'layer':"0", 'filename':filename, 'type':type}
    return(cell_dict)

def pad_params_dicter(envattack, envdecay, envsus, envrel, samstart, samlen, multisammode, loopmode, loopstart, loopend, beatcount, samtrigtype, cellmode, polymode):
    params_dict = {'gaindb': '0', 'pitch': '0', 'panpos': '0', 'samtrigtype': str(samtrigtype), 'loopmode': str(loopmode), 
                    'loopmodes': '0', 'midimode': '0', 'midioutchan': '0', 'reverse': '0', 'cellmode': str(cellmode), 
                    'envattack': str(envattack), 'envdecay': str(envdecay), 'envsus': str(envsus), 
                    'envrel': str(envrel), 'samstart': str(samstart), 'samlen': str(samlen), 'loopstart': str(loopstart), 
                    'loopend': str(loopend), 'quantsize': '3', 'synctype': '5', 'actslice': '1', 'outputbus': '0', 
                    'polymode': str(polymode), 'polymodeslice': '0', 'slicestepmode': '0', 'chokegrp': '0', 'dualfilcutoff': '0', 
                    'res': '500', 'rootnote': '0', 'beatcount': str(beatcount), 'fx1send': '0', 'fx2send': '0', 'multisammode': multisammode, 
                    'interpqual': '0', 'playthru': '0', 'slicerquantsize': '13', 'slicersync': '0', 'padnote': '0', 
                    'loopfadeamt': '0', 'lfowave': '0', 'lforate': '100', 'lfoamount': '1000', 'lfokeytrig': '0', 'lfobeatsync': '0', 
                    'lforatebeatsync': '0', 'grainsizeperc': '300', 'grainscat': '0', 'grainpanrnd': '0', 'graindensity': '600', 
                    'slicemode': '0', 'legatomode': '0', 'gainssrcwin': '0', 'grainreadspeed': '1000', 'recpresetlen': '0', 
                    'recquant': '3', 'recinput': '0', 'recinputmulti': '0', 'recusethres': '0', 'recthresh': '-20000', 'recmonoutbus': '0'}
    return(params_dict)

def make_drum_rack_pads(session, pad_list, tempo, project_label=''):
    """
    Create Blackbox pads from Drum Rack pad list.
    Each pad in pad_list contains: {'blackbox_pad': 0-15, 'simpler': device, 'choke_group': 0-16, ...}
    """
    # Convert tempo to float (it comes as a string from track_tempo_extractor)
    try:
        tempo = float(tempo)
    except (ValueError, TypeError):
        logger.warning(f'Invalid tempo value: {tempo}, using 120 BPM')
        tempo = 120.0
    
    assets = []
    
    for pad_info in pad_list:
        pad_num = pad_info['blackbox_pad']
        row, column = row_column(pad_num)
        
        slice_positions = []
        if not pad_info['is_empty'] and pad_info['simpler']:
            # Extract sample parameters from Simpler
            params = sampler_extract(pad_info['simpler'])
            
            if params is None:
                # Sample extraction failed, treat as empty pad
                logger.warning(f'  Pad {pad_num}: Sample extraction failed, creating empty pad')
                cell = ET.SubElement(session, 'cell')
                cell.attrib = pad_dicter(row, column, '', 'samtempl')
                param_elem = ET.SubElement(cell, 'params')
                param_elem.attrib = pad_params_dicter('0', '0', '1000', '4', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0')
                param_elem.attrib['chokegrp'] = '0'
            else:
                # Get sample filepath for copying and determine output filename
                source_filepath = params.get('filepaths', [''])[0] if params.get('filepaths') else ''
                original_filename = source_filepath.split('/')[-1] if source_filepath else ''
                
                # Use Simpler preset name (UserName) as output filename when available
                preset_name = pad_info.get('preset_name', '').strip()
                if preset_name and original_filename:
                    ext = os.path.splitext(original_filename)[1] or '.wav'
                    # Sanitise preset name: strip characters not safe in filenames
                    safe_name = re.sub(r'[\\/:*?"<>|]', '_', preset_name)
                    sample_filename = safe_name + ext
                else:
                    sample_filename = original_filename

                sample_filename = _append_project_suffix(sample_filename, project_label)
                
                logger.info(f'  Pad {pad_num}: {sample_filename} (source: {original_filename})')
                
                # Track source→dest for the copy step
                if source_filepath:
                    assets.append((source_filepath, sample_filename))
                
                filename_path = '.\\' + sample_filename if sample_filename else ''
                cell = ET.SubElement(session, 'cell')
                cell.attrib = pad_dicter(row, column, filename_path, 'sample')
                
                # Get WAV file info for sample length
                filepath = params.get('filepaths', [''])[0] if params.get('filepaths') else ''
                wav_info = get_wav_info(filepath) if filepath else None
                
                # Set sample length from WAV file
                samlen = '0'
                if wav_info:
                    samlen = str(wav_info['sample_length_samples'])
                    logger.debug(f'    WAV info: {wav_info["sample_length_samples"]} samples, {wav_info["duration_seconds"]:.2f}s @ {wav_info["sample_rate"]}Hz')
                
                # Check if this is a warped stem
                warp_info = detect_warped_stem(pad_info['simpler'])
                slicing_preview = params.get('slicing') or {}
                simpler_pb = slicing_preview.get('simpler_playback_mode')

                # Determine loop mode and trigger mode
                loopmode = '0'  # off by default
                loopstart = params.get('loop_start', '0')
                loopend = params.get('loop_end', samlen)
                beatcount = '0'
                samtrigtype = '0'  # Default: gate
                cellmode = '0'  # Default: sampler mode
                
                # Extract trigger mode (1-shot vs classic/gate)
                trigger_mode = warp_info.get('trigger_mode', 'gate')
                if trigger_mode == 'trigger':
                    samtrigtype = '1'  # 1-shot/trigger
                elif trigger_mode == 'toggle':
                    samtrigtype = '2'  # Toggle
                else:
                    samtrigtype = '0'  # Gate (classic mode)
                
                # Check if warp info contains beat count OR calculate from sample duration
                beat_count = warp_info.get('beat_count', 0)
                sample_duration = warp_info.get('sample_duration_seconds', 0)
                
                # If no beat_count but we have duration, calculate it from tempo
                if beat_count == 0 and sample_duration > 0:
                    # Calculate beats from duration and tempo
                    # beats = (duration_seconds * tempo) / 60
                    beats_calculated = (sample_duration * tempo) / 60
                    # Round to nearest beat (not to nearest 4 beats) for accuracy
                    # This matches Ableton's calculation more closely
                    beat_count = int(round(beats_calculated))
                    if beat_count < 1:
                        beat_count = 0
                    logger.debug(f'    Calculated {beat_count} beats from {sample_duration:.2f}s @ {tempo} BPM')
                
                if beat_count > 0:
                    # Sample has beat count (either from warp or calculated)
                    beatcount = str(beat_count)
                    
                    # Use clip mode if sample is warped (regardless of beat_count)
                    # Warped: clip mode only when not Live Classic (0) or Slice/Slicer (2)
                    if warp_info.get('is_warped', False):
                        if simpler_pb == 2:
                            cellmode = '0'
                            loopmode = '0'
                            logger.info(
                                f'    → Warped Slicer mode: {beat_count} beats — cell mode from slice points'
                            )
                        elif simpler_pb == 0:
                            cellmode = '0'
                            loopmode = '0'
                            logger.info(
                                f'    → Warped Classic: {beat_count} beats — sample mode (not clip)'
                            )
                        else:
                            cellmode = '1'  # Clip mode for other / legacy warped samples
                            loopmode = '1'  # Loop enabled
                            logger.info(
                                f'    → Warped sample: {beat_count} beats ({beat_count/4} bars), clip mode enabled'
                            )
                    else:
                        # Unwarped sample - sampler mode
                        loopmode = '0'  # Don't enable loop by default for unwarped samples
                        logger.info(f'    → Unwarped sample: {beat_count} beats ({beat_count/4} bars), sampler mode (no auto-loop)')
                else:
                    # No warp beat info - check if loop is manually enabled
                    loop_on = params.get('loop_on', '0') == '1' or params.get('loop_on', '0') == 'true'
                    
                    if loop_on and wav_info:
                        # Calculate beat count from loop length
                        try:
                            loop_length_samples = float(loopend) - float(loopstart)
                            loop_length_seconds = loop_length_samples / wav_info['sample_rate']
                            
                            # Calculate beats from duration and tempo
                            beats_calculated = (loop_length_seconds * tempo) / 60
                            beats = int(round(beats_calculated))  # Round to nearest beat
                            if beats < 1:
                                beats = 0
                            
                            if beats >= 8:
                                cellmode = '1'  # Clip mode
                                loopmode = '1'  # Loop enabled
                                beatcount = str(beats)
                                logger.info(f'    → Looped sample: {loop_length_seconds:.1f}s = {beats} beats, clip mode enabled')
                            else:
                                # Short loop - use sampler mode with loop
                                loopmode = '1'  # Loop enabled
                                beatcount = str(beats)
                                logger.info(f'    → Short loop: {loop_length_seconds:.1f}s = {beats} beats, sampler mode with loop')
                        except (ValueError, TypeError, ZeroDivisionError) as e:
                            logger.debug(f'    Could not calculate loop length: {e}')
                            pass
                
                # Add choke group
                choke_group = str(pad_info.get('choke_group', 0))
                
                # Create params element
                param_elem = ET.SubElement(cell, 'params')
                
                slicing_info = params.get('slicing', {}) or {}
                has_slices = slicing_info.get('has_slices', False)
                if has_slices:
                    cellmode = '2'
                    loopmode = '0'
                # Beta: warped classic (clip) — keep cellmode 1; embed onsets under <slices> (not full slicer mode)
                clip_transient_embed_only = (
                    not has_slices
                    and cellmode == '1'
                    and warp_info.get('is_warped', False)
                    and bool(slicing_info.get('clip_transient_embed_samples'))
                )
                
                # For clip mode samples, use default envelope settings
                # 0% attack, 100% decay, 100% sustain, 20% release
                if cellmode == '1' or has_slices:
                    env_attack = '0'
                    env_decay = '1000'  # 100% = 1000
                    env_sustain = '1000'  # 100% = 1000
                    env_release = '200'  # 20% = 200
                else:
                    # Use extracted envelope settings for sampler mode
                    env_attack = params.get('attack', '0')
                    env_decay = params.get('decay', '0')
                    env_sustain = params.get('sustain', '1000')
                    env_release = params.get('release', '4')
                
                param_attrib = pad_params_dicter(
                    env_attack,
                    env_decay,
                    env_sustain,
                    env_release,
                    params.get('sample_start', '0'),
                    samlen,  # Use actual sample length from WAV file
                    params.get('multisammode', '0'),
                    loopmode,
                    loopstart,  # From Ableton loop settings
                    loopend,    # From Ableton loop settings
                    beatcount,
                    samtrigtype,  # Use extracted trigger mode
                    cellmode,  # Use clip mode for warped samples
                    '0'   # polymode
                )
                param_attrib['chokegrp'] = choke_group
                
                transpose_cents = params.get('transpose_cents')
                if transpose_cents is not None:
                    try:
                        param_attrib['pitch'] = str(int(transpose_cents))
                    except (TypeError, ValueError):
                        pass
                
                if has_slices:
                    param_attrib['cellmode'] = '2'
                    param_attrib['loopmode'] = '0'
                    param_attrib['quantsize'] = '8'
                    param_attrib['slicerquantsize'] = '8'
                    param_attrib['playthru'] = '1' if slicing_info.get('playthrough') else '0'
                    if warp_info.get('is_warped', False):
                        param_attrib['synctype'] = '6'
                        param_attrib['slicersync'] = '1'
                    else:
                        param_attrib['synctype'] = '5'
                        param_attrib['slicersync'] = '0'
                
                param_elem.attrib = param_attrib
                
                if has_slices:
                    default_rate = slicing_info.get('default_sample_rate')
                    slice_positions = list(slicing_info.get('slice_positions_samples', []))
                    if not slice_positions and slicing_info.get('slice_times_seconds'):
                        rate = wav_info['sample_rate'] if wav_info else default_rate
                        if rate:
                            slice_positions = [int(round(sec * float(rate))) for sec in slicing_info['slice_times_seconds']]
                    if slice_positions and wav_info and default_rate and default_rate > 0:
                        try:
                            if float(default_rate) != float(wav_info['sample_rate']):
                                scale = float(wav_info['sample_rate']) / float(default_rate)
                                slice_positions = [int(round(pos * scale)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            pass
                    if slice_positions:
                        try:
                            samlen_int = int(float(samlen))
                            slice_positions = [max(0, min(int(pos), samlen_int)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            slice_positions = [max(0, int(pos)) for pos in slice_positions]
                        slice_positions = sorted(set(slice_positions))
                elif clip_transient_embed_only:
                    default_rate = slicing_info.get('default_sample_rate')
                    slice_positions = list(slicing_info.get('clip_transient_embed_samples', []))
                    if slice_positions and wav_info and default_rate and default_rate > 0:
                        try:
                            if float(default_rate) != float(wav_info['sample_rate']):
                                scale = float(wav_info['sample_rate']) / float(default_rate)
                                slice_positions = [int(round(pos * scale)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            pass
                    if slice_positions:
                        try:
                            samlen_int = int(float(samlen))
                            slice_positions = [max(0, min(int(pos), samlen_int)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            slice_positions = [max(0, int(pos)) for pos in slice_positions]
                        slice_positions = sorted(set(slice_positions))
                        before_ct = len(slice_positions)
                        try:
                            samlen_int_lim = int(float(samlen))
                        except (TypeError, ValueError):
                            samlen_int_lim = None
                        slice_positions = limit_slice_positions_density_priority(
                            slice_positions, CLIP_TRANSIENT_SLICE_MAX_BETA, samlen_int_lim
                        )
                        if before_ct > len(slice_positions):
                            logger.info(
                                f'    → Clip transient embed: {before_ct} onsets → {len(slice_positions)} '
                                f'(max {CLIP_TRANSIENT_SLICE_MAX_BETA}, density priority)'
                            )
                elif not has_slices and slicing_info.get('non_slicer_slice_samples'):
                    default_rate = slicing_info.get('default_sample_rate')
                    slice_positions = list(slicing_info.get('non_slicer_slice_samples', []))
                    if slice_positions and wav_info and default_rate and default_rate > 0:
                        try:
                            if float(default_rate) != float(wav_info['sample_rate']):
                                scale = float(wav_info['sample_rate']) / float(default_rate)
                                slice_positions = [int(round(pos * scale)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            pass
                    if slice_positions:
                        try:
                            samlen_int = int(float(samlen))
                            slice_positions = [max(0, min(int(pos), samlen_int)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            slice_positions = [max(0, int(pos)) for pos in slice_positions]
                        slice_positions = sorted(set(slice_positions))
                        before_ct = len(slice_positions)
                        try:
                            samlen_int_lim = int(float(samlen))
                        except (TypeError, ValueError):
                            samlen_int_lim = None
                        slice_positions = limit_slice_positions_density_priority(
                            slice_positions, CLIP_TRANSIENT_SLICE_MAX_BETA, samlen_int_lim
                        )
                        if before_ct > len(slice_positions):
                            logger.info(
                                f'    → Non-slicer slice markers: {before_ct} → {len(slice_positions)} '
                                f'(max {CLIP_TRANSIENT_SLICE_MAX_BETA})'
                            )
                
                # Handle multisample mode
                if params.get('multisammode') == '1':
                    logger.info(f'    → Multisample mode enabled')
                    for i, filepath in enumerate(params.get('filepaths', [])):
                        # Add modsource for multisamples
                        modsource = ET.SubElement(cell, 'modsource')
                        modsource.attrib = {
                            'dest': "samsel",
                            'src': "midipitch",
                            'slot': "0",
                            'amount': "100",
                            'keylo': params.get('keyrangemins', ['0'])[i],
                            'keyhi': params.get('keyrangemaxs', ['127'])[i],
                            'rootkey': params.get('rootkeys', ['60'])[i]
                        }
                        # Assets already tracked above; no duplicate append needed
                
                # Add velocity and pan modsources for all sample pads
                modsource = ET.SubElement(cell, 'modsource')
                modsource.attrib = {'dest': "gaindb", 'src': "midivol", 'slot': "2", 'amount': "1000"}
                modsource = ET.SubElement(cell, 'modsource')
                modsource.attrib = {'dest': "panpos", 'src': "midipan", 'slot': "2", 'amount': "1000"}
        else:
            # Empty pad
            logger.info(f'  Pad {pad_num}: Empty pad')
            cell = ET.SubElement(session, 'cell')
            cell.attrib = pad_dicter(row, column, '', 'samtempl')
            param_elem = ET.SubElement(cell, 'params')
            param_elem.attrib = pad_params_dicter('0', '0', '1000', '4', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0')
            param_elem.attrib['chokegrp'] = '0'
        
        slices = ET.SubElement(cell, 'slices')
        if slice_positions:
            for pos in slice_positions:
                ET.SubElement(slices, 'slice', {'pos': str(pos)})
    
    # Expected preset has 20 pad cells: 16 + 4 empty at column 4 (row 0-3)
    for row in range(4):
        cell = ET.SubElement(session, 'cell')
        cell.attrib = pad_dicter(row, 4, '', 'samtempl')
        param_elem = ET.SubElement(cell, 'params')
        param_elem.attrib = pad_params_dicter('0', '0', '1000', '4', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0')
        param_elem.attrib['chokegrp'] = '0'
        ET.SubElement(cell, 'slices')
    
    logger.info(f'Created {len(pad_list)} drum rack pads + 4 empty column-4 cells')
    return session, assets


def _tick_distance_to_grid(tick_pos, grid_ticks):
    """Distance from tick_pos to the nearest multiple of grid_ticks (0 .. grid_ticks//2)."""
    if grid_ticks <= 0:
        return 0
    r = int(tick_pos) % grid_ticks
    return min(r, grid_ticks - r)


def _tick_aligns_to_grid(tick_pos, grid_ticks, tolerance_ticks):
    """True if tick_pos is within tolerance_ticks of an integer multiple of grid_ticks."""
    return _tick_distance_to_grid(tick_pos, grid_ticks) <= tolerance_ticks


def _grid_alignment_tolerance(grid_ticks, is_triplet):
    """
    Allowed deviation (ticks at ticks_per_beat=3840) when testing grid alignment.

    Straight grids: tight tolerance (float / export noise only).
    Triplet grids: wider tolerance so lightly humanised 1/16T / 1/8T grooves still
    register as triplets and can export quantised (step_len 11/9) instead of
    falling through to unquantised with straight step_len.
    """
    if is_triplet:
        return max(12, min(grid_ticks // 2 - 1, 72))
    return max(1, min(4, grid_ticks // 80))


def detect_note_grid_pattern(events, ticks_per_beat=3840):
    """
    Detect the note grid pattern and quantization state.
    
    Analyzes note timing to determine:
    - Grid resolution (1/16, 1/32, triplets, etc.)
    - Whether notes are quantised or unquantised
    - If triplets are mixed with straight notes (requires unquantised)
    
    Args:
        events: List of note events with 'time_val' values (in beats)
        ticks_per_beat: Ticks per beat (3840 for quantised, 960 for unquantised)
    
    Returns:
        dict with keys:
            'is_unquantised': bool
            'step_len': int (Blackbox step_len value: 14=1/32, 12=1/32T, 10=1/16, 11=1/16T, etc.)
            'has_triplets': bool
            'has_straight': bool
            'grid_resolution': int (ticks)
    """
    if not events:
        return {'is_unquantised': False, 'step_len': 10, 'has_triplets': False, 'has_straight': False, 'grid_resolution': 240}
    
    # Convert time values to ticks for analysis
    tick_positions = []
    for event in events:
        time_val = event.get('time_val', 0)
        tick_pos = int(time_val * ticks_per_beat)
        tick_positions.append(tick_pos)
    
    # Test different grid resolutions
    # 1/32 note = 120 ticks (at 3840 ticks/beat)
    # 1/32 triplet = 80 ticks (3840/3/16) - 3 notes in time of 2 32nd notes
    # 1/16 note = 240 ticks
    # 1/16 triplet = 160 ticks (3840/3/8) - 3 notes in time of 2 16th notes
    # 1/8 note = 480 ticks
    # 1/8 triplet = 320 ticks (3840/3/4) - 3 notes in time of 2 8th notes
    
    resolutions = [
        (120, 14, '1/32'),      # step_len 14 = 1/32
        (160, 11, '1/16T'),     # step_len 11 = 1/16T (triplet)
        (240, 10, '1/16'),      # step_len 10 = 1/16
        (320, 9, '1/8T'),       # step_len 9 = 1/8T (triplet)
        (480, 8, '1/8'),        # step_len 8 = 1/8
        (960, 6, '1/4'),        # step_len 6 = 1/4
        (1920, 4, '1/2'),       # step_len 4 = 1/2
    ]
    
    # Note: 1/32T (step_len 12) uses 160 ticks, same as 1/16T
    # We'll handle 1/32T separately if needed, but typically 1/16T is more common
    
    # Check alignment to each grid
    # CRITICAL: Default to 1/16 (step_len=10) unless we actually need finer resolution
    # Only use 1/32 if we have notes that fall on 32nd note positions that are NOT also 16th note positions
    best_match = None
    best_score = 0
    triplet_aligned = 0
    straight_aligned = 0
    
    # Check triplet grids separately
    # 1/32T uses 80 ticks, but we'll use 160 ticks (1/16T) as it's more common
    # If we detect 1/32T specifically, we can use step_len=12
    triplet_grids = [(160, 11, '1/16T'), (320, 9, '1/8T')]
    straight_grids = [(120, 14, '1/32'), (240, 10, '1/16'), (480, 8, '1/8'), (960, 6, '1/4'), (1920, 4, '1/2')]
    
    # First, check if we have notes that require 32nd note resolution
    # Notes at 0.125, 0.375, 0.625, 0.875 beats (odd 32nd notes) require 1/32 resolution
    # Notes at 0.0, 0.25, 0.5, 0.75 beats (even 32nd notes = 16th notes) can use 1/16
    has_32nd_notes = False
    if len(tick_positions) > 0:
        for tick_pos in tick_positions:
            # Check if this note is on a 32nd note grid but NOT on a 16th note grid
            # 32nd note = 120 ticks, 16th note = 240 ticks
            # Use tolerance for rounding errors
            remainder_32nd = tick_pos % 120
            remainder_16th = tick_pos % 240
            is_32nd = (remainder_32nd == 0 or remainder_32nd == 1 or remainder_32nd == 119)
            is_16th = (remainder_16th == 0 or remainder_16th == 1 or remainder_16th == 239)
            if is_32nd and not is_16th:
                has_32nd_notes = True
                break
    
    # Score each resolution, but prefer 1/16 over 1/32 unless we actually need 32nd notes
    # Also prefer straight notes over triplets when scores are equal
    best_straight_match = None
    best_straight_score = 0
    for grid_ticks, step_len, name in resolutions:
        # Skip 1/32 if we don't actually have 32nd notes
        if step_len == 14 and not has_32nd_notes:
            continue  # Skip 1/32 if we don't need it
        
        aligned = 0
        is_triplet_res = step_len in [11, 9]
        tol = _grid_alignment_tolerance(grid_ticks, is_triplet_res)
        for tick_pos in tick_positions:
            if _tick_aligns_to_grid(tick_pos, grid_ticks, tol):
                aligned += 1
        
        score = aligned / len(tick_positions)
        
        # Track best straight match separately
        is_triplet = step_len in [11, 9]  # Triplet step_len values
        if not is_triplet:
            if score > best_straight_score or (score == best_straight_score and step_len == 10 and best_straight_match and best_straight_match[1] == 14):
                best_straight_score = score
                best_straight_match = (grid_ticks, step_len, name)
        
        # Prefer 1/16 (step_len=10) over 1/32 (step_len=14) if scores are equal
        # Prefer straight notes over triplets when scores are equal
        # This ensures we default to 1/16 straight when both align equally well
        is_better = score > best_score
        is_equal_but_preferred = False
        if score == best_score:
            # If equal, prefer straight over triplet, and 1/16 over 1/32
            if not is_triplet and best_match and best_match[1] in [11, 9]:  # Current is triplet, new is straight
                is_equal_but_preferred = True
            elif step_len == 10 and best_match and best_match[1] == 14:  # Current is 1/32, new is 1/16
                is_equal_but_preferred = True
        
        if is_better or is_equal_but_preferred:
            best_score = score
            best_match = (grid_ticks, step_len, name)
    
    # Check if notes align to triplet grids (for mixed pattern detection and triplet preference)
    best_triplet_match = None
    best_triplet_score = 0
    triplet_count = 0
    for grid_ticks, step_len, name in triplet_grids:
        aligned = 0
        tol_t = _grid_alignment_tolerance(grid_ticks, True)
        for tick_pos in tick_positions:
            if _tick_aligns_to_grid(tick_pos, grid_ticks, tol_t):
                aligned += 1
        score = aligned / len(tick_positions) if len(tick_positions) > 0 else 0
        if aligned > len(tick_positions) * 0.5:  # More than 50% aligned to triplet
            triplet_aligned = max(triplet_aligned, aligned)
            triplet_count = max(triplet_count, aligned)
        if score > best_triplet_score:
            best_triplet_score = score
            best_triplet_match = (grid_ticks, step_len, name)
    
    # Check if notes align to straight grids (for mixed pattern detection)
    straight_count = 0
    for grid_ticks, step_len, name in straight_grids:
        aligned = 0
        tol_s = _grid_alignment_tolerance(grid_ticks, False)
        for tick_pos in tick_positions:
            if _tick_aligns_to_grid(tick_pos, grid_ticks, tol_s):
                aligned += 1
        if aligned > len(tick_positions) * 0.5:  # More than 50% aligned to straight
            straight_aligned = max(straight_aligned, aligned)
            straight_count = max(straight_count, aligned)
    
    # CRITICAL: Check for mixed triplets + straight (requires unquantised)
    # Mixed pattern = notes that align to triplets but NOT straight, AND notes that align to straight but NOT triplets
    # Notes that align to BOTH grids (like whole beats) should NOT count as mixed
    mixed_pattern = False
    if len(tick_positions) > 0:
        # Count notes that align ONLY to triplets (not to straight)
        triplet_only_count = 0
        straight_only_count = 0
        
        for tick_pos in tick_positions:
            aligns_to_triplet = False
            aligns_to_straight = False
            
            # Check if aligns to any triplet grid (wider tolerance for humanised triplets)
            for grid_ticks, step_len, name in triplet_grids:
                tol_t = _grid_alignment_tolerance(grid_ticks, True)
                if _tick_aligns_to_grid(tick_pos, grid_ticks, tol_t):
                    aligns_to_triplet = True
                    break
            
            # Check if aligns to any straight grid (tight tolerance)
            for grid_ticks, step_len, name in straight_grids:
                tol_s = _grid_alignment_tolerance(grid_ticks, False)
                if _tick_aligns_to_grid(tick_pos, grid_ticks, tol_s):
                    aligns_to_straight = True
                    break
            
            # Count notes that align to one but not the other
            if aligns_to_triplet and not aligns_to_straight:
                triplet_only_count += 1
            elif aligns_to_straight and not aligns_to_triplet:
                straight_only_count += 1
        
        # Mixed if we have notes that align ONLY to triplets AND notes that align ONLY to straight
        # Both must be present (>20% each) to be considered truly mixed
        triplet_only_ratio = triplet_only_count / len(tick_positions) if triplet_only_count > 0 else 0
        straight_only_ratio = straight_only_count / len(tick_positions) if straight_only_count > 0 else 0
        
        if triplet_only_ratio > 0.2 and straight_only_ratio > 0.2:
            mixed_pattern = True
            logger.debug(f'  Grid analysis: Mixed triplets ({triplet_only_ratio*100:.0f}% triplet-only) and straight ({straight_only_ratio*100:.0f}% straight-only) detected')
    
    # CRITICAL: If triplets are detected and have good alignment, prefer triplet step_len
    # Use a slightly lower bar than straight grids: humanised 1/16T often scores 90–94%
    # with tolerant triplet detection but still beats the straight match.
    # BUT: Prefer straight notes over triplets when both align equally well (default to straight)
    # Mixed patterns must always be unquantised
    if best_triplet_match and best_triplet_score >= 0.88 and not mixed_pattern:
        # Only use triplet step_len if it's BETTER than the straight match
        # If scores are equal, prefer straight (default behavior)
        # Check if best_match is a straight note grid (not triplet)
        best_is_triplet = best_match and best_match[1] in [11, 9]  # Triplet step_len values
        
        if best_triplet_score > best_straight_score:
            # Triplets align better than straight - use triplet step_len
            best_match = best_triplet_match
            best_score = best_triplet_score
            logger.debug(f'  Grid analysis: Triplets detected ({best_triplet_score*100:.0f}% aligned), using triplet step_len')
        elif (
            best_triplet_score == best_straight_score
            and best_straight_match is not None
            and best_triplet_match is not None
            and best_straight_match[1] in (6, 8)
            and best_triplet_match[1] in (9, 11)
        ):
            # Tie: coarse straight (1/4, 1/8) vs triplet (1/8T, 1/16T). Beat-only patterns score
            # 100% on both; prefer triplet so export uses 8T/16T (notesteplen 9 + stride 960) like Seq2,
            # otherwise step_len 6 sounds one hit per quarter on hardware.
            best_match = best_triplet_match
            best_score = best_triplet_score
            logger.debug(
                '  Grid analysis: Straight vs triplet tie on coarse grid → prefer triplet step_len '
                f'({best_triplet_match[2]} over {best_straight_match[2]})'
            )
        elif best_triplet_score == best_straight_score and best_is_triplet:
            # Scores equal but current best_match is triplet - prefer straight (default)
            if best_straight_match:
                best_match = best_straight_match
                best_score = best_straight_score
            logger.debug(f'  Grid analysis: Triplets detected but equal to straight, preferring straight step_len (default)')
        else:
            # Straight notes align better or are already selected
            logger.debug(f'  Grid analysis: Triplets detected but straight notes align better, using straight step_len')
    
    # Determine if unquantised
    # Unquantised if:
    # 1. Below alignment threshold for the chosen grid (95% straight / 88% triplet — triplets
    #    use a lower bar because tolerant triplet scoring still reflects a clear triplet intent)
    # 2. Mixed triplets + straight notes (CRITICAL: always unquantised when mixed)
    quantised_align_threshold = 0.95
    if best_match and best_match[1] in (11, 9):
        quantised_align_threshold = 0.88
    is_unquantised = best_score < quantised_align_threshold or mixed_pattern
    
    if best_match:
        grid_ticks, step_len, grid_name = best_match
        if is_unquantised:
            logger.info(f'  Grid analysis: {best_score*100:.0f}% aligned to {grid_name}, unquantised detected (mixed={mixed_pattern})')
            # Debug: Log why it's unquantised with more detail
            if best_score < quantised_align_threshold:
                logger.debug(
                    f'    Reason: Alignment score {best_score*100:.1f}% < {quantised_align_threshold*100:.0f}% threshold'
                )
                # Log sample tick positions for debugging
                if len(tick_positions) > 0:
                    sample_ticks = tick_positions[:5]
                    sample_beats = [t / ticks_per_beat for t in sample_ticks]
                    remainders = [t % grid_ticks for t in sample_ticks]
                    logger.debug(f'    Sample ticks: {sample_ticks}, beats: {[f"{b:.3f}" for b in sample_beats]}, remainders: {remainders}')
            if mixed_pattern:
                logger.debug(f'    Reason: Mixed triplets and straight notes detected')
        else:
            logger.debug(f'  Grid analysis: {best_score*100:.0f}% aligned to {grid_name}, quantised')
    else:
        # No match found (shouldn't happen, but default to 1/16)
        step_len = 10  # Default to 1/16
        grid_ticks = 240
        is_unquantised = True
    
    # CRITICAL: Ensure we default to 1/16 if no match found
    # (1/32 is already skipped in the loop if not needed, so this is just a safety check)
    if not best_match:
        step_len = 10  # Default to 1/16
        grid_ticks = 240
        logger.debug(f'  Grid analysis: No grid match found, defaulting to 1/16')

    # Humanised straight 16th: notes sit near a 16th grid but tolerant triplet scoring can
    # falsely pick 1/16T / 1/8T. Prefer 16th background (step_len=10); use unquantised when
    # alignment is good but not grid-locked (step mode off, 16th step_len in XML).
    straight_16_score = 0.0
    if tick_positions:
        tol_16 = _grid_alignment_tolerance(240, False)
        straight_16_score = sum(
            1 for t in tick_positions if _tick_aligns_to_grid(t, 240, tol_16)
        ) / len(tick_positions)
    if straight_16_score >= 0.70:
        step_len = 10
        grid_ticks = 240
        if straight_16_score >= 0.95:
            is_unquantised = False
            logger.info(
                f'  Grid analysis: {straight_16_score * 100:.0f}% on 1/16 straight → quantised 16th'
            )
        else:
            is_unquantised = True
            logger.info(
                f'  Grid analysis: {straight_16_score * 100:.0f}% on 1/16 straight '
                f'(humanised) → unquantised with 16th background'
            )
        best_match = (240, 10, '1/16')
    
    return {
        'is_unquantised': is_unquantised,
        'step_len': step_len if not is_unquantised else 10,  # Use detected step_len only if quantised
        'has_triplets': triplet_aligned > 0,
        'has_straight': straight_aligned > 0,
        'grid_resolution': grid_ticks if best_match else 240
    }


def _midi_clip_signature(midi_clip):
    """
    Extract a comparable signature from a MidiClip element: (note_count, length_beats, notes_list).
    notes_list is a sorted list of (time, pitch, velocity) for each note (raw MIDI content).
    Used to test that session view and arrangement view clips match when both exist.
    Returns None if clip is None or has no Notes; otherwise (note_count, length_beats, sorted_notes).
    """
    if midi_clip is None:
        return None
    length_beats = 0.0
    # LoopStart/LoopEnd live inside Loop child of MidiClip in ALS
    loop_elem = find_element_by_tag(midi_clip, 'Loop')
    if loop_elem is not None:
        loop_start = find_element_by_tag(loop_elem, 'LoopStart')
        loop_end = find_element_by_tag(loop_elem, 'LoopEnd')
        if loop_start is not None and 'Value' in loop_start.attrib and loop_end is not None and 'Value' in loop_end.attrib:
            try:
                length_beats = float(loop_end.attrib['Value']) - float(loop_start.attrib['Value'])
            except (ValueError, TypeError):
                pass
    notes_elem = find_element_by_tag(midi_clip, 'Notes')
    if notes_elem is None:
        return (0, length_beats, [])
    key_tracks = find_element_by_tag(notes_elem, 'KeyTracks')
    if not key_tracks:
        return (0, length_beats, [])
    notes_list = []
    for key_track in key_tracks:
        midi_key = find_element_by_tag(key_track, 'MidiKey')
        key_notes = find_element_by_tag(key_track, 'Notes')
        if midi_key is None or 'Value' not in midi_key.attrib or key_notes is None:
            continue
        pitch = int(midi_key.attrib['Value'])
        for note_event in key_notes:
            if 'Time' in note_event.attrib:
                t = float(note_event.attrib.get('Time', 0))
                v = int(float(note_event.attrib.get('Velocity', 100)))
            else:
                time_el = find_element_by_tag(note_event, 'Time')
                vel_el = find_element_by_tag(note_event, 'Velocity')
                if time_el is None or vel_el is None:
                    continue
                t = float(time_el.attrib.get('Value', 0))
                v = int(float(vel_el.attrib.get('Value', 100)))
            notes_list.append((round(t, 6), pitch, v))
    notes_list.sort()
    return (len(notes_list), length_beats, notes_list)


def _midi_note_event_time_duration(note_event):
    """
    Read (Time, Duration) from a MidiNoteEvent.
    Ableton 12.3+ uses XML attributes; older projects use <Time>/<Duration> child elements.
    Matches extraction in make_drum_rack_sequences / _midi_clip_signature.
    """
    if 'Time' in note_event.attrib:
        try:
            t = float(note_event.attrib.get('Time', 0))
            d = float(note_event.attrib.get('Duration', 0))
            return (t, d)
        except (ValueError, TypeError):
            return None
    time_el = find_element_by_tag(note_event, 'Time')
    dur_el = find_element_by_tag(note_event, 'Duration')
    if time_el is None:
        return None
    try:
        t = float(time_el.attrib.get('Value', 0))
        d = float(dur_el.attrib.get('Value', 0)) if dur_el is not None else 0.0
        return (t, d)
    except (ValueError, TypeError):
        return None


def _max_note_span_in_clip(midi_clip):
    """Max (Time + Duration) over all MidiNoteEvents in clip, in ALS time units."""
    max_end = 0.0
    notes_elem = find_element_by_tag(midi_clip, 'Notes')
    if notes_elem is None:
        return 0.0
    key_tracks = find_element_by_tag(notes_elem, 'KeyTracks')
    if key_tracks is None:
        return 0.0
    for key_track in key_tracks:
        notes_container = find_element_by_tag(key_track, 'Notes')
        if notes_container is None:
            continue
        for note_event in notes_container:
            parsed = _midi_note_event_time_duration(note_event)
            if parsed is None:
                continue
            t, d = parsed
            max_end = max(max_end, t + d)
    return max_end


# Arrangement clips can span many bars; notestepcount is coarsened to ≤256 steps in
# make_drum_rack_sequences. Capping clip length at 256 beats here truncates timelines
# (e.g. 264 beats → 64 bars vs 66 bars on pad 15 layer B).
_MAX_CLIP_LENGTH_BEATS = 4096.0


def _step_count_from_clip_and_grid(clip_length_beats, steps_per_beat):
    """
    Convert clip length (beats) to step count for the active step grid.

    Use ceil(beats * steps_per_beat): raw int() truncation can drop one step when Live
    exports clip end slightly below N.0 beats (e.g. 15.999… → int*3 = 47 → wrong length),
    or make triplet lengths (16×3 = 48) look inconsistent next to note-based expansion.
    """
    if clip_length_beats <= 0:
        return 1
    spb = float(steps_per_beat)
    raw = float(clip_length_beats) * spb
    n = int(math.ceil(raw - 1e-12))
    return min(256, max(1, n))


def _clip_length_beats_from_midi_clip(midi_clip, extracted_note_count=0, als_major_version=None):
    """
    Convert ALS clip loop / arrangement bounds to sequence length in beats.

    extracted_note_count: notes already parsed for this sub-layer (must match KeyTracks). When > 0,
    we never collapse a long arrangement slot to the short “empty gap” cap even if XML span is ~0.

    Do not use raw MIDI note Duration here for output length: step_count uses this value;
    long-held notes in clips are not reliable indicators of loop length.

    Older code used (LoopEnd-LoopStart)/2 and (CurrentEnd-CurrentStart)/2 everywhere.
    That matches some Live exports where values are in 'raw' units, but **halves**
    beat-native clips (e.g. Hack Into Your Soul: loop 0–4 = 4 beats, notes at 0–3 beats).
    Other projects use integer spans where legacy /32 applies (Connection Error style).

    Arrangement vs notes (loop off): when play_range > note_max * ARR_NOTE_REPEAT_RATIO + eps
    (1.5×), clip_length_beats = play_range — the timeline block is the musical length for a
    repeated/extended clip slot.

    See docs/SEQUENCE_TIMING_WORKFLOW.md for step_count usage of this length.
    """
    if midi_clip is None:
        return 1.0

    eps = 0.05
    rel_tol = 0.02
    # Loop off: if arrangement block is much longer than note span, treat timeline as beats (see below).
    ARR_NOTE_REPEAT_RATIO = 1.5

    current_start_elem = find_element_by_tag(midi_clip, 'CurrentStart')
    current_end_elem = find_element_by_tag(midi_clip, 'CurrentEnd')
    play_range = 0.0
    if current_start_elem is not None and 'Value' in current_start_elem.attrib and \
       current_end_elem is not None and 'Value' in current_end_elem.attrib:
        try:
            play_range = float(current_end_elem.attrib['Value']) - float(current_start_elem.attrib['Value'])
        except (ValueError, TypeError):
            play_range = 0.0

    note_max = _max_note_span_in_clip(midi_clip)

    loop_elem = find_element_by_tag(midi_clip, 'Loop')
    loop_on = False
    loop_span = 0.0
    if loop_elem is not None:
        loop_on_elem = find_element_by_tag(loop_elem, 'LoopOn')
        if loop_on_elem is not None and 'Value' in loop_on_elem.attrib:
            v = str(loop_on_elem.attrib['Value']).strip().lower()
            loop_on = v in ('1', 'true', 'on')
        ls_el = find_element_by_tag(loop_elem, 'LoopStart')
        le_el = find_element_by_tag(loop_elem, 'LoopEnd')
        if ls_el is not None and 'Value' in ls_el.attrib and le_el is not None and 'Value' in le_el.attrib:
            try:
                loop_span = float(le_el.attrib['Value']) - float(ls_el.attrib['Value'])
            except (ValueError, TypeError):
                loop_span = 0.0

    # --- Loop on: loop brace defines repeating region ---
    if loop_on and loop_span > 0:
        # Live 11+ (MajorVersion >= 5): loop_span, arrangement slot, and note span all match in
        # beat-native units — e.g. Digital Waterfall Seq13 arrangement clip A: Loop 0–32 beats,
        # CurrentStart/End span 32 beats, notes fill the loop. Legacy branch below would treat
        # loop_span 32 as raw "large integer" and return 32/16 = 2 beats (wrong).
        if (
            als_major_version is not None
            and als_major_version >= 5
            and play_range > 0
            and abs(loop_span - play_range) <= eps * max(loop_span, play_range, 32.0)
            and abs(note_max - loop_span) <= eps * max(note_max, loop_span, 32.0)
        ):
            beats = play_range
            logger.debug(
                f'Clip length: beat-native loop matches timeline loop_span={loop_span} '
                f'play_range={play_range} note_max={note_max} -> {beats} beats'
            )
            # #region agent log
            _dbg(
                'xml_read.py:_clip_length_beats',
                'beat-native loop triple match (loop/timeline/notes)',
                {
                    'loop_span': loop_span,
                    'play_range': play_range,
                    'note_max': note_max,
                    'beats_out': beats,
                    'als_major_version': als_major_version,
                },
                hypothesis='H_clip_triple',
            )
            # #endregion
            return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
        # Large integer loop matching note span (legacy)
        if loop_span >= 32 and note_max >= 32 and abs(loop_span - note_max) <= eps * max(loop_span, 32.0):
            # Live uses different scalings: 128→4 beats (/32) vs 64→4 beats (/16) for same bar count.
            beats = (loop_span / 32.0) if loop_span >= 128 else (loop_span / 16.0)
            logger.debug(f'Clip length: legacy loop span={loop_span} note_max={note_max} -> {beats} beats (legacy /32 or /16)')
            return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
        # Beat-native: short loop with non-integer note extent (e.g. 4-bar loop, notes 0–3.25)
        if loop_span <= 64 and note_max <= 64 and (
            abs(loop_span - note_max) > 0.1 or abs(note_max - round(note_max)) > 1e-5
        ):
            logger.debug(f'Clip length: beat-native loop span={loop_span} note_max={note_max} -> {loop_span} beats')
            return max(1.0, min(loop_span, _MAX_CLIP_LENGTH_BEATS))
        # Small matching integer loop (legacy raw). Live 11+ (MajorVersion >= 5): loop brace is
        # already in beats (e.g. span 4 = 4 beats); do not halve (Frozen Seq1 would become 2 beats).
        if loop_span <= 32 and note_max <= 32 and abs(loop_span - note_max) < eps + 1e-6:
            if als_major_version is not None and als_major_version >= 5:
                beats = loop_span
                logger.debug(
                    f'Clip length: beat-native small loop span={loop_span} note_max={note_max} -> {beats} beats'
                )
            else:
                beats = loop_span / 2.0
                logger.debug(f'Clip length: small legacy loop span={loop_span} -> {beats} beats (/2)')
            return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
        if loop_span <= 64 and note_max <= 64:
            logger.debug(f'Clip length: short loop span={loop_span} note_max={note_max} -> {loop_span} beats')
            return max(1.0, min(loop_span, _MAX_CLIP_LENGTH_BEATS))
        beats = loop_span / 2.0
        logger.debug(f'Clip length: loop fallback span={loop_span} -> {beats} beats (/2)')
        return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))

    # --- Loop off: arrangement extent ---
    if not loop_on and play_range > 0:
        # Empty MIDI (layer placeholder / audio-only): timeline is already in beats in Live 11+.
        # Applying /2 wrongly halves (e.g. Seq13B 80→40 beats, Seq16B 244→122 beats).
        if note_max < eps:
            pr = max(1.0, min(play_range, _MAX_CLIP_LENGTH_BEATS))
            # If we already parsed notes from KeyTracks, never treat this as an empty clip — e.g. XML
            # span heuristics can miss some layouts; leading silence + notes still needs full timeline.
            if extracted_note_count > 0:
                logger.debug(
                    f'Clip length: XML note span ~0 but {extracted_note_count} notes extracted; '
                    f'play_range={pr} beats (arrangement)'
                )
                return pr
            logger.debug(f'Clip length: no MIDI notes play_range={pr} -> {pr} beats (arrangement)')
            return pr
        # Arrangement longer than note content (repeated / extended clip on timeline)
        #
        # Definitions (loop off, this MidiClip only):
        #   play_range = CurrentEnd − CurrentStart (arrangement span of this clip instance)
        #   note_max   = max over notes of (Time + Duration) in ALS units
        #
        # Condition: note_max > 0 AND play_range > note_max * ARR_NOTE_REPEAT_RATIO + eps
        #   ARR_NOTE_REPEAT_RATIO = 1.5 (plus eps=0.05 so tiny float noise doesn’t flip the branch)
        #
        # Meaning: the MIDI in the clip does not fill the whole timeline slot. In Live that usually
        # means the clip’s material is shorter than the block you drew on the arrangement (e.g. the
        # same pattern looped for many bars, or a short pattern in a long clip region). For Blackbox,
        # we want the **full arrangement block length** as the sequence length in **beats**, so we set
        # clip_length_beats = play_range (see _MAX_CLIP_LENGTH_BEATS), not note_max and not play_range/2.
        #
        # Example: note_max = 64, play_range = 192 → use 192 beats (not 64, not 96).
        if note_max > 0 and play_range > note_max * ARR_NOTE_REPEAT_RATIO + eps:
            logger.debug(f'Clip length: arrangement repeat play_range={play_range} note_max={note_max} -> {play_range} beats')
            out = max(1.0, min(play_range, _MAX_CLIP_LENGTH_BEATS))
            # #region agent log
            if play_range > 256:
                _dbg(
                    'xml_read.py:_clip_length_beats',
                    'arrangement repeat (long timeline)',
                    {'play_range': play_range, 'note_max': note_max, 'beats_out': out},
                    hypothesis='H_clip_beat_cap',
                )
            # #endregion
            return out
        # Timeline in beats, MIDI durations in 16th-note units (e.g. 4 beats vs 64)
        if play_range <= 32 and note_max > play_range * 8 - eps and \
           abs(note_max - play_range * 16) < eps * max(note_max, 1.0):
            logger.debug(f'Clip length: beat timeline vs 16ths play={play_range} note_max={note_max} -> {play_range} beats')
            return max(1.0, min(play_range, _MAX_CLIP_LENGTH_BEATS))
        # Note span ~2× timeline (storage quirk)
        if note_max > play_range * 1.2 + eps and abs(note_max - 2.0 * play_range) < 1.0 + eps:
            beats = note_max / 2.0
            logger.debug(f'Clip length: 2× timeline play={play_range} note_max={note_max} -> {beats} beats')
            return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
        # Note span >> timeline: long ALS Duration / loop storage (e.g. t=0 d=160) while
        # CurrentStart/CurrentEnd is beat-native (e.g. 32 beats = 8 bars). Without this branch,
        # we fall through to play_range/2 and halve real bar count (Frozen “8 bar → 4 bar”).
        # Symmetric to “arrangement repeat” above: when timeline is shorter in raw note units
        # but is the visible block length in beats, timeline wins.
        if note_max > play_range * ARR_NOTE_REPEAT_RATIO + eps:
            pr = max(1.0, min(play_range, _MAX_CLIP_LENGTH_BEATS))
            logger.debug(
                f'Clip length: note_max >> play_range note_max={note_max} play_range={play_range} '
                f'-> {pr} beats (timeline authoritative)'
            )
            # #region agent log
            _dbg(
                'xml_read.py:_clip_length_beats',
                'timeline authoritative (note storage >> play_range)',
                {'play_range': play_range, 'note_max': note_max, 'beats': pr},
                hypothesis='H8bar_timeline',
            )
            # #endregion
            return pr
        # Legacy: arrangement span matches note span (integer ALS timeline in older exports).
        # Live 11+ (MajorVersion >= 5): matching spans in beat space must not be divided by 16 —
        # e.g. 32 beats (8 bars) with note_max=32 was wrongly turned into 2 beats (Frozen Seq11).
        if note_max > 0 and abs(play_range - note_max) <= rel_tol * max(play_range, note_max) + eps:
            if play_range >= 128:
                beats = play_range / 32.0
                logger.debug(f'Clip length: legacy match play={play_range} -> {beats} beats (/32)')
            elif play_range >= 32:
                if als_major_version is not None and als_major_version >= 5:
                    beats = play_range
                    logger.debug(
                        f'Clip length: beat-native equal span (Live 11+) play={play_range} note_max={note_max} '
                        f'-> {beats} beats'
                    )
                else:
                    beats = play_range / 16.0
                    logger.debug(f'Clip length: legacy match play={play_range} -> {beats} beats (/16)')
            else:
                beats = play_range / 2.0
                logger.debug(f'Clip length: legacy match play={play_range} -> {beats} beats (/2)')
            return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
        beats = play_range / 2.0
        logger.debug(f'Clip length: arrangement fallback play_range={play_range} -> {beats} beats (/2)')
        return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))

    if play_range > 0:
        beats = play_range / 2.0
        return max(1.0, min(beats, _MAX_CLIP_LENGTH_BEATS))
    return 1.0


def _midi_clip_has_notes(midi_clip):
    """True if this MidiClip element has at least one note in KeyTracks."""
    if midi_clip is None:
        return False
    notes = find_element_by_tag(midi_clip, 'Notes')
    if notes is None:
        return False
    key_tracks = find_element_by_tag(notes, 'KeyTracks')
    if key_tracks is None:
        return False
    for key_track in key_tracks:
        notes_elem = find_element_by_tag(key_track, 'Notes')
        if notes_elem is not None and len(notes_elem) > 0:
            return True
    return False


def make_drum_rack_sequences(session, midi_tracks, pad_list, midi_track_info=None, unquantised=False,
                            als_major_version=None):
    """
    Create Blackbox sequences from MIDI tracks using firmware 2.3+ format.
    Each MIDI track can have up to 4 clips mapped to sub-layers A/B/C/D.
    Each sublayer is created as a separate cell element with type="noteseq".
    
    Timing: All sequences use 3840 ticks/beat. Unquantised detection is automatic.
    
    Args:
        session: The Blackbox session element
        midi_tracks: List of MIDI track elements
        pad_list: List of pad info dictionaries
        unquantised: Legacy parameter, now ignored (automatic detection used)
    """
    logger.info(f'Processing {len(midi_tracks)} MIDI tracks for sequences (firmware 2.3+ format)...')

    # Track which seq slots (0-15) have been filled by an actual track.
    # Slots not in this set at the end receive placeholder cells (e.g. missing Seq2).
    filled_seq_slots = set()

    # Create MIDI note to pad number mapping for pads mode
    # In pads mode, each seqevent's pitch value (0-15) determines which pad gets triggered
    midi_to_pad = build_midi_to_pad_map(pad_list)
    
    for track_idx, track in enumerate(midi_tracks[:16]):
        if track_idx >= len(pad_list):
            break
        
        # Detect sequence mode for this track
        seq_mode, mode_target = detect_sequence_mode(track)
        
        # Get track name for logging/identification (track names like "Seq1", "Seq2" are just labels)
        track_name = None
        if midi_track_info and track_idx < len(midi_track_info):
            track_name = midi_track_info[track_idx][1]
        
        # Determine target pad from routing (not from track name or index).
        # For Pads mode: use the HUMAN seq index (SeqN → dest=N-1) so that missing sequences
        # (e.g. Seq2 absent from the project) leave their slot empty and every present seq lands
        # at the correct Blackbox pad slot. Dense array index would shift every seq after a gap.
        seq_human_idx = _seq_index_from_track(track, track_idx, midi_track_info)
        target_pad = seq_human_idx  # Default: human-based pad slot

        if seq_mode == 'Keys' and mode_target is not None:
            # mode_target is the branch_id from the routing (e.g., B40 → 40)
            branch_id = mode_target

            # Find which pad has this branch_id
            target_found = False
            for pad in pad_list:
                if pad.get('branch_id') == branch_id:
                    target_pad = pad['blackbox_pad']
                    target_found = True
                    logger.info(f'  Keys mode: Branch Id {branch_id} maps to Pad {target_pad}')
                    break

            if not target_found:
                logger.warning(f'  Keys mode: Branch Id {branch_id} not found in drum rack')
                logger.warning(f'  Falling back to human seq index (Pad {seq_human_idx})')
                target_pad = seq_human_idx

        # Sequence grid location uses the same human-based index for both modes.
        sequence_location_pad = target_pad
        row, column = row_column(sequence_location_pad)
        filled_seq_slots.add(sequence_location_pad)
        
        # CRITICAL: Store row/column as local variables to avoid any potential scope issues
        sequence_row = row
        sequence_column = column
        
        track_name_str = f' (name="{track_name}")' if track_name else ''
        logger.info(f'Track {track_idx}{track_name_str}: Mode={seq_mode}, Branch/Channel={mode_target}, Target Pad={target_pad}, Sequence Location={sequence_location_pad} (row={sequence_row}, col={sequence_column})')
        
        # Extract up to 4 MIDI clips as sub-layers.
        # Slots 0-3 correspond to layers A/B/C/D.
        # Session view clips fill the slots by position; arrangement view clips
        # (named A/B/C/D) override the matching slot (arrangement takes priority).
        sub_layers = [None, None, None, None]  # indexed by layer: 0=A,1=B,2=C,3=D
        _layer_name_to_idx = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
        # Highest A–D slot index present in ArrangerAutomation (named clips). Matches hand-tuned
        # Frozen_Git presets: e.g. Seq13 with arrangement A+B+C → activeseqlayer=2 on sublayer 0 only.
        arrangement_max_layer_idx = None
        
        try:
            device_chain = find_element_by_tag(track, 'DeviceChain')
            if not device_chain:
                logger.debug(f'  Track {track_idx}: No DeviceChain found')
                continue
            
            main_sequencer = find_element_by_tag(device_chain, 'MainSequencer')
            if not main_sequencer:
                logger.debug(f'  Track {track_idx}: No MainSequencer found')
                continue
            
            # --- Session view clips (slots 0-3 → layers A/B/C/D) ---
            clip_slot_list = find_element_by_tag(main_sequencer, 'ClipSlotList')
            if clip_slot_list:
                for clip_idx, clip_slot_container in enumerate(clip_slot_list[:4]):
                    if len(clip_slot_container) > 1:
                        clip_slot = clip_slot_container[1]
                        if clip_slot.tag == 'ClipSlot' and len(clip_slot) > 0:
                            value_elem = clip_slot[0]
                            if value_elem.tag == 'Value' and len(value_elem) > 0:
                                if value_elem[0].tag == 'MidiClip':
                                    sub_layers[clip_idx] = value_elem[0]
                                    logger.info(f'  Track {track_idx}, Session slot {clip_idx}: Found clip for sub-layer {chr(65+clip_idx)}')
            
            # Keep copy of session clips for session-vs-arrangement match test
            session_clips = [sub_layers[i] for i in range(4)]
            
            # --- Arrangement view clips (named A/B/C/D → always override session for that slot) ---
            # Empty arrangement clips are intentional: they mean "no sequence" for that layer in that section.
            # When multiple arrangement clips exist per layer (different sections), use the EARLIEST one.
            # The first section's clip defines the loop length; later clips may span the full section (wrong length).
            clip_timeable = find_element_by_tag(main_sequencer, 'ClipTimeable')
            if clip_timeable:
                arr_automation = find_element_by_tag(clip_timeable, 'ArrangerAutomation')
                if arr_automation:
                    events = find_element_by_tag(arr_automation, 'Events')
                    if events:
                        # Per layer: keep (start_time, clip) with minimum start_time
                        best_per_layer = {}  # layer_idx -> (start_time, arr_clip)
                        for arr_clip in events:
                            if arr_clip.tag != 'MidiClip':
                                continue
                            name_el = find_element_by_tag(arr_clip, 'Name')
                            clip_name = (name_el.attrib.get('Value', '') if name_el is not None else '').strip().lower()
                            layer_idx = _layer_name_to_idx.get(clip_name)
                            if layer_idx is None:
                                continue
                            # Get clip start time (CurrentStart or Time) for ordering
                            start_time = float('inf')
                            cs = find_element_by_tag(arr_clip, 'CurrentStart')
                            if cs is not None and 'Value' in cs.attrib:
                                try:
                                    start_time = float(cs.attrib['Value'])
                                except (ValueError, TypeError):
                                    pass
                            if start_time == float('inf') and 'Time' in arr_clip.attrib:
                                try:
                                    start_time = float(arr_clip.attrib['Time'])
                                except (ValueError, TypeError):
                                    pass
                            if start_time == float('inf'):
                                start_time = 0.0
                            if layer_idx not in best_per_layer or start_time < best_per_layer[layer_idx][0]:
                                best_per_layer[layer_idx] = (start_time, arr_clip)
                        if best_per_layer:
                            arrangement_max_layer_idx = max(best_per_layer.keys())
                        for layer_idx, (_, arr_clip) in best_per_layer.items():
                            sub_layers[layer_idx] = arr_clip
                            sig = _midi_clip_signature(arr_clip)
                            n_notes = sig[0] if sig else 0
                            logger.info(f'  Track {track_idx}, Arrangement clip "{chr(65+layer_idx)}": Overrides sub-layer {chr(65+layer_idx)} ({n_notes} notes)')
            
            # Test: when both session and arrangement provided a clip for the same layer, they should match
            for layer_idx in range(4):
                sess_clip = session_clips[layer_idx] if layer_idx < len(session_clips) else None
                final_clip = sub_layers[layer_idx] if layer_idx < len(sub_layers) else None
                if sess_clip is None or final_clip is None or sess_clip is final_clip:
                    continue
                sig_sess = _midi_clip_signature(sess_clip)
                sig_arr = _midi_clip_signature(final_clip)
                if sig_sess is None and sig_arr is None:
                    continue
                if sig_sess is None or sig_arr is None:
                    logger.warning(f'  Track {track_idx}, layer {chr(65+layer_idx)}: session vs arrangement signature missing (session={sig_sess}, arr={sig_arr})')
                    continue
                n_sess, len_sess, notes_sess = sig_sess
                n_arr, len_arr, notes_arr = sig_arr
                if n_sess != n_arr or abs(len_sess - len_arr) > 0.001 or notes_sess != notes_arr:
                    logger.warning(
                        f'  Track {track_idx}, layer {chr(65+layer_idx)}: session view clip does NOT match arrangement clip '
                        f'(session: {n_sess} notes, {len_sess:.2f} beats; arrangement: {n_arr} notes, {len_arr:.2f} beats)'
                    )
                else:
                    logger.info(f'  Track {track_idx}, layer {chr(65+layer_idx)}: session and arrangement clips match ({n_sess} notes, {len_sess:.2f} beats)')
        
        except Exception as e:
            logger.warning(f"Error extracting MIDI clips from track {track_idx}: {e}")
            continue
        
        # Trim trailing None entries so sublayer loop matches actual data
        while sub_layers and sub_layers[-1] is None:
            sub_layers.pop()
        if not sub_layers:
            sub_layers = []
        
        # Lowest slot that actually has note data; used when there are no named arrangement clips.
        first_layer_with_notes = -1
        for si in range(4):
            mc = sub_layers[si] if si < len(sub_layers) else None
            if _midi_clip_has_notes(mc):
                first_layer_with_notes = si
                break
        # Active pattern head on sublayer-0 cells: which A/B/C/D layer the Blackbox treats as
        # primary for that sequence. Prefer the lowest slot that actually has MIDI (almost always
        # the main groove on pattern A). Using only the highest arrangement clip index wrongly
        # selects an overdub layer (e.g. B) while A carries the triplet hat — playback then sounds
        # sparse/straight vs the full groove (Digital Waterfall Seq2).
        # When no layer has notes, fall back to arrangement span or 0.
        track_activeseqlayer_head = (
            first_layer_with_notes if first_layer_with_notes >= 0
            else (arrangement_max_layer_idx if arrangement_max_layer_idx is not None else 0)
        )
        
        # Create sequence cells for each sublayer (firmware 2.3+ format)
        # Create all 4 sublayers (A/B/C/D) as separate cell elements
        total_notes_all_layers = 0
        
        # DEBUG: Log track info before processing sublayers
        # Verify we have the right clips by checking first clip's note count
        first_clip_notes = 0
        if len(sub_layers) > 0:
            first_clip = sub_layers[0]
            notes_elem = find_element_by_tag(first_clip, 'Notes')
            if notes_elem:
                key_tracks = find_element_by_tag(notes_elem, 'KeyTracks')
                if key_tracks:
                    for kt in key_tracks:
                        notes = find_element_by_tag(kt, 'Notes')
                        if notes:
                            first_clip_notes += len(notes)
        logger.info(f'  Track {track_idx}: Processing {len(sub_layers)} clips, first clip has {first_clip_notes} notes, sequence_location_pad={sequence_location_pad}, target_pad={target_pad}')
        
        for sublayer_idx in range(4):
            # Get the MIDI clip for this sublayer if it exists
            midi_clip = sub_layers[sublayer_idx] if sublayer_idx < len(sub_layers) else None
            # For clip length: always use the arrangement clip (midi_clip) when available.
            # When both session and arrangement clips exist, arrangement takes priority — it reflects
            # the actual placement and duration in the song. Do NOT fall back to session clip for length.
            length_clip = midi_clip
            
            # Extract all notes for this sublayer first (to check if we have events)
            # CRITICAL: Must create a new list for each sublayer to avoid reference issues
            sublayer_events = []
            
            # CRITICAL: Extract LoopStart offset BEFORE processing notes
            # Note times in Ableton are relative to the clip start (LoopStart)
            # LoopStart lives inside Loop child of MidiClip in ALS.
            loop_start_offset = 0.0
            if midi_clip:
                loop_elem = find_element_by_tag(midi_clip, 'Loop')
                if loop_elem is not None:
                    loop_start_elem = find_element_by_tag(loop_elem, 'LoopStart')
                    if loop_start_elem is not None and 'Value' in loop_start_elem.attrib:
                        try:
                            loop_start_offset = float(loop_start_elem.attrib['Value'])
                            if sublayer_idx == 0:  # Only log for first sublayer
                                logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: LoopStart = {loop_start_offset} beats (will subtract from note times)')
                        except (ValueError, TypeError) as e:
                            logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Error extracting LoopStart: {e}')
            
            if midi_clip:
                notes = find_element_by_tag(midi_clip, 'Notes')
                if notes:
                    key_tracks = find_element_by_tag(notes, 'KeyTracks')
                    if key_tracks:
                        # Extract ALL KeyTracks and ALL notes
                        # Debug: Count notes before extraction
                        note_count_before = 0
                        for kt in key_tracks:
                            notes_elem = find_element_by_tag(kt, 'Notes')
                            if notes_elem:
                                note_count_before += len(notes_elem)
                        if sublayer_idx == 0:  # Only log for first sublayer to avoid spam
                            logger.info(f'    Track {track_idx}, Sub-layer {chr(65+sublayer_idx)}: Extracting {note_count_before} notes from clip')
                        for key_track in key_tracks:
                            midi_key = find_element_by_tag(key_track, 'MidiKey')
                            notes_elem = find_element_by_tag(key_track, 'Notes')
                            
                            if midi_key is not None and 'Value' in midi_key.attrib and notes_elem is not None and len(notes_elem) > 0:
                                midi_note = int(midi_key.attrib['Value'])
                                
                                # Determine chan and pitch based on sequence mode
                                if seq_mode == 'Pads':
                                    # Pads mode: chan determines pad, pitch is always 0
                                    pad_number = midi_to_pad.get(midi_note, 0)
                                    event_chan = 256 + pad_number
                                    event_pitch = 0
                                elif seq_mode == 'Keys':
                                    # Keys mode: chan depends on quantisation state
                                    # - Quantised: chan=256+target_pad (seqstepmode="1")
                                    # - Unquantised: chan=256 (seqstepmode="0")
                                    # We'll determine this when we know is_unquantised, but for now use target_pad
                                    # This will be overridden below based on is_unquantised
                                    event_chan = 256 + target_pad  # Default, will be updated if unquantised
                                    event_pitch = midi_note
                                elif seq_mode == 'MIDI':
                                    # MIDI mode: pitch is MIDI note, chan is MIDI channel
                                    event_chan = mode_target  # MIDI channel (0-15)
                                    event_pitch = midi_note
                                
                                # Extract note events
                                for note_event in notes_elem:
                                    # Handle both Ableton 12.3+ (attributes) and older (elements) formats
                                    if 'Time' in note_event.attrib:
                                        time_val_raw = float(note_event.attrib.get('Time', 0))
                                        dur_val = float(note_event.attrib.get('Duration', 0))
                                        vel_val = int(float(note_event.attrib.get('Velocity', 100)))
                                    else:
                                        time = find_element_by_tag(note_event, 'Time')
                                        duration = find_element_by_tag(note_event, 'Duration')
                                        velocity = find_element_by_tag(note_event, 'Velocity')
                                        
                                        if not (time and duration and velocity):
                                            continue
                                            
                                        time_val_raw = float(time.attrib.get('Value', 0))
                                        dur_val = float(duration.attrib.get('Value', 0))
                                        vel_val = int(float(velocity.attrib.get('Value', 100)))
                                    
                                    # CRITICAL: Subtract LoopStart offset from note times
                                    # Note times in Ableton are relative to the clip start (LoopStart)
                                    # We need to subtract this offset to get times relative to sequence start
                                    time_val = time_val_raw - loop_start_offset
                                    # Ensure time_val is not negative (shouldn't happen, but safety check)
                                    if time_val < 0:
                                        logger.warning(f'    Note time {time_val_raw} - LoopStart {loop_start_offset} = {time_val} (negative, clamping to 0)')
                                        time_val = 0
                                    
                                    # DEBUG: Log raw time values for sequence 5 (Track 4) to diagnose timing issue
                                    if track_idx == 4 and sublayer_idx == 0 and len(sublayer_events) < 5:
                                        logger.info(f'    DEBUG Track 4: time_val_raw={time_val_raw}, loop_start_offset={loop_start_offset}, time_val={time_val}')
                                    
                                    # Calculate timing (always use tick-based format for firmware 2.3+)
                                    # We'll detect unquantised later and recalculate if needed
                                    # Default: use 3840 ticks/beat (quantised)
                                    step = int(time_val * 4)  # 4 steps per beat (16th notes)
                                    strtks = int(time_val * 3840)  # 1 beat = 3840 ticks (960 per 16th note)
                                    lentks = int(dur_val * 3840)
                                    lencount = max(1, int(dur_val * 4))  # Will be set to 0 for unquantised
                                    
                                    # Store event data (tick rate will be adjusted if unquantised)
                                    # CRITICAL: Create a new dict for each event to avoid reference issues
                                    event_dict = {
                                        'step': step,
                                        'chan': str(event_chan),
                                        'type': 'note',
                                        'strtks': strtks,
                                        'pitch': event_pitch,
                                        'lencount': lencount,
                                        'lentks': lentks,
                                        'velocity': vel_val,
                                        'time_val': time_val,  # Store adjusted time (after subtracting LoopStart)
                                        'dur_val': dur_val     # Store original duration for recalculation
                                    }
                                    sublayer_events.append(event_dict)
            
            # Extract clip length from MIDI clip to calculate step_count (see _clip_length_beats_from_midi_clip).
            clip_length_beats = 1.0
            clip_for_length = length_clip if length_clip is not None else midi_clip
            if clip_for_length:
                clip_length_beats = _clip_length_beats_from_midi_clip(
                    clip_for_length, extracted_note_count=len(sublayer_events),
                    als_major_version=als_major_version,
                )
                logger.info(
                    f'    Sub-layer {chr(65+sublayer_idx)}: clip_length_beats={clip_length_beats:.2f} '
                    f'(_clip_length_beats_from_midi_clip)'
                )

            # Sequence length (step_count) follows clip / arrangement / loop bounds only — not MIDI
            # note Duration. Long notes in the ALS often span loop cycles or full clip storage while
            # the audible loop is shorter (e.g. Seq15); using max note end wrongly blows up step_count.

            # Extend short note durations: when notes visually fill the clip but XML has short durations,
            # extend each note to reach the next note (or clip end). Only lengthen, never shorten.
            if sublayer_events and clip_length_beats > 1.0:
                min_dur_to_extend = min(1.0, clip_length_beats / max(1, len(sublayer_events)))  # Only extend if short
                sorted_events = sorted(sublayer_events, key=lambda e: (e.get('time_val', 0), e.get('chan', '')))
                for i, event in enumerate(sorted_events):
                    time_val = event.get('time_val', 0)
                    dur_val = event.get('dur_val', 0)
                    if dur_val >= min_dur_to_extend:
                        continue  # Already long enough, skip
                    if i + 1 < len(sorted_events):
                        next_start = sorted_events[i + 1].get('time_val', time_val + dur_val)
                        # If next note starts at same time (chord/simultaneous), extend to clip end
                        target_end = clip_length_beats if next_start <= time_val else next_start
                    else:
                        target_end = clip_length_beats
                    target_dur = max(dur_val, target_end - time_val)
                    if target_dur > dur_val and target_dur > 0:
                        event['dur_val'] = target_dur
                        logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Extended note at {time_val:.2f} from {dur_val:.2f} to {target_dur:.2f} beats')
            
            # ALS Duration often spans the whole clip on disk while clip_length_beats is the loop /
            # arrangement window (Seq15 A). lencount is derived from dur_val — clamp so gates fit the
            # exported pattern (seq length follows clip bounds; note tail must not exceed that).
            if sublayer_events and clip_length_beats > 0:
                for event in sublayer_events:
                    tv = float(event.get('time_val', 0))
                    dv = float(event.get('dur_val', 0))
                    max_dur = max(0.0, float(clip_length_beats) - tv)
                    if dv > max_dur + 1e-4:
                        logger.debug(
                            f'    Sub-layer {chr(65+sublayer_idx)}: Clamp note dur {dv:.3f} -> {max_dur:.3f} '
                            f'beats (clip_length={clip_length_beats:.3f})'
                        )
                        event['dur_val'] = max_dur
            
            # Sanity cap only when clip_length came from CurrentEnd (unreliable - can be time in sec).
            # LoopStart/LoopEnd and note-derived lengths are trusted; allow up to 256 beats (64 bars).
            # CurrentEnd > 64 is already rejected above, so no extra cap needed for 32-bar clips.
            
            total_notes_all_layers += len(sublayer_events)
            
            # Detect note grid pattern and quantization state (independent of pad/keys mode)
            # Analyze with 3840 ticks/beat first to detect grid pattern
            # This must happen BEFORE step_len calculation so we can use detected step_len
            if sublayer_events:
                # Debug: Log first few note times for verification
                if sublayer_idx == 0:  # Only for first sublayer
                    first_times = [event.get('time_val', 0) for event in sublayer_events[:5]]
                    logger.info(f'    Track {track_idx}, Sub-layer {chr(65+sublayer_idx)}: All note times (beats): {[f"{t:.6f}" for t in first_times]}')
            grid_analysis = detect_note_grid_pattern(sublayer_events, ticks_per_beat=3840)
            is_unquantised = grid_analysis['is_unquantised']
            detected_step_len = grid_analysis['step_len']
            
            # DEBUG: Log detection result for all tracks to diagnose quantisation detection
            if sublayer_idx == 0:  # Only for first sublayer
                logger.info(f'    Track {track_idx}, Sub-layer {chr(65+sublayer_idx)}: Detection result - is_unquantised={is_unquantised}, detected_step_len={detected_step_len}, seq_mode={seq_mode}')
            
            # Calculate step_len and step_count from clip length
            # Relationship: clip_length_beats = step_count * step_len (e.g. 128 steps * 1/16 = 32 beats)
            # So step_count = clip_length_beats / step_len_in_beats = clip_length_beats * steps_per_beat
            # Step length values:
            # 14 = 1/32, 12 = 1/32T, 10 = 1/16, 11 = 1/16T, 8 = 1/8, 9 = 1/8T, 6 = 1/4, 4 = 1/2, 3 = 1 Bar, 2 = 2 Bars, 1 = 4 Bars, 0 = 8 Bars
            # 1 bar = 4 beats
            # Only use default when there is NO clip at all; otherwise extract length from clip (even if empty)
            if not midi_clip and len(sublayer_events) == 0:
                step_len = 10
                step_count = 1  # Truly empty slot, no clip
            else:
                # For quantised sequences, use detected step_len (e.g., 14 for 1/32 notes)
                # For unquantised, use default 1/16 (step_len=10)
                if not is_unquantised and detected_step_len:
                    step_len = detected_step_len
                    # Calculate step_count based on detected step_len
                    # step_len 14 = 1/32: 1 beat = 8 steps, 1 bar = 32 steps
                    # step_len 12 = 1/32T: 1 beat = 6 steps (triplet), 1 bar = 24 steps
                    # step_len 10 = 1/16: 1 beat = 4 steps, 1 bar = 16 steps
                    # step_len 11 = 1/16T: 1 beat = 3 steps (triplet), 1 bar = 12 steps
                    # step_len 8 = 1/8: 1 beat = 2 steps, 1 bar = 8 steps
                    # step_len 9 = 1/8T: 1 beat = 1.5 steps (triplet), 1 bar = 6 steps
                    # Note: steps_per_beat_map is defined later for step recalculation
                    steps_per_beat_map_temp = {
                        14: 8,   # 1/32
                        12: 6,   # 1/32T
                        10: 4,   # 1/16
                        11: 3,   # 1/16T
                        8: 2,    # 1/8
                        9: 1.5,  # 1/8T (will round)
                    }
                    steps_per_beat = steps_per_beat_map_temp.get(step_len, 4)
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, steps_per_beat)
                else:
                    # Unquantised or no detection: use default 1/16
                    step_len = 10
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 4)
                
                # If step_count exceeds 256: when grid is 1/16 and clip is short (<=64 beats), cap at 256
                # (fixes misread clip_length e.g. from CurrentEnd). For long clips (>64 beats), coarsen to 1/8 etc.
                if step_count > 256 and (not is_unquantised and detected_step_len == 10) and clip_length_beats <= 64.0:
                    step_count = min(256, step_count)
                    logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Capping step_count at 256, keeping step_len=10 (short clip)')
                
                # Otherwise use coarser resolution until step_count <= 256
                if step_count > 256:
                    # Try 1/8 notes: 1 beat = 2 steps, 1 bar = 8 steps
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 2)
                    step_len = 8
                    
                if step_count > 256:
                    # Try 1/4 notes: 1 beat = 1 step, 1 bar = 4 steps
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 1)
                    step_len = 6
                    
                if step_count > 256:
                    # Try 1/2 notes: 2 beats = 1 step, 1 bar = 2 steps
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 0.5)
                    step_len = 4
                    
                if step_count > 256:
                    # Try 1 Bar: 4 beats = 1 step
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 0.25)
                    step_len = 3
                    
                if step_count > 256:
                    # Try 2 Bars: 8 beats = 1 step
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 0.125)
                    step_len = 2
                    
                if step_count > 256:
                    # Try 4 Bars: 16 beats = 1 step
                    step_count = _step_count_from_clip_and_grid(clip_length_beats, 0.0625)
                    step_len = 1
                    
                if step_count > 256:
                    # Max: 8 Bars: 32 beats = 1 step
                    step_count = max(1, min(256, _step_count_from_clip_and_grid(clip_length_beats, 0.03125)))
                    step_len = 0
                
                # Ensure step_count is at least 1
                if step_count < 1:
                    step_count = 1
                
                logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: {clip_length_beats} beats → step_count={step_count}, step_len={step_len} (unquantised={is_unquantised})')
            
            # Recalculate step values based on detected step_len
            # This must happen AFTER step_len is determined
            steps_per_beat_map = {
                14: 8,   # 1/32
                12: 6,   # 1/32T
                10: 4,   # 1/16
                11: 3,   # 1/16T
                8: 2,    # 1/8
                9: 1.5,  # 1/8T (will round)
            }
            steps_per_beat = steps_per_beat_map.get(step_len, 4)

            # Quantised triplets: Blackbox "8T" UI is notesteplen=9 with strtks = step_index * 960.
            # Reference: Digital Waterfall fixed preset (seq2). 1280 (=3840/3) breaks on-device grid.
            # Step indices snap on the *detected* note grid (16T→3/beat); step_count may use 8T (48 steps).
            notesteplen_written = step_len
            triplet_quant_strtks_stride = None
            triplet_snap_steps_per_beat = None
            if not is_unquantised and step_len in (11, 9):
                notesteplen_written = 9
                triplet_quant_strtks_stride = 960
                grid_for_snap = detected_step_len if detected_step_len in (11, 9) else step_len
                triplet_snap_steps_per_beat = steps_per_beat_map.get(grid_for_snap, steps_per_beat)

            step_count_pre_expand = step_count

            # Quantised: ensure notestepcount spans every step index used by note times. Clip-length-only
            # step_count can truncate (especially int(beats * 1.5)); that clamps notes to step_count-1
            # and stacks them — wrong on hardware.
            if not is_unquantised and sublayer_events:
                if notesteplen_written == 9 or step_len in (11, 9):
                    spb_pre = float(triplet_snap_steps_per_beat if triplet_snap_steps_per_beat else steps_per_beat)
                else:
                    spb_pre = float(steps_per_beat)
                max_step_ix = 0
                for _ev in sublayer_events:
                    max_step_ix = max(max_step_ix, int(round(float(_ev.get('time_val', 0)) * spb_pre)))
                need_steps = max_step_ix + 1
                if need_steps > step_count:
                    if need_steps > 256:
                        logger.warning(
                            f'    Sub-layer {chr(65+sublayer_idx)}: Notes need step index up to {max_step_ix} '
                            f'({need_steps} steps) but Blackbox max is 256; some steps may clamp.'
                        )
                    step_count = min(256, max(step_count, need_steps))
                    logger.info(
                        f'    Sub-layer {chr(65+sublayer_idx)}: step_count → {step_count} '
                        f'to cover notes through step index {max_step_ix}'
                    )
            # #region agent log
            if not is_unquantised and sublayer_events:
                if notesteplen_written == 9 or step_len in (11, 9):
                    spb_dbg = float(triplet_snap_steps_per_beat if triplet_snap_steps_per_beat else steps_per_beat)
                else:
                    spb_dbg = float(steps_per_beat)
                max_ix_dbg = 0
                for _ev in sublayer_events:
                    max_ix_dbg = max(max_ix_dbg, int(round(float(_ev.get('time_val', 0)) * spb_dbg)))
                _dbg(
                    'xml_read.py:step_count_expand',
                    'H_trunc/MIDI step grid: pre count vs max note step index',
                    data={
                        'track_idx': track_idx,
                        'sublayer': chr(65 + sublayer_idx),
                        'seq_mode': seq_mode,
                        'step_count_pre_expand': step_count_pre_expand,
                        'notestepcount_after': step_count,
                        'max_step_ix': max_ix_dbg,
                        'need_steps': max_ix_dbg + 1,
                        'notesteplen_written': notesteplen_written,
                        'triplet_snap_steps_per_beat': triplet_snap_steps_per_beat,
                        'spb_used_for_max_ix': spb_dbg,
                        'clip_length_beats': float(clip_length_beats),
                        'event_count': len(sublayer_events),
                        'runId': 'verify-seq5',
                    },
                    hypothesis='H_trunc',
                )
            # #endregion
            
            # CRITICAL: Tick rate depends ONLY on quantisation state, NOT on sequence mode
            # Quantised sequences (both Keys and Pads): Use 3840 ticks/beat
            # Unquantised sequences (both Keys and Pads): Use 960 ticks/beat
            # Quantisation detection is independent of Keys/Pads mode
            if is_unquantised:
                # Unquantised: use 960 ticks/beat for strtks; lencount=0 uses lentks for gate length.
                logger.info(f'    Sub-layer {chr(65+sublayer_idx)}: {seq_mode} mode, unquantised → using 960 ticks/beat, setting lencount=0')
                for event in sublayer_events:
                    time_val = event.get('time_val', 0)
                    dur_val = event.get('dur_val', 0)
                    event['strtks'] = int(time_val * 960)  # 960 ticks/beat for unquantised (both Keys and Pads)
                    if seq_mode == 'MIDI':
                        event['lentks'] = max(1, int(dur_val * 960))
                    else:
                        event['lentks'] = 240  # Constant 240 ticks for unquantised Keys/Pads (matches reference)
                    event['lencount'] = 0  # Use precise lentks timing - CRITICAL: must be 0 for unquantised
                    # For unquantised sequences, step = floor(strtks / 960) (beat position, not step_len resolution)
                    event['step'] = int(event['strtks'] // 960)
                    # For unquantised Keys mode, chan should be 256 (not 256+target_pad)
                    if seq_mode == 'Keys':
                        current_chan = event.get('chan', 256)
                        # Convert to int if it's a string, then check if it's >= 256
                        chan_int = int(current_chan) if isinstance(current_chan, str) else current_chan
                        if chan_int >= 256:
                            # Update chan to 256 for unquantised Keys mode
                            event['chan'] = 256
                # Debug: verify lencount was set correctly
                if sublayer_events:
                    sample_lencount = sublayer_events[0].get('lencount')
                    logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: After unquantised fix, first event lencount={sample_lencount}')
                if sublayer_events:
                    sublayer_events.sort(key=lambda e: (
                        int(e.get('strtks', 0)),
                        int(e.get('pitch', 0)),
                        int(str(e.get('chan', 0))),
                    ))
            else:
                # Quantised: use 3840 ticks/beat for both Keys/MIDI and Pads mode
                # CRITICAL: Step values must match the step_len resolution
                logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: {seq_mode} mode, quantised, detected step_len={detected_step_len}, recalculating step values with {steps_per_beat} steps/beat, using 3840 ticks/beat')
                for event in sublayer_events:
                    time_val = event.get('time_val', 0)
                    dur_val = event.get('dur_val', 0)
                    if notesteplen_written == 9 or step_len in (11, 9):
                        # Lock step index to detected triplet grid; strtks uses 960/step for notesteplen 9.
                        spb = float(triplet_snap_steps_per_beat if triplet_snap_steps_per_beat else steps_per_beat)
                        stride = float(triplet_quant_strtks_stride) if triplet_quant_strtks_stride else (
                            3840.0 / spb
                        )
                        snap_st = round(time_val * spb)
                        si = int(snap_st)
                        si = max(0, min(si, max(0, step_count - 1)))
                        event['step'] = si
                        event['strtks'] = int(round(si * stride))
                    else:
                        event['step'] = int(time_val * steps_per_beat)
                        event['strtks'] = int(time_val * 3840)  # 3840 ticks/beat for quantised
                    if seq_mode == 'MIDI':
                        event['lentks'] = max(1, int(dur_val * 3840))
                        event['lencount'] = max(1, int(dur_val * steps_per_beat))
                    else:
                        # Drum-rack Keys/Pads: pad triggers use fixed gate (reference presets).
                        event['lentks'] = 960
                        event['lencount'] = 1
                # Sort events chronologically (by strtks) — Ableton's KeyTrack iteration produces
                # events grouped by pitch; FIX presets sort strictly by strtks so the device reads
                # the sequence in time order.
                sublayer_events.sort(key=lambda e: (
                    int(e.get('strtks', 0)),
                    int(e.get('pitch', 0)),
                    int(str(e.get('chan', 0))),
                ))
                if seq_mode == 'Keys':
                    sublayer_events = _dedupe_keys_quantised_same_step_strtks(sublayer_events)
                # #region agent log
                if sublayer_events:
                    _dbg('xml_read.py:2837', 'H1/H7 lencount=1 + chronological sort', data={
                        'track_idx': track_idx, 'sublayer': chr(65+sublayer_idx),
                        'seq_loc_pad': sequence_location_pad, 'seq_mode': seq_mode,
                        'steps_per_beat': steps_per_beat,
                        'first3_dur_val': [round(float(e.get('dur_val', 0)), 4) for e in sublayer_events[:3]],
                        'first3_lencount': [int(e.get('lencount', -1)) for e in sublayer_events[:3]],
                        'first3_lentks': [int(e.get('lentks', -1)) for e in sublayer_events[:3]],
                        'first3_pitch': [int(e.get('pitch', -1)) for e in sublayer_events[:3]],
                        'first3_strtks': [int(e.get('strtks', -1)) for e in sublayer_events[:3]],
                        'first3_chan': [int(str(e.get('chan', 0))) for e in sublayer_events[:3]],
                        'event_count': len(sublayer_events),
                        'runId': 'post-fix',
                    }, hypothesis='H1_H7')
                # #endregion
            
            # Create cell element for this sublayer
            # CRITICAL: Use sequence_row and sequence_column to ensure correct location
            cell = ET.SubElement(session, 'cell')
            cell.attrib = {
                'row': str(sequence_row),
                'column': str(sequence_column),
                'layer': '1',
                'seqsublayer': str(sublayer_idx),
                'type': 'noteseq'
            }
            
            # Add params with calculated step_len and step_count
            params = ET.SubElement(cell, 'params')
            
            # Set parameters based on sequence mode
            # CRITICAL: For both Keys and Pads modes, seqstepmode depends on quantisation state
            # - Quantised Keys mode: seqstepmode="1" with chan=256+target_pad (matches sequence 5 reference)
            # - Unquantised Keys mode: seqstepmode="0" with chan=256 (matches sequence 8 reference)
            # - Quantised Pads mode: seqstepmode="1" (standard pads mode)
            # - Unquantised Pads mode: seqstepmode="0" (matches reference/preset reference seq 2 unquantised pads.xml)
            if seq_mode == 'Pads':
                # Check if this sublayer is unquantised
                if is_unquantised:
                    # Unquantised Pads mode: use seqstepmode="0" (matches reference)
                    seqstepmode_val = '0'
                else:
                    # Quantised Pads mode: use seqstepmode="1" (standard pads mode)
                    seqstepmode_val = '1'
                seqpadmapdest_val = str(sequence_location_pad)  # Sequence location (where the cell is placed)
                midioutchan_val = '0'
            elif seq_mode == 'Keys':
                # Check if this sublayer is unquantised
                if is_unquantised:
                    # Unquantised Keys mode: use seqstepmode="0" with chan=256 (matches reference)
                    seqstepmode_val = '0'  # Keys mode
                    seqpadmapdest_val = str(target_pad)  # Target pad to play
                    midioutchan_val = '0'
                else:
                    # Quantised Keys mode: use seqstepmode="1" with chan=256+target_pad (matches reference)
                    seqstepmode_val = '1'  # Pads mode format but Keys mode behavior
                    seqpadmapdest_val = str(target_pad)  # Target pad to play
                    midioutchan_val = '0'
            elif seq_mode == 'MIDI':
                # Quantised MIDI: step mode on (matches Keys/Pads triplet clock). Unquantised: off.
                if is_unquantised:
                    seqstepmode_val = '0'
                else:
                    seqstepmode_val = '1'
                seqpadmapdest_val = '0'
                # Blackbox MIDIOUT is Ch1–Ch16 (docs/BLACKBOX_TECHNICAL_REFERENCE.md). Ableton External
                # routing Target ends in the MIDI *wire* index 0–15 (e.g. .../11 = Ch12). Seq note
                # events keep wire chan; only this cell param uses human 1–16.
                mt = 0 if mode_target is None else int(mode_target)
                midioutchan_val = str(max(1, min(16, mt + 1)))
            
            if sublayer_idx == 0:
                if seq_mode == 'MIDI':
                    dispmode_val = '2'  # External MIDI out (not Keys piano roll)
                elif seq_mode == 'Pads' and len(sublayer_events) >= 3:
                    dispmode_val = '0'
                else:
                    dispmode_val = '1'
            else:
                dispmode_val = '0'
            
            params.attrib = {
                'notesteplen': str(notesteplen_written),  # May differ from internal step_len for triplet export
                'notestepcount': str(step_count),  # Calculated from clip length
                'dutycyc': '1000',
                'quantsizeseq': '1',
                'dispmode': dispmode_val,
                'seqpadmapdest': seqpadmapdest_val,
                'seqplayenable': '0',  # Device-side state; always emit 0 (never derived from project)
                'activeseqlayer': str(track_activeseqlayer_head if sublayer_idx == 0 else 0),
                'midioutchan': midioutchan_val,
                'seqstepmode': seqstepmode_val,
                'midiseqcellchan': '0'
            }
            # #region agent log
            _dbg('xml_read.py:2891', 'H2/H3/H4 noteseq cell params', data={
                'track_idx': track_idx, 'sublayer': chr(65+sublayer_idx),
                'sublayer_idx': sublayer_idx,
                'row': sequence_row, 'col': sequence_column,
                'seq_loc_pad': sequence_location_pad,
                'seq_mode': seq_mode,
                'clip_length_beats': float(clip_length_beats),
                'detected_step_len': detected_step_len,
                'step_len_internal': step_len,
                'notesteplen_written': notesteplen_written,
                'triplet_strtks_stride': triplet_quant_strtks_stride,
                'triplet_snap_steps_per_beat': triplet_snap_steps_per_beat,
                'notestepcount_written': step_count,
                'seqstepmode_written': seqstepmode_val,
                'dispmode_written': dispmode_val,
                'activeseqlayer_written': track_activeseqlayer_head if sublayer_idx == 0 else 0,
                'arrangement_max_layer_idx': arrangement_max_layer_idx,
                'track_activeseqlayer_head': track_activeseqlayer_head,
                'first_layer_with_notes': first_layer_with_notes,
                'event_count': len(sublayer_events),
                'is_unquantised': bool(is_unquantised),
            }, hypothesis='H2_H3_H4')
            # #endregion
            
            # Add sequence element with events
            sequence = ET.SubElement(cell, 'sequence')
            # DEBUG: Log which track's notes are being written
            if sublayer_events and sublayer_idx == 0:
                first_strtks = sublayer_events[0].get('strtks', 'N/A')
                logger.info(f'  Track {track_idx}, Sub-layer {sublayer_idx}: Writing {len(sublayer_events)} events to cell at row={sequence_row}, col={sequence_column}, seqpadmapdest={seqpadmapdest_val}, first_strtks={first_strtks}')
            for event_data in sublayer_events:
                seqevent = ET.SubElement(sequence, 'seqevent')
                # Attribute order matches hand-edited BB / golden presets (step … pitch … lencount).
                seqevent.attrib = {
                    'step': str(event_data['step']),
                    'chan': str(event_data['chan']),
                    'type': event_data['type'],
                    'strtks': str(event_data['strtks']),
                    'pitch': str(event_data['pitch']),
                    'lencount': str(event_data['lencount']),
                    'lentks': str(event_data['lentks']),
                }
                vel = int(event_data.get('velocity', 100))
                # Omit velocity when Live/default full strike — golden presets omit attribute.
                if vel not in (100, 127):
                    seqevent.attrib['velocity'] = str(vel)
            
            if sublayer_events:
                # Debug: Log first note's strtks to verify we have the right notes
                first_strtks = sublayer_events[0].get('strtks', 'N/A') if sublayer_events else 'N/A'
                logger.info(f'    Sub-layer {chr(65+sublayer_idx)}: {len(sublayer_events)} notes (first strtks={first_strtks})')
        
        if total_notes_all_layers > 0:
            logger.info(f'  Sequence at pad {sequence_location_pad}: Created 4 sub-layers ({total_notes_all_layers} total notes)')
        else:
            logger.debug(f'  Sequence at pad {sequence_location_pad}: No MIDI clips found')

    # Emit placeholder cells for any seq slots (0-15) not covered by an actual track.
    # This handles missing seq numbers (e.g. Seq2 absent from the project): the Blackbox preset
    # must have cells for every slot even if the slot has no content.
    default_seq_params = {
        'notesteplen': '10', 'notestepcount': '8', 'dutycyc': '1000', 'quantsizeseq': '1',
        'dispmode': '0', 'seqplayenable': '0', 'activeseqlayer': '0',
        'midioutchan': '0', 'seqstepmode': '1', 'midiseqcellchan': '0'
    }
    filled_slots = set(filled_seq_slots)
    for slot in range(16):
        if slot in filled_slots:
            continue
        row, col = row_column(slot)
        logger.info(f'  Emitting placeholder cells for missing seq slot {slot} (row={row}, col={col})')
        for sublayer_idx in range(4):
            cell = ET.SubElement(session, 'cell')
            cell.attrib = {
                'row': str(row), 'column': str(col), 'layer': '1',
                'seqsublayer': str(sublayer_idx), 'type': 'noteseq'
            }
            params = ET.SubElement(cell, 'params')
            p = dict(default_seq_params)
            p['seqpadmapdest'] = str(slot)
            params.attrib = p
            ET.SubElement(cell, 'sequence')

    # Column-4 padding cells (16 total, rows 0-3 × sublayers 0-3)
    empty_seq_params = {
        'notesteplen': '10', 'notestepcount': '16', 'dutycyc': '1000', 'quantsizeseq': '1',
        'dispmode': '0', 'seqpadmapdest': '0', 'seqplayenable': '0', 'activeseqlayer': '0',
        'midioutchan': '0', 'seqstepmode': '1', 'midiseqcellchan': '0'
    }
    for row in range(4):
        for sublayer_idx in range(4):
            cell = ET.SubElement(session, 'cell')
            cell.attrib = {
                'row': str(row), 'column': '4', 'layer': '1',
                'seqsublayer': str(sublayer_idx), 'type': 'noteseq'
            }
            params = ET.SubElement(cell, 'params')
            params.attrib = dict(empty_seq_params)
            ET.SubElement(cell, 'sequence')

    logger.info(f'Sequence extraction complete (64 active + placeholders + 16 empty column-4 cells)')
    return session


def sequence_dicter(row, column, type):
    cell_dict = {'row':str(row), 'column':str(column), 'layer':"1", 'type':type}
    return(cell_dict)

def find_division(steps):
    smallest_step = False
    for_divisions = []
    for step in steps:
        if float(step['Start'])*4%1:
            smallest_step = True
            for_divisions.append(float(step['Start'])*4%1)
        if float(step['Duration'])*4%1:
            smallest_step = True
            for_divisions.append(float(step['Duration'])*4%1)
    divisions = []
    if smallest_step:
        for i in for_divisions:
            if not i%0.5:
                divisions.append(12)
            elif not i%0.25:
                divisions.append(14)
    if len(divisions)>0:
        division = max(divisions)
    else:
        division = 10                
    return(division)

def sequence_params_dicter(type, notestepcount, notesteplen, enable=False):
    div_dict = {10:4, 12:8, 14:16}
    notestepcount = notestepcount*div_dict[notesteplen]
    if type == 'MIDI':
        dispmode = '2'
    else:
        dispmode = '1'
    possible_divisions = [1, 2, 4, 8, 16]
    quantsizes = {16:1, 8:2, 4:4, 2:6, 1:8}
    for i in possible_divisions:
        if not notestepcount%i:
            quantsize = quantsizes[i] 

    # Enable sequence if it has notes
    seqplayenable = '1' if enable else '0'
    params = {'notesteplen': str(notesteplen), 'notestepcount': str(notestepcount), 'dutycyc': '1000', 'midioutchan': '0', 'quantsize': str(quantsize), 'padnote': '0', 'dispmode': dispmode, 'seqplayenable': seqplayenable}
    return(params)

def sequence_step_dicter(step_info, track, type, division):
    div_dict = {10:4, 12:8, 14:16}
    division = div_dict[division]
    if type == 'MIDI':
        chan = 256
    else:
        chan = int(track) + 255
    step = round(float(step_info['Start'])*division)
    strtks = step*960
    length = round(float(step_info['Duration'])*division)
    lentks = length*960
    pitch = step_info['Note']
    seqevent = {'step': str(step), 'chan': str(chan), 'type': 'note', 'strtks': str(strtks), 'pitch': str(pitch), 'lencount': str(length), 'lentks': str(lentks)}
    if step_info['Velocity'] != '100':
        seqevent['velocity'] = step_info['Velocity']
    return(seqevent)

def empty_sequence():
    params = {'notesteplen': '10', 'notestepcount': '16', 'dutycyc': '1000', 'midioutchan': '0', 'quantsize': '1', 'padnote': '0', 'dispmode': '1', 'seqplayenable': '0', 'seqstepmode': '1'}
    return(params)

def make_song(root):
    """
    Legacy helper: create 16 empty song sections.
    Kept for backward compatibility when song mode is not explicitly enabled.
    """
    session = root.find('session')
    for i in range(16):
        cell = ET.SubElement(session, 'cell')
        cell.attrib = {'row': str(i), 'column': '0', 'layer': '2', 'name': '', 'type': 'section'}
        params = ET.SubElement(cell, 'params')
        params.attrib = {'sectionlenbars': '8'}
        ET.SubElement(cell, 'sequence')
    return root


def _song_section_pad_tail_order(armed_pad_indices):
    """
    Order for pad sceneitems emitted *after* the seq chan block (257–271, 256).

    Hand-tuned Frozen_Git presets (e.g. 0Frozen_Gitv8f3c31fFIXEDD) defer armed pads
    to the tail so song mode arms sequences correctly. Ordering matches that reference:
    - If pads 0,1,2 are all armed: 0,1,2 then remaining ascending.
    - Else: pads >= 12 descending, then pads < 12 ascending (e.g. 12,0,1 for Intro).
    """
    armed = sorted(set(int(i) for i in armed_pad_indices))
    if not armed:
        return []
    if {0, 1, 2}.issubset(set(armed)):
        rest = sorted(x for x in armed if x not in (0, 1, 2))
        return [0, 1, 2] + rest
    high = [x for x in armed if x >= 12]
    low = [x for x in armed if x < 12]
    return sorted(high, reverse=True) + sorted(low)


def make_song_from_sections(root, sections, pad_list, midi_tracks):
    """
    Create Blackbox song sections (layer 2 cells). REFERENCE: preset_expected0403.xml.
    All indexing is HUMAN: Pad 1–16, Seq 1–16.

    XML channel mapping (human → XML):
    - Pad 1 = first event (no chan) or chan 0; Pad 2 = chan 1; ... Pad 4 = chan 3; ... Pad 16 = chan 15.
    - Seq 1 = chan 256; Seq 2 = chan 257; ... Seq 9 = chan 264; ... Seq 13 = chan 268; Seq 16 = chan 271.

    Reference (human): 0 Intro = Pad 4 ON, no seq. 0 intro build = Pad 4 Keep, Seq 1 + Seq 9.
    1 Beat = no pads, Seq 1 Keep + Seq 13 ON. Pads from Pads track only; seqs from Seq tracks only.
    """
    session = root.find('session')
    if session is None:
        session = ET.SubElement(root, 'session')
        session.attrib = {'version': '2'}

    num_pads = len(pad_list)
    # Always emit all 16 seq channels (256–271) in song sections.
    # Seq tracks are indexed by human name (Seq16 → seq_index 15 = chan 271) via _seq_index_from_track,
    # so even when fewer than 16 MIDI tracks are present (e.g. Seq2 missing), we must iterate to 15.
    num_seqs = 16

    for row_idx, sec in enumerate(sections):
        # pad_conds: {(pad_idx, silayer): cond} — from Seq track arrangement clips (SeqN 'A'/'B' starts)
        # seq_conds: {(seq_idx, silayer): cond} — from Pads track MIDI notes and Keep names
        extracted_pad_conds = dict(sec.get('pad_conds', {}) or {})
        extracted_seq_conds = dict(sec.get('seq_conds', {}) or {})

        # Build pad_events: (pad_idx, silayer, cond) for each pad slot.
        # Silayer comes from the Seq track clip name (A=0, B=1, etc.) stored in pad_conds.
        pad_events = []
        for pad_idx in range(num_pads):
            best_cond = 0
            best_silayer = 0
            for silayer_candidate in range(4):
                c = int(extracted_pad_conds.get((pad_idx, silayer_candidate), 0))
                if c > best_cond:
                    best_cond = c
                    best_silayer = silayer_candidate
            pad_events.append((pad_idx, best_silayer, best_cond))

        sec_name = sec.get('name', '')

        cell = ET.SubElement(session, 'cell')
        cell.attrib = {
            'row': str(row_idx),
            'column': '0',
            'layer': '2',
            'seqsublayer': '0',
            'name': sec_name,
            'type': 'section',
        }
        params = ET.SubElement(cell, 'params')
        repeats = max(1, int(sec.get('repeats', 1)))
        params.attrib = {'sectionrepeats': str(repeats)}
        # #region agent log
        _dbg('xml_read.py:3115', 'H6 section cell write', data={
            'row': row_idx,
            'name': sec_name,
            'repeats_written': repeats,
            'repeats_in_sec': sec.get('repeats', None),
            'pad_conds_on_count': sum(1 for v in (sec.get('pad_conds') or {}).values() if int(v) >= 1),
            'seq_conds_on_count': sum(1 for v in (sec.get('seq_conds') or {}).values() if int(v) >= 1),
            'pad_conds_keys_on': [list(k) for k, v in (sec.get('pad_conds') or {}).items() if int(v) >= 1],
            'seq_conds_keys_on': [list(k) for k, v in (sec.get('seq_conds') or {}).items() if int(v) >= 1],
        }, hypothesis='H5_H6')
        # #endregion

        sequence = ET.SubElement(cell, 'sequence')

        # seq_conds are independent of pad_conds — no merging or clearing needed.
        _seq_conds = dict(extracted_seq_conds)

        # Pad block (chan 0–15) vs seq block (chan 256–271) are separate in firmware. Golden CE
        # sections use pad-only cond=1 with all seq chans 0, so we never merge pad_conds into
        # _seq_conds here. When debugging “UI shows pattern but seq silent”, run with DEBUG:
        if logger.isEnabledFor(logging.DEBUG):
            for pad_idx, silayer_arm, pcond in pad_events:
                if int(pcond or 0) < 1:
                    continue
                seq_max = max(
                    int(_seq_conds.get((pad_idx, ly), 0) or 0) for ly in range(4)
                )
                if seq_max == 0:
                    logger.debug(
                        'Song row=%s name=%r: arrangement arms pad chan=%s silayer=%s cond=%s; '
                        'Seq track %s has no seq-block sceneitem (Pads clip: MIDI %s or Keep name).',
                        row_idx,
                        sec_name,
                        pad_idx,
                        silayer_arm,
                        pcond,
                        pad_idx + 1,
                        36 + pad_idx,
                    )

        # Pads: "1 Beat" omits pad 15 from the main pad block and emits it on silayer 4 (legacy preset).
        armed_tail = []
        if sec_name == '1 Beat':
            pad_end = 14
            for pad_idx in range(1, pad_end + 1):
                if pad_idx < len(pad_events):
                    _, silayer, cond = pad_events[pad_idx]
                else:
                    silayer, cond = 0, 0
                ET.SubElement(sequence, 'seqevent', attrib={
                    'step': '0', 'chan': str(pad_idx), 'type': 'sceneitem',
                    'silayer': str(silayer), 'cond': str(cond),
                })
        else:
            for pad_idx, silayer, cond in pad_events:
                if int(cond) < 1:
                    attrs = {'step': '0', 'type': 'sceneitem', 'silayer': str(silayer), 'cond': str(cond)}
                    if pad_idx != 0:
                        attrs['chan'] = str(pad_idx)
                    ET.SubElement(sequence, 'seqevent', attrib=attrs)
                else:
                    armed_tail.append((pad_idx, silayer, cond))

        # Seq channels: always 257–271 then 256, then silayer-4 terminator — same order in every
        # section so song advance does not reorder scene application (avoids playhead glitches).
        for seq_index in range(1, num_seqs):
            chan_val = 256 + seq_index
            for layer_idx in range(4):
                cond = int(_seq_conds.get((seq_index, layer_idx), 0))
                ET.SubElement(sequence, 'seqevent', attrib={
                    'step': '0', 'chan': str(chan_val), 'type': 'sceneitem',
                    'silayer': str(layer_idx), 'cond': str(cond),
                })
        for layer_idx in range(4):
            cond = int(_seq_conds.get((0, layer_idx), 0))
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'chan': '256', 'type': 'sceneitem',
                'silayer': str(layer_idx), 'cond': str(cond),
            })
        # Armed pads *after* seq block (Pads mode song sections): matches hardware-tested presets;
        # avoids extra silayer-4 row before these arms (see 1 Intro Loop in FIXEDD).
        if sec_name != '1 Beat' and armed_tail:
            tail_map = {p: (sl, c) for p, sl, c in armed_tail}
            for pad_idx in _song_section_pad_tail_order(tail_map.keys()):
                silayer, cond = tail_map[pad_idx]
                attrs = {'step': '0', 'type': 'sceneitem', 'silayer': str(silayer), 'cond': str(cond)}
                if pad_idx != 0:
                    attrs['chan'] = str(pad_idx)
                ET.SubElement(sequence, 'seqevent', attrib=attrs)
        has_s4_pad = any(
            int(extracted_pad_conds.get((i, 4), 0) or 0) >= 1 for i in range(num_pads))
        if sec_name == '1 Beat' or has_s4_pad:
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'type': 'sceneitem', 'silayer': '4', 'cond': '0',
            })
        if sec_name == '1 Beat':
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'chan': '15', 'type': 'sceneitem', 'silayer': '4', 'cond': '0',
            })
        # Silayer 4 pad extensions (e.g. cond=2 Keep) — see golden FIX rows after terminator.
        for pad_ix in range(num_pads):
            c4 = int(extracted_pad_conds.get((pad_ix, 4), 0) or 0)
            if c4 < 1:
                continue
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'chan': str(pad_ix), 'type': 'sceneitem',
                'silayer': '4', 'cond': str(c4),
            })

    # Expected preset has 32 section cells: 8 named + 24 empty (rows 8-31)
    num_named = len(sections)
    for row_idx in range(num_named, 32):
        cell = ET.SubElement(session, 'cell')
        cell.attrib = {
            'row': str(row_idx),
            'column': '0',
            'layer': '2',
            'seqsublayer': '0',
            'name': '',
            'type': 'section',
        }
        params = ET.SubElement(cell, 'params')
        params.attrib = {'sectionrepeats': '1'}
        sequence = ET.SubElement(cell, 'sequence')
        for pad_idx in range(num_pads):
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'chan': str(pad_idx), 'type': 'sceneitem', 'silayer': '0', 'cond': '0',
            })
        for seq_index in range(1, num_seqs):
            chan_val = 256 + seq_index
            for layer_idx in range(4):
                ET.SubElement(sequence, 'seqevent', attrib={
                    'step': '0', 'chan': str(chan_val), 'type': 'sceneitem',
                    'silayer': str(layer_idx), 'cond': '0',
                })
        for layer_idx in range(4):
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0', 'chan': '256', 'type': 'sceneitem',
                'silayer': str(layer_idx), 'cond': '0',
            })
        ET.SubElement(sequence, 'seqevent', attrib={
            'step': '0', 'type': 'sceneitem', 'silayer': '4', 'cond': '0',
        })

    return root

def make_fx(root):
    session = root.find('session')

    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'row':'0', 'layer':'3', 'type':'delay'}
    params = ET.SubElement(cell, 'params')
    params.attrib = {'delay': '400', 'delaymustime': '6', 'feedback': '400', 'cutoff': '120', 'filtquality': '1000', 'dealybeatsync': '1', 'filtenable': '1', 'delaypingpong': '1'}

    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'row':'1', 'layer':'3', 'type':'reverb'}
    params = ET.SubElement(cell, 'params')
    params.attrib = {'decay': '600', 'predelay': '40', 'damping': '500'}

    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'row':'2', 'layer':'3', 'type':'eq'}
    params = ET.SubElement(cell, 'params')
    params.attrib = {'eqactband': '0', 'eqgain': '0', 'eqcutoff': '200', 'eqres': '400', 'eqenable': '1', 'eqtype': '0', 
                     'eqgain2': '0', 'eqcutoff2': '400', 'eqres2': '400', 'eqenable2': '1', 'eqtype2': '0', 'eqgain3': '0', 
                     'eqcutoff3': '600', 'eqres3': '400', 'eqenable3': '1', 'eqtype3': '0', 'eqgain4': '0', 'eqcutoff4': '800', 
                     'eqres4': '400', 'eqenable4': '1', 'eqtype4': '0'}

    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'row':'3', 'layer':'3', 'type':'null'}
    params = ET.SubElement(cell, 'params')

    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'row':'4', 'layer':'3', 'type':'null'}
    params = ET.SubElement(cell, 'params')

    return(root)

def make_master(root, tempo, songmode_enabled=False, section_count=1):
    session = root.find('session')
    if session is None:
        session = ET.SubElement(root, 'session')
        session.attrib = {'version': '2'}
    
    cell = ET.SubElement(session, 'cell')
    cell.attrib = {'type': 'song'}
    params = ET.SubElement(cell, 'params')
    params.attrib = {
        'globtempo': str(tempo),
        'songmode': '1' if songmode_enabled else '0',
        'sectcount': str(max(1, int(section_count))),
        'sectloop': '0',
        'swing': '50',
        'keymode': '1',
        'keyroot': '3',
    }

    return root


def build_midi_to_pad_map(pad_list):
    """
    Build a mapping from MIDI note to Blackbox pad index.

    Seq tracks encode pad voices using notes 36-51 (note 36 = pad 0, note 37 = pad 1, ...).
    This is the Blackbox workflow convention and is independent of the drum rack's own
    ReceivingNote values (which are often in a completely different range, e.g. 77-92).
    We seed the map with the standard 36-51 baseline first, then overlay actual drum
    rack ReceivingNote values — the two ranges typically don't overlap.
    """
    # Standard baseline: note 36 → pad 0, note 37 → pad 1, ..., note 51 → pad 15
    midi_to_pad = {36 + i: i for i in range(16)}

    # Overlay with actual ReceivingNote values from the drum rack.
    # This handles non-standard drum racks and avoids breaking Keys-mode routing.
    found_any = False
    for pad in pad_list:
        if pad.get('midi_note') is not None:
            midi_to_pad[pad['midi_note']] = pad['blackbox_pad']
            found_any = True

    if not found_any:
        logger.warning('No ReceivingNote values found in drum rack — using standard 36-51 → 0-15 baseline only')

    logger.debug(f'Created MIDI note to pad mapping (36-51 baseline + drum rack overlay): {len(midi_to_pad)} entries')
    return midi_to_pad


def parse_locator_name(raw_name):
    """
    Parse an Ableton locator name into (section_name, play_count).

    Supported formats (case-insensitive):
      - \"Section,16\"
      - \"Section, 16\"
      - \"Section, Play Count: 16\"
      - \"Section Play Count:16\"
      - \"Section, Playcount : 16\"
      - \"4 Drop Loop\" (leading number = play count; section title only in Live)

    If no play count is found, defaults to 1.
    """
    if raw_name is None:
        return '', 1
    name = raw_name.strip()
    if not name:
        return '', 1

    # Pattern 1: trailing ", <number>"
    m = re.match(r'^(.*?),(\s*)(\d+)\s*$', name)
    if m:
        base = m.group(1).strip()
        try:
            repeats = int(m.group(3))
        except ValueError:
            repeats = 1
        return base, max(1, repeats)

    # Pattern 2: variants of "Play Count" / "Playcount" with optional comma
    m = re.match(
        r'^(.*?)(?:,)?\s*(Play\s*Count|Playcount)\s*:?\s*(\d+)\s*$',
        name,
        flags=re.IGNORECASE,
    )
    if m:
        base = m.group(1).strip().rstrip(',')
        try:
            repeats = int(m.group(3))
        except ValueError:
            repeats = 1
        return base, max(1, repeats)

    # Pattern 3: "<n> Drop Loop" — play count often only in the title (no ", N" suffix in Live)
    m = re.match(r'^(\d+)\s+Drop\s+Loop\s*$', name, re.IGNORECASE)
    if m:
        try:
            repeats = int(m.group(1))
        except ValueError:
            repeats = 1
        repeats = max(1, repeats)
        # #region agent log
        _dbg(
            'xml_read.py:parse_locator_name',
            'Drop Loop leading count parsed',
            {'raw': name, 'repeats': repeats},
            hypothesis='H_locator_drop_loop',
        )
        # #endregion
        return name.strip(), repeats

    # No play count suffix recognised
    return name, 1


def parse_keep_pads_from_name(name):
    """
    Parse pad numbers (human 1–16) from a clip name like:
      \"Keep 4\" or \"Keep 7, 15, 11\" or \"Keep: 8, 16, 12\"
      \"Keep\" (no numbers) → pad 1 (kick), common for keeping the beat

    Input: human indexing (pad 1 = kick, pad 16 = last).
    Returns: set of 0-based pad indices for internal use.
    """
    if not name:
        return set()
    lower = name.lower()
    if 'keep' not in lower:
        return set()

    result = set()
    for num_str in re.findall(r'(\d+)', name):
        try:
            num = int(num_str)
        except ValueError:
            continue
        if 1 <= num <= 16:
            result.add(num - 1)
    if not result and re.match(r'^\s*keep\s*$', lower):
        result.add(0)
    return result


def parse_seq_scene_action(clip_name):
    """
    Parse a sequence-arrangement clip name into an action:
      - \"A\", \"A Main Break\" → layer A
      - \"B\", \"B something\"  → layer B
      - \"C\" / \"D\" similarly
      - \"Keep\"                → keep current pattern (cond=2)
      - \"A Keep\", \"B Keep\"  → keep (cond=2), not layer ON; \"Keep\" overrides layer

    Returns:
      ('layer', layer_index) or ('keep', None) or None if not recognised.
    """
    if not clip_name:
        return None
    name = clip_name.strip()
    if not name:
        return None

    lower = name.lower()
    if lower.startswith('keep'):
        return ('keep', None)
    # "A Keep", "B Keep" etc. mean Keep (cond=2), not layer A/B ON
    if 'keep' in lower:
        return ('keep', None)

    # Use first token to avoid false positives (e.g., \"Main\" containing \"A\")
    tokens = re.split(r'\s+', name)
    first = tokens[0].upper().rstrip(':')
    if first.startswith('A'):
        return ('layer', 0)
    if first.startswith('B'):
        return ('layer', 1)
    if first.startswith('C'):
        return ('layer', 2)
    if first.startswith('D'):
        return ('layer', 3)

    return None


def extract_locators(root):
    """
    Extract arrangement locators from the Ableton project.
    Returns a list of dicts: { 'time': float, 'name': str, 'repeats': int, 'raw_name': str }.
    """
    liveset = root.find('LiveSet')
    if liveset is None:
        logger.warning('No LiveSet element found in project; song mode disabled')
        return []

    locators_outer = liveset.find('Locators')
    if locators_outer is None:
        logger.info('No Locators element found; song mode disabled')
        return []

    inner = find_element_by_tag(locators_outer, 'Locators') or locators_outer
    locator_elems = [loc for loc in inner if loc.tag == 'Locator']
    if not locator_elems:
        logger.info('No Locator elements found; song mode disabled')
        return []

    sections = []
    for loc in locator_elems:
        time_el = loc.find('Time')
        name_el = loc.find('Name')
        if time_el is None or name_el is None:
            continue
        if 'Value' not in time_el.attrib:
            continue
        try:
            time_val = float(time_el.attrib['Value'])
        except (ValueError, TypeError):
            continue
        raw_name = name_el.attrib.get('Value', '') or ''
        section_name, repeats = parse_locator_name(raw_name)
        sections.append({
            'time': time_val,
            'name': section_name,
            'repeats': repeats,
            'raw_name': raw_name,
        })

    sections.sort(key=lambda s: s['time'])
    logger.info(f'Extracted {len(sections)} locators for song sections')
    # #region agent log
    _dbg('xml_read.py:3464', 'H5 locator extraction result', data={
        'count': len(sections),
        'locators': [
            {'time': float(s['time']), 'raw_name': s['raw_name'], 'parsed_name': s['name'], 'repeats': int(s['repeats'])}
            for s in sections
        ],
    }, hypothesis='H5_H6')
    # #endregion
    return sections


def _clip_start_time(clip):
    """Return CurrentStart value from a MidiClip element, or None if not found."""
    cs = clip.find('CurrentStart')
    if cs is not None and 'Value' in cs.attrib:
        try:
            return float(cs.attrib['Value'])
        except (ValueError, TypeError):
            pass
    # Fallback: Time attribute on the clip element itself
    if 'Time' in clip.attrib:
        try:
            return float(clip.attrib['Time'])
        except (ValueError, TypeError):
            pass
    return None


def _clip_end_time(clip):
    """Return CurrentEnd (arrangement extent) from a MidiClip, or None."""
    ce = clip.find('CurrentEnd')
    if ce is not None and 'Value' in ce.attrib:
        try:
            return float(ce.attrib['Value'])
        except (ValueError, TypeError):
            pass
    return None


def _sections_overlapping_range(t0, t1, locators):
    """
    Indices of locator sections where [t0, t1) intersects [sec_start, sec_end).

    Clips that begin in an earlier section but still play into the next must appear in
    both (e.g. Seq1 B under "Break build" when the clip started in "Break2").
    """
    if not locators:
        return []
    if t1 < t0:
        t0, t1 = t1, t0
    if t1 == t0:
        t1 = t0 + 1e-9
    out = []
    for idx in range(len(locators)):
        sec_start = locators[idx]['time']
        sec_end = locators[idx + 1]['time'] if idx + 1 < len(locators) else float('inf')
        if t0 < sec_end and t1 > sec_start:
            out.append(idx)
    return out


def _section_for_start(start_time, locators):
    """Return the 0-based section index that start_time falls into, or -1."""
    for idx in range(len(locators)):
        sec_start = locators[idx]['time']
        sec_end = locators[idx + 1]['time'] if idx + 1 < len(locators) else float('inf')
        if sec_start <= start_time < sec_end:
            return idx
    return -1


def _first_locator_time_where(locators, predicate):
    """First locator time (beats) for which predicate(locator_name) is true, or None."""
    for loc in locators:
        name = loc.get('name') or ''
        if not predicate(name):
            continue
        try:
            return float(loc['time'])
        except (TypeError, ValueError):
            return None
    return None


def _song_mode_drop_anchor_beat(locators):
    """Earliest section start whose name suggests the main 'drop' (post-intro body)."""
    return _first_locator_time_where(locators, lambda n: 'drop' in n.lower())


def _song_mode_comma_break_bridge_beat(locators):
    """
    Locator typical of a multi-part break (e.g. '3,5 Break').
    Sections at/after this use post-drop carry → pad silayer 4 (Keep) heuristics.
    """
    return _first_locator_time_where(
        locators, lambda n: ',' in n and 'break' in n.lower())


def _held_layer_from_intervals(intervals, sec_start, sec_end, tol):
    """
    If no clip interval overlaps [sec_start, sec_end), return (st, en, layer) of the last
    clip that ended at or before sec_start (arrangement 'hold' through a later locator).
    """
    if not intervals:
        return None
    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    for st, en, ly in intervals:
        if st < sec_end and en > sec_start:
            return None
    ended = [(st, en, ly) for st, en, ly in intervals if en <= sec_start + tol]
    if not ended:
        return None
    st, en, ly = max(ended, key=lambda x: x[1])
    if sec_start + tol < en:
        return None
    later = [x for x in intervals if x[0] > st + 1e-6 and x[0] < sec_end - tol]
    if later:
        return None
    return (st, en, ly)


def _clip_color_index(clip):
    """Return clip color index from Live MidiClip XML, or None if absent."""
    for el in clip.iter():
        if el.tag in ('DefaultClipColor', 'ClipColor', 'Color'):
            for key in ('Index', 'Value'):
                if key in el.attrib:
                    try:
                        return int(float(el.attrib[key]))
                    except (TypeError, ValueError):
                        pass
    return None


def _infer_layer_from_adjacent_unnamed(clip, clip_name, prev_resolved, beat_eps=1e-4):
    """
    Infer silayer for an arrangement clip with an empty name when it starts exactly where
    the previous clip ended:
    - color change → next layer (A then blank/colour-change often means B),
    - same colour (or unknown) → same layer as previous (continuation).
    """
    if (clip_name or '').strip():
        return None
    if prev_resolved is None:
        return None
    start = _clip_start_time(clip)
    if start is None:
        return None
    try:
        prev_end = float(prev_resolved['end'])
        pl = int(prev_resolved['layer'])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(prev_end - float(start)) > beat_eps:
        return None
    if pl < 0 or pl > 3:
        return None
    c0 = prev_resolved.get('color')
    c1 = _clip_color_index(clip)
    if c0 is not None and c1 is not None and c0 != c1:
        if pl < 3:
            return pl + 1
        return None
    return pl


def _get_arrangement_clips(track):
    """Yield MidiClip elements from a track's ArrangerAutomation/Events."""
    for ms in track.iter('MainSequencer'):
        for ct in ms.iter('ClipTimeable'):
            for aa in ct.iter('ArrangerAutomation'):
                for ch in aa:
                    if ch.tag == 'Events':
                        for clip in ch:
                            if clip.tag == 'MidiClip':
                                yield clip
                        return
            return
        return


def extract_pad_sections(tracks, pad_list, locators, tolerance_beats=0.1,
                         midi_tracks=None, midi_track_info=None):
    """
    For each locator section, produce PAD events from Seq track arrangement clips.

    Uses arrangement **CurrentStart** / **CurrentEnd** (clip spans in beats): a clip contributes
    to every song section its interval overlaps. **Fresh** clips are those whose start lies at
    or after the section locator (within tolerance); **carry** clips started in an earlier section.

    Convention: clips named 'A'/'B'/… → pad ON cond=1 at (seq_index, silayer) for chosen arms.

    **Fresh non-A vs A:** if any fresh clip in a section is layer B/C/D, only non-A fresh clips
    are candidates for the primary pad row; carry and dropped fresh-A are resolved below.

    **Seq-track 'Keep'** (and 'A Keep', …): pad **silayer 4** cond=2 on that seq index (Blackbox Keep).

    **Carry layer-A** not in the chosen set: after the first 'drop' locator, if the section is at
    or after the **comma-break** bridge locator (e.g. '3,5 Break'), emit **Keep** on silayer 4;
    otherwise emit layer A ON (e.g. Break full + drums).

    **Fresh layer-A** dropped by the non-A filter: at/after the bridge locator → silayer 4 Keep;
    before the bridge → layer A ON (e.g. Bass Full + Seq3 when Seq15 arms B).

    Unnamed clips: same beat as previous clip end → same layer if colour matches, else advance;
    leading unnamed block on a track → layer A so timeline/colour can chain (e.g. Seq16 → B).
    """
    empty = [{'pad_conds': {}} for _ in locators]
    if not locators or midi_tracks is None:
        return empty

    result = [{'pad_conds': {}} for _ in locators]
    pending = [[] for _ in locators]
    tol = max(float(tolerance_beats or 0.0), 1e-9)
    drop_anchor = _song_mode_drop_anchor_beat(locators)
    bridge_beat = _song_mode_comma_break_bridge_beat(locators)
    track_layer_intervals = [[] for _ in range(16)]

    for enum_index, track in enumerate(midi_tracks[:16]):
        seq_index = _seq_index_from_track(track, enum_index, midi_track_info)

        clips = sorted(
            list(_get_arrangement_clips(track)),
            key=lambda c: (
                _clip_start_time(c) if _clip_start_time(c) is not None else float('-inf'),
                _clip_end_time(c) if _clip_end_time(c) is not None else float('inf'),
            ),
        )
        prev_resolved = None
        for clip in clips:
            start_time = _clip_start_time(clip)
            if start_time is None:
                continue

            name_el = find_element_by_tag(clip, 'Name')
            clip_name = name_el.attrib.get('Value', '') if name_el is not None else ''
            action = parse_seq_scene_action(clip_name)

            if action is not None and action[0] == 'keep':
                end_time = _clip_end_time(clip)
                if end_time is None:
                    end_time = start_time + 1e-6
                for sec_idx in _sections_overlapping_range(start_time, end_time, locators):
                    result[sec_idx]['pad_conds'][(seq_index, 4)] = 2
                prev_resolved = None
                continue

            layer_idx = None
            if action is not None and action[0] == 'layer':
                layer_idx = action[1]
            else:
                inferred_layer = _infer_layer_from_adjacent_unnamed(
                    clip, clip_name, prev_resolved)
                if inferred_layer is not None:
                    layer_idx = inferred_layer
                    # #region agent log
                    _dbg(
                        'xml_read.py:extract_pad_sections',
                        'inferred silayer from unnamed adjacent clip',
                        {
                            'seq_index': seq_index,
                            'inferred_silayer': layer_idx,
                            'start': start_time,
                            'end': _clip_end_time(clip),
                            'prev_layer': prev_resolved.get('layer')
                            if prev_resolved else None,
                            'prev_color': prev_resolved.get('color')
                            if prev_resolved else None,
                            'clip_color': _clip_color_index(clip),
                        },
                        hypothesis='H_unnamed_adjacent_layer',
                    )
                    # #endregion
                elif not (clip_name or '').strip() and prev_resolved is None:
                    layer_idx = 0

            if layer_idx is None:
                prev_resolved = None
                continue

            end_time = _clip_end_time(clip)
            if end_time is None:
                end_time = start_time + 1e-6
            for sec_idx in _sections_overlapping_range(start_time, end_time, locators):
                pending[sec_idx].append(
                    (seq_index, layer_idx, start_time, end_time, clip_name)
                )
            track_layer_intervals[enum_index].append((start_time, end_time, layer_idx))

            prev_resolved = {
                'end': end_time,
                'layer': layer_idx,
                'color': _clip_color_index(clip),
            }

    for sec_idx in range(len(locators)):
        sec_start = locators[sec_idx]['time']
        sec_end = (
            locators[sec_idx + 1]['time'] if sec_idx + 1 < len(locators) else float('inf')
        )
        have_seq = {x[0] for x in pending[sec_idx]}
        for seq_index in range(16):
            if seq_index in have_seq:
                continue
            iv = _held_layer_from_intervals(
                track_layer_intervals[seq_index],
                sec_start,
                sec_end,
                tol,
            )
            if iv is None:
                continue
            st, en, ly = iv
            pending[sec_idx].append((seq_index, ly, st, en, ''))

    for sec_idx, overlapping in enumerate(pending):
        if not overlapping:
            continue
        sec_start = locators[sec_idx]['time']
        fresh = [x for x in overlapping if x[2] + tol >= sec_start]
        if not fresh:
            chosen = overlapping
        elif any(x[1] > 0 for x in fresh):
            chosen = [x for x in fresh if x[1] > 0]
        else:
            chosen = fresh

        o_set = {(x[0], x[1]) for x in overlapping}
        c_set = {(x[0], x[1]) for x in chosen}
        # #region agent log
        if o_set != c_set:
            _dbg(
                'xml_read.py:extract_pad_sections',
                'fresh-start / layer filter applied',
                {
                    'sec_idx': sec_idx,
                    'section': locators[sec_idx].get('name', ''),
                    'sec_start': sec_start,
                    'overlapping': sorted(o_set),
                    'chosen': sorted(c_set),
                },
                hypothesis='H_fresh_pad_filter',
            )
        # #endregion

        pc = result[sec_idx]['pad_conds']
        chosen_pairs = {(x[0], x[1]) for x in chosen}

        seen = set()
        for seq_index, layer_idx, start_time, end_time, clip_name in chosen:
            key = (seq_index, layer_idx)
            if key in seen:
                continue
            seen.add(key)
            pc[key] = 1
            logger.debug(
                f'  Song: Seq{seq_index+1} clip "{clip_name}" [{start_time:g},{end_time:g}) '
                f'→ PAD ON section {sec_idx} silayer={layer_idx}'
            )

        used_non_a_fresh_filter = bool(fresh) and any(x[1] > 0 for x in fresh)
        if used_non_a_fresh_filter:
            for seq_index, layer_idx, _, _, _ in fresh:
                if layer_idx != 0:
                    continue
                if (seq_index, 0) in chosen_pairs:
                    continue
                if bridge_beat is not None and sec_start + tol >= bridge_beat:
                    pc[(seq_index, 4)] = 2
                else:
                    pc[(seq_index, 0)] = 1

        by_seq = {}
        for seq_index, layer_idx, st, en, _nm in overlapping:
            if layer_idx != 0:
                continue
            if st - tol <= sec_start < en:
                prev = by_seq.get(seq_index)
                if prev is None or st > prev[0]:
                    by_seq[seq_index] = (st, en, layer_idx)

        narrow_pre_drop = (
            drop_anchor is not None
            and sec_start + tol < drop_anchor
            and bool(fresh)
            and (not any(x[1] > 0 for x in fresh))
            and len(fresh) == 1
            and fresh[0][1] == 0
            and fresh[0][0] >= 12
        )
        sec_name_low = (locators[sec_idx].get('name') or '').lower()

        for seq_index, (st, en, _ly) in by_seq.items():
            if (seq_index, 0) in chosen_pairs or (seq_index, 0) in pc or (seq_index, 4) in pc:
                continue
            is_fresh = st + tol >= sec_start
            if is_fresh:
                continue
            post_drop = (
                drop_anchor is not None
                and bridge_beat is not None
                and sec_start + tol >= bridge_beat
                and st + tol >= drop_anchor
                and 'outro' not in sec_name_low
            )
            if narrow_pre_drop and seq_index == 0:
                continue
            if post_drop:
                pc[(seq_index, 4)] = 2
            else:
                pc[(seq_index, 0)] = 1

    return result


def _seq_index_from_track(track, enum_index, midi_track_info=None):
    """
    Resolve seq index for a track. Uses human indexing: track name 'Seq15' → seq 15 human → index 14.
    Returns 0-based seq index (0–15).
    """
    if midi_track_info and enum_index < len(midi_track_info):
        _, track_name, _ = midi_track_info[enum_index]
        if track_name:
            m = re.match(r'[Ss]eq\s*(\d+)', track_name.strip())
            if m:
                n = int(m.group(1))
                if 1 <= n <= 16:
                    return n - 1
    return enum_index


def extract_seq_sections(midi_tracks, locators, midi_track_info=None, tolerance_beats=0.1,
                         tracks=None):
    """
    For each locator section, produce SEQ events from the Pads track arrangement clips.

    Convention:
    - Pads track clip with MIDI note key K starting in section S
      → SEQ ON event: seq_index = K - 36, silayer = 0, cond = 1.
    - Pads track clip named "Keep N, M, P" (with or without notes) starting in section S
      → SEQ Keep events: seq_index = N-1 for each N, silayer = 0, cond = 2.
      (If a clip has a Keep name, the Keep rule applies; note keys in that clip are ignored.)

    seq_conds format: {(seq_idx, silayer): cond}
    """
    empty = [{'seq_conds': {}} for _ in locators]
    if not locators or tracks is None:
        return empty

    # Find the Pads track: first MidiTrack named 'Pads'.
    # Note: UserName/EffectiveName are nested inside <Name>, so we need iter() not find_element_by_tag().
    pads_track = None
    for t in tracks:
        if t.tag != 'MidiTrack':
            continue
        name = ''
        for tag_name in ('UserName', 'EffectiveName'):
            for e in t.iter(tag_name):
                val = e.attrib.get('Value', '')
                if val:
                    name = val.strip()
                    break
            if name:
                break
        if name.lower() == 'pads':
            pads_track = t
            break
    if pads_track is None:
        logger.warning('Song mode: could not find Pads track; seq events will be empty')
        return empty

    result = [{'seq_conds': {}} for _ in locators]

    def _apply_pads_clip_seq_to_sections(clip, clip_name, sec_indices):
        """Write Keep / MIDI key seq_conds for each section index in sec_indices."""
        if not sec_indices:
            return
        keep_seqs = parse_keep_pads_from_name(clip_name)  # returns set of 0-based indices
        if keep_seqs:
            for sec_idx in sec_indices:
                for seq_idx in keep_seqs:
                    result[sec_idx]['seq_conds'][(seq_idx, 0)] = 2
                    logger.debug(f'  Song: Pads clip "{clip_name}" → SEQ Keep chan={256+seq_idx} sec={sec_idx}')
            return
        notes_elem = find_element_by_tag(clip, 'Notes')
        key_tracks_el = find_element_by_tag(notes_elem, 'KeyTracks') if notes_elem is not None else None
        if key_tracks_el is not None:
            for kt in key_tracks_el:
                midi_key_el = find_element_by_tag(kt, 'MidiKey')
                if midi_key_el is None or 'Value' not in midi_key_el.attrib:
                    continue
                try:
                    midi_key = int(float(midi_key_el.attrib['Value']))
                except (ValueError, TypeError):
                    continue
                seq_idx = midi_key - 36  # note 36 = seq_index 0 (Seq1), 37 = 1, …
                if 0 <= seq_idx <= 15:
                    for sec_idx in sec_indices:
                        result[sec_idx]['seq_conds'][(seq_idx, 0)] = 1
                        logger.debug(f'  Song: Pads clip key={midi_key} → SEQ ON chan={256+seq_idx} sec={sec_idx}')

    for clip in _get_arrangement_clips(pads_track):
        start_time = _clip_start_time(clip)
        if start_time is None:
            continue

        name_el = find_element_by_tag(clip, 'Name')
        clip_name = name_el.attrib.get('Value', '') if name_el is not None else ''

        end_time = _clip_end_time(clip)
        if end_time is None:
            end_time = start_time + 1e-6
        sec_indices = _sections_overlapping_range(start_time, end_time, locators)
        if not sec_indices:
            continue
        _apply_pads_clip_seq_to_sections(clip, clip_name, sec_indices)

    # Pads clips that start exactly on locator B are assigned to the *next* section only
    # by half-open overlap [A,B). Long sections with no overlapping Pads clip (e.g. "Bass Full"
    # with the next section's Keep starting at B) would then have an empty seq block and seqs
    # stay silent until the next section — wrong on hardware. Copy that boundary-starting clip's
    # seq into the *previous* section when that section is still seq-empty and long enough that
    # this isn't the common short "intro → first hit" case (where the next section already gets
    # the same clip from normal overlap once you enter it).
    min_prev_sec_len_for_boundary_seq = 64.0  # beats
    for sec_idx in range(len(locators) - 1):
        if result[sec_idx]['seq_conds']:
            continue
        try:
            sec_start = float(locators[sec_idx]['time'])
            boundary = float(locators[sec_idx + 1]['time'])
        except (TypeError, ValueError):
            continue
        if boundary - sec_start < min_prev_sec_len_for_boundary_seq - 1e-9:
            continue
        for clip in _get_arrangement_clips(pads_track):
            st = _clip_start_time(clip)
            if st is None:
                continue
            if abs(float(st) - boundary) > tolerance_beats:
                continue
            name_el = find_element_by_tag(clip, 'Name')
            clip_name = name_el.attrib.get('Value', '') if name_el is not None else ''
            # #region agent log
            _dbg(
                'xml_read.py:extract_seq_sections boundary_seq',
                'Applying Pads clip at locator boundary to previous section seq_conds',
                {
                    'prev_sec_idx': sec_idx,
                    'prev_sec_name': locators[sec_idx].get('name'),
                    'boundary_beat': boundary,
                    'clip_name': clip_name,
                },
                hypothesis='H_seq_boundary',
            )
            # #endregion
            _apply_pads_clip_seq_to_sections(clip, clip_name, [sec_idx])

    return result


def build_song_sections(root, tracks, pad_list, midi_tracks, midi_track_info=None, tolerance_beats=0.1):
    """
    High-level helper to build song-section data structure from locators
    and arrangement clips.
    """
    locators = extract_locators(root)
    if not locators:
        return []

    pad_sections = extract_pad_sections(tracks, pad_list, locators, tolerance_beats=tolerance_beats,
                                        midi_tracks=midi_tracks, midi_track_info=midi_track_info)
    seq_sections = extract_seq_sections(midi_tracks, locators, midi_track_info=midi_track_info,
                                        tolerance_beats=tolerance_beats, tracks=tracks)

    sections = []
    for idx, loc in enumerate(locators):
        sec = {
            'name': loc['name'],
            'repeats': loc['repeats'],
            'pad_conds': pad_sections[idx].get('pad_conds', {}),
            'seq_conds': seq_sections[idx].get('seq_conds', {}),
        }
        sections.append(sec)

    logger.info(f'Built {len(sections)} song sections from locators and arrangement clips')
    return sections


def indent_xml(elem, level=0):
    """
    Indent XML elements for pretty printing.
    Compatible with Python 3.7+
    """
    i = "\n" + level * "    "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "    "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            indent_xml(child, level+1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def save_xml(root, preset_filepath):
    tree = ET.ElementTree(root)
    # Use custom indent function for Python 3.7 compatibility
    indent_xml(root)
    tree.write(preset_filepath, encoding="utf-8", xml_declaration=True)
    logger.info(f"Saved preset to: {preset_filepath}")


def main(args):
    logger.info('=== Ableton to Blackbox Converter v0.3 (Drum Rack Edition) ===')
    logger.info('Reading Ableton project...')
    # #region agent log
    _dbg('xml_read.py:main', 'conversion start', data={
        'input': args.Input,
        'output': args.Output,
        'song_mode': bool(getattr(args, 'song_mode', False)),
        'unquantised': bool(getattr(args, 'unquantised', False)),
        'utility_master_gain': bool(getattr(args, 'utility_master_gain', False)),
    }, hypothesis='startup')
    # #endregion
    
    try:
        root = read_project(args.Input)
        als_major_version = None
        try:
            als_major_version = int(str(root.attrib.get('MajorVersion', '') or '0'))
        except (ValueError, TypeError):
            als_major_version = None

        linear_master_gain = None
        want_utility_bake = getattr(args, 'utility_master_gain', False)
        master_utility_gain_db = extract_master_utility_gain_db(
            root, log_on_match=want_utility_bake
        )
        if want_utility_bake:
            if master_utility_gain_db is not None:
                linear_master_gain = 10.0 ** (master_utility_gain_db / 20.0)
            else:
                logger.info(
                    '--utility-master-gain enabled but no usable master Utility Gain '
                    '(no Utility found, unreadable Gain, bypassed chain, etc.) '
                    '- exporting drum samples unchanged (same as Freeze / source files)'
                )
        elif (
            master_utility_gain_db is not None
            and abs(master_utility_gain_db) > 1e-6
        ):
            logger.warning(
                'Master chain Utility Gain is about %.2f dB on disk in this .als, but '
                'exported drum-rack WAVs are still plain **copies** of your sample/Freeze '
                'files — **not** run through Ableton\'s master channel. Levels match the '
                'source file unless you convert with **--utility-master-gain**. '
                '(Blackbox pad gain XML is untouched either way unless you tune it.)'
                % master_utility_gain_db
            )

        tracks, tempo = track_tempo_extractor(root)
        logger.info(f'The project tempo is: {tempo} bpm')
        
        logger.info('Extracting track data...')
        pad_list, midi_tracks, midi_track_info = track_iterator(tracks)
        
        if not pad_list:
            logger.error('Failed to extract drum rack. Aborting.')
            sys.exit(1)
        
        logger.info('________________\n')
        logger.info('Building Blackbox preset...')
        
        # Create base document structure (firmware 3.1.2 format)
        bb_root = ET.Element('document')
        # No attributes on document element for firmware 3.x
        session = ET.SubElement(bb_root, 'session')
        session.attrib = {'version': '2'}
        
        # Create pads from drum rack
        project_label = _project_label_from_input_path(args.Input)
        if project_label:
            logger.info(f'Using project label for sample names: [{project_label}]')
        session, assets = make_drum_rack_pads(session, pad_list, tempo, project_label=project_label)
        
        # Create sequences from MIDI tracks
        session = make_drum_rack_sequences(
            session, midi_tracks, pad_list, midi_track_info, unquantised=args.unquantised,
            als_major_version=als_major_version,
        )
        
        # Build song sections (optional song mode)
        song_sections = []
        if getattr(args, 'song_mode', False):
            logger.info('Song mode enabled – extracting locators and arrangement data')
            song_sections = build_song_sections(root, tracks, pad_list, midi_tracks, midi_track_info=midi_track_info, tolerance_beats=0.1)
            if not song_sections:
                logger.warning('Song mode requested but no valid locators found; falling back to default empty sections')
        
        # Add song, FX, and master sections
        if song_sections:
            bb_root = make_song_from_sections(bb_root, song_sections, pad_list, midi_tracks)
            bb_root = make_fx(bb_root)
            bb_root = make_master(bb_root, tempo, songmode_enabled=True, section_count=len(song_sections))
        else:
            bb_root = make_song(bb_root)
            bb_root = make_fx(bb_root)
            bb_root = make_master(bb_root, tempo, songmode_enabled=False, section_count=1)
        
        # Create output directory
        try:
            os.makedirs(args.Output, exist_ok=True)
        except Exception as e:
            logger.warning(f'Could not create output directory: {e}')
        
        # Handle assets (sample files)
        if assets:
            logger.info(f'Processing {len(assets)} sample files...')
            
            # Copy samples if not using -m flag
            if not args.Manual:
                for asset in assets:
                    # Assets are (source_path, dest_filename) tuples; handle plain strings too
                    if isinstance(asset, tuple):
                        asset_path, dest_name = asset
                    else:
                        asset_path = asset
                        dest_name = os.path.basename(asset_path)
                    if asset_path and os.path.exists(asset_path):
                        dest_path = os.path.join(args.Output, dest_name)
                        try:
                            if linear_master_gain is not None:
                                copy_wav_with_master_gain(asset_path, dest_path, linear_master_gain)
                                logger.info(f'  Wrote (Utility gain bake): {dest_name}')
                            else:
                                shutil.copy2(asset_path, dest_path)
                                logger.info(f'  Copied: {dest_name} (from {os.path.basename(asset_path)})')
                        except Exception as e:
                            logger.warning(f'  Could not export {dest_name}: {e}')
        
        preset_filepath = os.path.join(args.Output, 'preset.xml')
        
        logger.info('Saving preset XML...')
        save_xml(bb_root, preset_filepath)
        
        logger.info('=== Conversion complete! ===')
        logger.info(f'Output saved to: {args.Output}')
        
    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    prog = "Ableton Drum Rack to Blackbox Converter"
    description = """Version 0.3 - Drum Rack Workflow
    
    Converts Ableton Live Drum Rack projects to 1010music Blackbox presets
    
    REQUIREMENTS:
    - Track 1: Drum Rack with up to 16 Simplers
    - Tracks 2-17: MIDI tracks for sequences (optional)
    
    FEATURES:
    - 16-pad drum rack mapping (1:1)
    - Choke group extraction
    - Warped stem detection and loop mode
    - Multi-layer sequences (A/B/C/D sub-layers)
    - Compatible with Ableton Live 10, 11, and 12
    """
    epilog = "For more info, see DRUM_RACK_WORKFLOW.md"
    parser = argparse.ArgumentParser(prog=prog, 
                                     description=description, 
                                     epilog=epilog,
                                     formatter_class=RawTextHelpFormatter)
    parser.add_argument("-i", "--Input", help="Ableton live project input (.als file)", type=str, required=True)
    parser.add_argument("-o", "--Output", help="BB project name and location (directory path)", type=str, required=True)
    parser.add_argument("-V", "--Version", help="3-digit version number (e.g., 001, 002) - appends to output path", type=str, default=None)
    parser.add_argument("-m", "--Manual", help="Manual sample extraction (don't copy samples)", action='store_true')
    parser.add_argument("-u", "--unquantised", help="Unquantised MIDI timing (precise timing, not grid-locked)", action='store_true')
    parser.add_argument("-s", "--song-mode", help="Enable song mode: map arrangement locators and clips to Blackbox song sections", action='store_true')
    parser.add_argument(
        "--utility-master-gain",
        dest="utility_master_gain",
        help=(
            "Experimental: multiply every exported Drum Rack WAV by the master's first Utility Gain "
            "(Ableton StereoGain). Preset pad gain values are untouched. Prefer "
            "optional packages soundfile+numpy or ffmpeg (see README)."
        ),
        action="store_true",
    )
    parser.add_argument("-v", "--verbose", help="Verbose output", action='store_true')
    args = parser.parse_args()
    
    # Get git commit hash for versioning (if in a git repo)
    git_version = None
    try:
        # Get the script's directory to find git repo
        script_dir = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], 
                              cwd=script_dir, 
                              capture_output=True, 
                              text=True, 
                              timeout=5)
        if result.returncode == 0:
            git_version = result.stdout.strip()
            logger.info(f'Git version: {git_version}')
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug(f'Could not get git version: {e}')
    
    # Validate and append version to output path if provided
    if args.Version:
        # Allow "exception" as a special version name, otherwise validate as 3-digit number
        if args.Version != "exception" and (not args.Version.isdigit() or len(args.Version) != 3):
            logger.error(f'Version must be a 3-digit number (e.g., 001, 002) or "exception", got: {args.Version}')
            sys.exit(1)
        args.Output = os.path.join(args.Output, f'v{args.Version}')
    elif git_version:
        # Use git commit hash so output folder matches the code version
        args.Output = os.path.join(args.Output, f'v{git_version}')
        logger.info(f'Using git commit hash: v{git_version}')
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    main(args)

