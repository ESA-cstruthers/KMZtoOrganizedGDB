# Usage

End-to-end walkthrough of the two tools plus a scripting recipe for the underlying package.

## Pre-flight: Inspect KMZ/KML

Recommended before any real conversion run, especially on unfamiliar source KMZs.

1. Catalog pane -> expand `KMZ_Tools.pyt` -> double-click **Inspect KMZ/KML (Preflight Check)**.
2. Pick one or more files. Multi-select works.
3. (Optional) Set an output `.txt` path to save the full report.
4. Run.

The report streams to the geoprocessing messages pane and (if a path was set) is written to disk. If a non-`.txt` extension was supplied, it is normalized to `.txt` with a warning.

### Example report excerpt

```
======================================================================
HazardsReport.kmz
======================================================================
  Path:       ...
  Size:       418.0 KB
  Format:     KMZ

ELEMENTS
  Placemarks (total):       646
    with geometry:          646
      Point:                 584
      MultiGeometry:         62
  Folders:                  16
  Styles:                   16
  NetworkLinks:             0

FOLDER PATH STATS
  Depth (min/max/avg):      2 / 2 / 2.0
  Distinct full paths:      15
  Distinct last-3 keys:     15

POPUPS
  Parse tier (sample of 100):
    html_table:            100

POTENTIAL ISSUES / NOTES
  [INFO   ] 62 MultiGeometry placemark(s); each will be split across
            Point/Line/Polygon FCs by sub-geometry type
```

### Issues to act on before conversion

| Flag | What to do |
|---|---|
| `[WARNING] lxml recovery applied` | Source KML is malformed. Output may be incomplete -- spot-check feature counts. |
| `N placemarks have no geometry` | These are dropped by the converter; the count is logged but the records are gone. |
| `N NetworkLinks found` | Enable **Follow NetworkLinks** on the converter to pull external content inline. |
| `Broken styleUrl refs` | Style hints will be missing for those features (the data itself is fine). |
| `>25% of sampled popups unparseable` | Features will only have `RawPopup`, no typed fields. Inspect source `description` content. |
| `Paths deeper than 3 levels` | The folder-pair / leaf / parent strategies use only the last 3 segments; deeper context lives in `FolderPath`. |

## Main conversion: KMZ to Organized GDB

The converter is driven by a Waterfall of four orthogonal questions. Each question narrows what's possible next, so the dialog updates as you answer.

### The four questions

1. **Q1: Output container** -- `One merged GDB` (all sources combined into a single GDB) vs `One GDB per source KMZ` (N output GDBs, one each).
2. **Q2: Split into Feature Datasets?** -- yes or no.
3. **Q3a: FD source** (visible only if Q2=yes):
   - `Top folder (segments[-3])`
   - `Top two folders combined (segments[-3]+segments[-2])`
   - `Source KMZ filename` -- hidden when Q1=per-KMZ (would always be a one-FD GDB).
4. **Q3b: FC naming strategy** -- choice set depends on Q2:
   - **With FDs:** per-geometry only / leaf folder / leaf + geometry / parent + leaf + geometry / folder pair + geometry.
   - **Without FDs:** per-geometry only / top folder + geometry / leaf + geometry / parent + leaf + geometry / folder pair + geometry.

Unsupported combinations (e.g. top-folder-as-FC with top-folder-as-FD, which would duplicate the FD name in the FC name) are refused with a clear message.

Then pick **Output Coordinate System** from the shortlist (WGS 1984 pass-through, NAD 1983 HARN State Plane WA/OR/CA, or NAD 1983 UTM 9N--12N) and, optionally, expand **NetworkLink Options** to enable following + tune max depth and timeout.

### What happens internally

```
Input KMZ(s)
    |
    v
[Phase 1: Parse & extract]   <-- lxml recovery; raw fields written; always WGS 1984
    |
    v
Scratch GDB(s)               <-- kept on disk for re-runs
    |
    v
[Phase 2: Organize]          <-- popups parsed into typed fields; Folder1/2/3 added;
    |                            geometries projected to chosen SR
    v
Final output GDB(s)
```

Scratch lives at:
- Merged container: `<output_gdb_parent>/<output_gdb_stem>_scratch/`
- Per-KMZ container: `<output_folder>/_scratch/`

### Worked examples

Sample input KMZ `WRIA1_Birds.kmz` with placemarks at folder paths like:

```
Surveys / 2026 / BUOW / Known
Surveys / 2026 / BUOW / Probable
Surveys / 2026 / TRES / Known
Surveys / 2025 / BUOW / Known
```

#### Example 1: "One big GDB grouped by source KMZ, per-geometry FCs"
- Q1: One merged GDB
- Q2: Yes (FDs on)
- Q3a: Source KMZ filename
- Q3b: Per-geometry only

Result: `Out.gdb / WRIA1_Birds / {Points, Polylines, Polygons}`. All placemarks land in three FCs under one FD per source.

