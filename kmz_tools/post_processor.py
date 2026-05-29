# -*- coding: utf-8 -*-
"""Phase 2: Post-process raw scratch GDBs into an organized final GDB.

Reads the raw GDBs produced by Phase 1 (Scratch folder with Point/Polyline/
Polygon FCs containing Name, FolderPath, RawPopup, SourceKMZ fields) and:
  - Reshapes the data into one of four organization modes
  - Parses RawPopup HTML into individual attribute fields
  - Applies name sanitization
  - Infers field types
  - Writes to a single organized output GDB

Organization modes
------------------
hierarchy
    Folders 1..N-2 -> FD, last folder -> FC name (with collision
    disambiguation). No Folder1/2/3 columns. (Original behavior.)

fd_fields
    Third-to-last folder segment -> FD; geometries collapse to per-geometry
    FCs (Points/Polylines/Polygons) inside each FD. Folder1/2/3 columns hold
    the last 3 segments, right-aligned (Folder3 = deepest).

flat
    No FDs. One FC per geometry at the GDB root. Folder1/2/3 columns hold
    the last 3 segments.

per_kmz
    Source KMZ filename -> FD; per-geometry FCs inside. Folder1/2/3 columns
    hold the last 3 segments.

The 3-segment rule applies uniformly: any folder context beyond the last 3
segments is preserved only in the joined FolderPath text field.
"""

from pathlib import Path
from collections import defaultdict
from datetime import datetime

import arcpy

from .popup_parser import PopupParser
from .naming import NamingResolver, suffix_for_geometry_type
from .schema_inference import SchemaInferencer
from .gdb_writer import SR_WGS84


# Map raw scratch FC names -> KML geometry type
SCRATCH_FC_TO_GEOM = {
    "Point": "Point",
    "Polyline": "LineString",
    "Polygon": "Polygon",
}

# Map KML geometry type -> arcpy CreateFeatureclass geometry token
GEOM_TO_ARCPY = {
    "Point": "POINT",
    "LineString": "POLYLINE",
    "Polygon": "POLYGON",
}

# Maximum FC name length when an FC lives inside a file-GDB Feature Dataset.
# File GDBs nominally allow 160 chars, but in-FD FCs are silently truncated
# at 64 by arcpy in current Pro versions. We cap proactively so the
# truncation lands on a clean boundary and preserves the geometry suffix.
_FC_NAME_MAX_LEN = 64


def _truncate_fc_name(name, suffix=""):
    """Cap an FC name at _FC_NAME_MAX_LEN, preserving the trailing suffix.

    If `name` already ends with `suffix` and the combined length exceeds
    the cap, the LEADING portion is truncated -- not the suffix. Trailing
    underscores in the truncated prefix are stripped so the result reads
    cleanly (no `Foo__Polygons`).

    Examples (cap=64):
        _truncate_fc_name("short_Polygons", "_Polygons")
            -> "short_Polygons"
        _truncate_fc_name(
            "CCWD_HAZMAT...AntiochLandfill_Polygons", "_Polygons"
        ) -> "CCWD_HAZMAT...AntiochLan_Polygons"   # suffix intact, leaf clipped
    """
    if len(name) <= _FC_NAME_MAX_LEN:
        return name
    if suffix and name.endswith(suffix):
        keep_prefix = _FC_NAME_MAX_LEN - len(suffix)
        if keep_prefix > 0:
            return name[:keep_prefix].rstrip("_") + suffix
    # No suffix to preserve (or suffix doesn't fit) -- plain truncate.
    return name[:_FC_NAME_MAX_LEN].rstrip("_")


# Per-geometry FC base name used by non-hierarchy modes
GEOM_TO_FC_BASE = {
    "Point": "Points",
    "LineString": "Polylines",
    "Polygon": "Polygons",
}

# Organization modes
MODE_HIERARCHY = "hierarchy"
MODE_FD_FIELDS = "fd_fields"
MODE_FLAT = "flat"
MODE_PER_KMZ = "per_kmz"
# Same FD shape as MODE_PER_KMZ (FD per source KMZ), but FC names additionally
# split by the (segments[-3], segments[-2]) folder pair so the folder context
# travels into the FC name. The leaf folder (segments[-1]) lives in Folder3.
# Placemarks with fewer than 3 folder segments fall back to geometry-only FC.
MODE_KMZ_FD_FOLDER_FC = "kmz_fd_folder_fc"
# Like MODE_FD_FIELDS (top folder = segments[-3]) but with NO Feature Dataset
# wrapper -- the FC sits at the GDB root and carries the top-folder name
# directly (e.g. "Birds_2026_Points"). Folder2 / Folder3 distinguish rows
# within. Used by the experimental waterfall tool to expose the "FD off but
# top-folder still drives FC name" combination.
MODE_TOP_FOLDER_FC = "top_folder_fc"
# Produces one full GDB per source KMZ (not just an FD inside a shared GDB).
# Each output GDB has flat Points/Polylines/Polygons FCs with folder fields
# and parsed popups -- same shape as MODE_FLAT, but isolated per KMZ.
MODE_PER_KMZ_GDB = "per_kmz_gdb"

VALID_MODES = (
    MODE_HIERARCHY,
    MODE_FD_FIELDS,
    MODE_FLAT,
    MODE_PER_KMZ,
    MODE_KMZ_FD_FOLDER_FC,
    MODE_TOP_FOLDER_FC,
    MODE_PER_KMZ_GDB,
)


# ---------------------------------------------------------------------------
# Strategy-based dispatch (used by the experimental Waterfall tool).
# Legacy mode constants above bundle a (FD source, FC naming) pair into one
# name. The strategy constants below let callers compose any combination of
# FD source and FC naming -- needed once the user-facing UI starts asking
# for those choices independently (the Waterfall does this).
# Legacy code paths keep working via _MODE_TO_STRATEGIES below.
# ---------------------------------------------------------------------------

