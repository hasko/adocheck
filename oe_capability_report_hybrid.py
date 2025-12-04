#!/usr/bin/env python3
"""
OE Capability Report Generator - Hybrid Version

Shows both organic (RC_REALIZATION) and aggregated capability links.
Detects links to deprecated "(do not use)" capabilities.
"""

import os
import sys
import json
import argparse
import datetime
import logging
from typing import Dict, List, Any, Optional, Set, Tuple
from pathlib import Path
from collections import defaultdict

from adoit_api import AdoitApi

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


class OECapabilityReporterHybrid:
    """Generates OE-grouped capability mapping reports using embedded relationships."""

    def __init__(self, api: AdoitApi, use_cache: bool = False):
        """Initialize the reporter."""
        self.api = api
        self.use_cache = use_cache
        self.oe_cache = {}  # {oe_id: oe_name}
        self.capability_cache = {}  # {cap_id: (cap_name, cap_level, is_deprecated)}

    def extract_oes_from_application(self, app: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Extract OE IDs and names from application's "Using" relation."""
        oes = []
        for attr in app.get('attributes', []):
            if attr.get('metaName') == 'RC_CUST_ORG_UNIT_USING':
                targets = attr.get('targets', [])
                for target in targets:
                    oe_id = target.get('id', '').strip('{}')
                    oe_name = target.get('name', 'Unknown OE')
                    if oe_id:
                        oes.append((oe_id, oe_name))
                        self.oe_cache[oe_id] = oe_name
        return oes

    def get_capability_level(self, cap_id: str, cap_name: Optional[str] = None) -> Optional[str]:
        """
        Determine GDM level from capability name.

        Capabilities are repository objects, not entities, so we parse the level
        from the name pattern: "3.2 Hr" → L3, "2.2.1 IT Operations" → L2.

        The level is encoded as the first digit in "X.Y..." format.

        Returns: 'L1', 'L2', 'L3', etc. or None
        """
        if cap_id in self.capability_cache:
            return self.capability_cache[cap_id][1]

        if not cap_name:
            return None

        # Parse level from name (e.g., "3.2 Hr" → L3, "2.2.1 IT Operations" → L2)
        import re
        match = re.match(r'^(\d+)\.', cap_name)
        if match:
            level_num = int(match.group(1))
            if 1 <= level_num <= 3:  # Only L1, L2, L3 are valid GDM levels
                level = f'L{level_num}'
                is_deprecated = "(do not use)" in cap_name.lower()
                self.capability_cache[cap_id] = (cap_name, level, is_deprecated)
                return level

        return None

    def extract_capabilities_from_application(
        self,
        app: Dict[str, Any],
        target_levels: List[str] = ['L1', 'L2', 'L3']
    ) -> Dict[str, Any]:
        """
        Extract both organic and aggregated capability links.

        Returns:
            {
                'organic': {cap_name: [(cap_id, level, is_deprecated), ...]},
                'aggregated': {cap_name: [(cap_id, level, is_deprecated), ...]}
            }
        """
        result = {
            'organic': defaultdict(list),
            'aggregated': defaultdict(list)
        }

        # Map of aggregated attributes
        aggregated_attrs = {
            'RC_CUST_AGGREGATED_DOMAIN_AREAS',
            'RC_CUST_AGGREGATED_DOMAINS',
            'RC_CUST_AGGREGATED_SUB_DOMAINS',
            'RC_CUST_AGGREGATED_CAPABILITIES',
            'RC_CUST_AGGREGATED_SUB_CAPABILITIES'
        }

        for attr in app.get('attributes', []):
            if attr.get('attrType') != 'RELATION':
                continue

            metaName = attr.get('metaName', '')
            targets = attr.get('targets', [])

            for target in targets:
                target_meta = target.get('metaName', '')

                # Only process capability targets
                if 'CAPABILITY' not in target_meta.upper():
                    continue

                cap_id = target.get('id', '').strip('{}')
                cap_name = target.get('name', 'Unknown')

                if not cap_id:
                    continue

                # Determine if deprecated
                is_deprecated = "(do not use)" in cap_name.lower()

                # Get level (with caching)
                if cap_id in self.capability_cache:
                    _, level, _ = self.capability_cache[cap_id]
                else:
                    level = self.get_capability_level(cap_id, cap_name)
                    if cap_id not in self.capability_cache:
                        self.capability_cache[cap_id] = (cap_name, level, is_deprecated)

                # Only include target levels
                if level not in target_levels:
                    continue

                # Categorize as organic or aggregated
                if metaName in aggregated_attrs:
                    result['aggregated'][cap_name].append((cap_id, level, is_deprecated))
                else:
                    result['organic'][cap_name].append((cap_id, level, is_deprecated))

        return result

    def generate_oe_report(
        self,
        app_specialisation: str = "Bus. App.",
        gdm_levels: List[str] = ['L1', 'L2', 'L3'],
        output_path: str = "data/oe_capability_report_hybrid.json"
    ) -> str:
        """Generate the complete OE capability report."""
        logger.info("=" * 60)
        logger.info("OE CAPABILITY REPORT - START (Hybrid Method)")
        logger.info("=" * 60)

        # Step 1: Fetch business applications
        logger.info("=" * 60)
        logger.info("Fetching business applications...")
        logger.info("=" * 60)
        logger.info(f"Filter: Specialisation = '{app_specialisation}'")
        logger.info(f"Target GDM Levels: {', '.join(gdm_levels)}")

        try:
            filters = [
                {"className": ["C_APPLICATION_COMPONENT"]},
                {"attrName": "A_APPLICATION_COMPONENT_SPEC", "value": app_specialisation, "op": "OP_EQ"}
            ]
            applications = self.api.get_entities_by_filters(filters, force_refresh=not self.use_cache)
            logger.info(f"✓ Found {len(applications)} business applications")

            if not applications:
                logger.warning("No applications found")
                return ""

        except Exception as e:
            logger.error(f"Error fetching applications: {e}")
            return ""

        # Step 2: Extract capabilities and OEs
        logger.info("=" * 60)
        logger.info("Extracting capabilities and OEs...")
        logger.info("=" * 60)

        oe_results = defaultdict(lambda: {
            'oe_name': '',
            'applications': [],
            'statistics': {
                'total_applications': 0,
                'organic_mapped': 0,
                'aggregated_mapped': 0,
                'unmapped': 0,
                'organic_new_model': 0,
                'organic_old_model': 0,
                'aggregated_new_model': 0,
                'aggregated_old_model': 0,
                'capabilities_by_type': {
                    'organic': defaultdict(int),
                    'aggregated': defaultdict(int)
                }
            }
        })

        no_oe_results = {
            'applications': [],
            'statistics': {
                'total_applications': 0,
                'organic_mapped': 0,
                'aggregated_mapped': 0,
                'unmapped': 0,
                'organic_new_model': 0,
                'organic_old_model': 0,
                'aggregated_new_model': 0,
                'aggregated_old_model': 0,
                'capabilities_by_type': {
                    'organic': defaultdict(int),
                    'aggregated': defaultdict(int)
                }
            }
        }

        for i, app in enumerate(applications):
            if (i + 1) % 50 == 0:
                logger.info(f"Progress: {i + 1}/{len(applications)}")

            app_id = app.get('id', '').strip('{}')
            app_name = app.get('name', 'Unknown')
            app_type = app.get('type', 'Application Component')

            # Extract OEs
            oes = self.extract_oes_from_application(app)

            # Extract capabilities
            app_capabilities = self.extract_capabilities_from_application(app, gdm_levels)

            # Analyze organic and aggregated separately
            organic_caps = app_capabilities['organic']
            aggregated_caps = app_capabilities['aggregated']

            has_organic = len(organic_caps) > 0
            has_aggregated = len(aggregated_caps) > 0

            organic_deprecated = any(
                is_dep for cap_list in organic_caps.values()
                for _, _, is_dep in cap_list
            )
            aggregated_deprecated = any(
                is_dep for cap_list in aggregated_caps.values()
                for _, _, is_dep in cap_list
            )

            # Build application data
            app_data = {
                'id': app_id,
                'name': app_name,
                'type': app_type,
                'organic_capabilities': {
                    cap_name: [{
                        'id': cap_id,
                        'level': level,
                        'deprecated': is_dep
                    } for cap_id, level, is_dep in cap_list]
                    for cap_name, cap_list in organic_caps.items()
                },
                'aggregated_capabilities': {
                    cap_name: [{
                        'id': cap_id,
                        'level': level,
                        'deprecated': is_dep
                    } for cap_id, level, is_dep in cap_list]
                    for cap_name, cap_list in aggregated_caps.items()
                },
                'has_organic_links': has_organic,
                'has_aggregated_links': has_aggregated,
                'organic_uses_deprecated': organic_deprecated,
                'aggregated_uses_deprecated': aggregated_deprecated
            }

            # Update statistics
            def update_stats(stats_dict):
                stats_dict['total_applications'] += 1

                if has_organic:
                    stats_dict['organic_mapped'] += 1
                    if organic_deprecated:
                        stats_dict['organic_old_model'] += 1
                    else:
                        stats_dict['organic_new_model'] += 1
                    for cap_name in organic_caps:
                        stats_dict['capabilities_by_type']['organic'][cap_name] += 1

                if has_aggregated:
                    stats_dict['aggregated_mapped'] += 1
                    if aggregated_deprecated:
                        stats_dict['aggregated_old_model'] += 1
                    else:
                        stats_dict['aggregated_new_model'] += 1
                    for cap_name in aggregated_caps:
                        stats_dict['capabilities_by_type']['aggregated'][cap_name] += 1

                if not has_organic and not has_aggregated:
                    stats_dict['unmapped'] += 1

            # Add to OE groups
            if not oes:
                no_oe_results['applications'].append(app_data)
                update_stats(no_oe_results['statistics'])
            else:
                for oe_id, oe_name in oes:
                    oe_results[oe_id]['oe_name'] = oe_name
                    oe_results[oe_id]['applications'].append(app_data)
                    update_stats(oe_results[oe_id]['statistics'])

        # Convert defaultdicts
        for oe_id in oe_results:
            oe_results[oe_id]['statistics']['capabilities_by_type']['organic'] = dict(
                oe_results[oe_id]['statistics']['capabilities_by_type']['organic']
            )
            oe_results[oe_id]['statistics']['capabilities_by_type']['aggregated'] = dict(
                oe_results[oe_id]['statistics']['capabilities_by_type']['aggregated']
            )

        no_oe_results['statistics']['capabilities_by_type']['organic'] = dict(
            no_oe_results['statistics']['capabilities_by_type']['organic']
        )
        no_oe_results['statistics']['capabilities_by_type']['aggregated'] = dict(
            no_oe_results['statistics']['capabilities_by_type']['aggregated']
        )

        # Build summary
        summary_stats = {
            'total_oes': len(oe_results),
            'total_applications': len(applications),
            'organic_mapped': sum(oe['statistics']['organic_mapped'] for oe in oe_results.values()) + no_oe_results['statistics']['organic_mapped'],
            'aggregated_mapped': sum(oe['statistics']['aggregated_mapped'] for oe in oe_results.values()) + no_oe_results['statistics']['aggregated_mapped'],
            'unmapped': sum(oe['statistics']['unmapped'] for oe in oe_results.values()) + no_oe_results['statistics']['unmapped'],
            'organic_new_model': sum(oe['statistics']['organic_new_model'] for oe in oe_results.values()) + no_oe_results['statistics']['organic_new_model'],
            'organic_old_model': sum(oe['statistics']['organic_old_model'] for oe in oe_results.values()) + no_oe_results['statistics']['organic_old_model'],
            'aggregated_new_model': sum(oe['statistics']['aggregated_new_model'] for oe in oe_results.values()) + no_oe_results['statistics']['aggregated_new_model'],
            'aggregated_old_model': sum(oe['statistics']['aggregated_old_model'] for oe in oe_results.values()) + no_oe_results['statistics']['aggregated_old_model'],
            'applications_without_oe': no_oe_results['statistics']['total_applications']
        }

        # Calculate percentages
        if summary_stats['organic_mapped'] > 0:
            summary_stats['organic_new_pct'] = (summary_stats['organic_new_model'] / summary_stats['organic_mapped'] * 100)
            summary_stats['organic_old_pct'] = (summary_stats['organic_old_model'] / summary_stats['organic_mapped'] * 100)
        else:
            summary_stats['organic_new_pct'] = 0
            summary_stats['organic_old_pct'] = 0

        if summary_stats['aggregated_mapped'] > 0:
            summary_stats['aggregated_new_pct'] = (summary_stats['aggregated_new_model'] / summary_stats['aggregated_mapped'] * 100)
            summary_stats['aggregated_old_pct'] = (summary_stats['aggregated_old_model'] / summary_stats['aggregated_mapped'] * 100)
        else:
            summary_stats['aggregated_new_pct'] = 0
            summary_stats['aggregated_old_pct'] = 0

        # Generate report
        report = {
            'report_metadata': {
                'generated_at': datetime.datetime.now().isoformat(),
                'report_type': 'oe_capability_mapping_hybrid',
                'description': 'Shows both organic (RC_REALIZATION) and aggregated capability links',
                'filters_applied': {
                    'application_class': 'C_APPLICATION_COMPONENT',
                    'application_specialisation': app_specialisation,
                    'gdm_levels': gdm_levels
                },
                'summary_statistics': summary_stats
            },
            'by_oe': {oe_id: oe_data for oe_id, oe_data in sorted(
                oe_results.items(),
                key=lambda x: x[1]['oe_name']
            )},
            'applications_without_oe': no_oe_results
        }

        # Write to file
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info("\n" + "=" * 60)
        logger.info(f"✓ Report generated: {output_path}")
        logger.info(f"  Size: {Path(output_path).stat().st_size / 1024:.1f} KB")
        logger.info(f"\nSummary Statistics:")
        logger.info(f"  Total OEs: {summary_stats['total_oes']}")
        logger.info(f"  Total Applications: {summary_stats['total_applications']}")
        logger.info(f"\n  ORGANIC Links (RC_REALIZATION - actual relationships):")
        logger.info(f"    - Mapped: {summary_stats['organic_mapped']}")
        logger.info(f"    - New Model: {summary_stats['organic_new_model']} ({summary_stats['organic_new_pct']:.1f}%)")
        logger.info(f"    - Old Model (deprecated): {summary_stats['organic_old_model']} ({summary_stats['organic_old_pct']:.1f}%)")
        logger.info(f"\n  AGGREGATED Links (curated):")
        logger.info(f"    - Mapped: {summary_stats['aggregated_mapped']}")
        logger.info(f"    - New Model: {summary_stats['aggregated_new_model']} ({summary_stats['aggregated_new_pct']:.1f}%)")
        logger.info(f"    - Old Model (deprecated): {summary_stats['aggregated_old_model']} ({summary_stats['aggregated_old_pct']:.1f}%)")
        logger.info(f"\n  Unmapped: {summary_stats['unmapped']}")
        logger.info(f"  Without OE: {summary_stats['applications_without_oe']}")
        logger.info("\n" + "=" * 60)
        logger.info("OE CAPABILITY REPORT - COMPLETE")
        logger.info("=" * 60)

        return output_path


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description='Generate OE-grouped capability mapping report (hybrid method)'
    )

    parser.add_argument(
        '--output', '-o',
        default='data/oe_capability_report_hybrid.json',
        help='Output file path'
    )

    parser.add_argument(
        '--app-specialisation',
        default='Bus. App.',
        help='Application Specialisation value'
    )

    parser.add_argument(
        '--gdm-levels',
        nargs='+',
        default=['L1', 'L2', 'L3'],
        help='GDM levels to include'
    )

    parser.add_argument(
        '--use-cache',
        action='store_true',
        help='Use cached data'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level'
    )

    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    try:
        api = AdoitApi()
    except Exception as e:
        logger.error(f"Failed to initialize ADOit API: {e}")
        sys.exit(1)

    reporter = OECapabilityReporterHybrid(api, use_cache=args.use_cache)

    try:
        report_path = reporter.generate_oe_report(
            app_specialisation=args.app_specialisation,
            gdm_levels=args.gdm_levels,
            output_path=args.output
        )

        if report_path:
            print(f"\n✓ Report generated successfully: {report_path}")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
