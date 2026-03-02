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
                if receiving_note and 'Value' in receiving_note.attrib:
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


def _collect_slice_points(sample_part):
    """
    Locate slice definitions inside a Simpler sample part.
    Returns tuple: (source_tag, seconds_list, samples_list)
    """
    slice_tags = [
        'ManualSlicePoints',
        'SlicePoints',
        'RegionSlicePoints',
        'BeatSlicePoints',
        'InitialSlicePointsFromOnsets'
    ]
    
    def _parse_container(container):
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
            # InitialSlicePointsFromOnsets are auto-computed by Ableton for ALL samples
            # (including warped/clip-mode ones). Only treat them as actual slices when:
            #  • the sample is NOT warped, AND
            #  • AreSlicesFromOnsetsEditable is true (user is in Slice/Transient mode)
            if tag == 'InitialSlicePointsFromOnsets':
                if is_warped:
                    continue
                editable_el = find_element_by_tag(sample_part, 'AreSlicesFromOnsetsEditable')
                if editable_el is None or editable_el.attrib.get('Value', 'false').lower() != 'true':
                    continue
            seconds, samples = _parse_container(container)
            if seconds or samples:
                return tag, seconds, samples
    
    # Fallback: use user-defined onsets if nothing else is set and sample is not warped
    if not is_warped and warp_props is not None:
        user_onsets = warp_props.find('.//UserOnsets')
        if user_onsets is not None and len(user_onsets):
            seconds = []
            for onset in list(user_onsets):
                attrib = onset.attrib
                if 'Time' in attrib:
                    try:
                        seconds.append(float(attrib['Time']))
                    except ValueError:
                        continue
            if seconds:
                return 'UserOnsets', seconds, []
    return None, [], []


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
        'transpose_cents': extract_transpose_cents(device)
    }
    
    if device is None or sample_part is None:
        return info
    
    try:
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
        
        # Record slicing parameters stored on the sample part
        style_elem = find_element_by_tag(sample_part, 'SlicingStyle')
        if style_elem is not None and 'Value' in style_elem.attrib:
            info['slicing_style'] = style_elem.attrib['Value']
        
        beat_grid_elem = find_element_by_tag(sample_part, 'SlicingBeatGrid')
        if beat_grid_elem is not None and 'Value' in beat_grid_elem.attrib:
            info['beat_grid'] = beat_grid_elem.attrib['Value']
        
        source_tag, seconds, samples = _collect_slice_points(sample_part)
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
            # Always include slice at index 0 for completeness
            info['slice_positions_samples'].append(0)
            cleaned = sorted(set(max(0, int(pos)) for pos in info['slice_positions_samples']))
            info['slice_positions_samples'] = cleaned
        
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
    
    # Collect MIDI tracks following the drum rack track.
    # Skips GroupTrack containers (seq tracks may be inside a group track), and stops
    # once 16 MIDI tracks have been found.
    midi_tracks = []
    midi_track_info = []  # Store (track, name, index) tuples
    i = 1
    while i < len(tracks) and len(midi_tracks) < 16:
        track = tracks[i]
        i += 1
        if track.tag != 'MidiTrack':
            logger.info(f'  Skipping non-MIDI track at index {i} (tag={track.tag})')
            continue
        # Extract track name
        name_elem = find_element_by_tag(track, 'Name')
        track_name = name_elem.attrib.get('Value', '') if name_elem is not None else ''
        midi_tracks.append(track)
        midi_track_info.append((track, track_name, len(midi_tracks)-1))
        logger.info(f'  Found MIDI track at index {i} (name="{track_name}") for sequence (midi_tracks index {len(midi_tracks)-1})')
    
    logger.info(f'Extracted {len(pad_list)} drum pads and {len(midi_tracks)} MIDI tracks')
    return pad_list, midi_tracks, midi_track_info


# Helper functions for generating Blackbox XML

def row_column(pad):
    """
    Blackbox 4x4 grid for 16 pads/sequences. Column-major order:
    Pad 0->(0,0), 1->(1,0), 2->(2,0), 3->(3,0), 4->(0,1), ..., 15->(3,3).
    """
    p = int(pad)
    if 0 <= p <= 15:
        row = p % 4
        column = p // 4
        return (row, column)
    return (0, 0)

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