# FD source strategies
FD_STRATEGY_NONE = "fd_none"                  # No Feature Dataset; FCs at GDB root
FD_STRATEGY_TOP_FOLDER = "fd_top_folder"      # FD = segments[-3]
FD_STRATEGY_TOP_TWO = "fd_top_two_folders"    # FD = segments[-3] + "_" + segments[-2]
FD_STRATEGY_SOURCE_KMZ = "fd_source_kmz"      # FD = Path(source_kmz).stem
FD_STRATEGY_HIERARCHY = "fd_hierarchy"        # Legacy Mode A naming-resolver path
                                              # (returns both FD and FC together)

# FC naming strategies
FC_STRATEGY_PER_GEOM = "fc_per_geom"                  # "Points" / "Polylines" / "Polygons"
FC_STRATEGY_LEAF = "fc_leaf"                          # segments[-1] (Mode A behavior)
FC_STRATEGY_LEAF_GEOM = "fc_leaf_geom"                # "<leaf>_<geom>"
FC_STRATEGY_PARENT_LEAF_GEOM = "fc_parent_leaf_geom"  # "<parent>_<leaf>_<geom>"
FC_STRATEGY_TOP_GEOM = "fc_top_geom"                  # "<top>_<geom>"
FC_STRATEGY_PAIR_GEOM = "fc_pair_geom"                # "<top>_<parent>_<geom>"
FC_STRATEGY_FULL_PATH_GEOM = "fc_full_path_geom"      # "<top>_<parent>_<leaf>_<geom>"

# Map legacy mode -> (fd_strategy, fc_strategy). MODE_PER_KMZ_GDB has its
# own top-level dispatch in post_process() and is not represented here.
_MODE_TO_STRATEGIES = {
    MODE_HIERARCHY:        (FD_STRATEGY_HIERARCHY,  FC_STRATEGY_LEAF),
    MODE_FD_FIELDS:        (FD_STRATEGY_TOP_FOLDER, FC_STRATEGY_PER_GEOM),
    MODE_FLAT:             (FD_STRATEGY_NONE,       FC_STRATEGY_PER_GEOM),
    MODE_PER_KMZ:          (FD_STRATEGY_SOURCE_KMZ, FC_STRATEGY_PER_GEOM),
    MODE_KMZ_FD_FOLDER_FC: (FD_STRATEGY_SOURCE_KMZ, FC_STRATEGY_PAIR_GEOM),
    MODE_TOP_FOLDER_FC:    (FD_STRATEGY_NONE,       FC_STRATEGY_TOP_GEOM),
}

# FC strategies whose names already encode enough folder context that
# prepending the FD name would be redundant (and would make names long).
_FC_STRATEGIES_WITH_FOLDER_CONTEXT = {
    FC_STRATEGY_LEAF_GEOM,
    FC_STRATEGY_PARENT_LEAF_GEOM,
    FC_STRATEGY_TOP_GEOM,
    FC_STRATEGY_PAIR_GEOM,
    FC_STRATEGY_FULL_PATH_GEOM,
}

# Folder-field column names (right-aligned, Folder3 = deepest)
FOLDER_FIELD_NAMES = ("Folder1", "Folder2", "Folder3")

# Fields the pipeline writes itself; popup attributes that sanitize to one of
# these names get an "attr_" prefix to avoid collisions.
SYSTEM_FIELD_NAMES = {
    "Name", "FolderPath", "RawPopup", "SourceKMZ",
    "Folder1", "Folder2", "Folder3",
    "OBJECTID", "Shape",
}

# Source SR for all scratch FCs written by Tool 1.
SR_WGS84_WKID = 4326  # GCS_WGS_1984 (EPSG:4326)

# Output coordinate-system shortlist exposed in the UI. Each entry maps a
# human-readable label to its target WKID. The WGS84 -> target datum
# transformation is NOT hardcoded: it is resolved at runtime via
# arcpy.ListTransformations against the real data extent (see
# _resolve_transformation), which mirrors what the ArcGIS Pro Project tool
# picks by default and adapts to the install's transformation set.
#
# History: earlier revisions hardcoded ArcMap-era names like
# "NAD_1983_To_HARN_OR" / "NAD_1983_To_HARN_WA". Those names do not exist in
# ArcGIS Pro's geographic-transformation set (the real names follow the
# "NAD_1983_To_NAD_1983_HARN_<n>" pattern), so projectAs raised
# "ValueError: NAD_1983_To_HARN_OR" and every HARN target was unusable.
COORD_SYSTEMS = {
    # Pass-through (scratch is already WGS84)
    "WGS 1984 (no reproject)": {"wkid": 4326},

    # Washington State Plane NAD83(HARN), US Survey Feet
    "NAD 1983 HARN StatePlane Washington North (US Feet)": {"wkid": 2926},
    "NAD 1983 HARN StatePlane Washington South (US Feet)": {"wkid": 2927},

    # Oregon State Plane NAD83(HARN), International Feet
    "NAD 1983 HARN StatePlane Oregon North (Intl Feet)": {"wkid": 2992},
    "NAD 1983 HARN StatePlane Oregon South (Intl Feet)": {"wkid": 2993},

    # California State Plane NAD83(HARN), US Survey Feet
    "NAD 1983 HARN StatePlane California I (US Feet)": {"wkid": 2870},
    "NAD 1983 HARN StatePlane California II (US Feet)": {"wkid": 2871},
    "NAD 1983 HARN StatePlane California III (US Feet)": {"wkid": 2872},
    "NAD 1983 HARN StatePlane California IV (US Feet)": {"wkid": 2873},
    "NAD 1983 HARN StatePlane California V (US Feet)": {"wkid": 2874},
    "NAD 1983 HARN StatePlane California VI (US Feet)": {"wkid": 2875},

    # UTM NAD83 (plain) - meters. HARN UTM is per-state and uncommon.
    "NAD 1983 UTM Zone 9N (meters)": {"wkid": 26909},
    "NAD 1983 UTM Zone 10N (meters)": {"wkid": 26910},
    "NAD 1983 UTM Zone 11N (meters)": {"wkid": 26911},
    "NAD 1983 UTM Zone 12N (meters)": {"wkid": 26912},
}

