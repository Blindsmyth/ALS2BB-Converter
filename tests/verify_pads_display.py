#!/usr/bin/env python3
"""
Verify that Pads-mode sequence cells won't show as Keys on the Blackbox.

Logic: Multiple distinct pitches on the SAME chan (same pad) = Keys display.
       Multiple chans (different pads) = drum pattern = OK.

Usage:
  python verify_pads_display.py <preset.xml>
  python verify_pads_display.py tests/keys_display_logic_test.xml
"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def check_keys_risk(preset_path):
    """
    Check preset XML for cells that may display as Keys instead of Pads.
    Returns (risks: list of dict, ok_count: int).
    """
    tree = ET.parse(preset_path)
    root = tree.getroot()
    session = root.find("session")
    if session is None:
        return [], 0

    risks = []
    ok_count = 0

    for cell in session.findall("cell"):
        if cell.get("type") != "noteseq":
            continue
        seq = cell.find("sequence")
        if seq is None:
            continue

        row = cell.get("row", "?")
        col = cell.get("column", "?")
        sub = cell.get("seqsublayer", "0")
        params = cell.find("params")
        seqpad = params.get("seqpadmapdest", "?") if params is not None else "?"

        # Group note events by chan: {chan: set(pitches)}
        chan_pitches = defaultdict(set)
        for ev in seq.findall("seqevent"):
            if ev.get("type") != "note":
                continue
            try:
                c = int(ev.get("chan", 0))
                p = int(ev.get("pitch", 0))
                chan_pitches[c].add(p)
            except (ValueError, TypeError):
                continue

        if not chan_pitches:
            continue

        # Risk: any chan has more than one distinct pitch
        has_risk = any(len(pitches) > 1 for pitches in chan_pitches.values())
        if has_risk:
            details = {f"chan{c}": sorted(ps) for c, ps in chan_pitches.items()}
            risks.append({
                "cell": f"row={row} col={col} sub={sub} seqpad={seqpad}",
                "chan_pitches": details,
            })
        else:
            ok_count += 1

    return risks, ok_count


def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_pads_display.py <preset.xml>")
        sys.exit(2)

    preset_path = sys.argv[1]
    risks, ok_count = check_keys_risk(preset_path)

    print(f"Checked preset: {preset_path}")
    print(f"  OK (single pitch per chan): {ok_count}")
    print(f"  Keys risk (multi-pitch on same chan): {len(risks)}")
    for r in risks:
        print(f"    - {r['cell']}: {r['chan_pitches']}")

    if risks:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
