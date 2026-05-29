r"""FolderPath → FD\FC resolution and name sanitization."""

import re
import unicodedata
from typing import Tuple, Optional, List
from pathlib import Path


class NamingResolver:
    """Resolve folder hierarchies to FD/FC pairs and sanitize names."""

    # GDB-safe characters: A-Z, a-z, 0-9, underscore
    VALID_CHARS = re.compile(r'^[A-Za-z0-9_]+$')

    def __init__(self, gdb_path: Optional[str] = None):
        """
        Initialize resolver.

        Args:
            gdb_path: Path to GDB for validation (optional, uses arcpy if available)
        """
        self.gdb_path = gdb_path
        self.sanitization_log = []

    def resolve_fd_fc(self, folder_path_segments: List[str], kmz_filename_stem: str) -> Tuple[Optional[str], str]:
        """
        Resolve FolderPath to FD/FC pair following design rules.

        Args:
            folder_path_segments: List of folder names (already collapsed)
            kmz_filename_stem: Filename without extension

        Returns:
            Tuple of (fd_name or None, fc_name)
        """
        segments = folder_path_segments

        # Rule: empty path → no FD, FC = kmz_filename_stem
        if not segments:
            fd_name = None
            fc_name = kmz_filename_stem
        # Rule: 1-2 segments → no FD, FC = rightmost segment
        elif len(segments) <= 2:
            fd_name = None
            fc_name = segments[-1]
        # Rule: 3+ segments → FD = 2 segments left of FC, FC = rightmost
        else:
            fc_name = segments[-1]
            fd_segments = segments[-3:-1]
            fd_name = '_'.join(fd_segments)

        # Sanitize both names
        if fd_name:
            fd_name = self.sanitize(fd_name)
        fc_name = self.sanitize(fc_name)

        return (fd_name, fc_name)

    def sanitize(self, name: str) -> str:
        """
        Sanitize name for GDB using 8-step process.

        Returns sanitized name, logs all changes.
        """
        original = name

        if not name:
            return 'unnamed'

        # Step 1: Strip leading/trailing whitespace
        name = name.strip()

        # Step 2: Replace spaces with underscore
        name = name.replace(' ', '_')

        # Step 3: Replace non-alphanumeric (except underscore) with underscore
        name = re.sub(r'[^A-Za-z0-9_]', '_', name)

        # Step 4: Collapse runs of underscore into single
        name = re.sub(r'_+', '_', name)

        # Step 5: Strip non-ASCII characters
        name = name.encode('ascii', 'ignore').decode('ascii')

        # Step 6: If starts with digit, prepend prefix. File GDB requires
        # FC/FD names to start with a letter or underscore. A single
        # lowercase "x" is the most compact legal patch (e.g., "2026_BUOW"
        # -> "x2026_BUOW") and doesn't suggest a type like FD/FC/T would.
        if name and name[0].isdigit():
            name = 'x' + name

        # Step 7: Use arcpy validation if available
        try:
            import arcpy
            if self.gdb_path:
                name = arcpy.ValidateTableName(name, self.gdb_path)
        except Exception:
            pass

        # Step 8: Truncate to 64 characters
        name = name[:64]

        # Log if changed
        if name != original:
            self.sanitization_log.append(f"'{original}' → '{name}'")

        return name

    def get_sanitization_log(self) -> List[str]:
        """Return list of all sanitizations performed."""
        return self.sanitization_log.copy()

    def clear_sanitization_log(self):
        """Clear the sanitization log."""
        self.sanitization_log = []


def suffix_for_geometry_type(geom_type: str) -> str:
    """
    Return suffix for mixed-geometry feature classes.

    Args:
        geom_type: 'Point', 'LineString', 'Polygon'

    Returns:
        '_Point', '_Polyline', '_Polygon'
    """
    suffixes = {
        'Point': '_Point',
        'LineString': '_Polyline',
        'Polygon': '_Polygon',
    }
    return suffixes.get(geom_type, '')