DEFAULT_COORD_SYSTEM = "WGS 1984 (no reproject)"


def post_process(
    scratch_folder,
    output_gdb_path,
    mode=MODE_HIERARCHY,
    target_coord_system=DEFAULT_COORD_SYSTEM,
    log=print,
    _source_kmz_filter=None,
    fd_strategy=None,
    fc_strategy=None,
):
    """Organize raw scratch GDBs into a single final GDB.

    scratch_folder: folder containing raw GDBs from Phase 1
    output_gdb_path: path to the output GDB to create. For MODE_PER_KMZ_GDB,
        this is treated as a folder destination (the .gdb stem is ignored;
        the parent folder receives one GDB per source KMZ).
    mode: one of MODE_HIERARCHY, MODE_FD_FIELDS, MODE_FLAT, MODE_PER_KMZ,
        MODE_PER_KMZ_GDB
    target_coord_system: a key from COORD_SYSTEMS. Geometries are projected
        from WGS84 (the scratch SR) to this SR before insert. FDs/FCs are
        created in this SR. Default is WGS 1984 (pass-through).
    log: callable taking a single string
    _source_kmz_filter: internal use. If set, only raw GDBs whose stem matches
        this string are processed. Used by the Mode E dispatcher to scope each
        per-KMZ run to a single source.

    Returns dict: {'output_gdb', 'feature_classes': int, 'features': int}.
    For MODE_PER_KMZ_GDB additionally returns 'output_gdbs': list[str].
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"Unknown organization mode '{mode}'; expected one of {VALID_MODES}"
        )

    # Mode E: dispatch to the per-KMZ-GDB orchestrator and return early.
    if mode == MODE_PER_KMZ_GDB:
        # Pass any caller-supplied strategies through so per-KMZ container
        # can produce arbitrary FD/FC layouts inside each per-KMZ GDB
        # (not just flat-per-geometry, which was the historical default).
        return _post_process_per_kmz_gdb(
            scratch_folder, output_gdb_path, target_coord_system, log,
            fd_strategy=fd_strategy, fc_strategy=fc_strategy,
        )

    if target_coord_system not in COORD_SYSTEMS:
        raise ValueError(
            f"Unknown coordinate system '{target_coord_system}'; "
            f"expected one of {list(COORD_SYSTEMS)}"
        )
    cs_info = COORD_SYSTEMS[target_coord_system]
    target_sr = arcpy.SpatialReference(cs_info["wkid"])
    # The WGS84 -> target transformation is resolved below, after rows are
    # collected, so arcpy.ListTransformations can rank candidates against the
    # true data extent. Empty string means "no datum transformation needed".
    transformation = ""

    scratch_folder = Path(scratch_folder)
    output_gdb_path = Path(output_gdb_path)

    if not scratch_folder.exists():
        raise FileNotFoundError(f"Scratch folder not found: {scratch_folder}")

    raw_gdbs = sorted(
        [p for p in scratch_folder.iterdir() if p.suffix.lower() == ".gdb"]
    )
    if _source_kmz_filter:
        raw_gdbs = [p for p in raw_gdbs if p.stem == _source_kmz_filter]
        if not raw_gdbs:
            raise RuntimeError(
                f"No .gdb in {scratch_folder} matches filter '{_source_kmz_filter}'"
            )
    if not raw_gdbs:
        raise RuntimeError(f"No .gdb folders found in {scratch_folder}")

    log(f"Found {len(raw_gdbs)} raw GDB(s) in scratch folder")
    # Show the effective strategy pair, since callers using strategy kwargs
    # would otherwise see "Organization mode: hierarchy" (the unmodified
    # default) even though the strategies are doing the real work.
    if fd_strategy is not None and fc_strategy is not None:
        log(f"Organization strategies: fd={fd_strategy}, fc={fc_strategy}")
    else:
        log(f"Organization mode: {mode}")
    log(f"Output coordinate system: {target_coord_system}")

    if arcpy.Exists(str(output_gdb_path)):
        # Refuse to write into an existing GDB. Schema reconciliation across
        # re-runs is not supported and silently mixing old + new content is a
        # data-correctness risk.
        raise RuntimeError(
            f"Output GDB already exists: {output_gdb_path}\n"
            f"Delete it (or pick a new path) and re-run. Re-organizing without "
            f"re-parsing? Run OrganizeGDB against the kept scratch folder instead."
        )

    log(f"Creating output GDB: {output_gdb_path}")
    arcpy.management.CreateFileGDB(
        str(output_gdb_path.parent),
        output_gdb_path.stem,
    )

    if not arcpy.Exists(str(output_gdb_path)):
        raise RuntimeError(
            f"Failed to create output GDB: {output_gdb_path}\n"
            f"{arcpy.GetMessages(2)}"
        )

    popup_parser = PopupParser()
    naming_resolver = NamingResolver(gdb_path=str(output_gdb_path))
    schema_inferencer = SchemaInferencer()

    # Phase 1: collect rows (mode-agnostic flat list)
    all_rows = []
    for raw_gdb in raw_gdbs:
        log(f"\nReading {raw_gdb.name}...")
        _collect_rows(str(raw_gdb), all_rows, popup_parser, log)
    log(f"\nCollected {len(all_rows)} total rows")

    # Resolve the WGS84 -> target datum transformation now that the data is in
    # hand, so ListTransformations can rank candidates against the real extent.
    if target_sr.factoryCode == SR_WGS84_WKID:
        log("No reprojection (WGS84 pass-through)")
    else:
        data_extent = _wgs84_data_extent(all_rows)
        transformation = _resolve_transformation(target_sr, data_extent)
        if transformation:
            log(f"WGS84 -> {target_coord_system}: transformation '{transformation}'")
        else:
            log(
                f"WGS84 -> {target_coord_system}: projecting with no datum "
                f"transformation (same datum / pure projection change)"
            )

    # Per-KMZ context refinement: when we're inside the per-KMZ orchestrator
    # (_source_kmz_filter is set), the per-KMZ GDB itself already represents
    # the source KMZ. Many KMZs have a root folder named after the KMZ --
    # e.g. "CP2-3 Nesting Birds.kmz" with a root folder "CP2-3 Nesting Birds"
    # -- which makes the FD source "Top folder" pick a single redundant
    # level that lumps everything together. Strip that redundant top
    # segment from each row's folder_segments so the LAYOUT decisions (3a,
    # 3b) reflect the meaningful sub-structure (e.g. 2026 BUOW, 2026 SWHA).
    if _source_kmz_filter and all_rows:
        sanitized_kmz = naming_resolver.sanitize(_source_kmz_filter)
        stripped = 0
        for row in all_rows:
            segs = row.get("folder_segments") or []
            if segs and naming_resolver.sanitize(segs[0]) == sanitized_kmz:
                row["folder_segments"] = segs[1:]
                stripped += 1
        if stripped:
            log(
                f"  (Per-KMZ context: stripped redundant top folder "
                f"'{_source_kmz_filter}' from {stripped} row(s) so the layout "
                f"reflects sub-folders below it.)"
            )

    # Phase 2: group by mode-or-strategy and annotate each row with folder1/2/3
    groups = _group_rows(
        all_rows, mode, naming_resolver,
        fd_strategy=fd_strategy, fc_strategy=fc_strategy,
    )
    # Resolve which strategy pair is actually in effect (for logging + the
    # FC-name resolver branch below). If the caller passed strategies, use
    # those; otherwise fall back to the legacy mode mapping.
    if fd_strategy is None or fc_strategy is None:
        eff_fd_strategy, eff_fc_strategy = _MODE_TO_STRATEGIES.get(
            mode, (None, None)
        )
    else:
        eff_fd_strategy, eff_fc_strategy = fd_strategy, fc_strategy
    log(
        f"Grouped into {len(groups)} feature class buckets "
        f"(fd={eff_fd_strategy}, fc={eff_fc_strategy})"
    )

    # Phase 3: resolve final FC names.
    # MODE_HIERARCHY / FD_STRATEGY_HIERARCHY uses the collision resolver
    # (Mode A's "leaf folder name + geom suffix on collision" behavior).
    # Everything else uses the simple-prefix resolver, which now knows to
    # skip the FD prefix when the FC base already encodes folder context.
    if eff_fd_strategy == FD_STRATEGY_HIERARCHY:
        final_fc_names = _resolve_fc_name_collisions(groups, naming_resolver, log)
    else:
        final_fc_names = _compute_simple_fc_names(
            groups, naming_resolver, mode=mode, fc_strategy=eff_fc_strategy,
        )

    include_folder_fields = (eff_fd_strategy != FD_STRATEGY_HIERARCHY)

    # Phase 4: create FCs and insert
    total_features = 0
    total_fcs = 0

    for (fd_name, fc_name, geom_type), rows in groups.items():
        sanitized_fc = final_fc_names[(fd_name, fc_name, geom_type)]
        sanitized_fd = naming_resolver.sanitize(fd_name) if fd_name else None

        # Infer popup schema for this bucket
        popup_attrs_per_row = [r["popup_attrs"] for r in rows]
        attr_schema = schema_inferencer.infer_field_types(popup_attrs_per_row)

        # Resolve target parent (FD or GDB root)
        if sanitized_fd:
            fd_path = str(output_gdb_path / sanitized_fd)
            if not arcpy.Exists(fd_path):
                arcpy.management.CreateFeatureDataset(
                    str(output_gdb_path), sanitized_fd, target_sr
                )
            parent = fd_path
        else:
            parent = str(output_gdb_path)

        fc_path = str(Path(parent) / sanitized_fc)

        log(f"\nCreating {sanitized_fd or '(root)'}/{sanitized_fc} "
            f"({len(rows)} features, {len(attr_schema)} popup fields)")

        if not arcpy.Exists(fc_path):
            # Capture the actual returned path -- arcpy may silently rename
            # the FC during internal name validation (we've seen this happen
            # when many similar suffixed names already exist in the GDB).
            # AddField + InsertCursor must use the post-rename path or they
            # error with "Dataset does not exist".
            create_result = arcpy.management.CreateFeatureclass(
                parent,
                sanitized_fc,
                geometry_type=GEOM_TO_ARCPY[geom_type],
                spatial_reference=target_sr,
            )
            try:
                actual_path = create_result.getOutput(0)
            except Exception:
                actual_path = None
            if actual_path and Path(actual_path).name != sanitized_fc:
                log(
                    f"  [NOTE] arcpy renamed FC: "
                    f"{sanitized_fc} -> {Path(actual_path).name}"
                )
                fc_path = actual_path
            elif actual_path and actual_path != fc_path:
                # Same FC name, but the path arcpy returned differs from what
                # we computed (e.g. case normalization). Trust arcpy's path.
                fc_path = actual_path

            # Network-drive workaround: arcpy's in-memory workspace catalog
            # can briefly miss a newly-created FC on slower filesystems
            # (especially \\share or U:\ drives). Without this clear, the
            # next AddField call sometimes fails with
            # "ERROR 000732: Dataset does not exist or is not supported"
            # even though the FC is physically on disk.
            try:
                arcpy.management.ClearWorkspaceCache(str(output_gdb_path))
            except Exception:
                pass

            # Defensive sanity check with brief retry. If even ClearWorkspaceCache
            # didn't help, try once more with a short pause -- network filesystems
            # occasionally need a moment to commit the new directory entry.
            import time as _time
            for _retry in range(3):
                if arcpy.Exists(fc_path):
                    break
                _time.sleep(0.5 * (_retry + 1))
                try:
                    arcpy.management.ClearWorkspaceCache(str(output_gdb_path))
                except Exception:
                    pass
            else:
                raise RuntimeError(
                    f"CreateFeatureclass returned but the FC isn't visible "
                    f"at {fc_path} after retries. "
                    f"GP messages: {arcpy.GetMessages(2) or '(none)'}"
                )

            # System fields (always present)
            arcpy.management.AddField(fc_path, "Name", "TEXT", field_length=255)
            arcpy.management.AddField(fc_path, "FolderPath", "TEXT", field_length=500)
            arcpy.management.AddField(fc_path, "SourceKMZ", "TEXT", field_length=255)

            # Folder1/2/3 (non-hierarchy modes only)
            if include_folder_fields:
                for fname in FOLDER_FIELD_NAMES:
                    arcpy.management.AddField(
                        fc_path, fname, "TEXT", field_length=255
                    )

            arcpy.management.AddField(fc_path, "RawPopup", "TEXT", field_length=4000)

            # Popup-attribute fields (sanitized names, collision-protected)
            popup_field_map = {}
            for attr_name, field_info in attr_schema.items():
                sanitized_field = naming_resolver.sanitize(attr_name)[:60]
                if sanitized_field in SYSTEM_FIELD_NAMES:
                    sanitized_field = "attr_" + sanitized_field
                popup_field_map[attr_name] = sanitized_field

                ftype = field_info["type"]
                kwargs = {"field_type": ftype}
                if ftype == "TEXT":
                    kwargs["field_length"] = field_info.get("length") or 255
                arcpy.management.AddField(fc_path, sanitized_field, **kwargs)
        else:
            # FC already exists (re-running) — just look up sanitized names
            popup_field_map = {
                a: naming_resolver.sanitize(a)[:60] for a in attr_schema
            }

        # Build cursor field list in the same order we'll push values
        cursor_fields = ["SHAPE@", "Name", "FolderPath", "SourceKMZ"]
        if include_folder_fields:
            cursor_fields.extend(FOLDER_FIELD_NAMES)
        cursor_fields.append("RawPopup")
        popup_field_order = list(attr_schema.keys())
        cursor_fields.extend(popup_field_map[a] for a in popup_field_order)

        # Pre-resolve coercion specs once per FC
        popup_field_specs = [
            (
                attr_name,
                attr_schema[attr_name]["type"],
                attr_schema[attr_name].get("length"),
            )
            for attr_name in popup_field_order
        ]

        # Insert
        try:
            with arcpy.da.InsertCursor(fc_path, cursor_fields) as cursor:
                for row in rows:
                    geom = _project_geometry(
                        row["geometry"], target_sr, transformation
                    )
                    values = [
                        geom,
                        row.get("name", "") or "",
                        row.get("folder_path", "") or "",
                        row.get("source_kmz", "") or "",
                    ]
                    if include_folder_fields:
                        values.append(row.get("folder1"))
                        values.append(row.get("folder2"))
                        values.append(row.get("folder3"))
                    values.append((row.get("raw_popup", "") or "")[:4000])

                    popup_attrs = row.get("popup_attrs", {})
                    for attr_name, ftype, flen in popup_field_specs:
                        values.append(
                            _coerce_value(popup_attrs.get(attr_name), ftype, flen)
                        )
                    cursor.insertRow(values)
        except arcpy.ExecuteError:
            raise RuntimeError(
                f"Insert failed for {fc_path}:\n{arcpy.GetMessages(2)}"
            )

        total_features += len(rows)
        total_fcs += 1

    log(
        f"\n[POST-PROCESS DONE] {total_fcs} feature classes, "
        f"{total_features} features"
    )

    return {
        "output_gdb": str(output_gdb_path),
        "feature_classes": total_fcs,
        "features": total_features,
    }


def _post_process_per_kmz_gdb(
    scratch_folder, output_dest, target_coord_system, log,
    inner_mode=None, fd_strategy=None, fc_strategy=None,
):
    """Per-KMZ-container orchestrator: one organized GDB per source KMZ.

    Treats `output_dest` as a folder destination. If the caller passed a
    .gdb path (as the tool dialogs do), the .gdb's parent folder is used
    and its stem is ignored. The destination folder is created if missing.

    Each raw scratch GDB (whose stem is the sanitized source KMZ name) is
    organized into <dest>/<kmz_stem>.gdb using either:
      - the legacy `inner_mode` (defaults to MODE_FLAT for backward compat
        with the older "Mode G = flat per KMZ" semantics), OR
      - the explicit (fd_strategy, fc_strategy) pair (preferred path; lets
        the Waterfall tool offer the full set of FD/FC choices inside each
        per-KMZ GDB).

    Source-KMZ FD strategy is intentionally NOT supported here -- inside a
    per-KMZ GDB the GDB IS the source, so "FD per source" would just yield
    one redundant FD wrapping everything. The Waterfall UI filters that
    option out of its dropdown for this container choice.

    Each output GDB gets its own schema inferred ONLY from its own popups
    (no cross-source field merging).
    """
    scratch_folder = Path(scratch_folder)
    output_dest = Path(output_dest)

    # If the user picked a .gdb path (the usual case from the tool dialog),
    # use its parent folder as the destination.
    if output_dest.suffix.lower() == ".gdb":
        log(
            f"Mode G (per-KMZ container): ignoring '{output_dest.name}' "
            f"filename; using parent folder as destination"
        )
        dest_folder = output_dest.parent
    else:
        dest_folder = output_dest
    dest_folder.mkdir(parents=True, exist_ok=True)

    raw_gdbs = sorted(
        [p for p in scratch_folder.iterdir() if p.suffix.lower() == ".gdb"]
    )
    if not raw_gdbs:
        raise RuntimeError(f"No .gdb folders found in {scratch_folder}")

    log(f"Per-KMZ container: one GDB per source KMZ ({len(raw_gdbs)} sources)")
    log(f"Destination folder: {dest_folder}")
    # Decide what we're applying inside each per-KMZ GDB. Three call shapes:
    #  - explicit strategies passed -> use them
    #  - explicit inner_mode passed -> use that legacy mode
    #  - nothing passed -> default to MODE_FLAT (preserves prior behavior)
    if fd_strategy is not None and fc_strategy is not None:
        log(f"Inner layout: fd_strategy={fd_strategy}, fc_strategy={fc_strategy}")
        inner_call_kwargs = dict(fd_strategy=fd_strategy, fc_strategy=fc_strategy)
    else:
        eff_mode = inner_mode or MODE_FLAT
        log(f"Inner layout: legacy mode={eff_mode}")
        inner_call_kwargs = dict(mode=eff_mode)

    total_fcs = 0
    total_features = 0
    output_gdbs = []
    failures = []

    for raw_gdb in raw_gdbs:
        kmz_stem = raw_gdb.stem  # raw GDBs are named after the source KMZ
        per_kmz_out = dest_folder / f"{kmz_stem}.gdb"
        log("")
        log(f"--- {kmz_stem} -> {per_kmz_out.name} ---")
        try:
            result = post_process(
                scratch_folder=str(scratch_folder),
                output_gdb_path=str(per_kmz_out),
                target_coord_system=target_coord_system,
                log=log,
                _source_kmz_filter=kmz_stem,
                **inner_call_kwargs,
            )
            total_fcs += result["feature_classes"]
            total_features += result["features"]
            output_gdbs.append(str(per_kmz_out))
        except Exception as e:
            log(f"  [ERROR] {kmz_stem}: {e}")
            failures.append((kmz_stem, str(e)))

    log("")
    log(
        f"[POST-PROCESS DONE - Mode G] {len(output_gdbs)} GDB(s) written, "
        f"{total_fcs} feature classes, {total_features} features total"
    )
    if failures:
        log(f"  {len(failures)} source(s) failed:")
        for stem, msg in failures:
            log(f"    {stem}: {msg}")

    return {
        "output_gdb": str(dest_folder),  # the folder, not a single GDB
        "output_gdbs": output_gdbs,
        "feature_classes": total_fcs,
        "features": total_features,
    }


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

# Date formats tried in order when coercing strings -> datetime for DATE fields.
# Keep in sync with SchemaInferencer.DATE_PATTERNS.
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
]


def _coerce_value(value, field_type, field_length=None):
    """Coerce a popup string to the type required by an arcpy InsertCursor.

    Returns None for unparseable / empty values so the row still inserts.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None

    if field_type == "TEXT":
        text = value if isinstance(value, str) else str(value)
        if field_length:
            text = text[:field_length]
        return text

    if field_type == "LONG":
        # Don't silently truncate "3.14" to 3 -- if a string carries a decimal
        # point, treat it as a non-integer and return None. Schema inference
        # widens to DOUBLE when any row has a decimal, so seeing one here
        # means the inferencer didn't catch it (e.g. an outlier added after
        # schema inference). Returning None preserves data correctness.
        if isinstance(value, str) and "." in value:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    if field_type == "DOUBLE":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if field_type == "DATE":
        if isinstance(value, datetime):
            return value
        s = str(value)
        # Strip +HH:MM / -HH:MM offsets (file GDB DATE doesn't carry tz)
        if len(s) >= 6 and (s[-6] in "+-") and s[-3] == ":":
            s = s[:-6]
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # Unknown type: pass through
    return value


