# Claude Code guidance for KMZ Tools

This file orients Claude Code (and other AI coding agents) for work in this
repo. Keep it short. For deeper context, see `README.md`, `docs/USAGE.md`,
and `KMZ_Tools_Diagram.html`.

## What this is

ArcGIS Pro Python toolbox (`.pyt`) with two tools:
1. `KMZInspector` -- read-only preflight inspection of KMZ/KML files.
2. `KMZtoOrganizedGDB` -- KMZ/KML -> organized File Geodatabase. Waterfall
   UX with four orthogonal layout questions (container, split-FDs, FD
   source, FC naming) composed into FD/FC strategy constants.

All conversion logic lives in the `kmz_tools/` package; the `.pyt` is a thin
ArcGIS UI wrapper.

## Repo layout (governance v1.3 compliant)

```
Final/
├── KMZ_Tools.pyt                          # Tool entry point (kept at root for ArcGIS Pro folder connection)
├── KMZ_Tools.*.pyt.xml                    # Per-tool hover-help metadata
├── KMZ_Tools_Diagram.html                 # Waterfall flowchart (visual reference)
├── kmz_tools/                             # Engine (importable package)
├── docs/                                  # USAGE.md, QA_TEST_PLAN.md, KNOWN_ISSUES.md
├── README.md / CHANGELOG.md / OWNER.md / LICENSE
└── .claude/                               # Agent-specific scratch (gitignored where appropriate)
```

The `.pyt` and `kmz_tools/` together fill the role the governance doc's
generic `src/` placeholder describes. ArcGIS Pro requires the `.pyt` at a
known folder-connection root, so it is not moved into a subfolder.

## Conventions

- **No emoji, no em-dashes.** Use `--` if you need a dash. Match the
  existing tone in code comments and docs (terse, technical, opinionated).
- **Never write `*.md` docs unless asked.** README/CHANGELOG/etc. are
  user-owned. Edit existing ones; do not invent new top-level docs.
- **Hover-help XML** uses the CrownDelineation pattern: escaped HTML
  inside `<dialogReference>` (`<DIV STYLE="text-align:Left;"><P><SPAN>...`).
- **FGDB name sanitization** (`kmz_tools/naming.py`): digit-leading names
  get an `x` prefix (e.g. `2026_BUOW` -> `x2026_BUOW`). Do not change to
  `FD_` / `T_` / `_` -- that decision has been litigated.
- **FC names cap at 64 chars** inside Feature Datasets; geometry suffix
  (`_Points`/`_Polylines`/`_Polygons`) must be preserved on truncation.
- **`ClearWorkspaceCache` + retry** after `CreateFeatureclass` is required
  on network drives (combats catalog lag, ERROR 000732). Do not remove.
- **Output Folder param** uses `direction="Input"`, not `"Output"` --
  arcpy's Output direction enforces non-existence and raises ERROR 000725
  on existing folders. Counter-intuitive but correct.

## What lives where

| Concern | File |
|---|---|
| KML/KMZ parsing (lxml recovery, Zip-Slip guard, NetworkLink follow) | `kml_parser.py` + `network_loader.py` |
| Popup HTML -> typed fields (3-tier: extended_data / html_table / label_value) | `popup_parser.py` |
| Style + StyleMap resolution | `style_parser.py` |
| Geometry building (MultiGeometry normalization) | `geometry_builder.py` |
| Schema inference for popup attrs | `schema_inference.py` |
| FD/FC name resolution + sanitization | `naming.py` |
| Stage 1: KMZ -> scratch GDB | `converter.py` |
| Stage 2: scratch -> organized GDB; FD/FC strategy dispatch | `post_processor.py` |
| Inspector engine | `inspector.py` |
| arcpy FC creation, field schema, ClearWorkspaceCache retry | `gdb_writer.py` |

## Promotion status

Currently QA-tier candidate (`02_QA`). Bumped from sandbox via 1.1.0-qa
release. Promotion to Prod requires the QA->Prod gate in
`ESA_GIS_Tools_Governance_v1.3.docx`.
