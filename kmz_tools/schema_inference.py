"""Infer field types from sampled attribute values."""

import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime


class SchemaInferencer:
    """Infer GDB field types from attribute values."""

    # ISO 8601 and common date formats
    DATE_PATTERNS = [
        r'^\d{4}-\d{2}-\d{2}$',  # YYYY-MM-DD
        r'^\d{4}-\d{2}-\d{2}T',  # ISO 8601 with time
        r'^\d{1,2}/\d{1,2}/\d{4}$',  # MM/DD/YYYY
        r'^\d{1,2}-\d{1,2}-\d{4}$',  # MM-DD-YYYY
    ]

    def infer_field_types(self, attributes_per_feature: List[Dict[str, str]],
                         null_threshold: float = 0.95) -> Dict[str, Dict[str, str]]:
        """
        Infer field types from feature attributes.

        Args:
            attributes_per_feature: List of attribute dicts (one per feature)
            null_threshold: Skip type inference for fields >95% null (default to TEXT)

        Returns:
            Dict of field_name → {'type': 'LONG'|'DOUBLE'|'DATE'|'TEXT', 'length': int}
        """
        if not attributes_per_feature:
            return {}

        # Collect all field names
        all_fields = set()
        for attrs in attributes_per_feature:
            all_fields.update(attrs.keys())

        field_types = {}

        for field_name in all_fields:
            # Collect non-null values for this field
            values = []
            null_count = 0

            for attrs in attributes_per_feature:
                value = attrs.get(field_name)
                if value is None or value == '':
                    null_count += 1
                else:
                    values.append(value)

            # Skip if >threshold% null
            null_pct = null_count / len(attributes_per_feature) if attributes_per_feature else 0
            if null_pct > null_threshold:
                field_types[field_name] = {'type': 'TEXT', 'length': 255}
                continue

            # Infer type from values
            if not values:
                field_types[field_name] = {'type': 'TEXT', 'length': 255}
                continue

            inferred = self._infer_type(values)
            field_types[field_name] = inferred

        return field_types

    def _infer_type(self, values: List[str]) -> Dict[str, str]:
        """Infer single field type from value list."""
        if not values:
            return {'type': 'TEXT', 'length': 255}

        # Try LONG
        if all(self._is_int(v) for v in values):
            return {'type': 'LONG', 'length': None}

        # Try DOUBLE
        if all(self._is_numeric(v) for v in values):
            return {'type': 'DOUBLE', 'length': None}

        # Try DATE
        if all(self._is_date(v) for v in values):
            return {'type': 'DATE', 'length': None}

        # Default to TEXT with inferred length
        max_len = max(len(v) for v in values)
        text_len = max(int(max_len * 1.5), 50)
        text_len = min(text_len, 255)

        return {'type': 'TEXT', 'length': text_len}

    def _is_int(self, value: str) -> bool:
        """Check if value is parseable as integer."""
        try:
            int(value)
            return True
        except ValueError:
            return False

    def _is_numeric(self, value: str) -> bool:
        """Check if value is parseable as number (int or float)."""
        try:
            float(value)
            return True
        except ValueError:
            return False

    def _is_date(self, value: str) -> bool:
        """Check if value matches a date pattern."""
        for pattern in self.DATE_PATTERNS:
            if re.match(pattern, value.strip()):
                return True
        return False