# ---------------------------------------------------------------------------
# Grouping / name resolution
# ---------------------------------------------------------------------------

def _wgs84_data_extent(rows):
    """Union extent (in WGS84) of all geometries in rows, or None if empty.

    Used to give arcpy.ListTransformations a real extent so it can rank the
    candidate datum transformations the way the Project tool would for this
    data, rather than picking a region-agnostic default.
    """
    xmin = ymin = xmax = ymax = None
    for row in rows:
        geom = row.get("geometry")
        if geom is None:
            continue
        ext = getattr(geom, "extent", None)
        if ext is None:
            continue
        if xmin is None:
            xmin, ymin, xmax, ymax = ext.XMin, ext.YMin, ext.XMax, ext.YMax
        else:
            xmin = min(xmin, ext.XMin)
            ymin = min(ymin, ext.YMin)
            xmax = max(xmax, ext.XMax)
            ymax = max(ymax, ext.YMax)
    if xmin is None:
        return None
    extent = arcpy.Extent(xmin, ymin, xmax, ymax)
    extent.spatialReference = arcpy.SpatialReference(SR_WGS84_WKID)
    return extent


def _resolve_transformation(target_sr, extent=None):
    """Pick the WGS84 -> target_sr datum transformation arcpy recommends.

    Returns a transformation name string usable by Geometry.projectAs (arcpy
    accepts composite "A + B" strings as a single argument), or "" when none
    is needed (target is WGS84, or the change is a pure projection within the
    same datum). Uses the first entry of arcpy.ListTransformations, which ESRI
    ranks best-first for the supplied extent -- the same choice the ArcGIS Pro
    Project tool makes by default.
    """
    if target_sr.factoryCode == SR_WGS84_WKID:
        return ""
    wgs84 = arcpy.SpatialReference(SR_WGS84_WKID)
    try:
        if extent is not None:
            txs = arcpy.ListTransformations(wgs84, target_sr, extent)
        else:
            txs = arcpy.ListTransformations(wgs84, target_sr)
    except Exception:
        txs = []
    return txs[0] if txs else ""


