"""Pre-flight KMZ/KML inspection.

Reads a KMZ/KML without writing anything and reports element counts,
folder/path structure, popup parse-tier breakdown, style coverage, and
likely conversion concerns. Used by the "Inspect KMZ/KML" tool in the
Python toolbox and from the CLI.

Pure data: inspect_kmz() returns a dict; format_report() renders it to
printable lines. The toolbox layer streams the lines to arcpy messages.
"""

from pathlib import Path
from collections import Counter
from typing import Dict, List, Any

from .kml_parser import KMLParser
from .popup_parser import PopupParser


def inspect_kmz(kmz_path: str) -> Dict[str, Any]:
    """Inspect a KMZ/KML file and return a structured report.

    Returns dict with keys: file, counts, folder_stats, popups, styles,
    network_links, issues. See format_report() for the rendered shape.
    """
    path = Path(kmz_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {kmz_path}")

    report: Dict[str, Any] = {
        "file": {
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "format": "KMZ" if path.suffix.lower() == ".kmz" else "KML",
            "recovery_occurred": False,
        },
        "counts": {},
        "folder_stats": {},
        "popups": {},
        "styles": {},
        "network_links": [],
        "issues": [],
    }

    parser = KMLParser(str(path))
    try:
        parser.extract_and_parse()
        report["file"]["recovery_occurred"] = parser.recovery_occurred

        if parser.recovery_occurred:
            report["issues"].append({
                "severity": "warning",
                "message": (
                    "lxml recovery applied -- source KML was malformed; "
                    "some content may be skipped during conversion"
                ),
            })

        # --- Raw element counts via XML traversal (catches geometry-less PMs) ---
        root = parser.root
        ns = parser.namespaces

        total_placemarks = len(root.findall(".//kml:Placemark", ns))
        total_folders = len(root.findall(".//kml:Folder", ns))
        total_styles = len(root.findall(".//kml:Style", ns))
        total_stylemaps = len(root.findall(".//kml:StyleMap", ns))
        total_schemas = len(root.findall(".//kml:Schema", ns))
        total_networklinks = len(root.findall(".//kml:NetworkLink", ns))

        # --- Placemarks with usable geometry (KMLParser filters out no-geom) ---
        placemarks = parser.get_placemarks()
        with_geom = len(placemarks)
        no_geom = max(0, total_placemarks - with_geom)

        geom_counter: Counter = Counter()
        for pm in placemarks:
            geom = pm.get("geometry") or {}
            geom_counter[geom.get("type", "Unknown")] += 1

        report["counts"] = {
            "placemarks_total": total_placemarks,
            "placemarks_with_geometry": with_geom,
            "placemarks_no_geometry": no_geom,
            "geometry_breakdown": dict(geom_counter),
            "folders": total_folders,
            "styles": total_styles,
            "stylemaps": total_stylemaps,
            "schemas": total_schemas,
            "network_links": total_networklinks,
        }

        if no_geom > 0:
            report["issues"].append({
                "severity": "info",
                "message": (
                    f"{no_geom} placemark(s) have no geometry; "
                    f"they will be skipped during conversion"
                ),
            })

        multi_count = geom_counter.get("MultiGeometry", 0)
        if multi_count > 0:
            report["issues"].append({
                "severity": "info",
                "message": (
                    f"{multi_count} MultiGeometry placemark(s); each will be "
                    f"split across Point/Line/Polygon FCs by sub-geometry type"
                ),
            })

        # --- Folder path stats ---
        all_paths = [tuple(pm.get("folder_path_segments") or []) for pm in placemarks]
        depths = [len(p) for p in all_paths]
        if depths:
            empty_paths = sum(1 for p in all_paths if len(p) == 0)
            report["folder_stats"] = {
                "min_depth": min(depths),
                "max_depth": max(depths),
                "avg_depth": round(sum(depths) / len(depths), 1),
                "distinct_full_paths": len(set(all_paths)),
                "distinct_last3": len({tuple(p[-3:]) for p in all_paths}),
                "empty_paths": empty_paths,
            }
            if max(depths) > 3:
                deep = sum(1 for d in depths if d > 3)
                report["issues"].append({
                    "severity": "info",
                    "message": (
                        f"{deep} placemark(s) have folder paths deeper than "
                        f"3 levels; organize tool uses only the last 3 segments "
                        f"for FD/FC structural decisions"
                    ),
                })
            if empty_paths:
                report["issues"].append({
                    "severity": "info",
                    "message": (
                        f"{empty_paths} placemark(s) have empty folder paths; "
                        f"they will land at the GDB root in hierarchy mode"
                    ),
                })

        # --- Popup analysis (sampled to keep big KMZs fast) ---
        popup_parser = PopupParser()
        with_desc = sum(1 for pm in placemarks if pm.get("description"))
        with_ext = sum(1 for pm in placemarks if pm.get("extended_data"))

        tier_counter: Counter = Counter()
        sample_size = min(100, len(placemarks))
        for pm in placemarks[:sample_size]:
            ext_data = pm.get("extended_data") or {}
            desc = pm.get("description") or ""
            if ext_data:
                tier_counter["extended_data"] += 1
                continue
            if not desc:
                tier_counter["no_popup"] += 1
                continue
            # Probe which tier the converter would use
            attrs, _ = popup_parser.parse(desc, {}, strategy="html_table")
            if attrs:
                tier_counter["html_table"] += 1
                continue
            attrs, _ = popup_parser.parse(desc, {}, strategy="label_value")
            if attrs:
                tier_counter["label_value"] += 1
            else:
                tier_counter["unparseable"] += 1

        report["popups"] = {
            "with_description": with_desc,
            "empty": max(0, with_geom - with_desc),
            "with_extended_data": with_ext,
            "tier_breakdown_sample": dict(tier_counter),
            "sample_size": sample_size,
        }

        unparseable = tier_counter.get("unparseable", 0)
        if sample_size > 0 and unparseable > sample_size // 4:
            report["issues"].append({
                "severity": "warning",
                "message": (
                    f"{unparseable}/{sample_size} sampled popups did not "
                    f"resolve into key:value pairs -- those features will "
                    f"have only the RawPopup blob, no typed fields"
                ),
            })

        # --- Style coverage ---
        defined_styles = set(parser.get_styles().keys())
        with_styleurl = 0
        broken_refs: List[Dict[str, str]] = []
        for pm in placemarks:
            style_url = pm.get("style_url")
            if not style_url:
                continue
            with_styleurl += 1
            ref = style_url.lstrip("#")
            # External references (#styles.kml#foo) are out of scope here.
            if "/" not in ref and "#" not in style_url[1:] and ref not in defined_styles:
                broken_refs.append({
                    "name": pm.get("name") or "(unnamed)",
                    "ref": style_url,
                })

        report["styles"] = {
            "with_styleurl": with_styleurl,
            "defined": len(defined_styles),
            "broken_refs": broken_refs[:5],
            "broken_count": len(broken_refs),
        }

        if broken_refs:
            report["issues"].append({
                "severity": "warning",
                "message": (
                    f"{len(broken_refs)} placemark(s) reference undefined "
                    f"styles; their .lyrx symbology will be unset"
                ),
            })

        # --- NetworkLinks ---
        if total_networklinks > 0:
            nl_list = parser.get_network_links()
            report["network_links"] = nl_list
            report["issues"].append({
                "severity": "info",
                "message": (
                    f"{len(nl_list)} NetworkLink(s) found; enable "
                    f"'Follow NetworkLinks' on the converter to pull external "
                    f"content inline"
                ),
            })

    finally:
        parser.cleanup()

    return report


def format_report(report: Dict[str, Any], verbose: bool = False) -> List[str]:
    """Render an inspect_kmz() dict to printable lines.

    Pass verbose=True for a per-folder breakdown (currently a stub; the
    aggregate stats already convey most of what verbose mode would show).
    """
    lines: List[str] = []
    f = report["file"]
    lines.append("=" * 70)
    lines.append(f["name"])
    lines.append("=" * 70)

    size_kb = f["size_bytes"] / 1024.0
    lines.append(f"  Path:       {f['path']}")
    lines.append(f"  Size:       {size_kb:,.1f} KB")
    lines.append(f"  Format:     {f['format']}")
    if f["recovery_occurred"]:
        lines.append("  Recovery:   lxml repaired malformed XML to parse")

    c = report["counts"]
    lines.append("")
    lines.append("ELEMENTS")
    lines.append(f"  Placemarks (total):       {c['placemarks_total']}")
    lines.append(f"    with geometry:          {c['placemarks_with_geometry']}")
    if c["placemarks_no_geometry"]:
        lines.append(f"    NO geometry (skipped):  {c['placemarks_no_geometry']}")
    for geom, n in sorted(c["geometry_breakdown"].items(), key=lambda x: -x[1]):
        lines.append(f"      {(geom + ':'):<22} {n}")
    lines.append(f"  Folders:                  {c['folders']}")
    lines.append(f"  Styles:                   {c['styles']}")
    lines.append(f"  StyleMaps:                {c['stylemaps']}")
    lines.append(f"  Schemas:                  {c['schemas']}")
    lines.append(f"  NetworkLinks:             {c['network_links']}")

    if report["folder_stats"]:
        fs = report["folder_stats"]
        lines.append("")
        lines.append("FOLDER PATH STATS")
        lines.append(
            f"  Depth (min/max/avg):      "
            f"{fs['min_depth']} / {fs['max_depth']} / {fs['avg_depth']}"
        )
        lines.append(f"  Distinct full paths:      {fs['distinct_full_paths']}")
        lines.append(
            f"  Distinct last-3 keys:     "
            f"{fs['distinct_last3']}  (these drive FD/FC buckets in organize)"
        )
        if fs.get("empty_paths"):
            lines.append(f"  Empty paths:              {fs['empty_paths']}")

    p = report["popups"]
    if p:
        lines.append("")
        lines.append("POPUPS")
        lines.append(f"  With description:         {p['with_description']}")
        lines.append(f"  Empty:                    {p['empty']}")
        lines.append(f"  With ExtendedData:        {p['with_extended_data']}")
        lines.append(f"  Parse tier (sample of {p['sample_size']}):")
        for tier, n in sorted(p["tier_breakdown_sample"].items(), key=lambda x: -x[1]):
            lines.append(f"    {(tier + ':'):<22} {n}")

    s = report["styles"]
    if s:
        lines.append("")
        lines.append("STYLE COVERAGE")
        lines.append(f"  Placemarks w/ styleUrl:   {s['with_styleurl']}")
        lines.append(f"  Styles defined:           {s['defined']}")
        if s["broken_refs"]:
            lines.append(f"  Broken styleUrl refs:     {s['broken_count']}")
            for b in s["broken_refs"]:
                lines.append(f"    - {b['name'][:40]:<40} -> {b['ref']}")
            extra = s["broken_count"] - len(s["broken_refs"])
            if extra > 0:
                lines.append(f"    ... and {extra} more")

    if report["network_links"]:
        lines.append("")
        lines.append("NETWORKLINKS")
        for nl in report["network_links"][:10]:
            lines.append(f"  - {nl.get('name') or '(unnamed)'}")
            href = nl.get("href") or "(no href)"
            lines.append(f"      {href}")
        extra = len(report["network_links"]) - 10
        if extra > 0:
            lines.append(f"  ... and {extra} more")

    if report["issues"]:
        lines.append("")
        lines.append("POTENTIAL ISSUES / NOTES")
        for issue in report["issues"]:
            sev = issue["severity"].upper()
            lines.append(f"  [{sev:<7}] {issue['message']}")
    else:
        lines.append("")
        lines.append("No issues flagged.")

    return lines
