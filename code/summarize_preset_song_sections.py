#!/usr/bin/env python3
"""
Read a Blackbox preset.xml and print song sections in "bars×repeats" form for quick
comparison to a spreadsheet (e.g. 8X4 = 8 bars per pass × section repeats 4).

Repeats: from section cell <params sectionrepeats="N"> (locator play count in the ALS).

Bars (one pass), matching Blackbox-style “longest pattern driving this section”:

  • **Seq scene arms**: sceneitem `cond` 1 or 2 on `chan` 256–271 → use sequences on those
    seq slots (grid cell for Seq N), same as before.

    • **Pad scene arms**: sceneitem `cond` 1 or 2 on pad `chan` 0–15 (missing `chan` = pad 0).
    Use that event’s **silayer** as the seqsublayer on the pad’s grid cell (same row/col as the
    pad). Example: Bass Full arms pad 15 (`chan="14"`) with `silayer="1"` → read the `noteseq`
    at that cell with `seqsublayer="1"` (often the 192-step / steplen 6 pattern ⇒ **48** bars).

Section **bar length** from the Live timeline alone is not stored in `preset.xml`; if bars
still disagree with your sheet (e.g. break = 16 vs longest pad pattern = 8), pass a future
`--als` mode or compare locator span separately.

Usage:
  python3 summarize_preset_song_sections.py path/to/preset.xml
  python3 summarize_preset_song_sections.py a.xml b.xml   # two columns
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET

# Same as converter: step_len (notesteplen string) → steps per beat (4/4 grid)
_STEPS_PER_BEAT = {
    14: 8.0,
    12: 6.0,
    10: 4.0,
    11: 3.0,
    8: 2.0,
    9: 1.5,
    6: 1.0,
    4: 0.5,
    3: 0.25,
    2: 0.125,
    1: 0.0625,
    0: 0.03125,
}


def _row_column(pad: int) -> tuple[int, int]:
    rc_dict = {
        0: (0, 0), 1: (0, 1), 2: (0, 2), 3: (0, 3),
        4: (1, 0), 5: (1, 1), 6: (1, 2), 7: (1, 3),
        8: (2, 0), 9: (2, 1), 10: (2, 2), 11: (2, 3),
        12: (3, 0), 13: (3, 1), 14: (3, 2), 15: (3, 3),
        16: (0, 4), 17: (1, 4), 18: (2, 4), 19: (3, 4),
    }
    return rc_dict.get(int(pad), (0, 0))


def _load_root(path: str) -> ET.Element:
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    data = data.rstrip(b"\x00").rstrip()
    return ET.fromstring(data)


def _index_noteseqs(root: ET.Element) -> dict[tuple[int, int, int], list[tuple[int, dict]]]:
    """
    Key: (row, col, seqsublayer) for layer==1 noteseq cells.
    Value: list of (notestepcount_int, params_attrib_dict) — normally one cell per key.
    """
    out: dict[tuple[int, int, int], list[tuple[int, dict]]] = {}
    for cell in root.iter("cell"):
        if cell.get("type") != "noteseq":
            continue
        if cell.get("layer") != "1":
            continue
        try:
            r = int(cell.get("row", "0"))
            c = int(cell.get("column", "0"))
            sub = int(cell.get("seqsublayer", "0"))
        except ValueError:
            continue
        params = cell.find("params")
        if params is None:
            continue
        try:
            nsc = int(params.attrib.get("notestepcount", "1"))
        except ValueError:
            nsc = 1
        key = (r, c, sub)
        out.setdefault(key, []).append((nsc, dict(params.attrib)))
    return out


def _notestepcount_to_bars(notestepcount: int, notesteplen: str) -> float:
    try:
        sl = int(notesteplen)
    except ValueError:
        sl = 10
    spb = _STEPS_PER_BEAT.get(sl, 4.0)
    steps_per_bar = spb * 4.0
    if steps_per_bar <= 0:
        return 0.0
    return float(notestepcount) / steps_per_bar


def _max_bars_at_pad_silayer(
    pad_idx: int,
    silayer: int,
    noteseq_index: dict[tuple[int, int, int], list[tuple[int, dict]]],
) -> float:
    """Longest pattern on this pad grid cell for one seqsublayer (matches scene silayer)."""
    row, col = _row_column(pad_idx)
    if not (0 <= silayer <= 3):
        silayer = 0
    best = 0.0
    for nsc, pattr in noteseq_index.get((row, col, silayer), []):
        sl = pattr.get("notesteplen", "10")
        b = _notestepcount_to_bars(nsc, sl)
        if b > best:
            best = b
    return best


def _max_bars_for_seq_slot(
    seq_index: int,
    noteseq_index: dict[tuple[int, int, int], list[tuple[int, dict]]],
) -> float:
    row, col = _row_column(seq_index)
    best = 0.0
    for sub in range(4):
        for nsc, pattr in noteseq_index.get((row, col, sub), []):
            sl = pattr.get("notesteplen", "10")
            b = _notestepcount_to_bars(nsc, sl)
            if b > best:
                best = b
    return best


def _armed_pad_layers_from_section(seq_el: ET.Element | None) -> set[tuple[int, int]]:
    """(pad_index, silayer) pairs with scene ON/Keep on pads. silayer matches noteseq seqsublayer."""
    armed: set[tuple[int, int]] = set()
    if seq_el is None:
        return armed
    for ev in seq_el.findall("seqevent"):
        if ev.get("type") != "sceneitem":
            continue
        cond = ev.get("cond", "0")
        if cond not in ("1", "2"):
            continue
        try:
            silayer = int(ev.get("silayer", "0") or 0)
        except ValueError:
            silayer = 0
        ch = ev.get("chan")
        if ch is None or ch == "":
            armed.add((0, silayer))
            continue
        try:
            ci = int(ch)
        except ValueError:
            continue
        if 0 <= ci <= 15:
            armed.add((ci, silayer))
    return armed


def _sections_report(root: ET.Element) -> list[dict]:
    noteseq_index = _index_noteseqs(root)
    rows: list[tuple[int, ET.Element]] = []
    for cell in root.iter("cell"):
        if cell.get("type") != "section" or cell.get("layer") != "2":
            continue
        try:
            ri = int(cell.get("row", "0"))
        except ValueError:
            ri = 0
        rows.append((ri, cell))
    rows.sort(key=lambda x: x[0])

    report: list[dict] = []
    for ri, cell in rows:
        name = cell.get("name", "") or ""
        params = cell.find("params")
        try:
            repeats = int(params.attrib.get("sectionrepeats", "1")) if params is not None else 1
        except ValueError:
            repeats = 1
        seq = cell.find("sequence")
        armed_seq: set[int] = set()
        if seq is not None:
            for ev in seq.findall("seqevent"):
                if ev.get("type") != "sceneitem":
                    continue
                ch = ev.get("chan")
                cond = ev.get("cond", "0")
                if cond not in ("1", "2"):
                    continue
                if ch is None or ch == "":
                    continue
                try:
                    ci = int(ch)
                except ValueError:
                    continue
                if 256 <= ci <= 271:
                    armed_seq.add(ci - 256)
        armed_pad_layers = _armed_pad_layers_from_section(seq)
        max_seq_bars = 0.0
        for sidx in armed_seq:
            max_seq_bars = max(max_seq_bars, _max_bars_for_seq_slot(sidx, noteseq_index))
        max_pad_bars = 0.0
        for p, sl in armed_pad_layers:
            max_pad_bars = max(max_pad_bars, _max_bars_at_pad_silayer(p, sl, noteseq_index))
        max_bars = max(max_seq_bars, max_pad_bars)
        # Integer bars for display when close to whole bars
        bars_disp = int(round(max_bars)) if abs(max_bars - round(max_bars)) < 0.05 else round(max_bars, 2)
        report.append(
            {
                "row": ri,
                "name": name,
                "repeats": repeats,
                "max_bars": max_bars,
                "bars_disp": bars_disp,
                "label": f"{bars_disp}X{repeats}",
                "armed_seqs": sorted(armed_seq),
                "armed_pad_layers": sorted(armed_pad_layers),
            }
        )
    return report


def _print_report(path: str, rep: list[dict]) -> None:
    print(f"# {path}")
    print(
        "row\tname\tbars\trepeats\tbarsXrepeats\tarmed_seqs(0-based)\tarmed_pad_layers(pad,silayer)  "
        "[bars = max(seq-slot max, each armed pad+silayer cell)]"
    )
    for r in rep:
        if not r["name"] and r["row"] >= 8 and r["max_bars"] == 0 and r["repeats"] == 1:
            continue  # skip trailing empty padding rows
        print(
            f"{r['row']}\t{r['name']!r}\t{r['bars_disp']}\t{r['repeats']}\t{r['label']}\t"
            f"{r['armed_seqs']}\t{r['armed_pad_layers']}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize preset.xml song sections as bars×repeats")
    ap.add_argument("preset", nargs="+", help="One or two preset.xml paths")
    args = ap.parse_args()
    paths = args.preset
    if len(paths) == 1:
        rep = _sections_report(_load_root(paths[0]))
        _print_report(paths[0], rep)
        return 0
    if len(paths) == 2:
        r0 = _sections_report(_load_root(paths[0]))
        r1 = _sections_report(_load_root(paths[1]))
        n = min(len(r0), len(r1))
        print(f"A: {paths[0]}\nB: {paths[1]}\n")
        print("row\tname_A\tbarsX_A\tname_B\tbarsX_B\tmatch")
        for i in range(n):
            a, b = r0[i], r1[i]
            m = a["label"] == b["label"] and (a["name"] or "") == (b["name"] or "")
            print(
                f"{a['row']}\t{a['name']!r}\t{a['label']}\t{b['name']!r}\t{b['label']}\t{m}"
            )
        return 0
    print("Provide 1 or 2 preset.xml paths", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