def _project_geometry(geom, target_sr, transformation=""):
    """Project a WGS84 geometry to target_sr using the resolved transformation.

    transformation is a single arcpy transformation name (possibly a composite
    "A + B" string) or "" for none. Returns None if geom is None. Raises on
    projection failure rather than silently dropping data.
    """
    if geom is None:
        return None
    if target_sr.factoryCode == SR_WGS84_WKID:
        return geom
    if transformation:
        return geom.projectAs(target_sr, transformation)
    return geom.projectAs(target_sr)


def _folder_fields(segments):
    """Return (Folder1, Folder2, Folder3) right-aligned from segments.

    Folder3 is always the deepest segment present. Shorter paths leave
    shallower slots as None.
    """
    n = len(segments)
    f1 = segments[-3] if n >= 3 else None
    f2 = segments[-2] if n >= 2 else None
    f3 = segments[-1] if n >= 1 else None
    return f1, f2, f3


def _group_rows(rows, mode, naming_resolver, fd_strategy=None, fc_strategy=None):
    """Bucket rows by (fd, fc, geom) per the chosen mode or strategy pair.

    Two ways to call:
      * Legacy: pass mode=<MODE_*>. The function maps that to a strategy
        pair internally via _MODE_TO_STRATEGIES.
      * Composed (Waterfall): pass fd_strategy=<FD_STRATEGY_*> and
        fc_strategy=<FC_STRATEGY_*> directly. mode= is ignored.

    Also annotates each row with folder1/2/3 so the insert loop has values
    ready regardless of mode/strategy.
    """
    if fd_strategy is None or fc_strategy is None:
        # Legacy: derive strategies from the mode constant.
        try:
            fd_strategy, fc_strategy = _MODE_TO_STRATEGIES[mode]
        except KeyError:
            raise ValueError(f"Unknown mode: {mode}")

    groups = defaultdict(list)
    for row in rows:
        segs = row["folder_segments"]
        geom = row["geom_type"]
        source_kmz = row.get("source_kmz") or ""

        fd, fc_override = _compute_fd(
            fd_strategy, segs, source_kmz, naming_resolver
        )
        if fc_override is not None:
            # FD_STRATEGY_HIERARCHY returns both fd and fc together (the
            # naming resolver couples them for the Mode A behavior).
            fc = fc_override
        else:
            fc = _compute_fc(fc_strategy, segs, geom)

        f1, f2, f3 = _folder_fields(segs)
        row["folder1"] = f1
        row["folder2"] = f2
        row["folder3"] = f3

        groups[(fd, fc, geom)].append(row)

    return groups


