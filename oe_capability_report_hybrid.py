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
            # Exclude Group Standards from "Applications Without OE" - they don't need OE mapping
            is_group_standard = "(Group Standard)" in app_name or "(Declassified Group Standard)" in app_name or "(Future Group Standard)" in app_name

            if not oes:
                if not is_group_standard:
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

    def generate_html_report(self, json_data: Dict[str, Any], output_path: str) -> str:
        """Generate HTML version of the report from JSON data."""
        html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OE Capability Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            border-radius: 8px;
        }}

        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}

        h2 {{
            color: #34495e;
            margin-top: 30px;
            margin-bottom: 15px;
            padding: 10px;
            background: #ecf0f1;
            border-left: 4px solid #3498db;
        }}

        h3 {{
            color: #555;
            margin-top: 20px;
            margin-bottom: 10px;
        }}

        .metadata {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 30px;
            font-size: 0.9em;
            color: #666;
        }}

        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}

        .stat-card.organic {{
            background: linear-gradient(135deg, #56ab2f 0%, #a8e063 100%);
        }}

        .stat-card.aggregated {{
            background: linear-gradient(135deg, #f2994a 0%, #f2c94c 100%);
        }}

        .stat-card h3 {{
            margin: 0 0 10px 0;
            font-size: 1.1em;
            color: white;
        }}

        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            margin: 10px 0;
        }}

        .stat-detail {{
            font-size: 0.9em;
            opacity: 0.9;
        }}

        .oe-section {{
            margin-bottom: 40px;
            border: 1px solid #ddd;
            border-radius: 5px;
            overflow: hidden;
        }}

        .oe-header {{
            background: #34495e;
            color: white;
            padding: 15px 20px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .oe-header:hover {{
            background: #2c3e50;
        }}

        .oe-header h2 {{
            margin: 0;
            background: none;
            border: none;
            padding: 0;
            color: white;
        }}

        .oe-stats {{
            display: flex;
            gap: 20px;
            font-size: 0.9em;
        }}

        .oe-content {{
            padding: 20px;
        }}

        .oe-content.collapsed {{
            display: none;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            font-size: 0.9em;
        }}

        th {{
            background: #3498db;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }}

        td {{
            padding: 10px 12px;
            border-bottom: 1px solid #ecf0f1;
        }}

        tr:hover {{
            background: #f8f9fa;
        }}

        .capability-list {{
            margin: 5px 0;
        }}

        .capability-item {{
            display: inline-block;
            padding: 4px 10px;
            margin: 2px;
            border-radius: 4px;
            font-size: 0.85em;
            background: #e3f2fd;
            color: #1976d2;
        }}

        .capability-item.deprecated {{
            background: #ffebee;
            color: #c62828;
            text-decoration: line-through;
        }}

        .level-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: bold;
            margin-left: 5px;
        }}

        .level-badge.L1 {{
            background: #e8f5e9;
            color: #2e7d32;
        }}

        .level-badge.L2 {{
            background: #fff3e0;
            color: #ef6c00;
        }}

        .level-badge.L3 {{
            background: #fce4ec;
            color: #c2185b;
        }}

        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: 500;
        }}

        .badge.success {{
            background: #d4edda;
            color: #155724;
        }}

        .badge.warning {{
            background: #fff3cd;
            color: #856404;
        }}

        .badge.danger {{
            background: #f8d7da;
            color: #721c24;
        }}

        .toggle-icon {{
            transition: transform 0.3s;
        }}

        .toggle-icon.collapsed {{
            transform: rotate(-90deg);
        }}

        .no-data {{
            text-align: center;
            padding: 20px;
            color: #999;
            font-style: italic;
        }}

        .charts-section {{
            margin: 40px 0;
        }}

        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 30px;
            margin-top: 20px;
        }}

        .chart-container {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}

        .chart-container h3 {{
            margin: 0 0 15px 0;
            color: #2c3e50;
            font-size: 1.1em;
        }}

        .chart-wrapper {{
            position: relative;
            height: 300px;
        }}

        .chart-wrapper.tall {{
            height: 400px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>OE Capability Report</h1>

        <div class="metadata">
            <strong>Generated:</strong> {generated_at}<br>
            <strong>GDM Levels:</strong> {gdm_levels}<br>
            <strong>Application Specialisation:</strong> {app_specialisation}
        </div>

        <div class="summary">
            <div class="stat-card">
                <h3>Overview</h3>
                <div class="stat-value">{total_oes}</div>
                <div class="stat-detail">Organizational Entities</div>
                <div class="stat-detail">{total_applications} Applications</div>
                <div class="stat-detail">{unmapped} Unmapped</div>
            </div>

            <div class="stat-card organic">
                <h3>Organic Links</h3>
                <div class="stat-value">{organic_mapped}</div>
                <div class="stat-detail">New Model: {organic_new_model} ({organic_new_pct:.1f}%)</div>
                <div class="stat-detail">Old Model: {organic_old_model} ({organic_old_pct:.1f}%)</div>
            </div>

            <div class="stat-card aggregated">
                <h3>Aggregated Links</h3>
                <div class="stat-value">{aggregated_mapped}</div>
                <div class="stat-detail">New Model: {aggregated_new_model} ({aggregated_new_pct:.1f}%)</div>
                <div class="stat-detail">Old Model: {aggregated_old_model} ({aggregated_old_pct:.1f}%)</div>
            </div>
        </div>

        <div class="charts-section">
            <h2>Visual Analysis</h2>
            <div class="charts-grid">
                <div class="chart-container">
                    <h3>OE Assignment Distribution</h3>
                    <div class="chart-wrapper">
                        <canvas id="oeAssignmentChart"></canvas>
                    </div>
                </div>
                <div class="chart-container">
                    <h3>Mapping Status Distribution</h3>
                    <div class="chart-wrapper">
                        <canvas id="mappingStatusChart"></canvas>
                    </div>
                </div>
                <div class="chart-container">
                    <h3>Model Migration Progress</h3>
                    <div class="chart-wrapper">
                        <canvas id="modelMigrationChart"></canvas>
                    </div>
                </div>
                <div class="chart-container">
                    <h3>Applications by OE</h3>
                    <div class="chart-wrapper tall">
                        <canvas id="oeBreakdownChart"></canvas>
                    </div>
                </div>
            </div>
        </div>

        {no_oe_section}

        {oe_sections}
    </div>

    <script>
        function toggleOE(id) {{
            const content = document.getElementById('oe-content-' + id);
            const icon = document.getElementById('toggle-icon-' + id);
            content.classList.toggle('collapsed');
            icon.classList.toggle('collapsed');
        }}

        // Chart data
        const chartData = {chart_data_json};

        // Chart colors
        const colors = {{
            unmapped: '#dc3545',
            oldOnly: '#fd7e14',
            mixed: '#ffc107',
            newOnly: '#28a745',
            withOE: '#3498db',
            withoutOE: '#e74c3c'
        }};

        // OE Assignment Chart
        new Chart(document.getElementById('oeAssignmentChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Applications with OE', 'Applications without OE'],
                datasets: [{{
                    data: [chartData.apps_with_oe, chartData.apps_without_oe],
                    backgroundColor: [colors.withOE, colors.withoutOE],
                    borderWidth: 2,
                    borderColor: '#fff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ' + context.parsed + ' (' + percentage + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // Mapping Status Chart
        new Chart(document.getElementById('mappingStatusChart'), {{
            type: 'bar',
            data: {{
                labels: ['Unmapped', 'Old Model Only', 'Mixed', 'New Model Only'],
                datasets: [{{
                    label: 'Applications',
                    data: [
                        chartData.unmapped,
                        chartData.old_only,
                        chartData.mixed,
                        chartData.new_only
                    ],
                    backgroundColor: [
                        colors.unmapped,
                        colors.oldOnly,
                        colors.mixed,
                        colors.newOnly
                    ],
                    borderWidth: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            stepSize: 1
                        }}
                    }}
                }}
            }}
        }});

        // Model Migration Chart
        new Chart(document.getElementById('modelMigrationChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['New Model', 'Old Model (Deprecated)'],
                datasets: [{{
                    label: 'Organic',
                    data: [chartData.organic_new, chartData.organic_old],
                    backgroundColor: ['#28a745', '#dc3545'],
                    borderWidth: 2,
                    borderColor: '#fff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }},
                    title: {{
                        display: true,
                        text: 'Organic Links'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((context.parsed / total) * 100).toFixed(1);
                                return context.label + ': ' + context.parsed + ' (' + percentage + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // OE Breakdown Chart
        new Chart(document.getElementById('oeBreakdownChart'), {{
            type: 'bar',
            data: {{
                labels: chartData.oe_names,
                datasets: [
                    {{
                        label: 'Unmapped',
                        data: chartData.oe_unmapped,
                        backgroundColor: colors.unmapped
                    }},
                    {{
                        label: 'Old Model Only',
                        data: chartData.oe_old_only,
                        backgroundColor: colors.oldOnly
                    }},
                    {{
                        label: 'Mixed',
                        data: chartData.oe_mixed,
                        backgroundColor: colors.mixed
                    }},
                    {{
                        label: 'New Model Only',
                        data: chartData.oe_new_only,
                        backgroundColor: colors.newOnly
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }}
                }},
                scales: {{
                    x: {{
                        stacked: true,
                        beginAtZero: true
                    }},
                    y: {{
                        stacked: true
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

        # Extract data
        report_metadata = json_data.get('report_metadata', {})
        summary = report_metadata.get('summary_statistics', {})
        filters = report_metadata.get('filters_applied', {})
        oes = json_data.get('by_oe', {})
        no_oe = json_data.get('applications_without_oe', {})

        # Format OE sections
        oe_sections_html = []
        for i, (oe_id, oe_data) in enumerate(sorted(oes.items(), key=lambda x: x[1].get('oe_name', ''))):
            oe_name = oe_data.get('oe_name', 'Unknown OE')
            stats = oe_data.get('statistics', {})
            apps = oe_data.get('applications', [])

            apps_html = self._generate_applications_table(apps)

            oe_sections_html.append(f"""
        <div class="oe-section">
            <div class="oe-header" onclick="toggleOE('{i}')">
                <h2>{oe_name} <span class="toggle-icon" id="toggle-icon-{i}">▼</span></h2>
                <div class="oe-stats">
                    <span>{stats.get('total_applications', 0)} apps</span>
                    <span class="badge success">Organic: {stats.get('organic_mapped', 0)}</span>
                    <span class="badge warning">Aggregated: {stats.get('aggregated_mapped', 0)}</span>
                </div>
            </div>
            <div class="oe-content" id="oe-content-{i}">
                {apps_html}
            </div>
        </div>
            """)

        # Format no OE section
        no_oe_section_html = ""
        if no_oe.get('applications'):
            stats = no_oe.get('statistics', {})
            apps_html = self._generate_applications_table(no_oe['applications'])
            no_oe_section_html = f"""
        <div class="oe-section">
            <div class="oe-header" onclick="toggleOE('no-oe')">
                <h2>Applications Without OE <span class="toggle-icon" id="toggle-icon-no-oe">▼</span></h2>
                <div class="oe-stats">
                    <span>{stats.get('total_applications', 0)} apps</span>
                    <span class="badge success">Organic: {stats.get('organic_mapped', 0)}</span>
                    <span class="badge warning">Aggregated: {stats.get('aggregated_mapped', 0)}</span>
                </div>
            </div>
            <div class="oe-content" id="oe-content-no-oe">
                {apps_html}
            </div>
        </div>
            """

        # Calculate chart data
        chart_data = {
            'apps_with_oe': summary.get('total_applications', 0) - summary.get('applications_without_oe', 0),
            'apps_without_oe': summary.get('applications_without_oe', 0),
            'organic_new': summary.get('organic_new_model', 0),
            'organic_old': summary.get('organic_old_model', 0),
            'oe_names': [],
            'oe_unmapped': [],
            'oe_old_only': [],
            'oe_mixed': [],
            'oe_new_only': []
        }

        # Calculate category counts across all applications
        category_counts = {'unmapped': 0, 'old_only': 0, 'mixed': 0, 'new_only': 0}

        # Process all OEs
        for oe_id, oe_data in sorted(oes.items(), key=lambda x: x[1].get('oe_name', '')):
            oe_name = oe_data.get('oe_name', 'Unknown OE')
            apps = oe_data.get('applications', [])

            chart_data['oe_names'].append(oe_name)

            oe_counts = {'unmapped': 0, 'old_only': 0, 'mixed': 0, 'new_only': 0}
            for app in apps:
                priority, category = self._categorize_application(app)
                cat_key = category.lower().replace(' (old + new)', '').replace(' ', '_')
                if cat_key in oe_counts:
                    oe_counts[cat_key] += 1
                    category_counts[cat_key] += 1

            chart_data['oe_unmapped'].append(oe_counts['unmapped'])
            chart_data['oe_old_only'].append(oe_counts['old_only'])
            chart_data['oe_mixed'].append(oe_counts['mixed'])
            chart_data['oe_new_only'].append(oe_counts['new_only'])

        # Process applications without OE
        if no_oe.get('applications'):
            for app in no_oe['applications']:
                priority, category = self._categorize_application(app)
                cat_key = category.lower().replace(' (old + new)', '').replace(' ', '_')
                if cat_key in category_counts:
                    category_counts[cat_key] += 1

        chart_data['unmapped'] = category_counts['unmapped']
        chart_data['old_only'] = category_counts['old_only']
        chart_data['mixed'] = category_counts['mixed']
        chart_data['new_only'] = category_counts['new_only']

        chart_data_json = json.dumps(chart_data)

        # Generate HTML
        html = html_template.format(
            generated_at=report_metadata.get('generated_at', 'Unknown'),
            gdm_levels=', '.join(filters.get('gdm_levels', [])),
            app_specialisation=filters.get('application_specialisation', 'Unknown'),
            total_oes=summary.get('total_oes', 0),
            total_applications=summary.get('total_applications', 0),
            unmapped=summary.get('unmapped', 0),
            organic_mapped=summary.get('organic_mapped', 0),
            organic_new_model=summary.get('organic_new_model', 0),
            organic_old_model=summary.get('organic_old_model', 0),
            organic_new_pct=summary.get('organic_new_pct', 0),
            organic_old_pct=summary.get('organic_old_pct', 0),
            aggregated_mapped=summary.get('aggregated_mapped', 0),
            aggregated_new_model=summary.get('aggregated_new_model', 0),
            aggregated_old_model=summary.get('aggregated_old_model', 0),
            aggregated_new_pct=summary.get('aggregated_new_pct', 0),
            aggregated_old_pct=summary.get('aggregated_old_pct', 0),
            oe_sections='\n'.join(oe_sections_html),
            no_oe_section=no_oe_section_html,
            chart_data_json=chart_data_json
        )

        # Write HTML file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        return output_path

    def _categorize_application(self, app: Dict[str, Any]) -> tuple:
        """
        Categorize application by mapping status for sorting priority.

        Returns: (priority, category_name)
        Priority: 0=unmapped, 1=old only, 2=mixed, 3=new only
        """
        organic_caps = app.get('organic_capabilities', {})
        aggregated_caps = app.get('aggregated_capabilities', {})

        # Count deprecated vs non-deprecated capabilities
        def count_deprecated(caps):
            total = 0
            deprecated = 0
            for cap_list in caps.values():
                for cap in cap_list:
                    total += 1
                    if cap.get('deprecated', False):
                        deprecated += 1
            return total, deprecated

        organic_total, organic_dep = count_deprecated(organic_caps)
        agg_total, agg_dep = count_deprecated(aggregated_caps)

        total_caps = organic_total + agg_total
        total_dep = organic_dep + agg_dep

        # Categorize
        if total_caps == 0:
            return (0, "Unmapped")
        elif total_dep == total_caps:
            return (1, "Old Model Only")
        elif total_dep > 0:
            return (2, "Mixed (Old + New)")
        else:
            return (3, "New Model Only")

    def _generate_applications_table(self, applications: List[Dict[str, Any]]) -> str:
        """Generate HTML table for applications, sorted by priority."""
        if not applications:
            return '<div class="no-data">No applications</div>'

        # Sort applications by priority
        sorted_apps = sorted(applications, key=lambda app: self._categorize_application(app)[0])

        # Group by category
        rows = []
        current_category = None

        for app in sorted_apps:
            priority, category = self._categorize_application(app)

            # Add category header if changed
            if category != current_category:
                current_category = category
                category_colors = {
                    "Unmapped": "#dc3545",
                    "Old Model Only": "#fd7e14",
                    "Mixed (Old + New)": "#ffc107",
                    "New Model Only": "#28a745"
                }
                color = category_colors.get(category, "#6c757d")
                rows.append(f"""
                    <tr style="background: {color}15; border-left: 4px solid {color};">
                        <td colspan="3" style="font-weight: bold; color: {color}; padding: 12px;">
                            {category}
                        </td>
                    </tr>
                """)

            app_name = app.get('name', 'Unknown')

            # Format organic capabilities
            organic_html = self._format_capabilities_html(app.get('organic_capabilities', {}))

            # Format aggregated capabilities
            aggregated_html = self._format_capabilities_html(app.get('aggregated_capabilities', {}))

            # Status badges
            status_badges = []
            if app.get('has_organic_links'):
                badge_class = 'danger' if app.get('organic_uses_deprecated') else 'success'
                status_badges.append(f'<span class="badge {badge_class}">Organic</span>')
            if app.get('has_aggregated_links'):
                badge_class = 'danger' if app.get('aggregated_uses_deprecated') else 'warning'
                status_badges.append(f'<span class="badge {badge_class}">Aggregated</span>')
            if not status_badges:
                status_badges.append('<span class="badge">Unmapped</span>')

            rows.append(f"""
                <tr>
                    <td><strong>{app_name}</strong><br>{''.join(status_badges)}</td>
                    <td>{organic_html}</td>
                    <td>{aggregated_html}</td>
                </tr>
            """)

        return f"""
            <table>
                <thead>
                    <tr>
                        <th style="width: 30%">Application</th>
                        <th style="width: 35%">Organic Capabilities</th>
                        <th style="width: 35%">Aggregated Capabilities</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        """

    def _format_capabilities_html(self, capabilities: Dict[str, List[Dict[str, Any]]]) -> str:
        """Format capabilities as HTML."""
        if not capabilities:
            return '<span style="color: #999; font-style: italic;">None</span>'

        items = []
        for cap_name, cap_list in sorted(capabilities.items()):
            for cap in cap_list:
                level = cap.get('level', 'Unknown')
                deprecated = cap.get('deprecated', False)
                dep_class = ' deprecated' if deprecated else ''
                items.append(
                    f'<span class="capability-item{dep_class}">'
                    f'{cap_name}<span class="level-badge {level}">{level}</span>'
                    f'</span>'
                )

        return '<div class="capability-list">' + ''.join(items) + '</div>'

    def generate_markdown_report(self, json_data: Dict[str, Any], output_path: str) -> str:
        """Generate Markdown version of the report from JSON data."""
        lines = []

        # Extract data
        report_metadata = json_data.get('report_metadata', {})
        summary = report_metadata.get('summary_statistics', {})
        filters = report_metadata.get('filters_applied', {})
        oes = json_data.get('by_oe', {})
        no_oe = json_data.get('applications_without_oe', {})

        # Header
        lines.append("# OE Capability Report\n")

        # Metadata
        lines.append("## Report Information\n")
        lines.append(f"- **Generated:** {report_metadata.get('generated_at', 'Unknown')}")
        lines.append(f"- **GDM Levels:** {', '.join(filters.get('gdm_levels', []))}")
        lines.append(f"- **Application Specialisation:** {filters.get('application_specialisation', 'Unknown')}\n")

        # Summary Statistics
        lines.append("## Summary Statistics\n")
        lines.append("### Overview")
        lines.append(f"- **Total OEs:** {summary.get('total_oes', 0)}")
        lines.append(f"- **Total Applications:** {summary.get('total_applications', 0)}")
        lines.append(f"- **Unmapped Applications:** {summary.get('unmapped', 0)}")
        lines.append(f"- **Applications Without OE:** {summary.get('applications_without_oe', 0)}\n")

        lines.append("### Organic Links (RC_REALIZATION)")
        lines.append(f"- **Mapped:** {summary.get('organic_mapped', 0)}")
        lines.append(f"- **New Model:** {summary.get('organic_new_model', 0)} ({summary.get('organic_new_pct', 0):.1f}%)")
        lines.append(f"- **Old Model (deprecated):** {summary.get('organic_old_model', 0)} ({summary.get('organic_old_pct', 0):.1f}%)\n")

        lines.append("### Aggregated Links (Curated)")
        lines.append(f"- **Mapped:** {summary.get('aggregated_mapped', 0)}")
        lines.append(f"- **New Model:** {summary.get('aggregated_new_model', 0)} ({summary.get('aggregated_new_pct', 0):.1f}%)")
        lines.append(f"- **Old Model (deprecated):** {summary.get('aggregated_old_model', 0)} ({summary.get('aggregated_old_pct', 0):.1f}%)\n")

        # Applications Without OE (show first for priority)
        lines.append("## Organizational Entities\n")

        if no_oe.get('applications'):
            lines.append("### Applications Without OE\n")
            stats = no_oe.get('statistics', {})
            lines.append(f"**Statistics:**")
            lines.append(f"- Total Applications: {stats.get('total_applications', 0)}")
            lines.append(f"- Organic Mapped: {stats.get('organic_mapped', 0)}")
            lines.append(f"- Aggregated Mapped: {stats.get('aggregated_mapped', 0)}")
            lines.append(f"- Unmapped: {stats.get('unmapped', 0)}\n")
            lines.append(self._generate_applications_markdown_table(no_oe['applications']))

        # OE Sections
        for oe_id, oe_data in sorted(oes.items(), key=lambda x: x[1].get('oe_name', '')):
            oe_name = oe_data.get('oe_name', 'Unknown OE')
            stats = oe_data.get('statistics', {})
            apps = oe_data.get('applications', [])

            lines.append(f"### {oe_name}\n")
            lines.append(f"**Statistics:**")
            lines.append(f"- Total Applications: {stats.get('total_applications', 0)}")
            lines.append(f"- Organic Mapped: {stats.get('organic_mapped', 0)}")
            lines.append(f"- Aggregated Mapped: {stats.get('aggregated_mapped', 0)}")
            lines.append(f"- Unmapped: {stats.get('unmapped', 0)}\n")

            if apps:
                lines.append(self._generate_applications_markdown_table(apps))
            else:
                lines.append("_No applications_\n")

        # Write file
        markdown_content = '\n'.join(lines)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        return output_path

    def _generate_applications_markdown_table(self, applications: List[Dict[str, Any]]) -> str:
        """Generate Markdown table for applications, sorted by priority."""
        if not applications:
            return "_No applications_\n"

        # Sort applications by priority
        sorted_apps = sorted(applications, key=lambda app: self._categorize_application(app)[0])

        lines = []
        current_category = None

        for app in sorted_apps:
            priority, category = self._categorize_application(app)

            # Add category header if changed
            if category != current_category:
                current_category = category

                # Add table header for new category
                if lines:  # Add spacing between categories
                    lines.append("")

                category_emoji = {
                    "Unmapped": "🔴",
                    "Old Model Only": "🟠",
                    "Mixed (Old + New)": "🟡",
                    "New Model Only": "🟢"
                }
                emoji = category_emoji.get(category, "⚪")
                lines.append(f"#### {emoji} {category}\n")
                lines.append("| Application | Status | Organic Capabilities | Aggregated Capabilities |")
                lines.append("|------------|--------|---------------------|------------------------|")

            app_name = app.get('name', 'Unknown')

            # Status
            status_parts = []
            if app.get('has_organic_links'):
                if app.get('organic_uses_deprecated'):
                    status_parts.append("🔴 Organic")
                else:
                    status_parts.append("✅ Organic")
            if app.get('has_aggregated_links'):
                if app.get('aggregated_uses_deprecated'):
                    status_parts.append("🔴 Aggregated")
                else:
                    status_parts.append("⚠️ Aggregated")
            status = ", ".join(status_parts) if status_parts else "⚪ Unmapped"

            # Format capabilities
            organic_caps = self._format_capabilities_markdown(app.get('organic_capabilities', {}))
            aggregated_caps = self._format_capabilities_markdown(app.get('aggregated_capabilities', {}))

            lines.append(f"| {app_name} | {status} | {organic_caps} | {aggregated_caps} |")

        lines.append("")
        return '\n'.join(lines)

    def _format_capabilities_markdown(self, capabilities: Dict[str, List[Dict[str, Any]]]) -> str:
        """Format capabilities as Markdown."""
        if not capabilities:
            return "_None_"

        items = []
        for cap_name, cap_list in sorted(capabilities.items()):
            for cap in cap_list:
                level = cap.get('level', 'Unknown')
                deprecated = cap.get('deprecated', False)
                if deprecated:
                    items.append(f"~~{cap_name}~~ `{level}`")
                else:
                    items.append(f"{cap_name} `{level}`")

        return "<br>".join(items)


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

    parser.add_argument(
        '--html',
        action='store_true',
        help='Generate HTML report in addition to JSON'
    )

    parser.add_argument(
        '--markdown', '--md',
        action='store_true',
        help='Generate Markdown report in addition to JSON'
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
        # Generate JSON report
        report_path = reporter.generate_oe_report(
            app_specialisation=args.app_specialisation,
            gdm_levels=args.gdm_levels,
            output_path=args.output
        )

        if report_path:
            print(f"\n✓ JSON report generated successfully: {report_path}")

            # Read JSON data once for multiple formats
            json_data = None
            if args.html or args.markdown:
                with open(report_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

            # Generate HTML report if requested
            if args.html:
                html_path = Path(report_path).with_suffix('.html')
                html_report_path = reporter.generate_html_report(json_data, str(html_path))
                file_size_kb = Path(html_report_path).stat().st_size / 1024
                print(f"✓ HTML report generated successfully: {html_report_path} ({file_size_kb:.1f} KB)")

            # Generate Markdown report if requested
            if args.markdown:
                md_path = Path(report_path).with_suffix('.md')
                md_report_path = reporter.generate_markdown_report(json_data, str(md_path))
                file_size_kb = Path(md_report_path).stat().st_size / 1024
                print(f"✓ Markdown report generated successfully: {md_report_path} ({file_size_kb:.1f} KB)")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
