# Changelog

All notable changes to this toolbox. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- **ExtendedData attributes lost for description-less KMZs.** Placemarks that carry their attributes only in `<ExtendedData>` (`SchemaData`/`SimpleData`) with no `<description>` -- common in Esri/ArcGIS-exported KMZs -- produced output feature classes with no popup fields at all. Stage 1 parsed the ExtendedData but discarded it: `RawPopup` (the only popup carrier between stages) was built solely from `<description>`, which was empty. Stage 1 now synthesizes a 2-column HTML table from the parsed attributes into `RawPopup` when there is no description, so Stage 2 recovers the typed fields.
- **Bare-table popups parsed as empty.** `_parse_html_table` searched only descendant tables (`.//table`); when the popup HTML was a bare `<table>...</table>` with no surrounding element, lxml made the table the document root and it was missed, yielding zero fields. Now also matches a root-level table (`self::table`).
- **HARN projection crash.** Selecting any NAD83(HARN) State Plane output (Oregon, Washington, or any of the six California zones) raised `ValueError: NAD_1983_To_HARN_OR` (or `_WA` / `_CA_N` / `_CA_S`) during the organize phase. Those ArcMap-era transformation names do not exist in ArcGIS Pro's geographic-transformation set. The hardcoded transformation lists in `COORD_SYSTEMS` are removed; the WGS84 -> target transformation is now resolved at runtime via `arcpy.ListTransformations` against the real data extent (`_resolve_transformation` / `_wgs84_data_extent`), matching the default the ArcGIS Pro Project tool picks. UTM and WGS84 pass-through outputs are unaffected.

## [1.1.0-qa] - 2026-05-28

Second QA candidate. UX pivot plus a batch of correctness and ergonomics fixes on top of the 1.0.0-qa code. All 1.0.0-qa fixes (B1, B4, B5, B6, B7, B8, B9, B10, N3, N6, N12) remain in force.

### Changed
- **Waterfall UX.** The single multi-letter Mode dropdown (A/B/C/D/F/G) is gone. The converter now exposes four orthogonal questions:
  - **Q1:** Output container -- `One merged GDB` vs `One GDB per source KMZ`.
  - **Q2:** Split into Feature Datasets? -- yes / no.
  - **Q3a:** FD source (when Q2=yes) -- top folder (`segments[-3]`), top two folders combined (`segments[-3]+segments[-2]`), or source KMZ filename. Source-KMZ option is hidden when the container is already per-KMZ.
  - **Q3b:** FC naming strategy -- choice set depends on Q2:
    - FDs on: per-geometry only / leaf folder / leaf + geometry / parent + leaf + geometry / folder pair + geometry.
    - FDs off: per-geometry only / top folder + geometry / leaf + geometry / parent + leaf + geometry / folder pair + geometry.
  - Q2/Q3a/Q3b compose into `fd_strategy` (`fd_none`, `fd_top_folder`, `fd_top_two_folders`, `fd_source_kmz`, `fd_hierarchy`) and `fc_strategy` (`fc_per_geom`, `fc_leaf`, `fc_leaf_geom`, `fc_parent_leaf_geom`, `fc_pair_geom`, `fc_top_geom`) and dispatch via `post_process()`.
- **Per-KMZ container is fully composable.** It is no longer locked to per-geometry; any FD/FC strategy works inside it.
- **Digit-leading name prefix** changed from `FD_` to lowercase `x` (e.g. `2026_BUOW_Polygons` -> `x2026_BUOW_Polygons`). The `FD_` prefix was misleading -- it suggested "Feature Dataset" to readers.
- **Output Folder parameter direction** flipped from `Output` to `Input`. Fixes `ERROR 000725 "Dataset already exists"` when the user picks an existing folder, which is the normal case for a target output location.
- **`KMZ_Tools_Diagram.html`** rebuilt as a Waterfall flowchart. The old mode-grid mindmap is gone.

### Added
- **Unsupported-combination refusal.** Combinations that would produce duplicate or nonsensical names (e.g. top-folder-as-FC with top-folder-as-FD) are refused with a clear message before any GDB is created.
- **Auto-skip redundant source folder under per-KMZ.** When the container is per-KMZ, the KMZ's own root folder is stripped from `folder_segments` so it doesn't waste an FD level.
- **FD shallow-path fallback.** When `fd_top_folder` runs against a KMZ whose paths are shallow (<3 segments), the strategy falls back to `segs[0]` so a single FD is produced rather than dropping the row to the GDB root.
- **Catalog cache retry on FC creation.** `ClearWorkspaceCache` plus a short retry loop after `CreateFeatureclass`. Combats network-drive catalog lag that surfaced as `ERROR 000732 "Feature class not visible"` on slow shares.
- **FC name truncation preserves geometry suffix.** When a candidate FC name exceeds the 64-char FGDB limit, truncation now keeps the `_Points`/`_Polylines`/`_Polygons` suffix intact so geometry remains visually identifiable in the GDB.
- **Hover-help XML for all parameters.** Authored in the CrownDelineation style (`<DIV STYLE="text-align:Left;"><P><SPAN>` pattern, escaped HTML inside `<dialogReference>`).