def _compute_fd(strategy, segs, source_kmz, naming_resolver):
    """Compute the Feature Dataset name from a strategy.

    Returns (fd_name, optional_fc_override). Only FD_STRATEGY_HIERARCHY
    sets an fc_override (it's the one strategy that couples FD and FC
    naming together, via NamingResolver.resolve_fd_fc).
    """
    if strategy == FD_STRATEGY_NONE:
        return None, None
    if strategy == FD_STRATEGY_TOP_FOLDER:
        if len(segs) >= 3:
            return segs[-3], None
        # Graceful fallback for shallow paths (consistent with TOP_TWO):
        # use whatever top segment exists rather than dropping FD entirely.
        if segs:
            return segs[0], None
        return None, None
    if strategy == FD_STRATEGY_TOP_TWO:
        if len(segs) >= 3:
            return f"{segs[-3]}_{segs[-2]}", None
        # Fewer than 3 segments -- graceful degradation: use whatever
        # top segment exists rather than dropping the FD entirely. Keeps
        # behavior consistent between 1-seg and 2-seg inputs (both fall
        # back to the single available top segment).
        if len(segs) >= 1:
            return segs[0], None
        return None, None
    if strategy == FD_STRATEGY_SOURCE_KMZ:
        return (Path(source_kmz).stem if source_kmz else None), None
    if strategy == FD_STRATEGY_HIERARCHY:
        fd, fc = naming_resolver.resolve_fd_fc(
            segs, Path(source_kmz).stem if source_kmz else "unnamed"
        )
        return fd, fc
    raise ValueError(f"Unknown FD strategy: {strategy}")


