#!/usr/bin/env python3
"""
Diagnostic script to understand why no capabilities are captured.
"""

import json
from adoit_api import AdoitApi

def diagnose():
    print("=" * 60)
    print("CAPABILITY EXTRACTION DIAGNOSTICS")
    print("=" * 60)

    api = AdoitApi()

    # Hypothesis 1: Check search results structure
    print("\n[H1] Testing search endpoint data structure...")
    filters = [
        {"className": ["C_APPLICATION_COMPONENT"]},
        {"attrName": "A_APPLICATION_COMPONENT_SPEC", "value": "Bus. App.", "op": "OP_EQ"}
    ]
    applications = api.get_entities_by_filters(filters)

    if not applications:
        print("ERROR: No applications found")
        return

    sample_app = applications[0]
    print(f"  Sample app: {sample_app.get('name')}")
    print(f"  Keys: {list(sample_app.keys())}")
    print(f"  Total attributes: {len(sample_app.get('attributes', []))}")

    rel_attrs_search = [a for a in sample_app.get('attributes', [])
                        if a.get('attrType') == 'RELATION']
    print(f"  RELATION attributes in search: {len(rel_attrs_search)}")

    # Inspect RELATION attributes
    if rel_attrs_search:
        print("\n  RELATION attribute details:")
        for attr in rel_attrs_search[:5]:
            metaName = attr.get('metaName', 'Unknown')
            targets = attr.get('targets', [])
            print(f"    - {metaName}: {len(targets)} targets")
            if targets:
                target_types = set(t.get('metaName', 'Unknown') for t in targets)
                print(f"      Target types: {target_types}")
                # Check for capabilities
                has_cap = any('CAPAB' in t.get('metaName', '').upper() for t in targets)
                if has_cap:
                    print(f"      ** HAS CAPABILITY TARGETS **")
                    cap_samples = [t.get('name', 'Unknown')[:60] for t in targets
                                  if 'CAPAB' in t.get('metaName', '').upper()]
                    print(f"      Samples: {cap_samples[:3]}")

    # Hypothesis 2: Check full entity structure
    print("\n[H2] Testing full entity fetch...")
    app_id = sample_app.get('id', '').strip('{}')
    print(f"  Fetching full entity for app_id: {app_id}")
    full_app = api.get_entity(app_id)

    if full_app:
        print(f"  ✓ Full entity retrieved successfully")
        print(f"  Full app attributes: {len(full_app.get('attributes', []))}")

        rel_attrs_full = [a for a in full_app.get('attributes', [])
                         if a.get('attrType') == 'RELATION']
        print(f"  RELATION attributes in full fetch: {len(rel_attrs_full)}")

        # Check for capability relationships
        cap_relationships = []
        for attr in rel_attrs_full:
            metaName = attr.get('metaName', '')
            targets = attr.get('targets', [])

            cap_targets = [t for t in targets if 'CAPAB' in t.get('metaName', '').upper()]
            if cap_targets:
                cap_relationships.append({
                    'relation': metaName,
                    'count': len(cap_targets),
                    'sample': cap_targets[0].get('name') if cap_targets else None
                })

        print(f"  Capability relationships found: {len(cap_relationships)}")
        for rel in cap_relationships[:5]:
            print(f"    - {rel['relation']}: {rel['count']} targets")
            print(f"      Sample: {rel['sample']}")
    else:
        print(f"  ✗ ERROR: get_entity() returned None for app_id: {app_id}")

    # Hypothesis 4: Catalog all relationship types (using search results)
    print("\n[H4] Cataloging relationship types across sample...")
    rel_types = set()
    cap_rel_types = set()

    print(f"  Analyzing {min(20, len(applications))} applications from search results...")
    for i, app in enumerate(applications[:20]):  # Sample 20 apps
        for attr in app.get('attributes', []):
            if attr.get('attrType') == 'RELATION':
                metaName = attr.get('metaName', '')
                rel_types.add(metaName)

                # Check if this relation has capability targets
                targets = attr.get('targets', [])
                has_cap = any('CAPAB' in t.get('metaName', '').upper() for t in targets)
                if has_cap:
                    cap_rel_types.add(metaName)

    print(f"  Total relationship types found: {len(rel_types)}")
    print(f"  Relationship types with capability targets: {len(cap_rel_types)}")
    print("\n  Capability-related relationships:")
    for rel_type in sorted(cap_rel_types):
        print(f"    - {rel_type}")

    # Expected relationship types
    expected_aggregated = {
        'RC_CUST_AGGREGATED_DOMAIN_AREAS',
        'RC_CUST_AGGREGATED_DOMAINS',
        'RC_CUST_AGGREGATED_SUB_DOMAINS',
        'RC_CUST_AGGREGATED_CAPABILITIES',
        'RC_CUST_AGGREGATED_SUB_CAPABILITIES'
    }

    missing = expected_aggregated - cap_rel_types
    unexpected = cap_rel_types - expected_aggregated - {'RC_REALIZATION'}

    if missing:
        print(f"\n  Expected but NOT found: {missing}")
    if unexpected:
        print(f"\n  Found but NOT expected: {unexpected}")

    print("\n" + "=" * 60)
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    diagnose()
