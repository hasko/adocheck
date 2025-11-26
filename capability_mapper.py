#!/usr/bin/env python3
"""
Capability Mapper for ADOit entities.

Maps business applications to top-level capabilities through relationship traversal.
Uses BFS to find shortest paths from applications to capability domains.
"""

import os
import sys
import json
import argparse
import datetime
import logging
from typing import Dict, List, Any, Optional, Set, Tuple
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from adoit_api import AdoitApi

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Valid ArchiMate relationship types for forward traversal
VALID_RELATIONSHIP_TYPES = {
    # Structural relationships
    'composition',
    'aggregation',
    'realization',
    # Dependency relationships
    'serving',
    'access',
    'influence',
    # Association (bidirectional but used in practice)
    'association'
}


class CapabilityMapper:
    """Maps business applications to top-level capabilities via relationship traversal."""

    def __init__(self, api: AdoitApi, parallel_workers: int = 10, use_cache: bool = False):
        """
        Initialize the capability mapper.

        Args:
            api: Instance of AdoitApi to use for retrieving data
            parallel_workers: Number of parallel workers for relationship fetching
            use_cache: Whether to use cached relationship data
        """
        self.api = api
        self.parallel_workers = parallel_workers
        self.use_cache = use_cache
        self.graph = {}  # adjacency list: {entity_id: [(target_id, rel_type), ...]}
        self.entity_cache = {}  # {entity_id: entity_data}
        self.top_level_capabilities = []  # List of (entity_id, entity_name, entity_type)
        self.relationship_types_whitelist = set()

    def discover_top_level_capabilities(
        self,
        manual_ids: Optional[List[str]] = None
    ) -> List[Tuple[str, str, str]]:
        """
        Discover top-level capability entities.

        Args:
            manual_ids: Optional list of entity IDs to use instead of discovery

        Returns:
            List of tuples: [(entity_id, entity_name, entity_type), ...]
        """
        logger.info("=" * 60)
        logger.info("Discovering top-level capabilities...")
        logger.info("=" * 60)

        # If manual IDs provided, fetch those entities directly
        if manual_ids:
            logger.info(f"Using manually specified capability IDs: {manual_ids}")
            capabilities = []
            for entity_id in manual_ids:
                entity = self.api.get_entity(entity_id, force_refresh=not self.use_cache)
                if entity:
                    capabilities.append((
                        entity.get('id'),
                        entity.get('name', 'Unknown'),
                        entity.get('type', 'Unknown')
                    ))
                    logger.info(f"  ✓ {entity.get('name')} ({entity.get('type')})")
                else:
                    logger.error(f"  ✗ Entity {entity_id} not found")

            self.top_level_capabilities = capabilities
            return capabilities

        # Target capability names to search for
        target_names = [
            "Customer Centric Domains",
            "Enabling Cluster",
            "Corporate Cluster"
        ]

        capabilities = []

        # Strategy 1: Direct name search using API filtering
        logger.info("Strategy 1: Searching with name-based API filters...")

        # We need to search across all possible entity types
        # Get metamodel classes to search
        try:
            metamodel_classes = self.api.get_metamodel_classes()
            classes = metamodel_classes.get('classes', [])

            # Focus on capability-related classes
            capability_classes = []
            for cls in classes:
                meta_name = cls.get('metaName', '')
                if any(keyword in meta_name.upper() for keyword in ['CAPABILITY', 'DOMAIN', 'CLUSTER', 'FUNCTION']):
                    capability_classes.append(meta_name)

            logger.info(f"Found {len(capability_classes)} capability-related classes to search")

            # Search each target name using API filtering
            for target_name in target_names:
                logger.info(f"Searching for: '{target_name}'")

                # Try searching in capability classes with name filter
                found = False
                for class_name in capability_classes[:10]:  # Limit to first 10 to avoid too many requests
                    try:
                        # First discover the name attribute for this class
                        # Most ADOit entities use A_NAME, but let's try that first
                        filters = [
                            {"className": [class_name]},
                            {"attrName": "A_NAME", "value": target_name, "op": "OP_EQ"}
                        ]

                        logger.debug(f"  Trying {class_name} with name filter...")
                        entities = self.api.get_entities_by_filters(filters, force_refresh=not self.use_cache)

                        # Check results and filter out "(do not use)" versions
                        for entity in entities:
                            entity_name = entity.get('name', '')

                            # Exact match, excluding "(do not use)" versions
                            if entity_name == target_name and "(do not use)" not in entity_name.lower():
                                capabilities.append((
                                    entity.get('id'),
                                    entity_name,
                                    entity.get('type', class_name)
                                ))
                                logger.info(f"  ✓ Found: {entity_name} (ID: {entity.get('id')}, Type: {entity.get('type')})")
                                found = True
                                break

                        if found:
                            break

                    except Exception as e:
                        logger.debug(f"Search in {class_name} with name filter failed: {e}")

                        # Fallback: Try searching with LIKE operator for partial match
                        try:
                            filters = [
                                {"className": [class_name]},
                                {"attrName": "A_NAME", "value": target_name, "op": "OP_LIKE"}
                            ]
                            entities = self.api.get_entities_by_filters(filters, force_refresh=not self.use_cache)

                            for entity in entities:
                                entity_name = entity.get('name', '')

                                # Exact match, excluding "(do not use)" versions
                                if entity_name == target_name and "(do not use)" not in entity_name.lower():
                                    capabilities.append((
                                        entity.get('id'),
                                        entity_name,
                                        entity.get('type', class_name)
                                    ))
                                    logger.info(f"  ✓ Found: {entity_name} (ID: {entity.get('id')}, Type: {entity.get('type')})")
                                    found = True
                                    break

                            if found:
                                break
                        except:
                            continue

                if not found:
                    logger.warning(f"  ✗ Could not find '{target_name}'")

        except Exception as e:
            logger.error(f"Error during capability discovery: {e}")

        # Strategy 2: Pattern matching as fallback
        if len(capabilities) < len(target_names):
            logger.info("\nStrategy 2: Pattern matching with keywords...")

            patterns = {
                "Customer Centric Domains": ["customer", "centric"],
                "Enabling Cluster": ["enabling", "cluster"],
                "Corporate Cluster": ["corporate", "cluster"]
            }

            # Get already found names
            found_names = [name for _, name, _ in capabilities]

            # Search for missing capabilities
            for target_name, keywords in patterns.items():
                if target_name in found_names:
                    continue

                logger.info(f"Searching for pattern: {keywords}")
                # Could implement fuzzy matching here if needed
                # For now, we'll skip to avoid too many API calls

        if not capabilities:
            logger.error("\n" + "=" * 60)
            logger.error("ERROR: No top-level capabilities found!")
            logger.error("=" * 60)
            logger.error("Searched for:")
            for name in target_names:
                logger.error(f"  - {name}")
            logger.error("\nPlease use --target-capability-ids to specify manually:")
            logger.error("  python capability_mapper.py --target-capability-ids \"{id1}\" \"{id2}\" \"{id3}\"")
            sys.exit(1)

        logger.info(f"\n✓ Discovered {len(capabilities)} top-level capabilities")
        self.top_level_capabilities = capabilities
        return capabilities

    def discover_valid_relationship_types(
        self,
        manual_types: Optional[List[str]] = None
    ) -> Set[str]:
        """
        Discover valid relationship types from metamodel.

        Args:
            manual_types: Optional list of relationship metaNames to use

        Returns:
            Set of valid relationship metaNames
        """
        logger.info("Discovering valid relationship types...")

        # If manual types provided, use those
        if manual_types:
            logger.info(f"Using manually specified types: {manual_types}")
            self.relationship_types_whitelist = set(manual_types)
            return self.relationship_types_whitelist

        try:
            # Get metamodel to find relationship types
            metamodel = self.api.get_metamodel()
            relations = metamodel.get('relations', [])

            valid_types = set()

            for relation in relations:
                meta_name = relation.get('metaName', '')
                # Get display name or type field
                display_names = relation.get('displayNames', [])
                rel_type = ''
                if display_names and len(display_names) > 0:
                    rel_type = display_names[0].get('value', '').lower()

                # Check if this relation type matches our valid patterns
                for valid_pattern in VALID_RELATIONSHIP_TYPES:
                    if valid_pattern in rel_type or valid_pattern in meta_name.lower():
                        valid_types.add(meta_name)
                        logger.debug(f"Including: {meta_name} ({rel_type})")
                        break

            logger.info(f"Found {len(valid_types)} valid relationship types from metamodel")
            self.relationship_types_whitelist = valid_types

        except Exception as e:
            logger.warning(f"Could not fetch metamodel relations: {e}")
            logger.warning("Will include all relationship types")
            self.relationship_types_whitelist = set()  # Empty set means accept all

        return self.relationship_types_whitelist

    def fetch_business_applications(
        self,
        specialisation: str = "Bus. App."
    ) -> List[Dict[str, Any]]:
        """
        Fetch business applications with specified specialisation.

        Args:
            specialisation: Value for Specialisation attribute

        Returns:
            List of application entity dictionaries
        """
        logger.info("=" * 60)
        logger.info("Fetching business applications...")
        logger.info("=" * 60)
        logger.info(f"Filter: C_APPLICATION with Specialisation = '{specialisation}'")

        try:
            # We need to discover the specialisation attribute name first
            # Using similar pattern from report_generator.py
            from report_generator import ReportGenerator

            generator = ReportGenerator(self.api)
            attribute_mapping = generator.discover_attribute_names(
                "C_APPLICATION",
                ["Specialisation"]
            )

            specialisation_attr = attribute_mapping.get("Specialisation")

            if not specialisation_attr:
                logger.error("Could not find 'Specialisation' attribute in metamodel")
                logger.info("Fetching all C_APPLICATION entities instead...")
                filters = [{"className": ["C_APPLICATION"]}]
            else:
                logger.info(f"Discovered attribute: Specialisation -> {specialisation_attr}")
                filters = [
                    {"className": ["C_APPLICATION"]},
                    {"attrName": specialisation_attr, "value": specialisation, "op": "OP_EQ"}
                ]

            applications = self.api.get_entities_by_filters(filters, force_refresh=not self.use_cache)

            logger.info(f"✓ Found {len(applications)} business applications")

            if not applications:
                logger.warning("No applications found matching the criteria")
                logger.info("Try running with a different --app-specialisation value")

            return applications

        except Exception as e:
            logger.error(f"Error fetching applications: {e}")
            return []

    def fetch_all_relationships(self, entity_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch relationships for multiple entities in parallel.

        Args:
            entity_ids: List of entity IDs to fetch relationships for

        Returns:
            Dictionary mapping entity_id to list of relationship dicts
        """
        results = {}
        total = len(entity_ids)

        logger.info(f"Fetching relationships for {total} entities...")
        logger.info(f"Using {self.parallel_workers} parallel workers")

        completed = 0

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            # Submit all tasks
            future_to_id = {
                executor.submit(
                    self.api.get_relationships,
                    entity_id,
                    force_refresh=not self.use_cache
                ): entity_id
                for entity_id in entity_ids
            }

            # Collect results as they complete
            for future in as_completed(future_to_id):
                entity_id = future_to_id[future]
                try:
                    relations = future.result()
                    results[entity_id] = relations
                    completed += 1

                    if completed % 50 == 0 or completed == total:
                        progress = (completed / total) * 100
                        logger.info(f"Progress: {completed}/{total} ({progress:.1f}%)")

                except Exception as e:
                    logger.warning(f"Failed to fetch relationships for {entity_id}: {e}")
                    results[entity_id] = []

        logger.info(f"✓ Completed fetching relationships for {len(results)} entities")
        return results

    def build_graph(self, entity_ids: Set[str]) -> Dict[str, List[Tuple[str, str]]]:
        """
        Build adjacency list graph from entity relationships.

        Args:
            entity_ids: Set of entity IDs to include in graph

        Returns:
            Adjacency list: {entity_id: [(target_id, rel_type), ...]}
        """
        logger.info("=" * 60)
        logger.info("Building relationship graph...")
        logger.info("=" * 60)

        # Fetch all relationships in parallel
        all_relationships = self.fetch_all_relationships(list(entity_ids))

        # Build adjacency list
        graph = {}
        total_edges = 0
        filtered_edges = 0

        for entity_id, relationships in all_relationships.items():
            edges = []

            for rel in relationships:
                from_id = rel.get('fromId', '')
                to_id = rel.get('toId', '')
                rel_type = rel.get('relationType', '')

                # Only include forward relationships (where this entity is the source)
                if from_id == entity_id and to_id in entity_ids:
                    total_edges += 1

                    # Filter by relationship type if whitelist exists
                    if self.relationship_types_whitelist:
                        if rel_type in self.relationship_types_whitelist:
                            edges.append((to_id, rel_type))
                        else:
                            filtered_edges += 1
                    else:
                        # No whitelist, accept all
                        edges.append((to_id, rel_type))

            if edges:
                graph[entity_id] = edges

        logger.info(f"Graph statistics:")
        logger.info(f"  Nodes: {len(entity_ids)}")
        logger.info(f"  Edges (total): {total_edges}")
        if self.relationship_types_whitelist:
            logger.info(f"  Edges (filtered out): {filtered_edges}")
            logger.info(f"  Edges (included): {total_edges - filtered_edges}")
        logger.info(f"  Nodes with outgoing edges: {len(graph)}")

        self.graph = graph
        return graph

    def find_shortest_path(
        self,
        start_id: str,
        target_ids: Set[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Find shortest path from start entity to any target capability using BFS.

        Args:
            start_id: Starting entity ID
            target_ids: Set of target capability entity IDs

        Returns:
            Dictionary with path info or None if no path found:
            {'target_id': str, 'path': [entity_ids], 'path_length': int}
        """
        # Early exit if start is already a target
        if start_id in target_ids:
            return {
                'target_id': start_id,
                'path': [start_id],
                'path_length': 0
            }

        # BFS initialization
        queue = deque([(start_id, [start_id])])
        visited = {start_id}

        while queue:
            current_id, path = queue.popleft()

            # Explore forward relationships
            for neighbor_id, rel_type in self.graph.get(current_id, []):
                if neighbor_id in visited:
                    continue

                visited.add(neighbor_id)
                new_path = path + [neighbor_id]

                # Check if we've reached a target capability
                if neighbor_id in target_ids:
                    return {
                        'target_id': neighbor_id,
                        'path': new_path,
                        'path_length': len(new_path) - 1,
                        'path_ids': new_path
                    }

                # Continue exploring
                queue.append((neighbor_id, new_path))

        return None  # No path found

    def get_entity_details(self, entity_id: str) -> Dict[str, Any]:
        """
        Get entity details with caching.

        Args:
            entity_id: Entity ID to fetch

        Returns:
            Entity data dictionary
        """
        if entity_id in self.entity_cache:
            return self.entity_cache[entity_id]

        entity = self.api.get_entity(entity_id, force_refresh=not self.use_cache)
        if entity:
            self.entity_cache[entity_id] = entity
            return entity

        return {
            'id': entity_id,
            'name': 'Unknown',
            'type': 'Unknown'
        }

    def build_path_details(
        self,
        path_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Build detailed path information with entity names and relationship types.

        Args:
            path_ids: List of entity IDs forming the path

        Returns:
            List of path step dictionaries with entity and relationship info
        """
        path_details = []

        for i, entity_id in enumerate(path_ids):
            entity = self.get_entity_details(entity_id)

            step = {
                'entity_id': entity_id,
                'entity_name': entity.get('name', 'Unknown'),
                'entity_type': entity.get('type', 'Unknown')
            }

            # Add relationship info (except for first node)
            if i > 0:
                prev_id = path_ids[i - 1]
                # Find the relationship type from previous to current
                rel_type = None
                for target_id, rt in self.graph.get(prev_id, []):
                    if target_id == entity_id:
                        rel_type = rt
                        break

                step['relationship_from_previous'] = {
                    'type': rel_type or 'Unknown',
                    'from_id': prev_id
                }

            path_details.append(step)

        return path_details

    def map_all_applications(
        self,
        applications: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Map all applications to top-level capabilities.

        Args:
            applications: List of application entity dictionaries

        Returns:
            Mapping results with statistics
        """
        logger.info("=" * 60)
        logger.info("Mapping applications to capabilities...")
        logger.info("=" * 60)

        # Get target capability IDs
        target_ids = {cap_id for cap_id, _, _ in self.top_level_capabilities}

        # Collect all entity IDs we need to traverse
        # Start with applications and capabilities
        all_entity_ids = set()
        all_entity_ids.update(target_ids)

        for app in applications:
            all_entity_ids.add(app.get('id'))

        # We need to expand the graph to include intermediate entities
        # Strategy: Fetch relationships for all known entities iteratively
        # until we've built a comprehensive graph
        logger.info(f"Starting with {len(all_entity_ids)} entities (apps + capabilities)")
        logger.info("Expanding graph to include intermediate entities...")

        # Iterative graph expansion (limit to avoid infinite expansion)
        max_iterations = 3
        for iteration in range(max_iterations):
            logger.info(f"Graph expansion iteration {iteration + 1}/{max_iterations}")

            # Fetch relationships for current entity set
            current_relationships = self.fetch_all_relationships(list(all_entity_ids))

            # Find new entities
            new_entities = set()
            for entity_id, relationships in current_relationships.items():
                for rel in relationships:
                    from_id = rel.get('fromId', '')
                    to_id = rel.get('toId', '')

                    if from_id not in all_entity_ids:
                        new_entities.add(from_id)
                    if to_id not in all_entity_ids:
                        new_entities.add(to_id)

            if not new_entities:
                logger.info("No new entities found, graph is complete")
                break

            logger.info(f"Found {len(new_entities)} new entities")
            all_entity_ids.update(new_entities)

        logger.info(f"Final graph size: {len(all_entity_ids)} entities")

        # Build the graph
        self.build_graph(all_entity_ids)

        # Map each application
        results = {
            'mapped': {},
            'unmapped': [],
            'statistics': {
                'total_applications': len(applications),
                'mapped_applications': 0,
                'unmapped_applications': 0,
                'path_lengths': []
            }
        }

        # Group capabilities by name for easier reporting
        capability_map = {}
        for cap_id, cap_name, cap_type in self.top_level_capabilities:
            capability_map[cap_id] = (cap_name, cap_type)

        logger.info(f"\nProcessing {len(applications)} applications...")

        for i, app in enumerate(applications):
            app_id = app.get('id')
            app_name = app.get('name', 'Unknown')

            if (i + 1) % 10 == 0:
                logger.info(f"Progress: {i + 1}/{len(applications)}")

            # Find shortest path to any capability
            path_result = self.find_shortest_path(app_id, target_ids)

            if path_result:
                target_id = path_result['target_id']
                path_length = path_result['path_length']
                path_ids = path_result['path_ids']

                # Build detailed path info
                path_details = self.build_path_details(path_ids)

                # Get capability name
                cap_name, cap_type = capability_map.get(target_id, ('Unknown', 'Unknown'))

                # Store mapping
                if cap_name not in results['mapped']:
                    results['mapped'][cap_name] = {
                        'capability_id': target_id,
                        'capability_type': cap_type,
                        'applications': []
                    }

                results['mapped'][cap_name]['applications'].append({
                    'id': app_id,
                    'name': app_name,
                    'type': app.get('type', 'Unknown'),
                    'path_length': path_length,
                    'path_details': path_details
                })

                results['statistics']['mapped_applications'] += 1
                results['statistics']['path_lengths'].append(path_length)

                if path_length > 10:
                    logger.warning(f"Long path ({path_length}) for {app_name}")

            else:
                # No path found
                results['unmapped'].append({
                    'id': app_id,
                    'name': app_name,
                    'type': app.get('type', 'Unknown'),
                    'reason': 'No path found to any top-level capability'
                })
                results['statistics']['unmapped_applications'] += 1

        # Calculate final statistics
        stats = results['statistics']
        if stats['path_lengths']:
            stats['average_path_length'] = sum(stats['path_lengths']) / len(stats['path_lengths'])
            stats['max_path_length'] = max(stats['path_lengths'])
            stats['min_path_length'] = min(stats['path_lengths'])
        else:
            stats['average_path_length'] = 0
            stats['max_path_length'] = 0
            stats['min_path_length'] = 0

        if stats['total_applications'] > 0:
            stats['coverage_percentage'] = (stats['mapped_applications'] / stats['total_applications']) * 100
        else:
            stats['coverage_percentage'] = 0

        logger.info("\n" + "=" * 60)
        logger.info("Mapping Statistics:")
        logger.info("=" * 60)
        logger.info(f"Total applications: {stats['total_applications']}")
        logger.info(f"Mapped: {stats['mapped_applications']} ({stats['coverage_percentage']:.1f}%)")
        logger.info(f"Unmapped: {stats['unmapped_applications']}")
        if stats['path_lengths']:
            logger.info(f"Average path length: {stats['average_path_length']:.2f}")
            logger.info(f"Path length range: {stats['min_path_length']} - {stats['max_path_length']}")

        return results

    def generate_report(
        self,
        results: Dict[str, Any],
        output_path: str,
        filters_applied: Dict[str, Any]
    ) -> str:
        """
        Generate JSON report file.

        Args:
            results: Mapping results from map_all_applications()
            output_path: Path where to save the report
            filters_applied: Dictionary describing filters used

        Returns:
            Path to generated report file
        """
        logger.info("=" * 60)
        logger.info("Generating report...")
        logger.info("=" * 60)

        # Create output directory if needed
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build report structure
        report = {
            'report_metadata': {
                'generated_at': datetime.datetime.now().isoformat(),
                'report_type': 'application_capability_mapping',
                'filters_applied': filters_applied,
                'statistics': results['statistics'],
                'top_level_capabilities': [
                    {
                        'id': cap_id,
                        'name': cap_name,
                        'type': cap_type
                    }
                    for cap_id, cap_name, cap_type in self.top_level_capabilities
                ]
            },
            'mappings_by_capability': {},
            'unmapped_applications': results['unmapped']
        }

        # Format mappings by capability
        for cap_name, mapping_data in results['mapped'].items():
            report['mappings_by_capability'][cap_name] = {
                'capability_id': mapping_data['capability_id'],
                'capability_type': mapping_data['capability_type'],
                'application_count': len(mapping_data['applications']),
                'applications': mapping_data['applications']
            }

        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Report generated: {output_path}")
        logger.info(f"  Size: {Path(output_path).stat().st_size / 1024:.1f} KB")

        return output_path

    def run_mapping(
        self,
        app_specialisation: str = "Bus. App.",
        output_path: str = "data/capability_mapping.json",
        manual_capability_ids: Optional[List[str]] = None,
        manual_relationship_types: Optional[List[str]] = None,
        dry_run: bool = False
    ) -> str:
        """
        Run the complete capability mapping process.

        Args:
            app_specialisation: Application specialisation value to filter
            output_path: Where to save the JSON report
            manual_capability_ids: Optional manual capability IDs
            manual_relationship_types: Optional manual relationship types
            dry_run: If True, only discover entities without mapping

        Returns:
            Path to generated report file (or empty string for dry run)
        """
        logger.info("=" * 60)
        logger.info("CAPABILITY MAPPING - START")
        logger.info("=" * 60)

        # Step 1: Discover top-level capabilities
        self.discover_top_level_capabilities(manual_capability_ids)

        # Step 2: Discover valid relationship types
        self.discover_valid_relationship_types(manual_relationship_types)

        # Step 3: Fetch business applications
        applications = self.fetch_business_applications(app_specialisation)

        if not applications:
            logger.warning("No applications to map. Exiting.")
            return ""

        if dry_run:
            logger.info("\n" + "=" * 60)
            logger.info("DRY RUN - Discovery complete, skipping mapping")
            logger.info("=" * 60)
            return ""

        # Step 4: Map applications to capabilities
        results = self.map_all_applications(applications)

        # Step 5: Generate report
        filters_applied = {
            'application_class': 'C_APPLICATION',
            'application_filter': f'Specialisation = "{app_specialisation}"',
            'relationship_types': list(self.relationship_types_whitelist) if self.relationship_types_whitelist else 'all'
        }

        report_path = self.generate_report(results, output_path, filters_applied)

        logger.info("\n" + "=" * 60)
        logger.info("CAPABILITY MAPPING - COMPLETE")
        logger.info("=" * 60)

        return report_path


def main():
    """Command-line interface for capability mapper."""
    parser = argparse.ArgumentParser(
        description='Map business applications to top-level capabilities'
    )

    parser.add_argument(
        '--output', '-o',
        default='data/capability_mapping.json',
        help='Output file path (default: data/capability_mapping.json)'
    )

    parser.add_argument(
        '--target-capability-ids',
        nargs='+',
        help='Manually specify target capability entity IDs'
    )

    parser.add_argument(
        '--relationship-types',
        help='Comma-separated list of relationship metaNames to include'
    )

    parser.add_argument(
        '--app-specialisation',
        default='Bus. App.',
        help='Application Specialisation value to filter (default: Bus. App.)'
    )

    parser.add_argument(
        '--use-cache',
        action='store_true',
        help='Use cached data instead of forcing refresh from API'
    )

    parser.add_argument(
        '--parallel-workers',
        type=int,
        default=10,
        help='Number of parallel workers for fetching relationships (default: 10)'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Discover entities but do not perform mapping'
    )

    args = parser.parse_args()

    # Configure logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Initialize API
    try:
        api = AdoitApi()
    except Exception as e:
        logger.error(f"Failed to initialize ADOit API: {e}")
        sys.exit(1)

    # Create mapper
    mapper = CapabilityMapper(
        api,
        parallel_workers=args.parallel_workers,
        use_cache=args.use_cache
    )

    # Parse relationship types if provided
    manual_rel_types = None
    if args.relationship_types:
        manual_rel_types = [t.strip() for t in args.relationship_types.split(',')]

    # Run mapping
    try:
        report_path = mapper.run_mapping(
            app_specialisation=args.app_specialisation,
            output_path=args.output,
            manual_capability_ids=args.target_capability_ids,
            manual_relationship_types=manual_rel_types,
            dry_run=args.dry_run
        )

        if report_path:
            print(f"\n✓ Report generated successfully: {report_path}")

    except Exception as e:
        logger.error(f"Error during mapping: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