def make_drum_rack_pads(session, pad_list, tempo):
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
                    if warp_info.get('is_warped', False):
                        cellmode = '1'  # Clip mode for warped samples
                        loopmode = '1'  # Loop enabled
                        logger.info(f'    → Warped sample: {beat_count} beats ({beat_count/4} bars), clip mode enabled')
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
                        if 0 not in slice_positions:
                            slice_positions.append(0)
                        try:
                            samlen_int = int(float(samlen))
                            slice_positions = [max(0, min(int(pos), samlen_int)) for pos in slice_positions]
                        except (TypeError, ValueError):
                            slice_positions = [max(0, int(pos)) for pos in slice_positions]
                        slice_positions = sorted(set(slice_positions))
                
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
        for tick_pos in tick_positions:
            # Check alignment with small tolerance for rounding errors (within 1 tick)
            # This handles cases where floating point conversion causes slight offsets
            remainder = tick_pos % grid_ticks
            if remainder == 0 or remainder == (grid_ticks - 1) or remainder == 1:
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
        for tick_pos in tick_positions:
            # Check alignment with small tolerance for rounding errors (within 1 tick)
            remainder = tick_pos % grid_ticks
            if remainder == 0 or remainder == (grid_ticks - 1) or remainder == 1:
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
        for tick_pos in tick_positions:
            if tick_pos % grid_ticks == 0:
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
            
            # Check if aligns to any triplet grid (with tolerance for rounding)
            for grid_ticks, step_len, name in triplet_grids:
                remainder = tick_pos % grid_ticks
                if remainder == 0 or remainder == (grid_ticks - 1) or remainder == 1:
                    aligns_to_triplet = True
                    break
            
            # Check if aligns to any straight grid (with tolerance for rounding)
            for grid_ticks, step_len, name in straight_grids:
                remainder = tick_pos % grid_ticks
                if remainder == 0 or remainder == (grid_ticks - 1) or remainder == 1:
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
    # Only do this if triplets are well-aligned (>95%) and NOT mixed with straight notes
    # BUT: Prefer straight notes over triplets when both align equally well (default to straight)
    # Mixed patterns must always be unquantised
    if best_triplet_match and best_triplet_score >= 0.95 and not mixed_pattern:
        # Only use triplet step_len if it's BETTER than the straight match
        # If scores are equal, prefer straight (default behavior)
        # Check if best_match is a straight note grid (not triplet)
        best_is_triplet = best_match and best_match[1] in [11, 9]  # Triplet step_len values
        
        if best_triplet_score > best_straight_score:
            # Triplets align better than straight - use triplet step_len
            best_match = best_triplet_match
            best_score = best_triplet_score
            logger.debug(f'  Grid analysis: Triplets detected ({best_triplet_score*100:.0f}% aligned), using triplet step_len')
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
    # 1. Less than 95% aligned to any grid (off-grid timing) - use 95% to allow for rounding errors
    # 2. Mixed triplets + straight notes (CRITICAL: always unquantised when mixed)
    is_unquantised = best_score < 0.95 or mixed_pattern
    
    if best_match:
        grid_ticks, step_len, grid_name = best_match
        if is_unquantised:
            logger.info(f'  Grid analysis: {best_score*100:.0f}% aligned to {grid_name}, unquantised detected (mixed={mixed_pattern})')
            # Debug: Log why it's unquantised with more detail
            if best_score < 0.95:
                logger.debug(f'    Reason: Alignment score {best_score*100:.1f}% < 95% threshold')
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
    loop_start = find_element_by_tag(midi_clip, 'LoopStart')
    loop_end = find_element_by_tag(midi_clip, 'LoopEnd')
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