def _compute_fc(strategy, segs, geom):
    """Compute the Feature Class base name (before any FD prefix).

    For strategies that append a geometry suffix (e.g. `_Points`), the
    returned name is pre-truncated to fit the file-GDB FC name limit while
    preserving the suffix. This avoids the "_Polyg" mid-word truncation
    we'd otherwise get when arcpy clips the tail.
    """
    base = GEOM_TO_FC_BASE[geom]
    suffix = f"_{base}"  # what to preserve for suffix-bearing strategies
    if strategy == FC_STRATEGY_PER_GEOM:
        return base
    if strategy == FC_STRATEGY_LEAF:
        # Legacy Mode A: bare leaf name. Collision resolver may add a
        # geom suffix later; we don't pre-pad here.
        return segs[-1] if segs else base
    if strategy == FC_STRATEGY_LEAF_GEOM:
        name = f"{segs[-1]}_{base}" if segs else base
        return _truncate_fc_name(name, suffix)
    if strategy == FC_STRATEGY_PARENT_LEAF_GEOM:
        if len(segs) >= 2:
            name = f"{segs[-2]}_{segs[-1]}_{base}"
        elif segs:
            name = f"{segs[-1]}_{base}"
        else:
            name = base
        return _truncate_fc_name(name, suffix)
    if strategy == FC_STRATEGY_TOP_GEOM:
        if len(segs) >= 3:
            name = f"{segs[-3]}_{base}"
        elif segs:
            name = f"{segs[0]}_{base}"
        else:
            name = base
        return _truncate_fc_name(name, suffix)
    if strategy == FC_STRATEGY_PAIR_GEOM:
        if len(segs) >= 3:
            name = f"{segs[-3]}_{segs[-2]}_{base}"
        else:
            name = base
        return _truncate_fc_name(name, suffix)
    if strategy == FC_STRATEGY_FULL_PATH_GEOM:
        if len(segs) >= 3:
            name = f"{segs[-3]}_{segs[-2]}_{segs[-1]}_{base}"
        elif len(segs) >= 2:
            name = f"{segs[-2]}_{segs[-1]}_{base}"
        elif segs:
            name = f"{segs[-1]}_{base}"
        else:
            name = base
        return _truncate_fc_name(name, suffix)
    raise ValueError(f"Unknown FC strategy: {strategy}")


