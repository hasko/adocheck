#!/usr/bin/env python3
"""
Consistency checks for ArchiMate data in ADOit.
"""

import os
import sys
import json
import sqlite3
import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

from adoit_api import AdoitApi

# Create data directory if it doesn't exist
Path("data").mkdir(exist_ok=True)

# SQLite database path
DB_PATH = "data/adoit_cache.db"

class ConsistencyChecker:
    """Runs consistency checks against ArchiMate data."""
    
    def __init__(self, api: AdoitApi):
        """
        Initialize the consistency checker.
        
        Args:
            api: Instance of AdoitApi to use for retrieving data
        """
        self.api = api
    
    def check_dangling_relationships(self) -> List[Dict[str, Any]]:
        """
        Check for relationships where source or target entity doesn't exist.
        
        Returns:
            List of problematic relationships
        """
        dangling = []
        
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Find relationships where source or target doesn't exist in entities table
            query = """
                SELECT r.* FROM relationships r
                LEFT JOIN entities e1 ON r.source_id = e1.id
                LEFT JOIN entities e2 ON r.target_id = e2.id
                WHERE e1.id IS NULL OR e2.id IS NULL
            """
            
            cursor.execute(query)
            for row in cursor.fetchall():
                relationship_data = json.loads(row["data"])
                dangling.append(relationship_data)
        
        return dangling
    
    def check_orphaned_entities(self) -> List[Dict[str, Any]]:
        """
        Check for entities that have no relationships.
        
        Returns:
            List of orphaned entities
        """
        orphaned = []
        
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Find entities with no relationships
            query = """
                SELECT e.* FROM entities e
                LEFT JOIN relationships r1 ON e.id = r1.source_id
                LEFT JOIN relationships r2 ON e.id = r2.target_id
                WHERE r1.id IS NULL AND r2.id IS NULL
            """
            
            cursor.execute(query)
            for row in cursor.fetchall():
                entity_data = json.loads(row["data"])
                orphaned.append(entity_data)
        
        return orphaned
    
    def check_missing_required_attributes(self) -> List[Dict[str, Any]]:
        """
        Check for entities missing required attributes based on their type.
        
        Returns:
            List of entities with missing attributes
        """
        # Implementation will depend on what attributes are required for each entity type
        pass
    
    def run_all_checks(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Run all consistency checks and return the results.
        
        Returns:
            Dictionary with check names as keys and lists of problematic items as values
        """
        results = {
            "dangling_relationships": self.check_dangling_relationships(),
            "orphaned_entities": self.check_orphaned_entities(),
            # Add more checks as implemented
        }
        
        return results

if __name__ == "__main__":
    api = AdoitApi()
    checker = ConsistencyChecker(api)
    
    print("Running consistency checks...")
    results = checker.run_all_checks()
    
    print("\nResults:")
    for check_name, issues in results.items():
        if issues:
            print(f"\n{check_name.replace('_', ' ').title()}: {len(issues)} issues found")
            for issue in issues[:5]:  # Show first 5 issues
                print(f"  - {issue.get('name', 'Unnamed')} ({issue.get('id', 'No ID')})")
            
            if len(issues) > 5:
                print(f"  ... and {len(issues) - 5} more")
        else:
            print(f"\n{check_name.replace('_', ' ').title()}: No issues found")