### Removed
- Mode codes A / B / C / D / F / G as a user-facing concept. Their semantics are now reachable as Waterfall combinations.

## [1.0.0-qa] - 2026-05-22

First QA candidate. Source for this release is the cleaned subset of the sandbox toolbox at
`U:\GIS\Tools\03_Sandbox\CS\KMZ_Tool\`, reduced to two user-facing tools and reviewed for
correctness before handoff.

### Added
- **`KMZInspector` tool** -- preflight inspection. Reads KMZ/KML files without writing
  anything; reports placemark / folder / style / NetworkLink counts, popup parse-tier
  breakdown, and flags conversion concerns.
- **`KMZtoOrganizedGDB` tool** -- one-step converter (KMZs -> organized GDB(s)) with
  six organization modes (A, B, C, D, F, G; E reserved) and a curated coordinate-system
  shortlist (WGS 1984, NAD 1983 HARN State Plane WA/OR/CA, NAD 1983 UTM 9N--12N) with
  correct WGS84 -> target transformation chains applied automatically.
- **Mode D** (FD per KMZ + Folder-Combo FCs). Same FD shape as Mode C but FC names split
  further by the (3rd-to-last + 2nd-to-last) folder pair; the leaf folder lives in `Folder3`.
- **Mode G** (One GDB per KMZ, Flat + Parsed). Produces N separate output GDBs, one per
  source KMZ, with isolated per-source schemas (no cross-KMZ field merging).
- **`KMZ_Tools_Diagram.html`** -- single-page mode-grid visual reference.
- Per-tool metadata XMLs (`*.pyt.xml`) populated with parameter hover help.
- `docs/USAGE.md`, `docs/QA_TEST_PLAN.md`, `docs/KNOWN_ISSUES.md`.

### Fixed (relative to sandbox)

These issues were caught in the pre-release red-team review:

- **B7 -- Zip Slip guard.** `KMLParser.extract_and_parse` validates every zip-member path
  before extraction; a malicious KMZ with `../` entries is rejected with a clear error.
- **B8 -- doc.kml fallback.** If a KMZ archive does not contain `doc.kml`, the parser
  falls back to the largest `.kml` at the shallowest depth.
- **B9 -- silent geometry-less drop visibility.** `KMLParser.get_placemarks_with_stats`
  returns a `dropped_no_geometry` count; the converter logs the total per file, and the
  All-in-One tool surfaces a cross-source warning if any drops occurred.
- **B5 -- re-run guard.** `post_process` refuses to write into an existing output GDB
  with a clear message.
- **B4 -- cross-iteration FC name dedup.** Name collision resolver uses a function-scope
  `used_names` so a disambiguated name from one collision bucket cannot collide with the
  singleton candidate of a different bucket.
- **B6 -- scratch GDB stem collision.** Two source KMZs whose stems sanitize to the same
  form (`Foo Bar.kmz` and `Foo_Bar.kmz`) no longer silently overwrite each other in the
  scratch folder; a numeric suffix is appended and the rename is logged.
- **B10 -- report file extension normalization.** Inspector tool forces `.txt` on the
  report path if a different extension was provided.
- **B1 -- coordinate-system validation.** The per-KMZ container path validates
  `target_coord_system` against `COORD_SYSTEMS` before entering the per-source loop;
  a bad CS string used to surface as "0 GDBs written" instead of an immediate abort.
- **N6 -- LONG decimal truncation guard.** `_coerce_value` no longer silently truncates
  `"3.14"` to `3` for LONG fields; returns `None` (NULL) to preserve correctness.
- **N3 -- ExtendedData XPath tightening.** Switched from descendant (`.//`) to direct
  child axis so a Placemark containing a child Folder cannot pollute the parent's
  ExtendedData attrs.
- **N12 -- bare except.** Two `except:` clauses (one in `kml_parser._get_text`, one in
  `naming.NamingResolver.sanitize`) tightened to `except Exception:`.
- **Converter folder layout.** Removed the implicit `Scratch/` subfolder the converter
  created inside its output folder. Output GDBs are written directly to the
  caller-supplied folder. Avoids double-nesting under the All-in-One tool.

### Removed
- `lyrx_generator.py` -- not reachable from the two shipping tools; removed to shrink
  the QA surface.
- The two helper tools (`KMZtoGDB`, `OrganizeGDB`) from the sandbox toolbox. The underlying
  functions remain in `kmz_tools.converter` and `kmz_tools.post_processor` for power-user
  scripting.

### Known issues
See `docs/KNOWN_ISSUES.md` for items deferred from the red-team review.
