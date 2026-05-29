"""Convert KML geometries to ArcPy-compatible geometry objects."""

from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict


class GeometryBuilder:
    """Build ArcPy geometry objects from parsed KML geometry data."""

    def __init__(self, coordinate_precision: int = 7, preserve_z: bool = False):
        """
        Initialize builder.

        Args:
            coordinate_precision: Number of decimal places to round coordinates
            preserve_z: If True, keep Z values; otherwise discard
        """
        self.coordinate_precision = coordinate_precision
        self.preserve_z = preserve_z

    def build_geometries(self, kml_geometry: Dict[str, Any],
                        placemark_id: str) -> List[Dict[str, Any]]:
        """
        Build geometry objects from KML geometry dict.

        Handles MultiGeometry by splitting mixed types into separate features.

        Args:
            kml_geometry: {'type': ..., 'data': ...} dict from parser
            placemark_id: UUID for linking split geometries

        Returns:
            List of geometry dicts, each with:
            - type: 'Point', 'LineString', 'Polygon'
            - coordinates: Normalized coordinate structure
            - placemark_id: UUID (same for all splits)
        """
        geom_type = kml_geometry.get('type')

        if geom_type == 'Point':
            return [self._build_point(kml_geometry, placemark_id)]

        elif geom_type == 'LineString':
            return [self._build_linestring(kml_geometry, placemark_id)]

        elif geom_type == 'Polygon':
            return [self._build_polygon(kml_geometry, placemark_id)]

        elif geom_type == 'MultiGeometry':
            return self._build_multi_geometry(kml_geometry, placemark_id)

        return []

    def _build_point(self, kml_geometry: Dict[str, Any], placemark_id: str) -> Dict[str, Any]:
        """Build Point geometry."""
        data = kml_geometry.get('data')
        if not data:
            return None

        lon, lat, alt = data[0], data[1], data[2] if len(data) > 2 else None
        lon, lat = self._round_coords(lon, lat)

        return {
            'type': 'Point',
            'coordinates': (lon, lat, alt) if (self.preserve_z and alt) else (lon, lat),
            'placemark_id': placemark_id,
        }

    def _build_linestring(self, kml_geometry: Dict[str, Any], placemark_id: str) -> Dict[str, Any]:
        """Build LineString (Polyline) geometry."""
        data = kml_geometry.get('data', [])
        if not data:
            return None

        coords = []
        for coord in data:
            lon, lat = coord[0], coord[1]
            alt = coord[2] if len(coord) > 2 else None
            lon, lat = self._round_coords(lon, lat)

            if self.preserve_z and alt:
                coords.append((lon, lat, alt))
            else:
                coords.append((lon, lat))

        return {
            'type': 'LineString',
            'coordinates': coords,
            'placemark_id': placemark_id,
        }

    def _build_polygon(self, kml_geometry: Dict[str, Any], placemark_id: str) -> Dict[str, Any]:
        """Build Polygon geometry."""
        data = kml_geometry.get('data', {})
        if 'outer' not in data:
            return None

        rings = {'outer': self._round_ring(data['outer'])}

        if 'inner' in data:
            rings['inner'] = [self._round_ring(ring) for ring in data['inner']]

        return {
            'type': 'Polygon',
            'coordinates': rings,
            'placemark_id': placemark_id,
        }

    def _build_multi_geometry(self, kml_geometry: Dict[str, Any],
                             placemark_id: str) -> List[Dict[str, Any]]:
        """
        Build MultiGeometry by grouping same types together.

        MultiGeometry containing 1 geometry → treat as bare type, no suffix.
        MultiGeometry containing N of same type → multipart feature.
        MultiGeometry containing mixed types → split into separate features.
        """
        geoms = kml_geometry.get('data', [])
        if not geoms:
            return []

        # Single geometry in MultiGeometry → treat as bare type
        if len(geoms) == 1:
            single_geom = geoms[0].copy()
            return self.build_geometries(single_geom, placemark_id)

        # Group by type
        by_type = defaultdict(list)
        for geom in geoms:
            by_type[geom['type']].append(geom)

        # Single type with multiple → multipart
        if len(by_type) == 1:
            geom_type = list(by_type.keys())[0]
            geom_data = [g['data'] for g in by_type[geom_type]]

            if geom_type == 'Point':
                # Multiple points → keep as individual points
                return [self._build_point({'type': 'Point', 'data': [g]}, placemark_id)
                       for g in geom_data]

            elif geom_type == 'LineString':
                # Multiple linestrings → build as multipart
                coords_list = []
                for geom in by_type[geom_type]:
                    coords_list.extend(geom['data'])

                return [{
                    'type': 'LineString',
                    'coordinates': self._round_coords_list(coords_list),
                    'placemark_id': placemark_id,
                }]

            elif geom_type == 'Polygon':
                # Multiple polygons → keep as individual polygons
                return [self._build_polygon({'type': 'Polygon', 'data': g['data']}, placemark_id)
                       for g in by_type[geom_type]]

        # Mixed types → split into separate features per type
        result = []
        for geom_type, geoms_of_type in by_type.items():
            for geom in geoms_of_type:
                built = self.build_geometries(geom, placemark_id)
                result.extend(built)

        return result

    def _round_coords(self, lon: float, lat: float) -> Tuple[float, float]:
        """Round a single coordinate pair."""
        return (
            round(lon, self.coordinate_precision),
            round(lat, self.coordinate_precision)
        )

    def _round_ring(self, ring: List[Tuple[float, float, Optional[float]]]) -> List[Tuple[float, float]]:
        """Round all coordinates in a ring."""
        rounded = []
        for coord in ring:
            lon, lat = coord[0], coord[1]
            alt = coord[2] if len(coord) > 2 else None
            lon, lat = self._round_coords(lon, lat)

            if self.preserve_z and alt:
                rounded.append((lon, lat, alt))
            else:
                rounded.append((lon, lat))

        return rounded

    def _round_coords_list(self, coords: List[Tuple[float, float, Optional[float]]]) -> List[Tuple[float, float]]:
        """Round all coordinates in a list."""
        rounded = []
        for coord in coords:
            lon, lat = coord[0], coord[1]
            alt = coord[2] if len(coord) > 2 else None
            lon, lat = self._round_coords(lon, lat)

            if self.preserve_z and alt:
                rounded.append((lon, lat, alt))
            else:
                rounded.append((lon, lat))

        return rounded