#### Example 2: "One GDB per KMZ, FDs by year (top folder), folder-pair-named FCs"
- Q1: One GDB per source KMZ
- Q2: Yes
- Q3a: Top two folders combined
- Q3b: Folder pair + geometry

Result (per source KMZ GDB): `WRIA1_Birds.gdb / Surveys_2026 / {BUOW_Known_Points, BUOW_Probable_Points, TRES_Known_Points}` and `WRIA1_Birds.gdb / Surveys_2025 / {BUOW_Known_Points}`. The KMZ's own root folder is auto-skipped so it doesn't waste an FD level.

#### Example 3: "Flat per-geometry merge across all sources"
- Q1: One merged GDB
- Q2: No
- Q3b: Per-geometry only

Result: `Out.gdb / {Points, Polylines, Polygons}`. Three FCs total, regardless of source count.

#### Example 4: "Merged GDB, FDs by top folder, leaf+geom FCs"
- Q1: One merged GDB
- Q2: Yes
- Q3a: Top folder (`segments[-3]`)
- Q3b: Leaf + geometry

Result: `Out.gdb / Surveys / {BUOW_Points, TRES_Points, ...}`. If a path is shallower than 3 segments, the FD source falls back to `segments[0]` so the row still lands in an FD rather than at the GDB root.

#### Example 5: "Per-KMZ GDBs, no FDs, leaf+geom FCs"
- Q1: One GDB per source KMZ
- Q2: No
- Q3b: Leaf + geometry

Result: `WRIA1_Birds.gdb / {BUOW_Known_Points, BUOW_Probable_Points, TRES_Known_Points, ...}`. Per-KMZ container is fully composable -- any FD/FC strategy works inside it.

### Re-running

The kept scratch folder lets you re-organize with different Waterfall answers (or a different coordinate system) without re-parsing. Delete the output GDB(s) and re-run; Phase 1 finds the existing scratch GDBs and skips straight to Phase 2.

The tool refuses to write into an existing output GDB to avoid schema mixing.

### Field schema

Every output FC carries: `Name`, `FolderPath`, `SourceKMZ`, `RawPopup` (first 4000 chars), all popup-derived typed fields (inferred per FC), and `Folder1` / `Folder2` / `Folder3` (last three folder segments).

FC names starting with a digit are prefixed `x` (e.g. `x2026_BUOW_Polygons`). Names exceeding the 64-char FGDB limit are truncated while preserving the geometry suffix.

## Scripting against the modules

```python
import sys
sys.path.insert(0, r"C:\Tools\KMZ_Tools")  # folder containing kmz_tools/

from kmz_tools.inspector import inspect_kmz, format_report
from kmz_tools.converter import convert_kmz_to_gdb
from kmz_tools.post_processor import (
    post_process,
    COORD_SYSTEMS,
    DEFAULT_COORD_SYSTEM,
    # FD strategies
    FD_NONE, FD_TOP_FOLDER, FD_TOP_TWO_FOLDERS, FD_SOURCE_KMZ, FD_HIERARCHY,
    # FC strategies
    FC_PER_GEOM, FC_LEAF, FC_LEAF_GEOM, FC_PARENT_LEAF_GEOM, FC_PAIR_GEOM, FC_TOP_GEOM,
)

# Inspect
report = inspect_kmz(r"C:\data\some.kmz")
for line in format_report(report):
    print(line)

# Convert one KMZ to a scratch GDB
result = convert_kmz_to_gdb(
    r"C:\data\some.kmz",
    r"C:\work\scratch",
    follow_network_links_enabled=False,
)
print(result["inserted"], "features in", result["gdb_path"])

# Organize a scratch folder
result = post_process(
    r"C:\work\scratch",
    r"C:\work\Final.gdb",
    fd_strategy=FD_SOURCE_KMZ,
    fc_strategy=FC_PER_GEOM,
    target_coord_system="NAD 1983 HARN StatePlane Washington N (US Ft)",
)
print(result["feature_classes"], "FCs,", result["features"], "features")
```

The exact constant names live in `kmz_tools/post_processor.py`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Output GDB already exists` error | Re-running into an existing output | Delete the GDB (or pick a new path); scratch is reused |
| `ERROR 000732` "Feature class not visible" | Network-drive catalog lag | The tool now auto-retries with `ClearWorkspaceCache`; if you still see it, re-run once |
| Inspector reports 0 placemarks but several NetworkLinks | Source is a NetworkLink wrapper KMZ | Enable Follow NetworkLinks on the converter |
| Dropped-no-geometry count > 0 | Source has placemarks without `<Point>`/`<LineString>`/`<Polygon>` | Inspect source; non-spatial "placemarks" are dropped by design |
| CS dropdown shows only "WGS 1984 (no reproject)" | `kmz_tools` import failed silently | Right-click toolbox -> Refresh; close + reopen the tool |
| Two per-KMZ GDBs collide (same sanitized stem) | Two source KMZs sanitize to the same name | Output gets `_2`, `_3` suffix; check the log |
| "Unsupported combination" message | Waterfall answers compose to a duplicating name | Pick a different FC strategy (typically per-geometry or leaf+geom) |
