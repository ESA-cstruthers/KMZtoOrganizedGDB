# QA Test Plan -- KMZ Tools 1.1.0-qa

## Scope

Two user-facing tools in `KMZ_Tools.pyt`:

1. **Inspect KMZ/KML (Preflight Check)**
2. **KMZ to Organized GDB** (Waterfall UX)

Plus the underlying `kmz_tools` Python package, used directly via scripting and indirectly via the two toolbox classes.

## Environment

- Windows 10 / 11, ArcGIS Pro 3.x (the `arcgispro-py3` conda env).
- No package installations required -- `lxml` ships with Pro.
- Tester needs **edit access** to a working folder for output GDBs and scratch.
- For network-drive regression checks (TC-14, TC-15), the output folder must live on a UNC / mapped network path.

## Test data

Place a curated set of KMZs in a tester-accessible folder. Minimum recommended set:

| File | Why |
|---|---|
| Real production KMZ with 100+ placemarks across 2+ folder levels | Happy-path E2E |
| KMZ that's only NetworkLinks (0 placemarks) | NetworkLink wrapper behavior |
| KMZ with MultiGeometry placemarks | Geometry-split logic |
| KMZ with malformed XML | lxml recovery flag visibility |
| KMZ whose folder paths exceed 3 levels deep | Last-3-segments rule |
| KMZ whose folder paths are shallow (1--2 segments) | FD shallow fallback (TC-19) |
| KMZ with placemarks that have NO geometry | Silent-drop visibility |
| KMZ with extended_data ExtendedData | Tier-1 popup parsing |
| KMZ with `<table>` popup in `<description>` | Tier-2 popup parsing |
| KMZ with `Field: value` plaintext in `<description>` | Tier-3 popup parsing |
| KMZ whose top folder name starts with a digit (e.g. `2026_BUOW`) | Digit prefix (TC-18) |

## Test cases

Each row: setup -> action -> expected result. Tick the box on pass.

### TC-01: Toolbox loads and registers both tools
- **Setup:** Add Folder Connection to the Final folder.
- **Action:** Expand `KMZ_Tools.pyt`.
- **Expect:** Two tools visible -- "Inspect KMZ/KML (Preflight Check)" and "KMZ to Organized GDB". No errors in the Catalog log.
- [ ] Pass [ ] Fail

### TC-02: Inspector happy path, single KMZ
- **Action:** Run Inspector on a real-world KMZ.
- **Expect:** Report shows element counts, folder stats, popup tier breakdown, style coverage, and zero or more `POTENTIAL ISSUES / NOTES`. AGGREGATE block shows `Files inspected: 1 of 1`.
- [ ] Pass [ ] Fail

### TC-03: Inspector multi-file + report extension normalization (B10)
- **Action:** Run Inspector on 3+ KMZs with the optional report path set to `report.xyz`.
- **Expect:** Per-file reports plus AGGREGATE. Warning that the extension was forced to `.txt`. File written as `report.txt` with the same content as GP messages.
- [ ] Pass [ ] Fail

### TC-04: Inspector flags NetworkLink-only KMZ
- **Action:** Run Inspector on a KMZ that has 0 placemarks but NetworkLinks.
- **Expect:** `Placemarks (total): 0`, `NetworkLinks: N (>0)`, plus an `[INFO]` note about enabling Follow NetworkLinks on the converter.
- [ ] Pass [ ] Fail

### TC-05: Waterfall dialog -- conditional parameter visibility
- **Action:** Open the converter. Toggle Q2 between Yes and No; toggle Q1 between merged and per-KMZ.
- **Expect:** Q3a only appears when Q2=Yes. Q3a's "Source KMZ filename" option is hidden when Q1=per-KMZ. Q3b's choice list updates based on Q2.
- [ ] Pass [ ] Fail

### TC-06: Merged container -- FD=source KMZ, FC=per-geom
- **Setup:** 2+ source KMZs. Output `<folder>\Merged_SrcKmz.gdb`.
- **Action:** Q1=merged, Q2=yes, Q3a=Source KMZ filename, Q3b=Per-geometry only.
- **Expect:** Output GDB has one FD per source KMZ; each FD contains `Points` / `Polylines` / `Polygons` as applicable.
- [ ] Pass [ ] Fail

### TC-07: Merged container -- FD=top folder, FC=leaf+geom
- **Action:** Q1=merged, Q2=yes, Q3a=Top folder, Q3b=Leaf + geometry.
- **Expect:** FDs named after `segments[-3]`. FCs named `<leaf>_<geom>` (e.g. `Known_Points`).
- [ ] Pass [ ] Fail

