# Known Issues

Deferred items as of `1.1.0-qa`. Each entry lists severity, scenario, current behavior, and the next step (typically: write a focused test, then decide whether to fix in a point release).

## Open

### KI-1 (low) -- `_project_geometry` does not assert source SR is WGS 1984
**Scenario:** A future caller constructs scratch FCs with a non-WGS84 SR (or no SR at all) and runs Phase 2.<br>
**Now:** `geom.projectAs()` may apply an unintended (or no) transformation; result is silently in the wrong place.<br>
**Next:** Add `assert geom.spatialReference and geom.spatialReference.factoryCode == 4326` in `_project_geometry`. Low likelihood given the current code path only writes scratch in WGS 1984.

### KI-2 (low) -- `NamingResolver` arcpy validation only kicks in once the output GDB exists
**Scenario:** First `sanitize()` calls run before the GDB is created; the `arcpy.ValidateTableName` guard inside the resolver swallows the missing-GDB exception silently.<br>
**Now:** Sanitization works correctly via the pure-Python branch; the arcpy-side double-check only catches edge cases on re-entry.<br>
**Next:** Defer `NamingResolver(gdb_path=...)` construction until after `CreateFileGDB`, or document the behavior explicitly. Not breaking.

### KI-3 (low) -- NetworkLink loader has no max-download-size or redirect cap
**Scenario:** A NetworkLink points to a 50 GB blob, or a redirect loop.<br>
**Now:** `urlopen` follows redirects unbounded and reads the full response into memory. Practical risk on private internal data is near-zero; would matter only if a malicious or broken upstream KMZ is processed.<br>
**Next:** Stream into a temp file with a `MAX_BYTES` cap (e.g. 100 MB) and a redirect counter.

### KI-4 (low) -- Coercion-failure stats not surfaced
**Scenario:** A popup field like `"date"` contains valid dates in most rows but `"see notes"` in a few. `_coerce_value` returns `None` for the bad rows but the count of coercion failures is not aggregated or logged.<br>
**Now:** Data is preserved as NULL; user has no count of how many rows lost a value.<br>
**Next:** Add a per-FC `coerce_failures` counter and a final summary line. Useful for QA audits, not a correctness bug.

### KI-5 (low) -- Traceback verbosity in GP messages
**Scenario:** Any uncaught exception in either tool's `execute()` calls `arcpy.AddError(traceback.format_exc())` in addition to the message.<br>
**Now:** Users see a stack trace in the GP messages pane. Useful for debugging, noisy for day-to-day failures.<br>
**Next:** Optional "Show full traceback on error" checkbox or hide traceback unless a `KMZ_TOOLS_DEBUG` env var is set.

### KI-6 (low) -- Inspector verbose flag is a placeholder
**Scenario:** User checks `Verbose mode` on the Inspector dialog expecting extra output.<br>
**Now:** Flag is plumbed through but `format_report(verbose=True)` produces the same output as `verbose=False`. The parameter label clarifies this with "(reserved for future per-folder detail)".<br>
**Next:** Either implement a per-folder breakdown or remove the parameter in a point release.

### KI-7 (low) -- MultiGeometry recursion depth
**Scenario:** A KMZ contains deeply nested `<MultiGeometry>` (unusual but not invalid).<br>
**Now:** `GeometryBuilder` recurses without a depth cap. Practical risk is low (real-world KMZs don't nest MultiGeometry more than 1 level).<br>
**Next:** Add `_MAX_GEOM_DEPTH = 8` guard in `geometry_builder.py`.

### KI-8 (by design) -- `.lyrx` generation removed
**Scenario:** User expects symbology output alongside FCs.<br>
**Now:** `lyrx_generator.py` was removed in `1.0.0-qa`; styling is preserved only as raw `styleUrl` references in the source. No symbology layer files are produced.<br>
**Next:** Deferred to a later milestone. Will need a fresh design pass; the v1 simple-renderer code is in git history if it ever comes back.

### KI-9 (by design) -- Coordinate system list is a hardcoded shortlist
**Scenario:** User wants to output to a CS not in the shortlist (e.g. a custom regional projection).<br>
**Now:** The CS dropdown is a curated shortlist (`COORD_SYSTEMS` in `post_processor.py`): WGS 1984 pass-through, NAD 1983 HARN State Plane WA/OR/CA, NAD 1983 UTM 9N--12N. This is intentional -- the curated list ships with verified WGS84 -> target transformation chains, which is the most common correctness trap with free CS pickers.<br>
**Workaround:** Add the desired CS string to `COORD_SYSTEMS` (and its transformation chain) and refresh the toolbox. Or run with WGS 1984 and reproject in Pro afterward.<br>
**Next:** Worth noting as a known constraint, not a defect.

### KI-10 (by design) -- Scratch folder is not auto-cleaned
**Scenario:** Output GDB is final; the `*_scratch/` folder sibling remains on disk.<br>
**Now:** Scratch is **kept intentionally** so re-runs with different Waterfall answers (or a different output CS) skip Phase 1. Disk cost is roughly the size of the parsed KMZ data.<br>
**Workaround:** Delete the `*_scratch/` folder manually once you're sure no further re-organization is needed.<br>
**Next:** Optional "Clean scratch on success" checkbox if user feedback warrants it.

### KI-11 (low) -- RawPopup 4000-char cap can truncate synthesized ExtendedData
**Scenario:** A description-less KMZ carries its attributes only in `<ExtendedData>` (`SchemaData`/`SimpleData`). Stage 1 now synthesizes a 2-column HTML table from those attributes into `RawPopup` so they survive to the output table. `RawPopup` is a `TEXT` field capped at 4000 characters.<br>
**Now:** For typical attribute sets (e.g. ~17 short fields ~= 900 chars) there is no issue. A placemark with very many fields, or fields with very long values, can exceed 4000 chars; the synthesized table is truncated mid-row on write and the trailing fields are dropped before Stage 2 re-parses them. Same cap has always applied to `<description>`-based popups.<br>
**Next:** If this surfaces in practice, carry parsed attributes between stages in a dedicated serialized field (e.g. a JSON blob) instead of round-tripping HTML through the capped `RawPopup`, removing the length limit entirely.

## Closed in 1.1.0-qa

UX pivot (Waterfall replaces A/B/C/D/F/G), per-KMZ unlocked for arbitrary FD/FC composition, auto-skip redundant source folder, FD shallow-path fallback, digit-leading `x` prefix, Output Folder direction fix, `ClearWorkspaceCache` retry, FC truncation preserves geometry suffix, hover-help XML, Waterfall diagram rebuild. See `CHANGELOG.md` for details.

## Closed in 1.0.0-qa

See `CHANGELOG.md` -> 1.0.0-qa **Fixed** section for issues that were addressed: B1, B4, B5, B6, B7, B8, B9, B10, N3, N6, N12.

## How to escalate

If QA discovers a new issue not listed here, file it as an issue on the repo with:
- A reproducible test case (KMZ + the four Waterfall answers + CS)
- The GP messages output
- Expected vs actual behavior
- Severity guess (blocker / nice-to-have / cosmetic)
