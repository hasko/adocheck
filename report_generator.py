#!/usr/bin/env python3
"""
Report generator for ADOit entities with attribute-based filtering.
"""

import os
import sys
import json
import argparse
import datetime
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

from adoit_api import AdoitApi, adoit_request

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class ReportGenerator:
    """Generates reports for ADOit entities with complex filtering."""

    def __init__(self, api: AdoitApi):
        """
        Initialize the report generator.

        Args:
            api: Instance of AdoitApi to use for retrieving data
        """
        self.api = api
        self.attribute_cache = {}

    def discover_attribute_names(self, class_name: str, target_attributes: List[str]) -> Dict[str, Optional[str]]:
        """
        Discover the internal attribute names (metaNames) for given display names.

        Args:
            class_name: The class to search for attributes (e.g., "C_APPLICATION")
            target_attributes: List of attribute display names to find (e.g., ["Specialisation", "Lifecycle State"])

        Returns:
            Dictionary mapping display names to metaNames, e.g.:
            {"Specialisation": "A_CUST_SPECIALISATION", "Lifecycle State": "A_LIFECYCLE_STATE"}
            Returns None for metaName if attribute not found
        """
        logger.info(f"Discovering attribute names for class {class_name}...")

        # Check cache first
        cache_key = f"{class_name}:{','.join(sorted(target_attributes))}"
        if cache_key in self.attribute_cache:
            logger.info("Using cached attribute mappings")
            return self.attribute_cache[cache_key]

        result = {}
        for attr in target_attributes:
            result[attr] = None

        try:
            # Get all metamodel classes
            classes_response = self.api.get_metamodel_classes()
            classes = classes_response.get('classes', [])

            # Find the target class
            target_class = None
            for cls in classes:
                if cls.get('metaName') == class_name:
                    target_class = cls
                    break

            if not target_class:
                logger.warning(f"Class {class_name} not found in metamodel")
                return result

            # Get detailed class information including attributes
            class_id = target_class.get('id', '').strip('{}')
            if not class_id:
                logger.warning(f"No ID found for class {class_name}")
                return result

            logger.info(f"Fetching details for class {class_name} (ID: {class_id})")
            class_response = adoit_request(f"2.0/metamodel/classes/{class_id}")

            if class_response.status_code != 200:
                logger.error(f"Failed to get class details: {class_response.status_code}")
                return result

            class_data = class_response.json()
            attributes = class_data.get('attributes', [])

            logger.info(f"Found {len(attributes)} attributes for class {class_name}")

            # Match target attributes by display name (case-insensitive)
            for attr_def in attributes:
                display_names = attr_def.get('displayNames', [])
                meta_name = attr_def.get('metaName', '')

                # Check all display names for matches
                for dn in display_names:
                    dn_value = dn.get('value', '').strip()

                    for target_attr in target_attributes:
                        if dn_value.lower() == target_attr.lower():
                            logger.info(f"Found match: '{target_attr}' -> '{meta_name}'")
                            result[target_attr] = meta_name
                            break

            # Cache the results
            self.attribute_cache[cache_key] = result

            # Log any missing attributes
            missing = [k for k, v in result.items() if v is None]
            if missing:
                logger.warning(f"Could not find metaNames for: {missing}")

            return result

        except Exception as e:
            logger.error(f"Error discovering attribute names: {e}")
            return result

    def list_all_attributes(self, class_name: str) -> List[Dict[str, str]]:
        """
        List all available attributes for a given class.

        Args:
            class_name: The class to list attributes for

        Returns:
            List of dictionaries with 'displayName' and 'metaName' keys
        """
        try:
            # Get all metamodel classes
            classes_response = self.api.get_metamodel_classes()
            classes = classes_response.get('classes', [])

            # Find the target class
            target_class = None
            for cls in classes:
                if cls.get('metaName') == class_name:
                    target_class = cls
                    break

            if not target_class:
                logger.warning(f"Class {class_name} not found")
                return []

            class_id = target_class.get('id', '').strip('{}')
            class_response = adoit_request(f"2.0/metamodel/classes/{class_id}")

            if class_response.status_code != 200:
                return []

            class_data = class_response.json()
            attributes = class_data.get('attributes', [])

            result = []
            for attr_def in attributes:
                display_names = attr_def.get('displayNames', [])
                meta_name = attr_def.get('metaName', '')

                # Get first display name (usually English)
                display_name = display_names[0].get('value', '') if display_names else ''

                result.append({
                    'displayName': display_name,
                    'metaName': meta_name
                })

            return result

        except Exception as e:
            logger.error(f"Error listing attributes: {e}")
            return []

    def build_search_query(self, class_name: str, attribute_filters: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Build a search filter array for the ADOit API.

        Args:
            class_name: The class name to filter by (e.g., "C_APPLICATION")
            attribute_filters: Dictionary of {metaName: value} pairs for filtering

        Returns:
            List of filter dictionaries suitable for API query
        """
        filters = [{"className": [class_name]}]

        for attr_name, attr_value in attribute_filters.items():
            filters.append({
                "attrName": attr_name,
                "value": attr_value,
                "op": "OP_EQ"
            })

        logger.info(f"Built query with {len(filters)} filters: className + {len(attribute_filters)} attributes")
        return filters

    def fetch_filtered_entities(self, filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Fetch entities matching the given filters.

        Args:
            filters: List of filter dictionaries

        Returns:
            List of entity data dictionaries
        """
        logger.info(f"Fetching entities with filters: {json.dumps(filters, indent=2)}")

        try:
            entities = self.api.get_entities_by_filters(filters)
            logger.info(f"Retrieved {len(entities)} entities")
            return entities
        except Exception as e:
            logger.error(f"Error fetching entities: {e}")
            return []

    def extract_report_data(self, entities: List[Dict[str, Any]],
                           attribute_meta_names: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
        """
        Extract relevant data from entities for the report.

        Args:
            entities: List of entity dictionaries from API
            attribute_meta_names: Mapping of display names to metaNames

        Returns:
            List of simplified entity dictionaries for reporting
        """
        report_data = []

        for entity in entities:
            # Start with basic info
            item = {
                'id': entity.get('id', ''),
                'name': entity.get('name', '')
            }

            # Extract specific attributes
            attributes = entity.get('attributes', [])

            for display_name, meta_name in attribute_meta_names.items():
                if meta_name is None:
                    item[display_name.lower().replace(' ', '_')] = None
                    continue

                # Find the attribute value in the entity
                attr_value = None
                for attr in attributes:
                    if attr.get('metaName') == meta_name:
                        attr_value = attr.get('value', '')
                        break

                # Use display name in lowercase with underscores as key
                key = display_name.lower().replace(' ', '_')
                item[key] = attr_value

            report_data.append(item)

        return report_data

    def generate_json_report(self, report_data: List[Dict[str, Any]],
                            filters_applied: Dict[str, Any],
                            output_path: str) -> str:
        """
        Generate a JSON report file.

        Args:
            report_data: List of entity data dictionaries
            filters_applied: Dictionary describing the filters used
            output_path: Path where to save the report

        Returns:
            Path to the generated report file
        """
        # Create output directory if it doesn't exist
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build report structure
        report = {
            'report_metadata': {
                'generated_at': datetime.datetime.now().isoformat(),
                'filters_applied': filters_applied,
                'total_count': len(report_data)
            },
            'applications': report_data
        }

        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"Report generated: {output_path}")
        logger.info(f"Total entities: {len(report_data)}")

        return output_path

    def run_report(self, class_name: str = "C_APPLICATION",
                   target_attributes: Dict[str, str] = None,
                   output_path: str = "data/application_report.json",
                   manual_mappings: Dict[str, str] = None) -> str:
        """
        Run the complete report generation process.

        Args:
            class_name: The ADOit class to report on
            target_attributes: Dictionary of {display_name: desired_value} for filtering
            output_path: Where to save the JSON report
            manual_mappings: Optional manual {display_name: metaName} mappings

        Returns:
            Path to the generated report file
        """
        logger.info("=" * 60)
        logger.info("Starting Application Component Report Generation")
        logger.info("=" * 60)

        if target_attributes is None:
            target_attributes = {
                "Specialisation": "Bus. App.",
                "Lifecycle State": "In production"
            }

        # Step 1: Discover attribute names
        if manual_mappings:
            logger.info("Using manually provided attribute mappings")
            attribute_meta_names = manual_mappings
        else:
            attribute_meta_names = self.discover_attribute_names(
                class_name,
                list(target_attributes.keys())
            )

        # Check if all attributes were found
        missing = [k for k, v in attribute_meta_names.items() if v is None]
        if missing:
            logger.error(f"Could not find metaNames for: {missing}")
            logger.info("\nAvailable attributes:")
            attrs = self.list_all_attributes(class_name)
            for attr in attrs[:20]:  # Show first 20
                logger.info(f"  - {attr['displayName']} ({attr['metaName']})")
            if len(attrs) > 20:
                logger.info(f"  ... and {len(attrs) - 20} more")

            raise ValueError(f"Missing attribute mappings for: {missing}")

        # Step 2: Build search query
        attribute_filters = {}
        for display_name, desired_value in target_attributes.items():
            meta_name = attribute_meta_names[display_name]
            if meta_name:
                attribute_filters[meta_name] = desired_value

        filters = self.build_search_query(class_name, attribute_filters)

        # Step 3: Fetch entities
        entities = self.fetch_filtered_entities(filters)

        if not entities:
            logger.warning("No entities found matching the criteria")

        # Step 4: Extract report data
        report_data = self.extract_report_data(entities, attribute_meta_names)

        # Step 5: Generate JSON report
        filters_applied = {
            'class': class_name,
            **target_attributes
        }
        report_path = self.generate_json_report(report_data, filters_applied, output_path)

        logger.info("=" * 60)
        logger.info("Report generation completed successfully!")
        logger.info("=" * 60)

        return report_path


def main():
    """Command-line interface for the report generator."""
    parser = argparse.ArgumentParser(
        description='Generate reports for ADOit application components with attribute filtering'
    )

    parser.add_argument(
        '--class-name',
        default='C_APPLICATION',
        help='ADOit class name to query (default: C_APPLICATION)'
    )

    parser.add_argument(
        '--specialisation',
        default='Bus. App.',
        help='Value for Specialisation attribute (default: Bus. App.)'
    )

    parser.add_argument(
        '--lifecycle',
        default='In production',
        help='Value for Lifecycle State attribute (default: In production)'
    )

    parser.add_argument(
        '--output',
        default='data/application_report.json',
        help='Output path for JSON report (default: data/application_report.json)'
    )

    parser.add_argument(
        '--list-attributes',
        action='store_true',
        help='List all available attributes for the specified class and exit'
    )

    parser.add_argument(
        '--manual-mapping',
        nargs=2,
        action='append',
        metavar=('DISPLAY_NAME', 'META_NAME'),
        help='Manually specify attribute mapping (can be used multiple times)'
    )

    args = parser.parse_args()

    # Initialize API
    try:
        api = AdoitApi()
    except Exception as e:
        logger.error(f"Failed to initialize ADOit API: {e}")
        sys.exit(1)

    # Create report generator
    generator = ReportGenerator(api)

    # List attributes if requested
    if args.list_attributes:
        logger.info(f"Listing all attributes for class: {args.class_name}")
        attrs = generator.list_all_attributes(args.class_name)

        if not attrs:
            logger.error(f"No attributes found or class not found: {args.class_name}")
            sys.exit(1)

        print(f"\nAttributes for {args.class_name}:")
        print("-" * 80)
        for attr in attrs:
            print(f"  {attr['displayName']:40} -> {attr['metaName']}")
        print(f"\nTotal: {len(attrs)} attributes")
        sys.exit(0)

    # Build manual mappings if provided
    manual_mappings = None
    if args.manual_mapping:
        manual_mappings = {display: meta for display, meta in args.manual_mapping}
        logger.info(f"Using manual mappings: {manual_mappings}")

    # Run report
    try:
        target_attributes = {
            "Specialisation": args.specialisation,
            "Lifecycle State": args.lifecycle
        }

        report_path = generator.run_report(
            class_name=args.class_name,
            target_attributes=target_attributes,
            output_path=args.output,
            manual_mappings=manual_mappings
        )

        print(f"\nReport generated successfully: {report_path}")

    except Exception as e:
        logger.error(f"Error generating report: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
