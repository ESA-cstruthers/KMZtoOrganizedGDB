# -*- coding: utf-8 -*-
"""GDB write operations for KMZ-to-GDB conversion.

Phase 1 (raw extraction): one GDB per KMZ with Point/Polyline/Polygon FCs
and minimal tracking fields (Name, FolderPath, RawPopup, SourceKMZ).
"""

from pathlib import Path
import arcpy


# WGS84 — KMZ source SR. Constant, shared across writers.
SR_WGS84 = arcpy.SpatialReference(4326)

# Geometry type -> (FC name, arcpy geometry type token)
GEOM_TYPE_MAP = {
    "Point": ("Point", "POINT"),
    "LineString": ("Polyline", "POLYLINE"),
    "Polygon": ("Polygon", "POLYGON"),
}

# Field schema for every FC created by this writer.
SCHEMA_FIELDS = [
    # (name, type, length_or_None)
    ("Name", "TEXT", 255),
    ("FolderPath", "TEXT", 500),
    ("RawPopup", "TEXT", 4000),
    ("SourceKMZ", "TEXT", 255),
]

# Cursor field order for inserts. Must match insert_feature row order.
CURSOR_FIELDS = ["SHAPE@", "Name", "FolderPath", "RawPopup", "SourceKMZ"]


class SimpleGDBWriter:
    """Creates a GDB with Point/Polyline/Polygon FCs and inserts features."""

    def __init__(self, output_gdb_path):
        """Create the GDB if missing. output_gdb_path may be str or Path."""
        self.gdb_path_obj = Path(output_gdb_path)
        self.gdb_path = str(self.gdb_path_obj)

        if not arcpy.Exists(self.gdb_path):
            arcpy.management.CreateFileGDB(
                str(self.gdb_path_obj.parent),
                self.gdb_path_obj.stem,
            )

        if not arcpy.Exists(self.gdb_path):
            raise RuntimeError(
                f"Failed to create geodatabase: {self.gdb_path}\n"
                f"{arcpy.GetMessages(2)}"
            )

        # Cache: geom_type_key -> fc_path (so we don't recreate)
        self._fc_cache = {}

    def get_or_create_fc(self, geometry_type):
        """Return path to FC for the given KML geometry type, creating if missing.

        geometry_type: 'Point', 'LineString', or 'Polygon' (KML names).
        """
        if geometry_type not in GEOM_TYPE_MAP:
            raise ValueError(f"Unsupported geometry type: {geometry_type}")

        fc_name, arcpy_type = GEOM_TYPE_MAP[geometry_type]

        if fc_name in self._fc_cache:
            return self._fc_cache[fc_name]

        fc_path = str(self.gdb_path_obj / fc_name)

        if not arcpy.Exists(fc_path):
            try:
                arcpy.management.CreateFeatureclass(
                    self.gdb_path,
                    fc_name,
                    geometry_type=arcpy_type,
                    spatial_reference=SR_WGS84,
                )
            except arcpy.ExecuteError:
                raise RuntimeError(
                    f"CreateFeatureclass failed for {fc_path}:\n"
                    f"{arcpy.GetMessages(2)}"
                )

            for field_name, field_type, field_length in SCHEMA_FIELDS:
                kwargs = {"field_type": field_type}
                if field_length is not None:
                    kwargs["field_length"] = field_length
                arcpy.management.AddField(fc_path, field_name, **kwargs)

            if not arcpy.Exists(fc_path):
                raise RuntimeError(
                    f"FC creation reported success but FC missing: {fc_path}\n"
                    f"{arcpy.GetMessages(2)}"
                )

        self._fc_cache[fc_name] = fc_path
        return fc_path

    def insert_features(self, geometry_type, rows):
        """Insert many features into the FC for this geometry type.

        rows: iterable of dicts with keys 'geometry', 'name', 'folder_path',
              'raw_popup', 'source_kmz'. Geometry must be an arcpy.Geometry
              with SR_WGS84 already set.

        Returns the number of features inserted.
        """
        fc_path = self.get_or_create_fc(geometry_type)

        inserted = 0
        try:
            with arcpy.da.InsertCursor(fc_path, CURSOR_FIELDS) as cursor:
                for row in rows:
                    geom = row.get("geometry")
                    if geom is None:
                        continue
                    cursor.insertRow((
                        geom,
                        row.get("name", "") or "",
                        row.get("folder_path", "") or "",
                        (row.get("raw_popup", "") or "")[:4000],
                        row.get("source_kmz", "") or "",
                    ))
                    inserted += 1
        except arcpy.ExecuteError:
            raise RuntimeError(
                f"InsertCursor failed on {fc_path}:\n{arcpy.GetMessages(2)}"
            )

        return inserted


def geometry_from_coords(geometry_type, coordinates):
    """Convert parsed KML coordinates to arcpy geometry (WGS84).

    geometry_type: 'Point' | 'LineString' | 'Polygon'
    coordinates: per geometry_builder output format
    Returns arcpy.Geometry or None.
    """
    if geometry_type == "Point":
        lon, lat = coordinates[0], coordinates[1]
        return arcpy.PointGeometry(arcpy.Point(lon, lat), SR_WGS84)

    if geometry_type == "LineString":
        pts = arcpy.Array([arcpy.Point(c[0], c[1]) for c in coordinates])
        return arcpy.Polyline(pts, SR_WGS84)

    if geometry_type == "Polygon":
        outer = coordinates.get("outer", [])
        inners = coordinates.get("inner", [])
        if not outer:
            return None

        rings = arcpy.Array()
        rings.add(arcpy.Array([arcpy.Point(c[0], c[1]) for c in outer]))
        for inner in inners:
            rings.add(arcpy.Array([arcpy.Point(c[0], c[1]) for c in inner]))
        return arcpy.Polygon(rings, SR_WGS84)

    return None
