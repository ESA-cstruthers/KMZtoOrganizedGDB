"""Three-tier popup attribute parser: ExtendedData, HTML table, label:value pattern."""

import re
import html as html_module
from typing import Dict, Optional, List, Tuple
from lxml import etree, html as lxml_html


class PopupParser:
    """Extract structured attributes from KML description fields via three-tier parsing."""

    def __init__(self, null_sentinels: Optional[List[str]] = None):
        """
        Initialize parser.

        Args:
            null_sentinels: List of strings to treat as null (in addition to '<Null>')
        """
        self.null_sentinels = null_sentinels or []

    def parse(self, description: str, extended_data: Dict[str, str],
              strategy: str = 'auto') -> Tuple[Dict[str, str], str]:
        """
        Parse popup attributes using three-tier strategy.

        Args:
            description: Raw description HTML string
            extended_data: Dict of ExtendedData fields already parsed from KML
            strategy: 'auto', 'extended_data_only', 'html_table', 'label_value'

        Returns:
            Tuple of (attributes_dict, raw_popup_truncated)
        """
        raw_popup = self._truncate_popup(description) if description else ''

        if strategy == 'extended_data_only':
            return self._clean_values(extended_data), raw_popup

        # Auto-detect and run tier 1-3
        if strategy == 'auto' or strategy == 'extended_data':
            if extended_data:
                return self._clean_values(extended_data), raw_popup

        if strategy == 'auto' or strategy == 'html_table':
            if description:
                attrs = self._parse_html_table(description)
                if attrs:
                    return self._clean_values(attrs), raw_popup

        if strategy == 'auto' or strategy == 'label_value':
            if description:
                attrs = self._parse_label_value(description)
                if attrs:
                    return self._clean_values(attrs), raw_popup

        # Fallback: return extended_data only
        return self._clean_values(extended_data), raw_popup

    def _parse_html_table(self, html_str: str) -> Optional[Dict[str, str]]:
        """
        Tier 2: Extract attributes from HTML table.

        Looks for table where each row has exactly 2 cells.
        Handles ArcGIS Pro's nested-table pattern.
        """
        if not html_str:
            return None

        try:
            # Parse HTML
            try:
                doc = lxml_html.fromstring(html_str)
            except:
                # Fallback for broken HTML
                doc = lxml_html.fragment_fromstring(html_str, create_parent='div')

            attrs = {}

            # Find all tables
            tables = doc.xpath('.//table')
            if not tables:
                return None

            for table in tables:
                rows = table.xpath('.//tr')
                if not rows:
                    continue

                # Check if this is a 2-column attribute table
                is_attr_table = all(len(row.xpath('.//td | .//th')) == 2 for row in rows if row.xpath('.//td | .//th'))

                if not is_attr_table:
                    # Try nested table pattern (ArcGIS Pro)
                    # Outer wrapper table with single cell containing inner attribute table
                    if len(rows) == 1:
                        cells = rows[0].xpath('.//td | .//th')
                        if len(cells) == 1:
                            inner_tables = cells[0].xpath('.//table')
                            if inner_tables:
                                # Recursively process inner table
                                inner_html = etree.tostring(inner_tables[0], encoding='unicode')
                                return self._parse_html_table(inner_html)
                    continue

                # Extract key-value pairs from 2-column table
                for row in rows:
                    cells = row.xpath('.//td | .//th')
                    if len(cells) == 2:
                        key = self._extract_cell_text(cells[0]).strip()
                        value = self._extract_cell_text(cells[1]).strip()
                        if key:
                            attrs[key] = value

                # Return first valid table found
                if attrs:
                    return attrs

        except Exception:
            pass

        return None

    def _parse_label_value(self, html_str: str) -> Optional[Dict[str, str]]:
        """
        Tier 3: Extract attributes from label:value pattern.

        Matches patterns like:
        - <b>SiteID:</b> A-042<br>
        - <strong>Name:</strong> Site A<br>
        - Label: Value<br>
        """
        if not html_str:
            return None

        attrs = {}

        # Unescape HTML entities first
        text = html_module.unescape(html_str)

        # Pattern: word(s) followed by colon, then value, then <br> or newline
        # Match <b>Label:</b> Value<br> or Label: Value<br>
        pattern = r'(?:<[^>]*>)?([^:<>\n]+)(?::</[^>]*>|:)(?:</[^>]*>)?\s*([^<\n]+?)(?:\s*<br|$)'

        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for label, value in matches:
            label = label.strip()
            value = value.strip()
            if label and value:
                attrs[label] = value

        return attrs if attrs else None

    def _clean_values(self, attrs: Dict[str, str]) -> Dict[str, str]:
        """
        Clean all attribute values: unescape HTML, strip whitespace, detect nulls.
        """
        cleaned = {}

        for key, value in attrs.items():
            if not value:
                cleaned[key] = None
                continue

            # Unescape HTML entities
            value = html_module.unescape(value)

            # Strip whitespace
            value = value.strip()

            # Detect null sentinels
            if self._is_null_sentinel(value):
                cleaned[key] = None
            else:
                cleaned[key] = value if value else None

        return cleaned

    def _is_null_sentinel(self, value: str) -> bool:
        """Check if value matches null sentinel."""
        if not value:
            return True

        value_lower = value.lower().strip()

        # Always detect literal <Null>
        if value_lower == '<null>':
            return True

        # Check user-configured sentinels
        for sentinel in self.null_sentinels:
            if value_lower == sentinel.lower():
                return True

        return False

    def _truncate_popup(self, html_str: str, max_len: int = 2000) -> str:
        """Clean and truncate raw popup HTML.

        ArcGIS Pro / Esri KMZs wrap popup content in a full HTML document
        with XSL/MSXSL namespace declarations (xmlns:fo, xmlns:msxsl, etc.).
        This strips the outer html/head/body wrappers and namespace noise,
        leaving just the meaningful inner HTML (tables, paragraphs) for
        downstream parsing or human readability.
        """
        if not html_str:
            return ''

        cleaned = html_str

        # Try to extract just the <body> inner HTML
        try:
            doc = lxml_html.fromstring(html_str)
            body = doc.find('.//body')
            if body is not None:
                parts = []
                if body.text:
                    parts.append(body.text)
                for child in body:
                    try:
                        parts.append(lxml_html.tostring(child, encoding='unicode'))
                    except Exception:
                        pass
                cleaned = ''.join(parts)
        except Exception:
            # Parser failure — fall through to regex cleanup
            pass

        # Strip xmlns="..." and xmlns:prefix="..." attributes that survived
        cleaned = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', '', cleaned)

        # Strip XML/HTML declarations and processing instructions
        cleaned = re.sub(r'<\?xml[^>]*\?>', '', cleaned)
        cleaned = re.sub(r'<!DOCTYPE[^>]*>', '', cleaned, flags=re.IGNORECASE)

        # Collapse runs of whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Decode HTML entities
        cleaned = html_module.unescape(cleaned)

        return cleaned[:max_len]

    def _extract_cell_text(self, cell_element) -> str:
        """Extract all text content from a table cell."""
        try:
            # Get all text including nested elements
            text_parts = []
            for text in cell_element.itertext():
                text_parts.append(text)
            return ''.join(text_parts)
        except:
            return ''