def make_drum_rack_sequences(session, midi_tracks, pad_list, midi_track_info=None, unquantised=False):
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
        
        # Determine target pad from routing (not from track name or index)
        # For Keys mode: routing to specific pad via branch_id determines target_pad
        # For Pads mode: sequence location is ALWAYS determined by track index (CRITICAL: must match track_idx)
        target_pad = track_idx  # Default: use track index for sequence location
        
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
                # Branch Id not found, fall back to track position
                logger.warning(f'  Keys mode: Branch Id {branch_id} not found in drum rack')
                logger.warning(f'  Falling back to track position (Pad {track_idx})')
                target_pad = track_idx
        
        # For row/column calculation:
        # CRITICAL: Sequence location (row/column) ALWAYS matches track index for both Pads and Keys mode
        # The seqpadmapdest parameter determines which pad the sequence plays, not where it's located
        # This ensures track 0 → location pad 0, track 1 → location pad 1, etc.
        sequence_location_pad = track_idx
        row, column = row_column(sequence_location_pad)
        
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
            
            # --- Arrangement view clips (named A/B/C/D → always override matching layer) ---
            # Empty arrangement clips are intentional (e.g. section length markers); always override.
            clip_timeable = find_element_by_tag(main_sequencer, 'ClipTimeable')
            if clip_timeable:
                arr_automation = find_element_by_tag(clip_timeable, 'ArrangerAutomation')
                if arr_automation:
                    events = find_element_by_tag(arr_automation, 'Events')
                    if events:
                        for arr_clip in events:
                            if arr_clip.tag != 'MidiClip':
                                continue
                            name_el = find_element_by_tag(arr_clip, 'Name')
                            clip_name = (name_el.attrib.get('Value', '') if name_el is not None else '').strip().lower()
                            layer_idx = _layer_name_to_idx.get(clip_name)
                            if layer_idx is not None:
                                sub_layers[layer_idx] = arr_clip
                                logger.info(f'  Track {track_idx}, Arrangement clip "{clip_name.upper()}": Overrides sub-layer {chr(65+layer_idx)}')
            
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
        
        # Create sequence cells for each sublayer (firmware 2.3+ format)
        # Create all 4 sublayers (A/B/C/D) as separate cell elements
        total_notes_all_layers = 0
        first_layer_with_notes = -1
        
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
            
            # Extract all notes for this sublayer first (to check if we have events)
            # CRITICAL: Must create a new list for each sublayer to avoid reference issues
            sublayer_events = []
            
            # CRITICAL: Extract LoopStart offset BEFORE processing notes
            # Note times in Ableton are relative to the clip start (LoopStart)
            # We need to subtract this offset to get times relative to sequence start
            loop_start_offset = 0.0
            if midi_clip:
                loop_start_elem = find_element_by_tag(midi_clip, 'LoopStart')
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
            
            # Extract clip length from MIDI clip to calculate step_count
            # Try multiple methods: LoopEnd, CurrentEnd, or calculate from note positions
            clip_length_beats = 1.0  # Default to 1 beat
            if midi_clip:
                # Method 1: Try LoopStart/LoopEnd
                loop_start_elem = find_element_by_tag(midi_clip, 'LoopStart')
                loop_end_elem = find_element_by_tag(midi_clip, 'LoopEnd')
                
                if loop_start_elem is not None and 'Value' in loop_start_elem.attrib and \
                   loop_end_elem is not None and 'Value' in loop_end_elem.attrib:
                    try:
                        loop_start = float(loop_start_elem.attrib['Value'])
                        loop_end = float(loop_end_elem.attrib['Value'])
                        clip_length_beats = loop_end - loop_start
                        if clip_length_beats <= 0:
                            clip_length_beats = loop_end  # Use LoopEnd directly if difference is 0
                        logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Clip length from LoopStart/LoopEnd = {clip_length_beats} beats')
                    except (ValueError, TypeError) as e:
                        logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Error extracting LoopStart/LoopEnd: {e}')
                
                # Method 2: If LoopEnd not found, try CurrentEnd
                if clip_length_beats <= 1.0:
                    current_end_elem = find_element_by_tag(midi_clip, 'CurrentEnd')
                    if current_end_elem is not None and 'Value' in current_end_elem.attrib:
                        try:
                            clip_length_beats = float(current_end_elem.attrib['Value'])
                            logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Clip length from CurrentEnd = {clip_length_beats} beats')
                        except (ValueError, TypeError):
                            pass
                
                # Method 3: Calculate from note positions (use latest note end time)
                if clip_length_beats <= 1.0 and len(sublayer_events) > 0:
                    max_time = 0.0
                    for event in sublayer_events:
                        # Use original time_val and dur_val for accurate calculation
                        note_start_beats = event.get('time_val', 0)
                        note_duration_beats = event.get('dur_val', 0)
                        note_end_beats = note_start_beats + note_duration_beats
                        max_time = max(max_time, note_end_beats)
                    
                    if max_time > 0:
                        # Round up to nearest bar (4 beats) for sequence length
                        clip_length_beats = int((max_time + 3) // 4) * 4
                        if clip_length_beats < 4:
                            clip_length_beats = max(4.0, max_time)  # At least 1 bar
                        logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Clip length calculated from notes = {clip_length_beats} beats (max note end: {max_time:.3f})')
                
                # If still no length found, use default
                if clip_length_beats <= 1.0:
                    logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Using default clip length = 1.0 beat')
                    clip_length_beats = 1.0
            
            # Extend note durations to fill the entire clip (arrangement view: notes should span to clip end)
            if midi_clip and clip_length_beats > 0 and sublayer_events:
                for event in sublayer_events:
                    t = event.get('time_val', 0)
                    fill_dur = max(0.0, clip_length_beats - t)
                    if fill_dur > 0:
                        event['dur_val'] = fill_dur
                logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: Extended note durations to fill clip ({clip_length_beats:.2f} beats)')
            
            # Track first layer with notes for activeseqlayer
            if sublayer_events and first_layer_with_notes == -1:
                first_layer_with_notes = sublayer_idx
            
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
            # Step length values:
            # 14 = 1/32, 12 = 1/32T, 10 = 1/16, 11 = 1/16T, 8 = 1/8, 9 = 1/8T, 6 = 1/4, 4 = 1/2, 3 = 1 Bar, 2 = 2 Bars, 1 = 4 Bars, 0 = 8 Bars
            # 1 bar = 4 beats
            # If no clip and no notes, use minimum length
            if not midi_clip and len(sublayer_events) == 0:
                step_len = 10  # 1/16 notes
                step_count = 1  # Minimum length for empty sublayer
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
                    step_count = int(clip_length_beats * steps_per_beat)
                else:
                    # Unquantised or no detection: use default 1/16
                    step_len = 10
                    step_count = int(clip_length_beats * 4)
                
                # If step_count exceeds 256, use coarser resolution
                if step_count > 256:
                    # Try 1/8 notes: 1 beat = 2 steps, 1 bar = 8 steps
                    step_count = int(clip_length_beats * 2)
                    step_len = 8
                    
                if step_count > 256:
                    # Try 1/4 notes: 1 beat = 1 step, 1 bar = 4 steps
                    step_count = int(clip_length_beats * 1)
                    step_len = 6
                    
                if step_count > 256:
                    # Try 1/2 notes: 2 beats = 1 step, 1 bar = 2 steps
                    step_count = int(clip_length_beats * 0.5)
                    step_len = 4
                    
                if step_count > 256:
                    # Try 1 Bar: 4 beats = 1 step
                    step_count = int(clip_length_beats * 0.25)
                    step_len = 3
                    
                if step_count > 256:
                    # Try 2 Bars: 8 beats = 1 step
                    step_count = int(clip_length_beats * 0.125)
                    step_len = 2
                    
                if step_count > 256:
                    # Try 4 Bars: 16 beats = 1 step
                    step_count = int(clip_length_beats * 0.0625)
                    step_len = 1
                    
                if step_count > 256:
                    # Max: 8 Bars: 32 beats = 1 step
                    step_count = max(1, int(clip_length_beats * 0.03125))
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
            
            # CRITICAL: Tick rate depends ONLY on quantisation state, NOT on sequence mode
            # Quantised sequences (both Keys and Pads): Use 3840 ticks/beat
            # Unquantised sequences (both Keys and Pads): Use 960 ticks/beat
            # Quantisation detection is independent of Keys/Pads mode
            if is_unquantised:
                # Unquantised: use 960 ticks/beat for strtks, constant lentks=240, lencount=0
                # Both Keys and Pads modes use identical timing parameters (960 ticks/beat)
                # CRITICAL: For unquantised Keys mode, chan should be 256 (not 256+target_pad)
                logger.info(f'    Sub-layer {chr(65+sublayer_idx)}: {seq_mode} mode, unquantised → using 960 ticks/beat, setting lencount=0')
                for event in sublayer_events:
                    time_val = event.get('time_val', 0)
                    dur_val = event.get('dur_val', 0)
                    event['strtks'] = int(time_val * 960)  # 960 ticks/beat for unquantised (both Keys and Pads)
                    event['lentks'] = 240  # Constant 240 ticks for unquantised sequences (matches reference)
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
            else:
                # Quantised: use 3840 ticks/beat for both Keys/MIDI and Pads mode
                # CRITICAL: Step values must match the step_len resolution
                logger.debug(f'    Sub-layer {chr(65+sublayer_idx)}: {seq_mode} mode, quantised, detected step_len={detected_step_len}, recalculating step values with {steps_per_beat} steps/beat, using 3840 ticks/beat')
                for event in sublayer_events:
                    time_val = event.get('time_val', 0)
                    dur_val = event.get('dur_val', 0)
                    # Recalculate step based on detected step_len (e.g., 8 steps/beat for 1/32 notes)
                    event['step'] = int(time_val * steps_per_beat)
                    # Recalculate strtks with 3840 ticks/beat for quantised sequences (both Keys and Pads mode)
                    # CRITICAL: For quantised sequences, lentks should be constant 960 ticks (matches reference format)
                    # This ensures step mode is ON (quantised) on the device
                    event['strtks'] = int(time_val * 3840)  # 3840 ticks/beat for quantised
                    event['lentks'] = 960  # Constant 960 ticks for quantised sequences (matches reference)
                    # Recalculate lencount based on detected step_len (steps_per_beat)
                    event['lencount'] = max(1, int(dur_val * steps_per_beat))  # Step-based length for quantised
            
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
                seqstepmode_val = '0'  # MIDI mode
                seqpadmapdest_val = '0'
                midioutchan_val = str(mode_target)  # MIDI channel (0-15)
            
            params.attrib = {
                'notesteplen': str(step_len),  # Calculated based on clip length
                'notestepcount': str(step_count),  # Calculated from clip length
                'dutycyc': '1000',
                'quantsizeseq': '1',
                'dispmode': '1' if sublayer_idx == 0 else '0',  # Only first layer visible by default
                'seqpadmapdest': seqpadmapdest_val,
                'seqplayenable': '1' if sublayer_events else '0',  # Enable if has notes
                'activeseqlayer': str(first_layer_with_notes if first_layer_with_notes >= 0 else 0),
                'midioutchan': midioutchan_val,
                'seqstepmode': seqstepmode_val,
                'midiseqcellchan': '0'
            }
            
            # Add sequence element with events
            sequence = ET.SubElement(cell, 'sequence')
            # DEBUG: Log which track's notes are being written
            if sublayer_events and sublayer_idx == 0:
                first_strtks = sublayer_events[0].get('strtks', 'N/A')
                logger.info(f'  Track {track_idx}, Sub-layer {sublayer_idx}: Writing {len(sublayer_events)} events to cell at row={sequence_row}, col={sequence_column}, seqpadmapdest={seqpadmapdest_val}, first_strtks={first_strtks}')
            for event_data in sublayer_events:
                seqevent = ET.SubElement(sequence, 'seqevent')
                seqevent.attrib = {
                    'step': str(event_data['step']),
                    'chan': str(event_data['chan']),
                    'type': event_data['type'],
                    'strtks': str(event_data['strtks']),
                    'lencount': str(event_data['lencount']),
                    'lentks': str(event_data['lentks'])
                }
                # In pads mode (seqstepmode=1), pitch should be the pad number (0-15)
                # This tells Blackbox which pad to trigger: pitch=0 → pad 0, pitch=1 → pad 1, etc.
                seqevent.attrib['pitch'] = str(event_data['pitch'])
                if event_data['velocity'] != 100:
                    seqevent.attrib['velocity'] = str(event_data['velocity'])
            
            if sublayer_events:
                # Debug: Log first note's strtks to verify we have the right notes
                first_strtks = sublayer_events[0].get('strtks', 'N/A') if sublayer_events else 'N/A'
                logger.info(f'    Sub-layer {chr(65+sublayer_idx)}: {len(sublayer_events)} notes (first strtks={first_strtks})')
        
        if total_notes_all_layers > 0:
            logger.info(f'  Sequence at pad {sequence_location_pad}: Created 4 sub-layers ({total_notes_all_layers} total notes)')
        else:
            logger.debug(f'  Sequence at pad {sequence_location_pad}: No MIDI clips found')
    
    # Expected preset has 80 sequence cells: 64 + 16 empty at column 4 (row 0-3 x seqsublayer 0-3)
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
    
    logger.info(f'Sequence extraction complete (64 + 16 empty column-4 cells)')
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


def make_song_from_sections(root, sections, pad_list, midi_tracks):
    """
    Create Blackbox song sections (layer 2 cells) from parsed locator/arrangement data.

    sections: list of dicts:
      {
        'name': str,
        'repeats': int,
        'pad_conds': {pad_index: cond_int_0_1_2},
        'seq_conds': {(seq_index, layer_index): cond_int_0_1_2}
      }
    """
    session = root.find('session')
    if session is None:
        session = ET.SubElement(root, 'session')
        session.attrib = {'version': '2'}

    num_pads = len(pad_list)
    num_seqs = min(16, len(midi_tracks))

    for row_idx, sec in enumerate(sections):
        # SWAP: We extract "pad_conds" from drum track (which pad has notes) and "seq_conds" from
        # sequence tracks (which seq overlaps section). Blackbox section XML uses chan 0-15 for
        # PAD conditions and chan 256+ for SEQ conditions. We had them backwards: write
        # extracted seq_conds (which sequence plays) -> pad events (chan 0-15), and
        # extracted pad_conds (which pad from drum) -> seq events (chan 256+).
        extracted_pad_conds = dict(sec.get('pad_conds', {}) or {})   # from drum track
        extracted_seq_conds = dict(sec.get('seq_conds', {}) or {})   # from seq tracks
        # XML pad events (chan 0-15): use extracted SEQ data (which sequence is on)
        pad_conds_for_xml = {}
        for pad_idx in range(num_pads):
            cond = max(int(extracted_seq_conds.get((pad_idx, layer_idx), 0)) for layer_idx in range(4))
            pad_conds_for_xml[pad_idx] = cond
        # XML seq events (chan 256+, silayer 0-3): use extracted PAD data (which pad from drum)
        seq_conds_for_xml = {}
        for seq_index in range(num_seqs):
            for layer_idx in range(4):
                seq_conds_for_xml[(seq_index, layer_idx)] = int(extracted_pad_conds.get(seq_index, 0))

        cell = ET.SubElement(session, 'cell')
        cell.attrib = {
            'row': str(row_idx),
            'column': '0',
            'layer': '2',
            'seqsublayer': '0',
            'name': sec.get('name', ''),
            'type': 'section',
        }
        params = ET.SubElement(cell, 'params')
        repeats = max(1, int(sec.get('repeats', 1)))
        params.attrib = {'sectionrepeats': str(repeats)}

        sequence = ET.SubElement(cell, 'sequence')

        # Pads: chan 0–15, silayer 0 (from extracted seq_conds: which sequence plays)
        for pad_idx in range(num_pads):
            cond = int(pad_conds_for_xml.get(pad_idx, 0))
            ET.SubElement(sequence, 'seqevent', attrib={
                'step': '0',
                'chan': str(pad_idx),
                'type': 'sceneitem',
                'silayer': '0',
                'cond': str(cond),
            })

        # Sequences: chan 256–271, silayer 0–3 (from extracted pad_conds: which pad from drum)
        for seq_index in range(num_seqs):
            chan_val = 256 + seq_index
            for layer_idx in range(4):
                cond = int(seq_conds_for_xml.get((seq_index, layer_idx), 0))
                ET.SubElement(sequence, 'seqevent', attrib={
                    'step': '0',
                    'chan': str(chan_val),
                    'type': 'sceneitem',
                    'silayer': str(layer_idx),
                    'cond': str(cond),
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
        for seq_index in range(num_seqs):
            chan_val = 256 + seq_index
            for layer_idx in range(4):
                ET.SubElement(sequence, 'seqevent', attrib={
                    'step': '0', 'chan': str(chan_val), 'type': 'sceneitem', 'silayer': str(layer_idx), 'cond': '0',
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
        'sectloop': '1',
        'swing': '50',
        'keymode': '1',
        'keyroot': '3',
    }

    return root


def build_midi_to_pad_map(pad_list):
    """
    Build a mapping from MIDI note to Blackbox pad index using pad metadata.
    Falls back to the standard drum rack convention (36–51 → pads 0–15)
    when explicit midi_note values are not present.
    """
    midi_to_pad = {}
    for pad in pad_list:
        if pad.get('midi_note') is not None:
            midi_to_pad[pad['midi_note']] = pad['blackbox_pad']
    
    # Fallback: standard drum rack MIDI mapping
    if not midi_to_pad:
        logger.warning('No MIDI notes found in pad_list, using standard mapping (36-51 → 0-15)')
        for pad in pad_list:
            pad_num = pad.get('blackbox_pad')
            if pad_num is not None:
                midi_to_pad[36 + pad_num] = pad_num
    
    logger.debug(f'Created MIDI note to pad mapping: {midi_to_pad}')
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

    # No play count suffix recognised
    return name, 1


def parse_keep_pads_from_name(name):
    """
    Parse pad indexes (1–16) from a clip name like:
      \"Keep 4\" or \"Keep 7, 15, 11\" or \"Keep: 8, 16, 12\"

    Returns a set of 0-based pad indices.
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
    return result


def parse_seq_scene_action(clip_name):
    """
    Parse a sequence-arrangement clip name into an action:
      - \"A\", \"A Main Break\" → layer A
      - \"B\", \"B something\"  → layer B
      - \"C\" / \"D\" similarly
      - \"Keep\"               → keep current pattern (cond=2)

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
    return sections


def extract_pad_sections(tracks, pad_list, locators, tolerance_beats=0.1):
    """
    For each locator, determine pad conditions (on/off/keep) from the drum-rack
    arrangement MIDI clips on Track 1.
    """
    pad_sections = []
    if not tracks or not pad_list or not locators:
        return [{'pad_conds': {}} for _ in locators]

    drum_track = tracks[0]
    midi_to_pad = build_midi_to_pad_map(pad_list)

    device_chain = find_element_by_tag(drum_track, 'DeviceChain')
    if not device_chain:
        return [{'pad_conds': {}} for _ in locators]

    main_sequencer = find_element_by_tag(device_chain, 'MainSequencer')
    if not main_sequencer:
        return [{'pad_conds': {}} for _ in locators]

    clip_timeable = find_element_by_tag(main_sequencer, 'ClipTimeable')
    if not clip_timeable:
        return [{'pad_conds': {}} for _ in locators]

    arranger_automation = find_element_by_tag(clip_timeable, 'ArrangerAutomation')
    if not arranger_automation:
        return [{'pad_conds': {}} for _ in locators]

    events = find_element_by_tag(arranger_automation, 'Events')
    if not events:
        return [{'pad_conds': {}} for _ in locators]

    # Pre-build section structures
    for _ in locators:
        pad_sections.append({'pads_on': set(), 'pads_keep': set()})

    for clip in events:
        if clip.tag != 'MidiClip':
            continue

        current_start = find_element_by_tag(clip, 'CurrentStart')
        if current_start is None or 'Value' not in current_start.attrib:
            continue
        try:
            start_time = float(current_start.attrib['Value'])
        except (ValueError, TypeError):
            continue

        # Find matching locator (by time within tolerance)
        section_index = None
        for idx, loc in enumerate(locators):
            if abs(loc['time'] - start_time) <= tolerance_beats:
                section_index = idx
                break
        if section_index is None:
            continue

        name_el = find_element_by_tag(clip, 'Name')
        clip_name = name_el.attrib.get('Value', '') if name_el is not None else ''
        keep_pads = parse_keep_pads_from_name(clip_name)

        section_data = pad_sections[section_index]
        section_data['pads_keep'].update(keep_pads)

        # Extract MIDI notes (one or more) and map to pads
        notes_elem = find_element_by_tag(clip, 'Notes')
        if notes_elem is not None:
            key_tracks = find_element_by_tag(notes_elem, 'KeyTracks')
            if key_tracks is not None:
                for kt in key_tracks:
                    midi_key = find_element_by_tag(kt, 'MidiKey')
                    notes_list = find_element_by_tag(kt, 'Notes')
                    if midi_key is None or 'Value' not in midi_key.attrib or notes_list is None:
                        continue
                    try:
                        midi_note = int(float(midi_key.attrib['Value']))
                    except (ValueError, TypeError):
                        continue
                    pad_index = midi_to_pad.get(midi_note)
                    if pad_index is not None:
                        section_data['pads_on'].add(pad_index)

    # Convert sets into cond maps
    result = []
    num_pads = len(pad_list)
    for sec in pad_sections:
        conds = {}
        for pad_idx in range(num_pads):
            if pad_idx in sec['pads_keep']:
                conds[pad_idx] = 2  # Keep
            elif pad_idx in sec['pads_on']:
                conds[pad_idx] = 1  # On
            else:
                conds[pad_idx] = 0  # Off
        result.append({'pad_conds': conds})
    return result


def extract_seq_sections(midi_tracks, locators, tolerance_beats=0.1):
    """
    For each locator, determine per-sequence, per-layer conditions
    (on/off/keep) from the sequence tracks' arrangement clips.
    """
    if not midi_tracks or not locators:
        return [{'seq_conds': {}} for _ in locators]

    section_seq_data = []
    for _ in locators:
        section_seq_data.append({'seq_conds': {}})

    for seq_index, track in enumerate(midi_tracks[:16]):
        device_chain = find_element_by_tag(track, 'DeviceChain')
        if not device_chain:
            continue
        main_sequencer = find_element_by_tag(device_chain, 'MainSequencer')
        if not main_sequencer:
            continue
        clip_timeable = find_element_by_tag(main_sequencer, 'ClipTimeable')
        if not clip_timeable:
            continue
        arranger_automation = find_element_by_tag(clip_timeable, 'ArrangerAutomation')
        if not arranger_automation:
            continue
        events = find_element_by_tag(arranger_automation, 'Events')
        if not events:
            continue

        for clip in events:
            if clip.tag != 'MidiClip':
                continue

            current_start = find_element_by_tag(clip, 'CurrentStart')
            start_time = None
            if current_start is not None and 'Value' in current_start.attrib:
                try:
                    start_time = float(current_start.attrib['Value'])
                except (ValueError, TypeError):
                    start_time = None
            if start_time is None and 'Time' in clip.attrib:
                try:
                    start_time = float(clip.attrib['Time'])
                except (ValueError, TypeError):
                    start_time = None
            if start_time is None:
                continue

            # Clip end: CurrentEnd (absolute) or start + loop length
            end_time = None
            current_end = find_element_by_tag(clip, 'CurrentEnd')
            if current_end is not None and 'Value' in current_end.attrib:
                try:
                    end_time = float(current_end.attrib['Value'])
                except (ValueError, TypeError):
                    pass
            if end_time is None or end_time <= start_time:
                loop_start = find_element_by_tag(clip, 'LoopStart')
                loop_end = find_element_by_tag(clip, 'LoopEnd')
                if loop_start is not None and 'Value' in loop_start.attrib and loop_end is not None and 'Value' in loop_end.attrib:
                    try:
                        end_time = start_time + (float(loop_end.attrib['Value']) - float(loop_start.attrib['Value']))
                    except (ValueError, TypeError):
                        pass
            if end_time is None or end_time <= start_time:
                end_time = start_time + 4.0  # fallback 1 bar

            name_el = find_element_by_tag(clip, 'Name')
            clip_name = name_el.attrib.get('Value', '') if name_el is not None else ''
            action = parse_seq_scene_action(clip_name)
            # Layer: A=0, B=1, C=2, D=3; unnamed or other → 0 (e.g. Seq1 looped across borders)
            layer_idx = 0
            if action is not None:
                kind, value = action
                if kind == 'layer':
                    layer_idx = value
                elif kind == 'keep':
                    # KEEP: set for every section this clip overlaps
                    for idx in range(len(locators)):
                        sec_start = locators[idx]['time']
                        sec_end = locators[idx + 1]['time'] if idx + 1 < len(locators) else (end_time + 1.0)
                        if start_time < sec_end and end_time > sec_start:
                            sec_map = section_seq_data[idx]['seq_conds']
                            for li in range(4):
                                sec_map[(seq_index, li)] = 2
                    continue

            # Clip overlaps section → seq ON for that section (e.g. Seq1 looped across locator borders)
            for idx in range(len(locators)):
                sec_start = locators[idx]['time']
                sec_end = locators[idx + 1]['time'] if idx + 1 < len(locators) else (end_time + 1.0)
                if start_time < sec_end and end_time > sec_start:
                    section_seq_data[idx]['seq_conds'][(seq_index, layer_idx)] = 1
                    logger.debug(f'  Seq {seq_index} layer {layer_idx} overlaps section {idx} ({sec_start:.1f}-{sec_end:.1f}) → ON')

    return section_seq_data


def build_song_sections(root, tracks, pad_list, midi_tracks, tolerance_beats=0.1):
    """
    High-level helper to build song-section data structure from locators
    and arrangement clips.
    """
    locators = extract_locators(root)
    if not locators:
        return []

    pad_sections = extract_pad_sections(tracks, pad_list, locators, tolerance_beats=tolerance_beats)
    seq_sections = extract_seq_sections(midi_tracks, locators, tolerance_beats=tolerance_beats)

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


def _section_content_from_root(root):
    """Extract list of section dicts: name, repeats, pad_conds {pad_idx: cond}, seq_conds {(seq_idx, silayer): cond}."""
    session = root.find('session')
    if session is None:
        return []
    out = []
    for cell in session.findall('cell'):
        if cell.get('layer') != '2' or cell.get('type') != 'section':
            continue
        name = cell.get('name', '')
        params = cell.find('params')
        repeats = int(params.get('sectionrepeats', 1)) if params is not None else 1
        seq_el = cell.find('sequence')
        pad_conds = {}
        seq_conds = {}
        if seq_el is not None:
            for ev in seq_el.findall('seqevent'):
                if ev.get('type') != 'sceneitem':
                    continue
                chan = ev.get('chan')
                silayer = ev.get('silayer', '0')
                cond = int(ev.get('cond', 0))
                if chan is None:
                    continue
                try:
                    c = int(chan)
                except ValueError:
                    continue
                if 0 <= c <= 15:
                    pad_conds[c] = cond
                elif 256 <= c <= 271:
                    seq_conds[(c - 256, int(silayer) if silayer else 0)] = cond
        out.append({'name': name, 'repeats': repeats, 'pad_conds': pad_conds, 'seq_conds': seq_conds})
    return out


def compare_section_content(expected_preset_path, actual_preset_path, max_sections=8):
    """
    Compare song section content (name, repeats, pad/seq conds) between expected and actual preset XML.
    Logs differences and returns number of mismatches.
    """
    try:
        with open(expected_preset_path, 'r') as f:
            exp_root = ET.fromstring(f.read().rstrip('\x00'))
    except Exception as e:
        logger.warning(f"Cannot load expected preset for comparison: {e}")
        return -1
    try:
        with open(actual_preset_path, 'r') as f:
            act_root = ET.fromstring(f.read())
    except Exception as e:
        logger.warning(f"Cannot load actual preset for comparison: {e}")
        return -1

    exp_sec = _section_content_from_root(exp_root)
    act_sec = _section_content_from_root(act_root)
    n = min(max_sections, len(exp_sec), len(act_sec))
    mismatches = 0
    logger.info('=== Song section content comparison (expected vs actual) ===')
    for i in range(n):
        e = exp_sec[i]
        a = act_sec[i]
        name_ok = e['name'] == a['name']
        rep_ok = e['repeats'] == a['repeats']
        pad_on_exp = {p for p, c in e['pad_conds'].items() if c == 1}
        pad_on_act = {p for p, c in a['pad_conds'].items() if c == 1}
        seq_on_exp = {k for k, c in e['seq_conds'].items() if c == 1}
        seq_on_act = {k for k, c in a['seq_conds'].items() if c == 1}
        pad_ok = pad_on_exp == pad_on_act
        seq_ok = seq_on_exp == seq_on_act
        if not (name_ok and rep_ok and pad_ok and seq_ok):
            mismatches += 1
            logger.warning(
                f"  Section {i} {e['name']!r}: repeats exp={e['repeats']} act={a['repeats']} {'OK' if rep_ok else 'MISMATCH'}; "
                f"pads ON exp={sorted(pad_on_exp)} act={sorted(pad_on_act)} {'OK' if pad_ok else 'MISMATCH'}; "
                f"seqs ON exp={sorted(seq_on_exp)} act={sorted(seq_on_act)} {'OK' if seq_ok else 'MISMATCH'}"
            )
    if mismatches == 0 and n > 0:
        logger.info(f'  All {n} section(s) match expected (name, repeats, pad/seq ON).')
    return mismatches


def main(args):
    logger.info('=== Ableton to Blackbox Converter v0.3 (Drum Rack Edition) ===')
    logger.info('Reading Ableton project...')
    
    try:
        root = read_project(args.Input)
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
        session, assets = make_drum_rack_pads(session, pad_list, tempo)
        
        # Create sequences from MIDI tracks
        session = make_drum_rack_sequences(session, midi_tracks, pad_list, midi_track_info, unquantised=args.unquantised)
        
        # Build song sections (optional song mode)
        song_sections = []
        if getattr(args, 'song_mode', False):
            logger.info('Song mode enabled – extracting locators and arrangement data')
            song_sections = build_song_sections(root, tracks, pad_list, midi_tracks, tolerance_beats=0.1)
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
                            shutil.copy2(asset_path, dest_path)
                            logger.info(f'  Copied: {dest_name} (from {os.path.basename(asset_path)})')
                        except Exception as e:
                            logger.warning(f'  Could not copy {dest_name}: {e}')
        
        preset_filepath = os.path.join(args.Output, 'preset.xml')
        
        logger.info('Saving preset XML...')
        save_xml(bb_root, preset_filepath)
        
        if getattr(args, 'compare', None):
            compare_section_content(args.compare, preset_filepath, max_sections=8)
        
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
    parser.add_argument("-c", "--compare", help="After conversion, compare section content (name, repeats, pad/seq ON) to this expected preset XML path", type=str, default=None)
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
        # Automatically use git commit hash as version name
        args.Output = os.path.join(args.Output, f'v{git_version}')
        logger.info(f'Using git commit hash as version: v{git_version}')
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    main(args)

