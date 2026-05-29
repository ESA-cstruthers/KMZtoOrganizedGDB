# -*- coding: utf-8 -*-
"""ArcGIS Pro Python Toolbox - KMZ to organized GDB converter.

Two tools:
    KMZInspector       -- Read-only preflight of KMZ/KML inputs.
    KMZtoOrganizedGDB  -- One-step conversion. The Waterfall UX decomposes
                          layout choices into orthogonal questions
                          (container, split-into-FDs, FD source, FC naming)
                          instead of a single multi-letter mode dropdown.
"""

import os
import sys
import traceback
from pathlib import Path

import arcpy

# Make sibling kmz_tools package importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


class Toolbox(object):
    def __init__(self):
        self.label = "KMZ Tools"
        self.alias = "kmztools"
        self.tools = [KMZInspector, KMZtoOrganizedGDB]


class KMZInspector(object):
    """Pre-flight inspection of KMZ/KML files.

    Reads inputs without writing anything. Reports element counts (placemarks
    by geometry, folders, styles, NetworkLinks), folder-path depth stats,
    popup parse-tier breakdown, style-coverage gaps, and flags anything that
    might affect the conversion tools (lxml recovery applied, placemarks
    without geometry, broken styleUrls, NetworkLinks present, deep folder
    paths).
    """

    def __init__(self):
        self.label = "Inspect KMZ/KML (Preflight Check)"
        self.description = (
            "Read one or more KMZ/KML files without writing anything. Reports "
            "element counts (placemarks by geometry, folders, styles, "
            "NetworkLinks), folder-path depth stats, popup parse-tier "
            "breakdown, style-coverage gaps, and flags issues that affect "
            "conversion (lxml recovery, missing geometry, broken style refs, "
            "NetworkLinks present, deep paths). Streams to geoprocessing "
            "messages and optionally writes a .txt report."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        p_input = arcpy.Parameter(
            displayName="Input KMZ/KML Files",
            name="input_kmz",
            datatype="DEFile",
            parameterType="Required",
            direction="Input",
            multiValue=True,
        )
        p_input.filter.list = ["kmz", "kml"]

        p_report = arcpy.Parameter(
            displayName="Output Report File (optional)",
            name="report_file",
            datatype="DEFile",
            parameterType="Optional",
            direction="Output",
        )
        p_report.filter.list = ["txt"]

        p_verbose = arcpy.Parameter(
            displayName="Verbose mode (per-file detail; aggregate stays the same)",
            name="verbose",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        p_verbose.value = False

        return [p_input, p_report, p_verbose]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        for mod_name in list(sys.modules):
            if mod_name == "kmz_tools" or mod_name.startswith("kmz_tools."):
                del sys.modules[mod_name]

        try:
            from kmz_tools.inspector import inspect_kmz, format_report
        except Exception as e:
            arcpy.AddError("Failed to import kmz_tools.inspector: {}".format(e))
            arcpy.AddError(traceback.format_exc())
            return

        raw_values = parameters[0].values or []
        input_paths = [str(v) for v in raw_values]
        report_file = parameters[1].valueAsText
        verbose = (
            bool(parameters[2].value) if parameters[2].value is not None else False
        )

        if not input_paths:
            arcpy.AddError("No input KMZ/KML files provided")
            return

        # Normalize report path to .txt -- the dialog's filter is advisory,
        # not enforced. Without this, a typo'd ".tx" or ".docx" would silently
        # write through.
        if report_file:
            rp = Path(report_file)
            if rp.suffix.lower() != ".txt":
                report_file = str(rp.with_suffix(".txt"))
                arcpy.AddWarning(
                    "Report file extension forced to .txt: {}".format(report_file)
                )

        arcpy.AddMessage(
            "Inspecting {} file(s)...".format(len(input_paths))
        )

        all_lines = []
        total_placemarks = 0
        total_with_geom = 0
        total_no_geom = 0
        total_networklinks = 0
        total_issues = 0
        files_inspected = 0
        files_failed = []

        for path in input_paths:
            arcpy.AddMessage("")
            try:
                report = inspect_kmz(path)
                lines = format_report(report, verbose=verbose)
                for line in lines:
                    arcpy.AddMessage(line)
                all_lines.extend(lines)
                all_lines.append("")
                files_inspected += 1
                c = report["counts"]
                total_placemarks += c["placemarks_total"]
                total_with_geom += c["placemarks_with_geometry"]
                total_no_geom += c["placemarks_no_geometry"]
                total_networklinks += c["network_links"]
                total_issues += len(report["issues"])
            except Exception as e:
                msg = "[ERROR] {}: {}".format(Path(path).name, e)
                arcpy.AddError(msg)
                all_lines.append(msg)
                all_lines.append(traceback.format_exc())
                files_failed.append(path)

        # Aggregate summary across all inputs.
        summary_lines = [
            "",
            "=" * 70,
            "AGGREGATE",
            "=" * 70,
            "  Files inspected:        {} of {}".format(
                files_inspected, len(input_paths)
            ),
            "  Total placemarks:       {}".format(total_placemarks),
            "  With geometry:          {}".format(total_with_geom),
            "  No geometry (skipped):  {}".format(total_no_geom),
            "  NetworkLinks total:     {}".format(total_networklinks),
            "  Issues / notes flagged: {}".format(total_issues),
        ]
        if files_failed:
            summary_lines.append(
                "  Failed to inspect:      {} file(s)".format(len(files_failed))
            )
        for line in summary_lines:
            arcpy.AddMessage(line)
        all_lines.extend(summary_lines)

        if report_file:
            try:
                report_path = Path(report_file)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(all_lines))
                arcpy.AddMessage("")
                arcpy.AddMessage("Report written to: {}".format(report_file))
            except Exception as e:
                arcpy.AddWarning("Failed to write report file: {}".format(e))


# =====================================================================
# Waterfall-style UX for KMZ -> organized GDB conversion.
# Decomposes the layout choice into orthogonal questions:
#   1. Container (one merged GDB vs. one per source KMZ)
#   2. Split into Feature Datasets? (yes/no)
#   3a. (if yes) Feature Dataset source -- top folder, top-two folders,
#       or source KMZ stem
#   3b. Feature Class naming strategy -- options depend on (2).
# updateParameters() enables/disables the downstream params so the user
# walks through the decision tree linearly.
# =====================================================================

class KMZtoOrganizedGDB(object):
    """KMZ -> organized GDB conversion with a Waterfall UX.

    Layout choices are decomposed into orthogonal questions instead of a
    single multi-letter mode dropdown:
      1. Container          (one merged GDB vs. one GDB per source KMZ)
      2. Split into FDs?    (yes/no)
      3a. FD source         (only if FDs on)
      3b. FC naming strategy
    The tool composes these into FD/FC strategies internally and refuses
    structurally meaningless combinations with a clear message.
    """

    # Choice labels (referenced in both getParameterInfo and execute)
    CONTAINER_MERGED = "One merged GDB"
    CONTAINER_PER_KMZ = "One GDB per source KMZ"

    FD_SOURCE_TOP = "Top folder (segments[-3])"
    FD_SOURCE_TOP2 = "Top two folders combined (segments[-3] + segments[-2])"
    FD_SOURCE_KMZ = "Source KMZ filename"

    FC_PER_GEOM = "Per-geometry only (Points / Polylines / Polygons)"
    FC_LEAF_FOLDER = "Leaf folder name (segments[-1])"
    FC_LEAF_GEOM = "Leaf + geometry (e.g. Known_Points)"
    FC_PARENT_LEAF_GEOM = "Parent + leaf + geometry (e.g. BUOW_Known_Points)"
    FC_FOLDER_PAIR = "Folder pair (segments[-3] + segments[-2]) + geometry"
    FC_TOP_FOLDER = "Top folder (segments[-3]) + geometry"

    def __init__(self):
        self.label = "KMZ to Organized GDB"
        self.description = (
            "Convert one or more KMZ/KML files into an organized File "
            "Geodatabase. The Waterfall UX decomposes layout into four "
            "orthogonal questions: container (one merged GDB vs. one per "
            "source KMZ), whether to split into Feature Datasets, FD "
            "source, and FC naming strategy. Popup HTML is parsed into "
            "typed attribute fields; output is projected to a chosen "
            "coordinate system."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        p_input = arcpy.Parameter(
            displayName="Input KMZ/KML Files",
            name="input_kmz",
            datatype="DEFile",
            parameterType="Required",
            direction="Input",
            multiValue=True,
        )
        p_input.filter.list = ["kmz", "kml"]

        # ---- Waterfall question 1: container ----
        p_container = arcpy.Parameter(
            displayName="1. Output container",
            name="container",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        p_container.filter.type = "ValueList"
        p_container.filter.list = [self.CONTAINER_MERGED, self.CONTAINER_PER_KMZ]
        p_container.value = self.CONTAINER_MERGED

        # ---- Output target -- one of these two depending on container ----
        # Both kept Optional so the disabled one doesn't block the dialog with
        # a "Required" error. updateMessages() raises an error on the active
        # one if the user leaves it blank.
        p_output_gdb = arcpy.Parameter(
            displayName="Output Geodatabase (used when container is 'One merged GDB')",
            name="output_gdb",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Output",
        )
        p_output_gdb.filter.list = ["Local Database"]

        p_output_folder = arcpy.Parameter(
            displayName="Output Folder (used when container is 'One GDB per source KMZ')",
            name="output_folder",
            datatype="DEFolder",
            parameterType="Optional",
            # direction="Input" because arcpy's "Output" direction enforces
            # that DEFolder targets do NOT exist yet (raises ERROR 000725
            # otherwise). For per-KMZ container we expect users to pick an
            # existing folder to write N GDBs into; tool will mkdir if
            # needed. "Input" disables the pre-existence check.
            direction="Input",
        )

        # ---- Waterfall question 2: split into FDs? ----
        p_split = arcpy.Parameter(
            displayName="2. Split output into Feature Datasets?",
            name="split_fds",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input",
        )
        p_split.value = True

        # ---- Waterfall question 3a: FD source (only if split is True) ----
        p_fd_source = arcpy.Parameter(
            displayName="3a. Feature Dataset source (only used if FDs are on)",
            name="fd_source",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        p_fd_source.filter.type = "ValueList"
        p_fd_source.filter.list = [
            self.FD_SOURCE_TOP, self.FD_SOURCE_TOP2, self.FD_SOURCE_KMZ,
        ]
        p_fd_source.value = self.FD_SOURCE_TOP2

        # ---- Waterfall question 3b: FC naming ----
        # Options depend on whether FDs are on. updateParameters() rewrites
        # the filter list when the split toggle changes.
        p_fc_naming = arcpy.Parameter(
            displayName="3b. Feature Class naming strategy",
            name="fc_naming",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        p_fc_naming.filter.type = "ValueList"
        p_fc_naming.filter.list = [
            self.FC_PER_GEOM, self.FC_LEAF_FOLDER, self.FC_LEAF_GEOM,
            self.FC_PARENT_LEAF_GEOM, self.FC_FOLDER_PAIR,
        ]
        p_fc_naming.value = self.FC_LEAF_FOLDER

        # ---- Coordinate system ----
        p_sr = arcpy.Parameter(
            displayName="Output Coordinate System",
            name="output_sr",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        p_sr.filter.type = "ValueList"
        for _mod_name in list(sys.modules):
            if _mod_name == "kmz_tools" or _mod_name.startswith("kmz_tools."):
                del sys.modules[_mod_name]
        try:
            from kmz_tools.post_processor import (
                COORD_SYSTEMS as _CS,
                DEFAULT_COORD_SYSTEM as _DEFAULT_CS,
            )
            p_sr.filter.list = list(_CS.keys())
            p_sr.value = _DEFAULT_CS
        except Exception as _e:
            p_sr.filter.list = [
                "WGS 1984 (no reproject)",
                "(import failed: {} -- check post_processor.py)".format(_e),
            ]
            p_sr.value = "WGS 1984 (no reproject)"

        # ---- NetworkLink options ----
        p_follow = arcpy.Parameter(
            displayName="Follow NetworkLinks",
            name="follow_network_links",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
            category="NetworkLink Options",
        )
        p_follow.value = False

        p_depth = arcpy.Parameter(
            displayName="NetworkLink Max Depth",
            name="network_link_max_depth",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input",
            category="NetworkLink Options",
        )
        p_depth.value = 2

        p_timeout = arcpy.Parameter(
            displayName="NetworkLink Download Timeout (seconds)",
            name="network_link_timeout",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input",
            category="NetworkLink Options",
        )
        p_timeout.value = 30

        # Parameter order:
        #   0  input_kmz
        #   1  container
        #   2  output_gdb        (enabled if merged)
        #   3  output_folder     (enabled if per-KMZ)
        #   4  split_fds
        #   5  fd_source
        #   6  fc_naming
        #   7  output_sr
        #   8  follow_nl
        #   9  nl_depth
        #   10 nl_timeout
        return [
            p_input,
            p_container,
            p_output_gdb, p_output_folder,
            p_split, p_fd_source, p_fc_naming,
            p_sr, p_follow, p_depth, p_timeout,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # Parameter indexes (see getParameterInfo):
        #   1=container, 2=output_gdb, 3=output_folder,
        #   4=split_fds, 5=fd_source, 6=fc_naming
        container = parameters[1].valueAsText
        split_fds = bool(parameters[4].value) if parameters[4].value is not None else True

        # Enable only the output picker that matches the container choice.
        # The unused one stays in the dialog but is greyed out so the user
        # sees what they're NOT picking and doesn't accidentally fill it.
        if container == self.CONTAINER_MERGED:
            parameters[2].enabled = True
            parameters[3].enabled = False
        elif container == self.CONTAINER_PER_KMZ:
            parameters[2].enabled = False
            parameters[3].enabled = True
        else:
            parameters[2].enabled = True
            parameters[3].enabled = True

        # FD source param is only meaningful when split_fds is True.
        parameters[5].enabled = split_fds

        # When container is per-KMZ, "Source KMZ" as FD source is meaningless
        # (the GDB IS the source). Trim it out of the dropdown for that path.
        fd_options = [self.FD_SOURCE_TOP, self.FD_SOURCE_TOP2]
        if container == self.CONTAINER_MERGED:
            fd_options.append(self.FD_SOURCE_KMZ)
        parameters[5].filter.list = fd_options
        if parameters[5].valueAsText not in fd_options:
            parameters[5].value = fd_options[-1] if fd_options else None

        # FC naming options depend on whether FDs are split.
        if split_fds:
            fc_options = [
                self.FC_PER_GEOM, self.FC_LEAF_FOLDER, self.FC_LEAF_GEOM,
                self.FC_PARENT_LEAF_GEOM, self.FC_FOLDER_PAIR,
            ]
        else:
            fc_options = [
                self.FC_PER_GEOM, self.FC_TOP_FOLDER, self.FC_LEAF_GEOM,
                self.FC_PARENT_LEAF_GEOM, self.FC_FOLDER_PAIR,
            ]
        parameters[6].filter.list = fc_options
        if parameters[6].valueAsText not in fc_options:
            parameters[6].value = fc_options[0]
        return

    def updateMessages(self, parameters):
        # Both output pickers are Optional in the dialog (so the disabled
        # one doesn't block the user with a "Required" red square). We
        # enforce here that the ACTIVE picker has a value.
        container = parameters[1].valueAsText
        p_gdb = parameters[2]
        p_folder = parameters[3]
        try:
            if container == self.CONTAINER_MERGED:
                if not p_gdb.valueAsText:
                    p_gdb.setErrorMessage(
                        "Output Geodatabase is required when container is "
                        "'One merged GDB'."
                    )
                elif Path(p_gdb.valueAsText).exists():
                    p_gdb.setWarningMessage(
                        "Output GDB already exists. The tool will refuse to "
                        "overwrite -- delete it (or pick a new path) before "
                        "running."
                    )
            elif container == self.CONTAINER_PER_KMZ:
                if not p_folder.valueAsText:
                    p_folder.setErrorMessage(
                        "Output Folder is required when container is "
                        "'One GDB per source KMZ'. Each input KMZ will "
                        "produce its own .gdb file in this folder."
                    )
        except Exception:
            pass
        return

    # Map FC dropdown labels -> FC strategy constants (in post_processor).
    _FC_LABEL_TO_STRATEGY = {
        FC_PER_GEOM: "fc_per_geom",
        FC_LEAF_FOLDER: "fc_leaf",
        FC_LEAF_GEOM: "fc_leaf_geom",
        FC_PARENT_LEAF_GEOM: "fc_parent_leaf_geom",
        FC_FOLDER_PAIR: "fc_pair_geom",
        FC_TOP_FOLDER: "fc_top_geom",
    }

    # Map FD source label -> FD strategy constant.
    _FD_LABEL_TO_STRATEGY = {
        FD_SOURCE_TOP: "fd_top_folder",
        FD_SOURCE_TOP2: "fd_top_two_folders",
        FD_SOURCE_KMZ: "fd_source_kmz",
    }

    def _resolve_strategies(self, container, split_fds, fd_source, fc_naming):
        """Translate waterfall choices to (fd_strategy, fc_strategy, container_mode).

        Returns a tuple (fd_strategy, fc_strategy, special_mode) where
        special_mode is non-None for paths that bypass the strategy
        dispatcher entirely (currently just per-KMZ container, which
        dispatches to MODE_PER_KMZ_GDB in post_process).

        Returns (None, None, None) for unsupported combinations.
        """
        # FD strategy: NONE if split is off; else look up from the dropdown.
        if not split_fds:
            fd_strategy = "fd_none"
        else:
            fd_strategy = self._FD_LABEL_TO_STRATEGY.get(fd_source)
            if fd_strategy is None:
                return (None, None, None)
            # Source KMZ FD source is redundant in per-KMZ container.
            if (container == self.CONTAINER_PER_KMZ
                    and fd_strategy == "fd_source_kmz"):
                return (None, None, None)

        fc_strategy = self._FC_LABEL_TO_STRATEGY.get(fc_naming)
        if fc_strategy is None:
            return (None, None, None)

        # Filter out structurally meaningless pairings.
        # FC_LEAF needs the hierarchy FD source to keep folder context.
        if fc_strategy == "fc_leaf":
            if fd_strategy != "fd_top_two_folders":
                return (None, None, None)
            return ("fd_hierarchy", "fc_leaf",
                    "per_kmz_gdb" if container == self.CONTAINER_PER_KMZ
                    else None)

        # FC_TOP_GEOM at the GDB root duplicates the FD name when FDs are on.
        if fc_strategy == "fc_top_geom" and fd_strategy != "fd_none":
            return (None, None, None)

        # When container is per-KMZ, return the strategies AND signal the
        # per-KMZ orchestrator path. The orchestrator iterates source KMZs
        # and applies the same (fd, fc) strategies inside each per-KMZ GDB.
        if container == self.CONTAINER_PER_KMZ:
            return (fd_strategy, fc_strategy, "per_kmz_gdb")
        return (fd_strategy, fc_strategy, None)

    def execute(self, parameters, messages):
        for mod_name in list(sys.modules):
            if mod_name == "kmz_tools" or mod_name.startswith("kmz_tools."):
                del sys.modules[mod_name]

        try:
            import kmz_tools.converter as converter
            import kmz_tools.post_processor as post_processor
        except Exception as e:
            arcpy.AddError("Failed to import kmz_tools modules: {}".format(e))
            arcpy.AddError(traceback.format_exc())
            return

        # Parameter indexes (see getParameterInfo):
        #   0=input_kmz, 1=container, 2=output_gdb, 3=output_folder,
        #   4=split_fds, 5=fd_source, 6=fc_naming, 7=output_sr,
        #   8=follow_nl, 9=nl_depth, 10=nl_timeout
        raw_values = parameters[0].values or []
        input_paths = [str(v) for v in raw_values]
        container = parameters[1].valueAsText
        output_gdb_param = parameters[2].valueAsText
        output_folder_param = parameters[3].valueAsText
        split_fds = bool(parameters[4].value) if parameters[4].value is not None else True
        fd_source = parameters[5].valueAsText if split_fds else None
        fc_naming = parameters[6].valueAsText
        coord_system = parameters[7].valueAsText
        follow_nl = bool(parameters[8].value) if parameters[8].value is not None else False
        nl_depth = int(parameters[9].value) if parameters[9].value is not None else 2
        nl_timeout = int(parameters[10].value) if parameters[10].value is not None else 30

        if not input_paths:
            arcpy.AddError("No input KMZ/KML files provided")
            return

        # Pick the output target based on container choice.
        if container == self.CONTAINER_MERGED:
            if not output_gdb_param:
                arcpy.AddError(
                    "Output Geodatabase is required when container is "
                    "'One merged GDB'."
                )
                return
            output_target = output_gdb_param
        elif container == self.CONTAINER_PER_KMZ:
            if not output_folder_param:
                arcpy.AddError(
                    "Output Folder is required when container is "
                    "'One GDB per source KMZ'."
                )
                return
            output_target = output_folder_param
        else:
            arcpy.AddError("Container choice missing.")
            return

        fd_strategy, fc_strategy, special_mode = self._resolve_strategies(
            container, split_fds, fd_source, fc_naming
        )
        if fd_strategy is None and fc_strategy is None and special_mode is None:
            arcpy.AddError(
                "This combination of choices is not supported:\n"
                "  Container: {}\n"
                "  Split into FDs: {}\n"
                "  FD source: {}\n"
                "  FC naming: {}\n"
                "Either the combination is structurally meaningless (e.g. "
                "Top-folder-as-FC with FDs on would duplicate the FD name) "
                "or it's only valid for the per-KMZ container with "
                "per-geometry FCs. Try a different pairing.".format(
                    container, split_fds, fd_source or "(n/a)", fc_naming
                )
            )
            return
        mode = special_mode if special_mode is not None else None

        if not coord_system or coord_system not in post_processor.COORD_SYSTEMS:
            arcpy.AddError(
                "Pick an Output Coordinate System from the dropdown (refresh "
                "the toolbox if it shows only one entry)."
            )
            return

        # Scratch folder convention:
        #   Merged: <output_gdb_parent>/<output_gdb_stem>_scratch/
        #   Per-KMZ: <output_folder>/_scratch/
        if container == self.CONTAINER_MERGED:
            output_path = Path(output_target)
            scratch_folder = output_path.parent / f"{output_path.stem}_scratch"
        else:
            output_path = Path(output_target)
            scratch_folder = output_path / "_scratch"
        scratch_folder.mkdir(parents=True, exist_ok=True)

        arcpy.AddMessage("=" * 60)
        arcpy.AddMessage("KMZ to Organized GDB")
        arcpy.AddMessage("=" * 60)
        arcpy.AddMessage("Waterfall choices:")
        arcpy.AddMessage("  Container:      {}".format(container))
        arcpy.AddMessage("  Split FDs:      {}".format(split_fds))
        arcpy.AddMessage("  FD source:      {}".format(fd_source or "(n/a)"))
        arcpy.AddMessage("  FC naming:      {}".format(fc_naming))
        if mode is not None:
            arcpy.AddMessage("  -> container mode: {}".format(mode))
        arcpy.AddMessage(
            "  -> strategies: fd={}, fc={}".format(fd_strategy, fc_strategy)
        )
        arcpy.AddMessage("Scratch folder: {}".format(scratch_folder))
        if container == self.CONTAINER_MERGED:
            arcpy.AddMessage("Output GDB:     {}".format(output_target))
        else:
            arcpy.AddMessage("Output folder:  {} (N GDBs per source KMZ)".format(output_target))
        arcpy.AddMessage("Coord system:   {}".format(coord_system))
        arcpy.AddMessage("")

        # ---------- Phase 1: KMZ -> scratch ----------
        arcpy.AddMessage("--- Phase 1: Converting KMZs to scratch GDBs ---")
        # produced_data: inputs that wrote a scratch GDB with >= 1 feature.
        # skipped_no_data: ran cleanly but produced nothing.
        # failures: raised.
        produced_data = []
        skipped_no_data = []
        failures = []
        for kmz_path in input_paths:
            arcpy.AddMessage("")
            arcpy.AddMessage("=== {} ===".format(Path(kmz_path).name))
            try:
                result = converter.convert_kmz_to_gdb(
                    kmz_path,
                    str(scratch_folder),
                    log=arcpy.AddMessage,
                    follow_network_links_enabled=follow_nl,
                    network_link_max_depth=nl_depth,
                    network_link_timeout=nl_timeout,
                )
                arcpy.AddMessage(
                    "[OK] {} inserted, {} skipped ({} placemarks total)".format(
                        result["inserted"], result["skipped"], result["placemarks"]
                    )
                )
                if result.get("gdb_path") and result.get("inserted", 0) > 0:
                    produced_data.append(kmz_path)
                else:
                    skipped_no_data.append(kmz_path)
            except Exception as e:
                arcpy.AddError("Error on {}: {}".format(Path(kmz_path).name, e))
                failures.append(kmz_path)

        arcpy.AddMessage("")
        arcpy.AddMessage(
            "[Phase 1 done] {} produced data, {} skipped (no placemarks), "
            "{} failed".format(
                len(produced_data), len(skipped_no_data), len(failures)
            )
        )
        for skipped in skipped_no_data:
            arcpy.AddWarning(
                "  No data from: {} (enable Follow NetworkLinks if this KMZ "
                "wraps external sources)".format(Path(skipped).name)
            )

        if not produced_data:
            arcpy.AddError(
                "No KMZs produced any data -- aborting before organize step. "
                "Common cause: input KMZs only contain NetworkLinks; enable "
                "Follow NetworkLinks and retry."
            )
            return

        # ---------- Phase 2: scratch -> organized output ----------
        arcpy.AddMessage("")
        arcpy.AddMessage("--- Phase 2: Organizing into output ---")
        try:
            pp_kwargs = dict(
                target_coord_system=coord_system,
                log=arcpy.AddMessage,
            )
            if fd_strategy is not None and fc_strategy is not None:
                pp_kwargs["fd_strategy"] = fd_strategy
                pp_kwargs["fc_strategy"] = fc_strategy
            if mode is not None:
                pp_kwargs["mode"] = mode
            result = post_processor.post_process(
                str(scratch_folder),
                output_target,
                **pp_kwargs,
            )
            arcpy.AddMessage("")
            arcpy.AddMessage("=" * 60)
            arcpy.AddMessage("[ALL DONE]")
            arcpy.AddMessage(
                "  Phase 1: {} of {} KMZs produced data ({} skipped, {} failed)".format(
                    len(produced_data), len(input_paths),
                    len(skipped_no_data), len(failures)
                )
            )
            arcpy.AddMessage(
                "  Phase 2: {} feature classes, {} features".format(
                    result["feature_classes"], result["features"]
                )
            )
            arcpy.AddMessage("  Scratch (kept): {}".format(scratch_folder))
            arcpy.AddMessage("  Output: {}".format(result["output_gdb"]))
            arcpy.AddMessage("=" * 60)
        except arcpy.ExecuteError:
            arcpy.AddError(
                "GP error in organize phase: {}".format(arcpy.GetMessages(2))
            )
        except RuntimeError as e:
            arcpy.AddError("Organize phase aborted: {}".format(e))
        except Exception as e:
            arcpy.AddError("Error in organize phase: {}".format(e))
            arcpy.AddError(traceback.format_exc())
