"""Parse KML styles and StyleMaps, resolve references."""

from typing import Dict, Optional, Tuple
from lxml import etree


class StyleParser:
    """Extract and resolve KML styles and StyleMaps."""

    def __init__(self, root_element):
        """
        Initialize parser with KML root element.

        Args:
            root_element: lxml root element from parsed KML
        """
        self.root = root_element
        self.namespaces = {
            'kml': 'http://www.opengis.net/kml/2.2',
            'gx': 'http://www.google.com/kml/ext/2.2'
        }
        self.styles = {}
        self.style_maps = {}
        self._extract_all_styles()

    def _extract_all_styles(self):
        """Extract all Style and StyleMap elements."""
        # Extract Style elements
        for style in self.root.findall('.//kml:Style', self.namespaces):
            style_id = style.get('id')
            if style_id:
                self.styles[style_id] = style

        # Extract StyleMap elements
        for style_map in self.root.findall('.//kml:StyleMap', self.namespaces):
            style_id = style_map.get('id')
            if style_id:
                self.style_maps[style_id] = style_map

    def resolve_style_id(self, style_url: str) -> Optional[str]:
        """
        Resolve a styleUrl reference to final style ID.

        Follows StyleMap "normal" state if styleUrl points to a StyleMap.

        Args:
            style_url: Value of <styleUrl> (e.g., '#styleId' or 'styles.kml#styleId')

        Returns:
            Final style ID, or None if not found
        """
        if not style_url:
            return None

        # Extract ID from URL (handle #id and filename#id formats)
        if '#' in style_url:
            style_id = style_url.split('#')[-1]
        else:
            style_id = style_url

        # Check if it's a StyleMap
        if style_id in self.style_maps:
            return self._resolve_style_map(style_id)

        # Check if it's a direct Style reference
        if style_id in self.styles:
            return style_id

        return None

    def _resolve_style_map(self, style_map_id: str) -> Optional[str]:
        """
        Resolve StyleMap to style ID via "normal" state.

        Args:
            style_map_id: ID of StyleMap element

        Returns:
            Style ID referenced by "normal" state, or None
        """
        style_map = self.style_maps.get(style_map_id)
        if style_map is None:
            return None

        # Find the "normal" Pair element
        for pair in style_map.findall('kml:Pair', self.namespaces):
            key = pair.find('kml:key', self.namespaces)
            if key is not None and key.text and key.text.strip() == 'normal':
                # Found "normal" pair, get its styleUrl
                style_url_elem = pair.find('kml:styleUrl', self.namespaces)
                if style_url_elem is not None and style_url_elem.text:
                    ref_style_url = style_url_elem.text.strip()
                    # Recursively resolve (in case it's another StyleMap)
                    if '#' in ref_style_url:
                        ref_id = ref_style_url.split('#')[-1]
                    else:
                        ref_id = ref_style_url

                    if ref_id in self.style_maps:
                        return self._resolve_style_map(ref_id)
                    elif ref_id in self.styles:
                        return ref_id

        return None

    def get_style_properties(self, style_id: str) -> Dict[str, any]:
        """
        Extract style properties (colors, widths, etc.).

        Args:
            style_id: ID of Style element

        Returns:
            Dict with extracted properties (format version 1: simple props only)
        """
        style = self.styles.get(style_id)
        if not style:
            return {}

        props = {'style_id': style_id}

        # IconStyle (Points)
        icon_style = style.find('kml:IconStyle', self.namespaces)
        if icon_style is not None:
            color = icon_style.find('kml:color', self.namespaces)
            if color is not None and color.text:
                props['icon_color'] = color.text.strip()

            scale = icon_style.find('kml:scale', self.namespaces)
            if scale is not None and scale.text:
                try:
                    props['icon_scale'] = float(scale.text)
                except ValueError:
                    pass

            icon_href = icon_style.find('kml:Icon/kml:href', self.namespaces)
            if icon_href is not None and icon_href.text:
                props['icon_href'] = icon_href.text.strip()

        # LineStyle
        line_style = style.find('kml:LineStyle', self.namespaces)
        if line_style is not None:
            color = line_style.find('kml:color', self.namespaces)
            if color is not None and color.text:
                props['line_color'] = color.text.strip()

            width = line_style.find('kml:width', self.namespaces)
            if width is not None and width.text:
                try:
                    props['line_width'] = float(width.text)
                except ValueError:
                    pass

        # PolyStyle (Polygons)
        poly_style = style.find('kml:PolyStyle', self.namespaces)
        if poly_style is not None:
            color = poly_style.find('kml:color', self.namespaces)
            if color is not None and color.text:
                props['poly_color'] = color.text.strip()

            fill = poly_style.find('kml:fill', self.namespaces)
            if fill is not None and fill.text:
                props['poly_fill'] = fill.text.strip() == '1'

            outline = poly_style.find('kml:outline', self.namespaces)
            if outline is not None and outline.text:
                props['poly_outline'] = outline.text.strip() == '1'

        return props

    def get_all_style_ids(self) -> list:
        """Get list of all defined style IDs."""
        return list(self.styles.keys()) + list(self.style_maps.keys())
