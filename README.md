# KMZ Tools (ArcGIS Pro)

ArcGIS Pro Python toolbox for converting KMZ/KML files into clean, organized file geodatabases.
Two user-facing tools:

| Tool | What it does |
|---|---|
| **Inspect KMZ/KML (Preflight Check)** | Read-only. Reports placemark counts, folder structure, popup parse-tier breakdown, style coverage, NetworkLinks, and flags conversion concerns. Writes nothing. |
| **KMZ to Organized GDB** | One-step converter (KMZ(s) -> organized GDB(s)) driven by a Waterfall UX: four orthogonal questions (output container, FD split, FD source, FC naming) compose into a layout. Parses popup HTML into typed fields and projects to a chosen coordinate system. |

See `KMZ_Tools_Diagram.html` for a one-page visual reference of the Waterfall decision flow.

---

## Requirements

- **ArcGIS Pro 3.x** (uses `arcpy`; tested against the `arcgispro-py3` conda env).
- No additional Python packages required. `lxml` (used by the parser) ships with Pro.

## Access

No install required. In ArcGIS Pro, navigate to this folder from the Catalog
pane (use the existing `GIS\Tools\02_QA\` connection or add a Folder
Connection to it), expand `KMZ_Tools.pyt`, and run either tool. The toolbox
imports only stdlib + `arcpy` + `lxml` (which ships with Pro).

## Quick start

### Inspect a KMZ before converting (recommended)
1. Open **Inspect KMZ/KML (Preflight Check)**.
2. Pick one or more KMZ/KML files.
3. (Optional) Pick a `.txt` path to save the report.
4. Run. The report streams to the geoprocessing messages pane and covers:
   - Placemark counts by geometry, including geometry-less drops
   - Folder-path depth stats (the layout strategies use the last 3 segments)
   - Popup parse-tier breakdown (`extended_data`, `html_table`, `label_value`, `unparseable`)
   - Style coverage and broken `styleUrl` references
   - NetworkLinks present (you'll need to enable "Follow NetworkLinks" on the converter)

### Convert KMZ -> organized GDB
1. Open **KMZ to Organized GDB**.
2. Pick input KMZs and an output location.
3. Answer the four Waterfall questions:
   - **Q1: Output container** -- `One merged GDB` or `One GDB per source KMZ`.
   - **Q2: Split into Feature Datasets?** -- yes/no.
   - **Q3a: FD source** (only if Q2=yes) -- top folder, top two folders combined, or source KMZ filename. (Source-KMZ option is hidden when the container is already per-KMZ.)
   - **Q3b: FC naming strategy** -- options depend on Q2. With FDs on: per-geometry only, leaf folder, leaf + geometry, parent + leaf + geometry, folder pair + geometry. With FDs off: per-geometry only, top folder + geometry, leaf + geometry, parent + leaf + geometry, folder pair + geometry.
4. Pick an **Output Coordinate System** from the shortlist (WGS 1984 pass-through, NAD 1983 HARN State Plane WA/OR/CA, or NAD 1983 UTM 9N--12N).
5. (Optional) Enable Follow NetworkLinks if the source KMZ references external KMZs.
6. Run.

Unsupported combinations (e.g. top-folder-as-FC with FDs on, which would duplicate the FD name) are refused with a clear message. See `KMZ_Tools_Diagram.html` for the full decision tree.

## Output FC field schema

Every output feature class carries these fields:

- `Name` -- placemark name
- `FolderPath` -- full joined string path
- `SourceKMZ` -- originating filename
- `RawPopup` -- first 4000 chars of source popup HTML
- All popup-derived typed fields (inferred from the source schema)
- `Folder1`, `Folder2`, `Folder3` -- the last three folder segments

Names starting with a digit are prefixed with a lowercase `x` so they remain valid FGDB identifiers (e.g. `2026_BUOW_Polygons` becomes `x2026_BUOW_Polygons`). Names longer than 64 characters are truncated while preserving the geometry suffix (`_Points` / `_Polylines` / `_Polygons`).

## Scratch and re-runs

Phase 1 (KMZ -> scratch GDB) is the slow step. Phase 2 (scratch -> organized GDB) is fast. The scratch folder is **kept** after the run for re-organization without re-parsing:

- Merged container: `<output_gdb_parent>/<output_gdb_stem>_scratch/`
- Per-KMZ container: `<output_folder>/_scratch/`

To re-organize with different Waterfall answers or a different coordinate system, delete the output GDB(s) and re-run. The kept scratch folder is reused automatically.

For scripting, `kmz_tools.post_processor.post_process()` can be called directly with `fd_strategy` / `fc_strategy` constants. See `docs/USAGE.md`.

## Repository layout

```
KMZ_Tools/
├── KMZ_Tools.pyt                            # Toolbox (the two tools)
├── KMZ_Tools.KMZInspector.pyt.xml           # Hover help: Inspector
├── KMZ_Tools.KMZtoOrganizedGDB.pyt.xml      # Hover help: Converter
├── KMZ_Tools.pyt.xml                        # Toolbox-level metadata
├── KMZ_Tools_Diagram.html                   # Waterfall visual reference
├── kmz_tools/                               # Helper modules (importable as a package)
│   ├── __init__.py
│   ├── kml_parser.py
│   ├── popup_parser.py
│   ├── style_parser.py
│   ├── geometry_builder.py
│   ├── gdb_writer.py
│   ├── naming.py
│   ├── schema_inference.py
│   ├── network_loader.py
│   ├── logging_utils.py
│   ├── converter.py             # Phase 1 (KMZ -> scratch GDB)
│   ├── post_processor.py        # Phase 2 (scratch GDB -> organized GDB)
│   └── inspector.py             # Pre-flight inspection
├── docs/
│   ├── USAGE.md                 # End-user walkthrough
│   ├── QA_TEST_PLAN.md          # QA checklist
│   └── KNOWN_ISSUES.md          # Deferred items
├── README.md                    # This file
├── CHANGELOG.md
├── OWNER.md
├── CLAUDE.md
├── .gitignore
└── .gitattributes
```

## Versioning

See `CHANGELOG.md` for the version history.

## Ownership

See `OWNER.md`.