def _compute_simple_fc_names(groups, naming_resolver, mode=None, fc_strategy=None):
    """For non-hierarchy modes: FC name = <sanitized_fd>_<base>, or <base> at root.

    The FD name is included in the FC name so file-GDB's global-uniqueness
    rule is satisfied by construction. A defensive numeric suffix breaks any
    residual collision.

    The FD prefix is SKIPPED when the FC base already encodes enough folder
    context to be unique on its own (e.g. "Birds_2026_BUOW_Points",
    "Known_Points", "BUOW_Known_Points"). Otherwise the FC name would
    duplicate the FD name and grow unnecessarily long.
    """
    # Legacy mode that skipped FD prefix predated the strategy refactor;
    # the equivalent strategy condition (below) catches all the same cases
    # plus the new ones. Keep the legacy check for any caller still passing
    # mode= directly with that specific mode constant.
    skip_fd_prefix = (
        mode == MODE_KMZ_FD_FOLDER_FC
        or fc_strategy in _FC_STRATEGIES_WITH_FOLDER_CONTEXT
    )
    final = {}
    used = set()
    for (fd_name, fc_name, geom_type) in groups.keys():
        base = naming_resolver.sanitize(fc_name)
        if fd_name and not skip_fd_prefix:
            sanitized_fd = naming_resolver.sanitize(fd_name)
            candidate = f"{sanitized_fd}_{base}"
        else:
            candidate = base
        n = 2
        unique = candidate
        while unique in used:
            unique = f"{candidate}_{n}"
            n += 1
        final[(fd_name, fc_name, geom_type)] = unique
        used.add(unique)
    return final


def _resolve_fc_name_collisions(groups, naming_resolver, log):
    """Build a {(fd, fc, geom): final_fc_name} map that avoids collisions.

    Used in hierarchy mode only. File geodatabases require feature class
    names to be unique across the entire GDB, even across feature datasets.
    For each candidate FC name (sanitized fc + geom suffix) used by more
    than one FD, we disambiguate by prepending a slug derived from the FD:
    first the FD's tail token, then the full sanitized FD if the tail
    isn't enough.
    """
    candidate = {}  # candidate_name -> list[(fd, fc, geom)]
    for (fd_name, fc_name, geom_type) in groups.keys():
        base = naming_resolver.sanitize(fc_name) + suffix_for_geometry_type(geom_type)
        candidate.setdefault(base, []).append((fd_name, fc_name, geom_type))

    final = {}
    # used_names is function-scoped and seeded with singleton candidates so
    # disambiguated names from one collision bucket cannot collide with the
    # singleton candidate of a different bucket (e.g. base "BUOW" picks
    # "BUOW_A"; another base "A" with singleton FD "BUOW" would otherwise
    # also produce "BUOW_A" and overwrite final[]).
    used_names = {base for base, keys in candidate.items() if len(keys) == 1}
    for base, keys in candidate.items():
        if len(keys) == 1:
            final[keys[0]] = base
            continue

        log(f"  [COLLISION] {len(keys)} groups want FC name '{base}'; disambiguating")
        deferred = []
        for key in keys:
            fd_name, _fc, _geom = key
            if not fd_name:
                deferred.append(key)
                continue
            sanitized_fd = naming_resolver.sanitize(fd_name)
            tail = sanitized_fd.split("_")[-1] if sanitized_fd else ""
            candidate_name = f"{tail}_{base}" if tail else base
            if candidate_name in used_names:
                deferred.append(key)
            else:
                final[key] = candidate_name
                used_names.add(candidate_name)
                log(f"    {fd_name or '(root)'} -> {candidate_name}")

        for key in deferred:
            fd_name, _fc, _geom = key
            if fd_name:
                sanitized_fd = naming_resolver.sanitize(fd_name)
                candidate_name = f"{sanitized_fd}_{base}"
            else:
                candidate_name = f"root_{base}"
            n = 2
            unique = candidate_name
            while unique in used_names:
                unique = f"{candidate_name}_{n}"
                n += 1
            final[key] = unique
            used_names.add(unique)
            log(f"    {fd_name or '(root)'} -> {unique}")

    return final


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _collect_rows(gdb_path, out_rows, popup_parser, log):
    """Read all FCs from a raw scratch GDB and append flat row dicts to out_rows.

    Each row carries:
      geometry, name, folder_path, folder_segments, geom_type,
      raw_popup, source_kmz, popup_attrs
    """
    # arcpy.env.workspace + arcpy.ListFeatureClasses() proved unreliable here
    # (silently returned [] for a GDB that demonstrably had FCs). The Describe
    # API enumerates children directly off the workspace object, no env state.
    desc = arcpy.Describe(gdb_path)
    fcs = [c.name for c in desc.children if c.dataType == "FeatureClass"]
    log(f"  Found {len(fcs)} FC(s): {fcs}")

    for fc_name in fcs:
        if fc_name not in SCRATCH_FC_TO_GEOM:
            log(f"  [SKIP] Unknown FC name: {fc_name}")
            continue

        geom_type = SCRATCH_FC_TO_GEOM[fc_name]
        fc_path = str(Path(gdb_path) / fc_name)

        fields = ["SHAPE@", "Name", "FolderPath", "RawPopup", "SourceKMZ"]
        count = 0

        with arcpy.da.SearchCursor(fc_path, fields) as cursor:
            for shape, name, folder_path, raw_popup, source_kmz in cursor:
                try:
                    popup_attrs, _ = popup_parser.parse(
                        raw_popup or "", {}, strategy="auto"
                    )
                except Exception:
                    popup_attrs = {}

                segments = folder_path.split("\\") if folder_path else []

                out_rows.append({
                    "geometry": shape,
                    "name": name,
                    "folder_path": folder_path,
                    "folder_segments": segments,
                    "geom_type": geom_type,
                    "raw_popup": raw_popup,
                    "source_kmz": source_kmz,
                    "popup_attrs": popup_attrs,
                })
                count += 1

        log(f"  [{fc_name}] {count} features collected")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print(
            "Usage: python -m kmz_tools.post_processor "
            "<scratch_folder> <output_gdb> [mode] [coord_system_label]"
        )
        print(f"  mode: one of {VALID_MODES} (default: {MODE_HIERARCHY})")
        print(f"  coord_system_label: a key from COORD_SYSTEMS "
              f"(default: '{DEFAULT_COORD_SYSTEM}')")
        sys.exit(1)
    mode = sys.argv[3] if len(sys.argv) > 3 else MODE_HIERARCHY
    cs = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_COORD_SYSTEM
    result = post_process(sys.argv[1], sys.argv[2], mode=mode, target_coord_system=cs)
    print(f"\n[DONE] {result['feature_classes']} FCs, {result['features']} features")
    print(f"       Output: {result['output_gdb']}")
