#!/usr/bin/env python3
"""
Read a Blackbox preset.xml and print song sections in "bars×repeats" form for quick
comparison to a spreadsheet (e.g. 8X4 = 8 bars per pass × section repeats 4).

Repeats: from section cell <params sectionrepeats="N"> (locator play count in the ALS).

Bars (one pass) — only **scene ON** (`cond=1`) counts toward length. **Keep** (`cond=2`)
carries prior state; it should not dominate the readout (e.g. **transition**: one pad ON at
layer B is 2 bars while bass Seq Keeps would otherwise read 8).

  • **Pad or seq** `chan`: use that line’s `silayer` with `notestepcount` / `notesteplen`.

Section **bar length** from the Live timeline alone is not stored in `preset.xml`; if bars
still disagree with your sheet (e.g. break = 16 vs longest pad pattern = 8), pass a future
`--als` mode or compare locator span separately.

Usage:
  python3 summarize_preset_song_sections.py path/to/preset.xml
  python3 summarize_preset_song_sections.py a.xml b.xml   # two columns
  python3 summarize_preset_song_sections.py --tsv path/to/preset.xml   # tab-separated for Numbers/Excel
  python3 summarize_preset_song_sections.py --tsv a.xml b.xml
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


def _armed_grid_layers_from_section(seq_el: ET.Element | None) -> set[tuple[int, int]]:
    """
    (grid_slot 0–15, silayer) for every pad or seq scene ON/Keep (debug; includes cond=2).
    """
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
        if not (0 <= silayer <= 3):
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
        elif 256 <= ci <= 271:
            armed.add((ci - 256, silayer))
    return armed


def _armed_on_layers_for_bars(seq_el: ET.Element | None) -> set[tuple[int, int]]:
    """Grid (slot, silayer) for scene ON only — used for bars×repeats bar count."""
    armed: set[tuple[int, int]] = set()
    if seq_el is None:
        return armed
    for ev in seq_el.findall("seqevent"):
        if ev.get("type") != "sceneitem":
            continue
        if ev.get("cond", "0") != "1":
            continue
        try:
            silayer = int(ev.get("silayer", "0") or 0)
        except ValueError:
            silayer = 0
        if not (0 <= silayer <= 3):
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
        elif 256 <= ci <= 271:
            armed.add((ci - 256, silayer))
    return armed


def _armed_seq_slots_from_section(seq_el: ET.Element | None) -> set[int]:
    """Seq indices (0–15) that have cond 1/2 on chan 256–271 (for debug column)."""
    out: set[int] = set()
    if seq_el is None:
        return out
    for ev in seq_el.findall("seqevent"):
        if ev.get("type") != "sceneitem":
            continue
        if ev.get("cond", "0") not in ("1", "2"):
            continue
        ch = ev.get("chan")
        if ch is None or ch == "":
            continue
        try:
            ci = int(ch)
        except ValueError:
            continue
        if 256 <= ci <= 271:
            out.add(ci - 256)
    return out


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
        armed_layers = _armed_grid_layers_from_section(seq)
        max_bars = 0.0
        for grid_slot, sl in _armed_on_layers_for_bars(seq):
            max_bars = max(max_bars, _max_bars_at_pad_silayer(grid_slot, sl, noteseq_index))
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
                "armed_seqs": sorted(_armed_seq_slots_from_section(seq)),
                "armed_layers": sorted(armed_layers),
            }
        )
    return report


def _should_skip_row(r: dict) -> bool:
    return not r["name"] and r["row"] >= 8 and r["max_bars"] == 0 and r["repeats"] == 1


def _print_report(path: str, rep: list[dict]) -> None:
    print(f"# {path}")
    print(
        "row\tname\tbars\trepeats\tbarsXrepeats\tarmed_seqs(0-based)\tarmed_layers(slot,silayer, all ON+Keep)"
    )
    for r in rep:
        if _should_skip_row(r):
            continue  # skip trailing empty padding rows
        print(
            f"{r['row']}\t{r['name']!r}\t{r['bars_disp']}\t{r['repeats']}\t{r['label']}\t"
            f"{r['armed_seqs']}\t{r['armed_layers']}"
        )


def _print_tsv_one(rep: list[dict]) -> None:
    print("Row\tSection\tBars\tRepeats\tBars×Repeats")
    for r in rep:
        if _should_skip_row(r):
            continue
        name = (r["name"] or "").replace("\t", " ")
        print(f"{r['row']}\t{name}\t{r['bars_disp']}\t{r['repeats']}\t{r['label']}")


def _print_tsv_two(rep_a: list[dict], rep_b: list[dict]) -> None:
    print("Row\tSection A\tBars\tRepeats\tBars×Repeats\tSection B\tBars\tRepeats\tBars×Repeats\tMatch")
    n = min(len(rep_a), len(rep_b))
    for i in range(n):
        a, b = rep_a[i], rep_b[i]
        if _should_skip_row(a) and _should_skip_row(b):
            continue
        m = a["label"] == b["label"] and (a["name"] or "") == (b["name"] or "")
        na = (a["name"] or "").replace("\t", " ")
        nb = (b["name"] or "").replace("\t", " ")
        print(
            f"{a['row']}\t{na}\t{a['bars_disp']}\t{a['repeats']}\t{a['label']}\t"
            f"{nb}\t{b['bars_disp']}\t{b['repeats']}\t{b['label']}\t{str(m).upper()}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize preset.xml song sections as bars×repeats")
    ap.add_argument("--tsv", action="store_true", help="Tab-separated output for Numbers / Excel paste")
    ap.add_argument("preset", nargs="+", help="One or two preset.xml paths")
    args = ap.parse_args()
    paths = args.preset
    if len(paths) == 1:
        rep = _sections_report(_load_root(paths[0]))
        if args.tsv:
            _print_tsv_one(rep)
        else:
            _print_report(paths[0], rep)
        return 0
    if len(paths) == 2:
        r0 = _sections_report(_load_root(paths[0]))
        r1 = _sections_report(_load_root(paths[1]))
        if args.tsv:
            _print_tsv_two(r0, r1)
            return 0
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
