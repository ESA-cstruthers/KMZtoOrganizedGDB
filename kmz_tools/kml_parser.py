"""KML parsing with lxml recovery, folder structure handling, and placemark extraction."""

import os
import tempfile
import zipfile
import uuid
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from lxml import etree


class KMLParser:
    """Parse KMZ/KML files with lxml recovery, extract placemarks, and build folder hierarchy."""

    def __init__(self, kmz_path: str, recover: bool = True):
        """
        Initialize parser for a KMZ or KML file.

        Args:
            kmz_path: Path to .kmz or .kml file
            recover: If True, use recover=True for malformed XML
        """
        self.kmz_path = Path(kmz_path)
        self.recover = recover
        self.temp_dir = None
        self.kml_path = None
        self.root = None
        self.recovery_occurred = False
        self.namespaces = {
            'kml': 'http://www.opengis.net/kml/2.2',
            'gx': 'http://www.google.com/kml/ext/2.2'
        }

    def extract_and_parse(self) -> bool:
        """
        Extract KMZ and parse KML with recovery. Returns True on success.
        """
        try:
            # Determine if input is KML or KMZ
            if self.kmz_path.suffix.lower() == '.kml':
                self.kml_path = self.kmz_path
            else:
                # Extract KMZ. Guard against Zip Slip (member paths that resolve
                # outside temp_dir) by validating every member before extract.
                self.temp_dir = tempfile.mkdtemp(prefix='kmz_')
                temp_root = Path(self.temp_dir).resolve()
                with zipfile.ZipFile(self.kmz_path, 'r') as zf:
                    for member in zf.infolist():
                        target = (temp_root / member.filename).resolve()
                        try:
                            target.relative_to(temp_root)
                        except ValueError:
                            raise RuntimeError(
                                f"KMZ archive contains an unsafe path "
                                f"(outside extraction root): {member.filename}"
                            )
                        zf.extract(member, self.temp_dir)

                # Prefer doc.kml; fall back to the largest .kml at shallowest
                # depth. Many real-world KMZs ship the KML as <stem>.kml.
                candidates = []  # list of (depth, -size, path) for sort
                for root, dirs, files in os.walk(self.temp_dir):
                    rel_depth = Path(root).relative_to(self.temp_dir).parts
                    depth = len(rel_depth)
                    for f in files:
                        if f.lower().endswith('.kml'):
                            p = Path(root) / f
                            if f.lower() == 'doc.kml':
                                self.kml_path = p
                                break
                            candidates.append((depth, -p.stat().st_size, p))
                    if self.kml_path:
                        break

                if not self.kml_path and candidates:
                    candidates.sort()
                    self.kml_path = candidates[0][2]

                if not self.kml_path:
                    raise FileNotFoundError(
                        f"No .kml file found inside {self.kmz_path.name}"
                    )

            # Parse KML with recovery
            parser = etree.XMLParser(recover=self.recover)
            tree = etree.parse(str(self.kml_path), parser)
            self.root = tree.getroot()

            # Check if recovery occurred
            if self.recover and parser.error_log:
                self.recovery_occurred = True

            return True
        except Exception as e:
            raise RuntimeError(f"Failed to parse KML: {e}")

    def get_placemarks(self) -> List[Dict[str, Any]]:
        """Extract placemarks with usable geometry. See get_placemarks_with_stats."""
        return self.get_placemarks_with_stats()[0]

    def get_placemarks_with_stats(self) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Extract placemarks with usable geometry plus drop statistics.

        Returns (placemarks, stats) where:
          placemarks: list of dicts (same shape as get_placemarks())
          stats: {'total_in_xml': N, 'dropped_no_geometry': M}

        Use stats to surface silent drops in the converter log -- a placemark
        without geometry is filtered out by _parse_placemark, which would
        otherwise be invisible to the caller.
        """
        if self.root is None:
            raise RuntimeError("KML not parsed. Call extract_and_parse() first.")

        total_in_xml = len(self.root.findall('.//kml:Placemark', self.namespaces))
        placemarks = []

        def walk_folder(element, parent_folders: List[str]) -> None:
            """Recursively walk folder structure and collect placemarks."""
            tag = self._get_local_tag(element.tag)

            if tag == 'Folder':
                name = self._get_text(element, 'kml:name')
                if name:
                    parent_folders.append(name)

            elif tag == 'Document':
                # Documents are transparent; don't add to folder path
                pass

            # Process children
            for child in element:
                child_tag = self._get_local_tag(child.tag)

                if child_tag == 'Placemark':
                    pm = self._parse_placemark(child, parent_folders[:])
                    if pm:
                        placemarks.append(pm)

                elif child_tag in ('Folder', 'Document'):
                    walk_folder(child, parent_folders[:])

        # Start walk from root's children
        for child in self.root:
            walk_folder(child, [])

        # Collapse consecutive duplicate folder names (case-insensitive)
        for pm in placemarks:
            pm['folder_path_segments'] = self._collapse_duplicates(pm['folder_path_segments'])

        stats = {
            'total_in_xml': total_in_xml,
            'dropped_no_geometry': max(0, total_in_xml - len(placemarks)),
        }
        return placemarks, stats

    def get_network_links(self) -> List[Dict[str, Any]]:
        """
        Extract all NetworkLink elements with the folder path where they live.

        Returns list of dicts with keys:
        - name: Link name
        - href: Link URL
        - refresh_mode: Refresh mode (if present)
        - refresh_interval: Refresh interval (if present)
        - view_refresh_mode: View refresh mode (if present)
        - folder_path_segments: List[str] folders containing this NetworkLink
        """
        if self.root is None:
            raise RuntimeError("KML not parsed. Call extract_and_parse() first.")

        network_links: List[Dict[str, Any]] = []

        def walk(element, parent_folders: List[str]) -> None:
            tag = self._get_local_tag(element.tag)

            # Folders contribute to the path; Document is transparent
            current_folders = parent_folders
            if tag == 'Folder':
                name = self._get_text(element, 'kml:name')
                if name:
                    current_folders = parent_folders + [name]

            for child in element:
                child_tag = self._get_local_tag(child.tag)

                if child_tag == 'NetworkLink':
                    network_links.append({
                        'name': self._get_text(child, 'kml:name') or 'Unnamed',
                        'href': self._get_text(child, 'kml:Link/kml:href') or '',
                        'refresh_mode': self._get_text(child, 'kml:Link/kml:refreshMode') or '',
                        'refresh_interval': self._get_text(child, 'kml:Link/kml:refreshInterval') or '',
                        'view_refresh_mode': self._get_text(child, 'kml:Link/kml:viewRefreshMode') or '',
                        'folder_path_segments': self._collapse_duplicates(current_folders[:]),
                    })

                elif child_tag in ('Folder', 'Document'):
                    walk(child, current_folders[:])

        for child in self.root:
            walk(child, [])

        return network_links

    def get_styles(self) -> Dict[str, Dict[str, Any]]:
        """
        Extract all Style and StyleMap elements.

        Returns dict keyed by style id with full XML element data.
        """
        if self.root is None:
            raise RuntimeError("KML not parsed. Call extract_and_parse() first.")

        styles = {}

        # Extract Style elements
        for style in self.root.findall('.//kml:Style', self.namespaces):
            style_id = style.get('id')
            if style_id:
                styles[style_id] = etree.tostring(style, encoding='unicode')

        # Extract StyleMap elements
        for style_map in self.root.findall('.//kml:StyleMap', self.namespaces):
            style_id = style_map.get('id')
            if style_id:
                styles[style_id] = etree.tostring(style_map, encoding='unicode')

        return styles

    def cleanup(self):
        """Clean up temporary directory if created."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)

    # Private helpers

    def _get_local_tag(self, tag) -> str:
        """Extract local tag name from namespaced tag.

        lxml represents comments/PIs with a function-valued .tag (e.g.
        Comment, ProcessingInstruction) rather than a string. Treat those
        as empty so the caller's tag comparisons just fall through.
        """
        if not isinstance(tag, str):
            return ''
        if '}' in tag:
            return tag.split('}')[1]
        return tag

    def _get_text(self, element, xpath: str) -> Optional[str]:
        """Get text content from element using xpath."""
        try:
            result = element.find(xpath, self.namespaces)
            if result is not None and result.text:
                return result.text.strip()
        except Exception:
            pass
        return None

    def _collapse_duplicates(self, segments: List[str]) -> List[str]:
        """Collapse consecutive duplicate folder names (case-insensitive)."""
        if not segments:
            return []

        result = [segments[0]]
        for seg in segments[1:]:
            if seg.lower() != result[-1].lower():
                result.append(seg)
        return result

    def _parse_placemark(self, pm_element, folder_path: List[str]) -> Optional[Dict[str, Any]]:
        """Parse a single Placemark element."""
        pm_dict = {
            'folder_path_segments': folder_path,
            'placemark_id': str(uuid.uuid4()),
            'name': self._get_text(pm_element, 'kml:name'),
            'description': self._get_text(pm_element, 'kml:description'),
            'style_url': self._get_text(pm_element, 'kml:styleUrl'),
            'timestamp': self._get_text(pm_element, 'kml:TimeStamp/kml:when'),
            'timespan': self._parse_timespan(pm_element),
            'extended_data': self._parse_extended_data(pm_element),
            'geometry': self._parse_geometry(pm_element),
        }

        return pm_dict if pm_dict['geometry'] else None

    def _parse_timespan(self, element) -> Optional[Dict[str, str]]:
        """Parse TimeSpan element."""
        ts = element.find('kml:TimeSpan', self.namespaces)
        if ts is not None:
            begin = self._get_text(ts, 'kml:begin')
            end = self._get_text(ts, 'kml:end')
            if begin or end:
                return {'begin': begin, 'end': end}
        return None

    def _parse_extended_data(self, element) -> Dict[str, str]:
        """Parse the Placemark's own ExtendedData (not descendants' -- a child
        Folder/Placemark could legally contain its own ExtendedData; using the
        descendant axis would slurp those in too)."""
        ext_data = {}

        # Handle <Data> elements (Placemark > ExtendedData > Data)
        for data_elem in element.findall('kml:ExtendedData/kml:Data', self.namespaces):
            name = data_elem.get('name')
            if name:
                value = self._get_text(data_elem, 'kml:value')
                if not value:
                    value = data_elem.text
                ext_data[name] = value or ''

        # Handle <SchemaData> with <SimpleData>
        for schema_data in element.findall(
            'kml:ExtendedData/kml:SchemaData/kml:SimpleData', self.namespaces
        ):
            name = schema_data.get('name')
            if name:
                ext_data[name] = schema_data.text or ''

        return ext_data

    def _parse_geometry(self, element) -> Optional[Dict[str, Any]]:
        """
        Parse geometry from Placemark.

        Returns dict with 'type' and 'data' keys, or None if no geometry found.
        """
        # Check for Point
        point = element.find('kml:Point', self.namespaces)
        if point is not None:
            coords = self._parse_coordinates(point)
            if coords:
                return {
                    'type': 'Point',
                    'data': coords[0] if coords else None,
                    'altitude_mode': self._get_text(point, 'kml:altitudeMode')
                }

        # Check for LineString
        linestring = element.find('kml:LineString', self.namespaces)
        if linestring is not None:
            coords = self._parse_coordinates(linestring)
            if coords:
                return {
                    'type': 'LineString',
                    'data': coords,
                    'altitude_mode': self._get_text(linestring, 'kml:altitudeMode')
                }

        # Check for Polygon
        polygon = element.find('kml:Polygon', self.namespaces)
        if polygon is not None:
            rings = self._parse_polygon_rings(polygon)
            if rings:
                return {
                    'type': 'Polygon',
                    'data': rings,
                    'altitude_mode': self._get_text(polygon, 'kml:altitudeMode')
                }

        # Check for MultiGeometry
        multi = element.find('kml:MultiGeometry', self.namespaces)
        if multi is not None:
            geometries = self._parse_multi_geometry(multi)
            if geometries:
                return {
                    'type': 'MultiGeometry',
                    'data': geometries
                }

        return None

    def _parse_coordinates(self, element) -> Optional[List[Tuple[float, float, Optional[float]]]]:
        """
        Parse <coordinates> element.

        Returns list of (lon, lat, alt) tuples, or None if empty.
        """
        coords_text = self._get_text(element, 'kml:coordinates')
        if not coords_text:
            return None

        coords = []
        for coord_str in coords_text.split():
            parts = coord_str.strip().split(',')
            if len(parts) >= 2:
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                    alt = float(parts[2]) if len(parts) > 2 else None
                    coords.append((lon, lat, alt))
                except ValueError:
                    continue

        return coords if coords else None

    def _parse_polygon_rings(self, polygon) -> Optional[Dict[str, List[Tuple[float, float, Optional[float]]]]]:
        """Parse outer and inner rings of a Polygon."""
        rings = {}

        # Outer boundary
        outer = polygon.find('kml:outerBoundaryIs/kml:LinearRing', self.namespaces)
        if outer is not None:
            coords = self._parse_coordinates(outer)
            if coords:
                rings['outer'] = coords

        # Inner boundaries
        inners = []
        for inner in polygon.findall('kml:innerBoundaryIs/kml:LinearRing', self.namespaces):
            coords = self._parse_coordinates(inner)
            if coords:
                inners.append(coords)
        if inners:
            rings['inner'] = inners

        return rings if 'outer' in rings else None

    def _parse_multi_geometry(self, multi) -> Optional[List[Dict[str, Any]]]:
        """Parse MultiGeometry element and extract all contained geometries."""
        geometries = []

        for child in multi:
            tag = self._get_local_tag(child.tag)

            if tag == 'Point':
                coords = self._parse_coordinates(child)
                if coords:
                    geometries.append({
                        'type': 'Point',
                        'data': coords[0]
                    })

            elif tag == 'LineString':
                coords = self._parse_coordinates(child)
                if coords:
                    geometries.append({
                        'type': 'LineString',
                        'data': coords
                    })

            elif tag == 'Polygon':
                rings = self._parse_polygon_rings(child)
                if rings:
                    geometries.append({
                        'type': 'Polygon',
                        'data': rings
                    })

            elif tag == 'MultiGeometry':
                # Recursive case
                nested = self._parse_multi_geometry(child)
                if nested:
                    geometries.extend(nested)

        return geometries if geometries else None