### TC-08: Merged container -- FD=top two folders, FC=folder pair + geom
- **Action:** Q1=merged, Q2=yes, Q3a=Top two folders combined, Q3b=Folder pair + geometry.
- **Expect:** FDs named `<seg[-3]>_<seg[-2]>`. FCs named `<seg[-3]>_<seg[-2]>_<leaf>_<geom>` or similar -- verify the leaf folder lives in `Folder3` even when collapsed.
- [ ] Pass [ ] Fail

### TC-09: Merged container -- no FDs, FC=top folder + geom
- **Action:** Q1=merged, Q2=no, Q3b=Top folder + geometry.
- **Expect:** No FDs. FCs at root named `<top_folder>_<geom>`.
- [ ] Pass [ ] Fail

### TC-10: Merged container -- no FDs, FC=per-geom (flat merge)
- **Action:** Q1=merged, Q2=no, Q3b=Per-geometry only.
- **Expect:** Output GDB has at most 3 FCs (`Points`, `Polylines`, `Polygons`) at root, regardless of source count.
- [ ] Pass [ ] Fail

### TC-11: Per-KMZ container -- no FDs, per-geom FCs
- **Setup:** 2+ source KMZs. Pick an output folder.
- **Action:** Q1=per-KMZ, Q2=no, Q3b=Per-geometry only.
- **Expect:** N output GDBs, one per source, each containing per-geometry FCs at root. `_scratch/` sibling folder is present.
- [ ] Pass [ ] Fail

### TC-12: Per-KMZ container with FDs (composability check)
- **Action:** Q1=per-KMZ, Q2=yes, Q3a=Top folder, Q3b=Leaf + geometry.
- **Expect:** N GDBs, each with FDs from `segments[-3]` and FCs `<leaf>_<geom>`. Confirms per-KMZ container is fully composable (1.1.0-qa unlock).
- [ ] Pass [ ] Fail

### TC-13: Unsupported combination refusal
- **Action:** Try Q1=merged, Q2=yes, Q3a=Top folder, Q3b=Top folder + geom (a combination that would duplicate the FD name in the FC name).
- **Expect:** Tool aborts before any GDB is created with a clear message naming the conflict.
- [ ] Pass [ ] Fail

### TC-14: Output Folder direction fix (regression for ERROR 000725)
- **Setup:** Choose an existing output folder.
- **Action:** Run a per-KMZ container conversion targeting that existing folder.
- **Expect:** Tool runs without `ERROR 000725 "Dataset already exists"`. The Output Folder param accepts an existing folder as `Input` direction.
- [ ] Pass [ ] Fail

### TC-15: Network-drive catalog lag retry (regression for ERROR 000732)
- **Setup:** Output GDB on a network share (UNC or mapped drive).
- **Action:** Run any merged-container conversion with several FCs.
- **Expect:** No `ERROR 000732 "Feature class not visible"`. If transient lag occurs, log shows the `ClearWorkspaceCache` retry succeeding silently.
- [ ] Pass [ ] Fail

### TC-16: NetworkLink follower (online)
- **Action:** Run on a NetworkLink-wrapper KMZ with Follow NetworkLinks enabled, depth 2, timeout 30s.
- **Expect:** Phase 1 log shows downloads from the linked URLs; placemark counts are non-zero.
- [ ] Pass [ ] Fail

### TC-17: Coordinate-system shortlist and projection correctness
- **Action:** Run on a KMZ with known coordinates near a state-plane zone (e.g. King County WA), output CS = `NAD 1983 HARN StatePlane Washington N (US Ft)`.
- **Expect:** CS dropdown shows the full shortlist (15+ entries; not just `WGS 1984 (no reproject)`). Output FC coordinates are in US-feet State Plane (large numbers, 1,000,000+). Correct WGS84 -> NAD83 -> HARN_WA transformation chain applied.
- [ ] Pass [ ] Fail

### TC-18: Digit-leading name prefix
- **Setup:** A source KMZ where a folder name (used by the chosen Q3a or Q3b) starts with a digit, e.g. `2026_BUOW`.
- **Action:** Run any layout that exposes that name as an FC or FD.
- **Expect:** Output identifier is `x2026_BUOW_Polygons` (or similar). Confirm the prefix is lowercase `x`, NOT the old `FD_`.
- [ ] Pass [ ] Fail

