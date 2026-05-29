"""Processing log formatting and dry-run output utilities."""

import csv
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path


class ProcessingLogger:
    """Log processing results to CSV and format dry-run output."""

    def __init__(self, output_path: Optional[Path] = None):
        """
        Initialize logger.

        Args:
            output_path: Optional path for CSV log file
        """
        self.output_path = output_path
        self.entries = []
        self.warnings = []
        self.sanitizations = []
        self.schema_collisions = []

    def log_entry(self, kmz_name: str, features_processed: int, feature_classes: Dict[str, int],
                 datasets: List[str], status: str = 'success', error_msg: Optional[str] = None):
        """Log a KMZ processing entry."""
        self.entries.append({
            'kmz_name': kmz_name,
            'timestamp': datetime.now().isoformat(),
            'features_processed': features_processed,
            'feature_classes': len(feature_classes),
            'datasets': len(datasets),
            'status': status,
            'error': error_msg or '',
        })

    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)

    def add_sanitization(self, original: str, sanitized: str):
        """Log a name sanitization."""
        self.sanitizations.append({'original': original, 'sanitized': sanitized})

    def add_schema_collision(self, fc_name: str, kmz1: str, kmz2: str, field_names: List[str]):
        """Log a schema collision warning."""
        self.schema_collisions.append({
            'feature_class': fc_name,
            'kmz1': kmz1,
            'kmz2': kmz2,
            'differing_fields': ', '.join(field_names)
        })

    def write_csv(self):
        """Write processing log to CSV file."""
        if not self.output_path:
            return

        with open(self.output_path, 'w', newline='', encoding='utf-8') as f:
            if not self.entries:
                f.write("No entries to log\n")
                return

            fieldnames = ['kmz_name', 'timestamp', 'features_processed', 'feature_classes',
                         'datasets', 'status', 'error']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.entries)

    def format_dry_run_output(self, planned_structure: Dict[str, any]) -> str:
        """
        Format dry-run output showing planned GDB structure.

        Args:
            planned_structure: Dict with 'datasets', 'feature_classes', 'warnings', etc.

        Returns:
            Formatted string for display
        """
        lines = ['=== DRY RUN: PLANNED OUTPUT ===', '']

        # GDB structure
        lines.append('output.gdb/')
        datasets = planned_structure.get('datasets', {})

        for ds_name, fcs in sorted(datasets.items()):
            lines.append(f'+-- {ds_name}/')
            for fc_name, fc_info in sorted(fcs.items()):
                num_features = fc_info.get('feature_count', 0)
                num_attrs = fc_info.get('attribute_count', 0)
                num_system = 7  # Standard system fields
                lines.append(f'|   +-- {fc_name:30} ({num_features} features, {num_attrs} + {num_system} fields)')

        # Root-level FCs
        root_fcs = planned_structure.get('root_feature_classes', {})
        if root_fcs:
            lines.append('\\-- (root)')
            for fc_name, fc_info in sorted(root_fcs.items()):
                num_features = fc_info.get('feature_count', 0)
                num_attrs = fc_info.get('attribute_count', 0)
                num_system = 7
                lines.append(f'    +-- {fc_name:30} ({num_features} features, {num_attrs} + {num_system} fields)')

        lines.append('')

        # Sanitizations
        if self.sanitizations:
            lines.append('Sanitizations:')
            for san in self.sanitizations:
                lines.append(f"  '{san['original']}' -> '{san['sanitized']}'")
            lines.append('')

        # Warnings
        if self.warnings:
            lines.append('Warnings:')
            for warning in self.warnings:
                lines.append(f'  - {warning}')
            lines.append('')

        # Schema collisions
        if self.schema_collisions:
            lines.append('Schema Collisions (fields will be unioned):')
            for collision in self.schema_collisions:
                lines.append(f"  - {collision['feature_class']}: {collision['kmz1']} vs {collision['kmz2']}")
                lines.append(f"    Differing fields: {collision['differing_fields']}")
            lines.append('')

        lines.append('=== NO FILES WRITTEN (DRY RUN) ===')

        return '\n'.join(lines)

    def summary(self) -> str:
        """Return brief summary of processing."""
        if not self.entries:
            return 'No entries processed'

        total_features = sum(e['features_processed'] for e in self.entries)
        total_fcs = sum(e['feature_classes'] for e in self.entries)

        return f"{len(self.entries)} KMZ files, {total_features} features, {total_fcs} feature classes"
