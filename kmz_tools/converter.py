# -*- coding: utf-8 -*-
"""KMZ → GDB conversion orchestrator.

Thin wrapper that ties together the parsers and the GDB writer. The .pyt
imports this and calls convert_kmz_to_gdb(); CLI users can call it too.
"""

from pathlib import Path
from collections import defaultdict

from .kml_parser import KMLParser
from .popup_parser import PopupParser
from .geometry_builder import GeometryBuilder
from .gdb_writer import SimpleGDBWriter, geometry_from_coords
from .naming import NamingResolver
from .network_loader import follow_network_links


def convert_kmz_to_gdb(kmz_path, output_folder, log=print,
                       follow_network_links_enabled=False,
                       network_link_max_depth=2,
                       network_link_timeout=30):
    """Convert one KMZ/KML file to a per-input GDB inside output_folder.

    kmz_path: str or Path to .kmz/.kml file
    output_folder: str or Path to folder. The GDB is written directly here
                   as <sanitized_stem>.gdb. If a GDB with the sanitized stem
                   already exists in this folder, a numeric suffix is added
                   to avoid silent overwrites (e.g. when two source KMZs
                   sanitize to the same stem).
    log: callable taking a single string (defaults to print; pass arcpy.AddMessage)

    Returns dict: {'gdb_path', 'inserted', 'skipped', 'placemarks',
                   'by_geometry': {'Point': N, 'LineString': N, 'Polygon': N},
                   'dropped_no_geometry': N}
    """
    kmz_path = Path(kmz_path)
    output_folder = Path(output_folder)
    if not output_folder.exists():
        raise FileNotFoundError(f"Output folder does not exist: {output_folder}")
    if not kmz_path.exists():
        raise FileNotFoundError(f"KMZ/KML not found: {kmz_path}")

    # Sanitize GDB name (no spaces, no invalid GDB chars). Disambiguate
    # against any existing GDB with the same sanitized stem -- two source
    # KMZs whose stems collapse to the same sanitized form would otherwise
    # silently overwrite each other.
    resolver = NamingResolver()
    kmz_name = kmz_path.name
    base_gdb_name = resolver.sanitize(kmz_path.stem)
    gdb_name = base_gdb_name
    suffix_n = 2
    while (output_folder / f"{gdb_name}.gdb").exists():
        gdb_name = f"{base_gdb_name}_{suffix_n}"
        suffix_n += 1
    if gdb_name != base_gdb_name:
        log(
            f"[NOTE] {base_gdb_name}.gdb already exists in scratch folder; "
            f"using {gdb_name}.gdb to avoid overwrite"
        )
    gdb_path = output_folder / f"{gdb_name}.gdb"
    log(f"Sanitized GDB name: '{kmz_path.stem}' -> '{gdb_name}'")

    log(f"Processing: {kmz_name}")
    log(f"Output GDB: {gdb_path}")

    # Parse KMZ
    parser = KMLParser(str(kmz_path))
    if not parser.extract_and_parse():
        parser.cleanup()
        raise RuntimeError(f"Failed to parse {kmz_name}")

    # Use the stats-bearing variant so we can surface silent geometry-less
    # drops to the user, instead of pretending they didn't exist.
    placemarks, parse_stats = parser.get_placemarks_with_stats()
    network_links = parser.get_network_links()
    log(
        f"Found {parse_stats['total_in_xml']} placemarks in source XML; "
        f"{len(placemarks)} have usable geometry, "
        f"{parse_stats['dropped_no_geometry']} dropped (no geometry)"
    )

    if network_links:
        log(f"Found {len(network_links)} NetworkLinks")
        for nl in network_links[:10]:
            log(f"  - {nl.get('name', '(unnamed)')}: {nl.get('href', '(no href)')[:120]}")
        if len(network_links) > 10:
            log(f"  ... and {len(network_links) - 10} more")

        if follow_network_links_enabled:
            log(f"\nFollowing NetworkLinks (max depth: {network_link_max_depth})...")
            extra_pms = follow_network_links(
                network_links,
                max_depth=network_link_max_depth,
                timeout=network_link_timeout,
                log=log,
            )
            placemarks.extend(extra_pms)
            log(f"  -> Pulled {len(extra_pms)} additional placemarks from NetworkLinks")
            log(f"  -> Total placemarks now: {len(placemarks)}")
        else:
            log("(NetworkLinks not followed - enable the 'Follow NetworkLinks' option to download)")

    if not placemarks:
        parser.cleanup()
        if network_links and not follow_network_links_enabled:
            log("[SKIP] No placemarks to convert. This KMZ only contains NetworkLinks. "
                "Enable 'Follow NetworkLinks' to download and parse them automatically.")
        elif network_links and follow_network_links_enabled:
            log("[SKIP] No placemarks were retrieved (NetworkLink downloads may have failed).")
        else:
            log("[SKIP] No placemarks found in this file.")
        return {
            "gdb_path": None,
            "inserted": 0,
            "skipped": 0,
            "placemarks": 0,
            "network_links": len(network_links),
            "by_geometry": {},
            "dropped_no_geometry": parse_stats["dropped_no_geometry"],
        }

    popup_parser = PopupParser()
    geometry_builder = GeometryBuilder(coordinate_precision=7, preserve_z=False)

    # Group rows by geometry type so we open one InsertCursor per type
    rows_by_type = defaultdict(list)
    skipped = 0

    for pm in placemarks:
        if not pm.get("geometry"):
            skipped += 1
            continue

        try:
            geometries = geometry_builder.build_geometries(
                pm["geometry"], pm["placemark_id"]
            )
        except Exception as e:
            log(f"  [WARN] geometry build error on {pm.get('placemark_id')}: {e}")
            skipped += 1
            continue

        if not geometries:
            skipped += 1
            continue

        try:
            _attrs, raw_popup = popup_parser.parse(
                pm.get("description", ""),
                pm.get("extended_data", {}),
                strategy="auto",
            )
        except Exception as e:
            log(f"  [WARN] popup parse error on {pm.get('placemark_id')}: {e}")
            raw_popup = ""

        name = pm.get("name") or "Unnamed"
        folder_path = "\\".join(pm.get("folder_path_segments", []))

        for geom_data in geometries:
            geom_type = geom_data["type"]
            arcpy_geom = geometry_from_coords(geom_type, geom_data.get("coordinates"))
            if arcpy_geom is None:
                skipped += 1
                continue

            rows_by_type[geom_type].append({
                "geometry": arcpy_geom,
                "name": name,
                "folder_path": folder_path,
                "raw_popup": raw_popup,
                "source_kmz": kmz_name,
            })

    parser.cleanup()

    # Write to GDB — one InsertCursor per geometry type
    writer = SimpleGDBWriter(gdb_path)
    inserted_total = 0
    by_geometry = {}

    for geom_type, rows in rows_by_type.items():
        log(f"Inserting {len(rows)} {geom_type} features...")
        inserted = writer.insert_features(geom_type, rows)
        inserted_total += inserted
        by_geometry[geom_type] = inserted
        log(f"  [OK] {inserted} {geom_type} features inserted")

    return {
        "gdb_path": str(gdb_path),
        "inserted": inserted_total,
        "skipped": skipped,
        "placemarks": len(placemarks),
        "by_geometry": by_geometry,
        "dropped_no_geometry": parse_stats["dropped_no_geometry"],
    }


if __name__ == "__main__":
    # Standalone CLI: python -m kmz_tools.converter <kmz> <output_folder>
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m kmz_tools.converter <kmz_file> <output_folder>")
        sys.exit(1)
    result = convert_kmz_to_gdb(sys.argv[1], sys.argv[2])
    print(f"\n[DONE] inserted={result['inserted']} skipped={result['skipped']}")
    print(f"       by_geometry={result['by_geometry']}")
    print(f"       gdb={result['gdb_path']}")