### TC-19: FD shallow-path fallback
- **Setup:** A KMZ where some placemarks have only 1 or 2 folder segments (paths shallower than 3).
- **Action:** Run merged + Q2=yes + Q3a=Top folder.
- **Expect:** Shallow placemarks still land in an FD (named after `segments[0]`) rather than at the GDB root. No errors.
- [ ] Pass [ ] Fail

### TC-20: Auto-skip redundant source folder under per-KMZ
- **Setup:** A KMZ whose top folder name matches the KMZ filename.
- **Action:** Run Q1=per-KMZ, Q2=yes, Q3a=Top folder.
- **Expect:** The redundant root folder is stripped; the FD level reflects the next folder down, not the KMZ name a second time.
- [ ] Pass [ ] Fail

### TC-21: Re-run guard (B5)
- **Action:** Run any conversion; then re-run with the same output GDB path.
- **Expect:** Tool errors out with `Output GDB already exists ... Delete it (or pick a new path) and re-run.` No silent overwrite.
- [ ] Pass [ ] Fail

### TC-22: Scratch reuse across re-runs
- **Action:** Delete the output GDB from TC-21. Re-run with different Waterfall answers (e.g. swap Q3b).
- **Expect:** Phase 1 is fast (no re-parsing); log indicates scratch GDB(s) reused. Output reflects the new Waterfall answers.
- [ ] Pass [ ] Fail

### TC-23: Zip Slip guard (B7)
- **Setup:** Construct a KMZ with a member path like `../evil.txt` (Python: `zipfile.ZipFile().writestr('../evil.txt', 'x')`).
- **Action:** Run Inspector against it.
- **Expect:** `RuntimeError` with "unsafe path (outside extraction root)". No file written outside the temp directory.
- [ ] Pass [ ] Fail

### TC-24: doc.kml fallback (B8)
- **Setup:** A KMZ whose inner KML is named `main.kml`, not `doc.kml`.
- **Action:** Run Inspector and converter.
- **Expect:** Both work; converter produces a GDB.
- [ ] Pass [ ] Fail

### TC-25: Silent-drop visibility (B9)
- **Setup:** A KMZ where some placemarks have no geometry.
- **Action:** Run converter.
- **Expect:** Per-file log shows `Found N placemarks ... M have usable geometry, X dropped (no geometry)`. Aggregate warning surfaces total dropped count.
- [ ] Pass [ ] Fail

### TC-26: Scratch stem collision (B6)
- **Setup:** Source `Foo Bar.kmz` and `Foo_Bar.kmz` (same sanitized stem).
- **Action:** Run any merged conversion with both.
- **Expect:** Log shows `[NOTE] Foo_Bar.gdb already exists in scratch folder; using Foo_Bar_2.gdb to avoid overwrite`. Both inputs end up as distinct scratch GDBs.
- [ ] Pass [ ] Fail

### TC-27: FC name truncation preserves geometry suffix
- **Setup:** Pick a layout where the composed FC name exceeds 64 characters (e.g. folder pair + leaf + geom with long folder names).
- **Action:** Run.
- **Expect:** Truncated FC names still end in `_Points` / `_Polylines` / `_Polygons`. No name collisions.
- [ ] Pass [ ] Fail

### TC-28: LONG decimal truncation guard (N6)
Run as a small Python check:
```python
from kmz_tools.post_processor import _coerce_value
assert _coerce_value("3.14", "LONG") is None
assert _coerce_value("3", "LONG") == 3
assert _coerce_value(3.14, "LONG") == 3
```
- [ ] Pass [ ] Fail

### TC-29: Hover help present on all parameters
- **Action:** Open the converter dialog and hover the info icon on every parameter.
- **Expect:** Every parameter has a populated hover tooltip (no blanks).
- [ ] Pass [ ] Fail

### TC-30: Diagram reference accurate
- **Action:** Open `KMZ_Tools_Diagram.html` in a browser.
- **Expect:** Page renders as a Waterfall flowchart matching the four-question UX. No leftover A/B/C/D/F/G mode references.
- [ ] Pass [ ] Fail

## Regression sanity checklist

After any code change in `kmz_tools/`, re-run at minimum: TC-01, TC-02, TC-06, TC-10, TC-11, TC-13, TC-14, TC-15, TC-21.

## Reporting issues

Open issues on the repo with:
- The four Waterfall answers (Q1, Q2, Q3a, Q3b) and chosen coordinate system
- Anonymized sample KMZ (if shareable) or its Inspector report output
- Full GP messages text
- ArcGIS Pro version (Help -> About ArcGIS Pro)
