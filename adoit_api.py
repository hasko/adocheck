#!/usr/bin/env python3
"""
ADOit API client that retrieves ArchiMate data and stores it in a SQLite database.
"""

import os
import sys
import json
import sqlite3
import requests
import datetime
import uuid
import hmac
import hashlib
import time
import locale
from base64 import b64encode
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from pathlib import Path
from yarl import URL

# Configure logging
import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
load_dotenv()

# ADOit API credentials and settings
ADOIT_URL = os.getenv("ADOIT_URL", "https://allianz-pp103380.boc-cloud.com")
ADOIT_API_ID = os.getenv("ADOIT_API_ID")
ADOIT_API_SECRET = os.getenv("ADOIT_API_SECRET")
ADOIT_REPO_ID = os.getenv("ADOIT_REPO_ID")

# Configure proxy settings if provided
HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")
NO_PROXY = os.getenv("NO_PROXY")

if HTTP_PROXY:
    os.environ["HTTP_PROXY"] = HTTP_PROXY
if HTTPS_PROXY:
    os.environ["HTTPS_PROXY"] = HTTPS_PROXY
if NO_PROXY:
    os.environ["NO_PROXY"] = NO_PROXY

# Ensure required environment variables are set
if not all([ADOIT_URL, ADOIT_API_ID, ADOIT_API_SECRET]):
    print("Error: Missing required environment variables. Please create a .env file with ADOIT_URL, ADOIT_API_ID, and ADOIT_API_SECRET.")
    sys.exit(1)

# Ensure ADOIT_URL doesn't end with a slash
ADOIT_URL = ADOIT_URL.rstrip("/")

# Create data directory if it doesn't exist
Path("data").mkdir(exist_ok=True)

# SQLite database path
DB_PATH = "data/adoit_cache.db"

# Set locale for token generation
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

# Check for locale sort bug
def has_sort_bug() -> bool:
    my_test = ['1637695007170', '{"filters":[{"className":"C_WORK_PACKAGE"}]}']
    my_s = sorted(my_test, key=locale.strxfrm)
    return my_s[0] != '{"filters":[{"className":"C_WORK_PACKAGE"}]}'

HAS_SORT_BUG = has_sort_bug()

def get_token(headers, q={}, secret=ADOIT_API_SECRET):
    """Generate authentication token for ADOit API."""
    l = []
    for (k, v) in headers.items():
        l.append(k)
        l.append(v)
    for (k, v) in q.items():
        l.append(k)
        if k == "query":
            # Always treat the query parameter as a single string
            l.append(v)
        else:
            # For other parameters, process each item in the list
            for p in v:
                l.append(p)
    l.append(secret)

    logger.debug(f"Token generation - query params: {q}")
    logger.debug(f"Token generation - items before sort: {l[:15]}")

    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    tokens_sorted = sorted(l, key=locale.strxfrm)
    logger.debug(f"Token generation - items after sort (first 10): {tokens_sorted[:10]}")

    # Part 2 of ugly hack to get around collation bug in certain Linux environments
    if HAS_SORT_BUG and "query" in q:
        logger.debug(f"Applying HAS_SORT_BUG hack - moving query to position 0")
        logger.debug(f"Query value: {q['query'][:80]}")
        # Remove the query value from its current position
        if q["query"] in tokens_sorted:
            tokens_sorted.remove(q["query"])
        # Insert it at position 0
        tokens_sorted.insert(0, q["query"])
        logger.debug(f"After hack - first 5 items: {tokens_sorted[:5]}")

    s = ''.join(tokens_sorted).encode('UTF-8')
    logger.debug(f"Final string for HMAC (first 200 chars): {s[:200]}")
    d = hmac.digest(bytes(secret, "UTF-8"), s, hashlib.sha512)
    return b64encode(d).decode('UTF-8')

def get_headers(q={}):
    """Generate headers for ADOit API request."""
    headers = {
        "x-axw-rest-identifier": ADOIT_API_ID,
        "x-axw-rest-guid": str(uuid.uuid4()),
        "x-axw-rest-timestamp": str(int(time.time() * 1000))
    }
    token = get_token(headers, q=q)
    headers["x-axw-rest-token"] = token
    return headers

def adoit_request(path, additional_headers={}, q={}):
    """Make a request to the ADOit API."""
    headers = get_headers(q)
    url = URL(f"{ADOIT_URL}/rest") / path % q
    logger.info(f"Requesting from ADOIT: {url}")
    logger.debug(f"Query params: {q}")
    logger.debug(f"Auth headers: {list(headers.keys())}")
    for (k, v) in additional_headers.items():
        headers[k] = v
    response = requests.get(url, headers=headers)
    if response.status_code == 401:
        logger.error(f"Authentication failed. Response: {response.text[:200]}")
    return response

def adoit_request_paginated(path, additional_headers={}, q={}, page_size=200):
    """
    Make a paginated request to the ADOit API, automatically handling pagination.
    
    This function will make multiple requests to retrieve all results, combining
    the "items" from each response into a single list.
    
    Args:
        path: The API path to request
        additional_headers: Additional headers to include in the request
        q: Query parameters
        page_size: Number of items to retrieve per request (default: 200)
        
    Returns:
        A response object with combined items from all pages
    """
    # Make a copy of the query parameters to avoid modifying the original
    query_params = q.copy()
    
    # Set initial range (0 to page_size)
    if "range-end" in query_params:
        # Store the original range-end value to check if we need pagination
        original_range_end = query_params["range-end"][0]
        # If range-end is -1 or a large number, we'll paginate
        paginate = original_range_end == "-1" or int(original_range_end) >= page_size
        if not paginate:
            # If range-end is smaller than page_size, just use the original request
            logger.debug(f"Range-end {original_range_end} is smaller than page_size {page_size}, not paginating")
            return adoit_request(path, additional_headers, q)
    else:
        # If range-end is not specified, we'll paginate
        paginate = True
    
    # Set initial range - range-end is exclusive, so we use page_size (not page_size-1)
    query_params["range-end"] = [str(page_size)]
    
    # Make the first request
    response = adoit_request(path, additional_headers, query_params)
    
    # If the request failed or we don't need to paginate, return the response as is
    if response.status_code != 200:
        logger.error(f"Initial request failed with status code {response.status_code}")
        return response
    
    # Parse the response
    response_data = response.json()
    
    # Check if we need to paginate
    hits_total = response_data.get("hitsTotal", 0)
    range_end = response_data.get("rangeEnd", 0)
    
    # If we've retrieved all items or there are no more items, return the response
    if range_end >= hits_total - 1 or hits_total <= page_size:
        logger.debug(f"No need to paginate: retrieved {range_end + 1} of {hits_total} items")
        return response
    
    # Store the items from the first response
    all_items = response_data.get("items", [])
    logger.info(f"Retrieved {len(all_items)} items (page 1), total: {hits_total}")
    
    # Calculate the number of additional requests needed
    remaining_items = hits_total - len(all_items)
    additional_requests = (remaining_items + page_size - 1) // page_size
    
    # Make additional requests to retrieve all items
    for i in range(additional_requests):
        # Calculate the range for this request
        range_start = (i + 1) * page_size
        range_end = min(range_start + page_size, hits_total)  # range-end is exclusive
        
        # Update the query parameters
        query_params_page = query_params.copy()
        query_params_page["range-start"] = [str(range_start)]
        query_params_page["range-end"] = [str(range_end)]
        
        logger.info(f"Retrieving items {range_start} to {range_end} (page {i + 2})")
        
        # Make the request
        page_response = adoit_request(path, additional_headers, query_params_page)
        
        # If the request failed, log an error but continue with the items we have
        if page_response.status_code != 200:
            logger.error(f"Request for page {i + 2} failed with status code {page_response.status_code}")
            continue
        
        # Parse the response and add the items to our list
        page_data = page_response.json()
        page_items = page_data.get("items", [])
        all_items.extend(page_items)
        
        logger.info(f"Retrieved {len(page_items)} items (page {i + 2}), total so far: {len(all_items)}")
    
    # Create a new response object with the combined items
    # We'll use the first response as a base and replace the items
    response_data["items"] = all_items
    response_data["rangeEnd"] = hits_total  # range-end is exclusive
    
    # Create a new response object
    combined_response = requests.Response()
    combined_response.status_code = 200
    combined_response._content = json.dumps(response_data).encode('utf-8')
    combined_response.headers = response.headers
    
    logger.info(f"Retrieved all {len(all_items)} items across {additional_requests + 1} pages")
    
    return combined_response

class AdoitApi:
    """Client for the ADOit REST API with caching capabilities."""
    
    def __init__(self):
        """Initialize the ADOit API client and set up the SQLite database."""
        self.setup_database()
    
    def setup_database(self):
        """Create SQLite database and tables if they don't exist."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Create entities table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT,
                    data TEXT NOT NULL,
                    retrieved_at TIMESTAMP NOT NULL
                )
            ''')
            
            # Create relationships table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    retrieved_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES entities(id),
                    FOREIGN KEY (target_id) REFERENCES entities(id)
                )
            ''')
            
            conn.commit()

        # Run schema migrations
        self._migrate_cache_schema_v2()

    def _migrate_cache_schema_v2(self):
        """Add entity_modified_at column for smart caching."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            # Check if column exists
            cursor.execute("PRAGMA table_info(entities)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'entity_modified_at' not in columns:
                cursor.execute("ALTER TABLE entities ADD COLUMN entity_modified_at DOUBLE")
                logger.info("Migrated cache schema to v2 (added entity_modified_at)")
            conn.commit()

    def _extract_entity_modified_at(self, entity_data: Dict[str, Any]) -> Optional[float]:
        """
        Extract DATE_OF_LAST_CHANGE from entity attributes.

        Returns:
            Modification timestamp (milliseconds) or None if not found
        """
        for attr in entity_data.get('attributes', []):
            if attr.get('metaName') == 'DATE_OF_LAST_CHANGE':
                return attr.get('value')
        return None

    def get_repos(self) -> List[Dict[str, Any]]:
        """Get list of available repositories from ADOit."""
        response = adoit_request("2.0/repos")
        response.raise_for_status()
        return response.json()

    def get_metamodel(self) -> Dict[str, Any]:
        """
        Get the complete metamodel information.

        Returns:
            Metamodel data including classes, relations, etc.
        """
        response = adoit_request("2.0/metamodel")
        response.raise_for_status()
        return response.json()

    def get_metamodel_classes(self) -> List[Dict[str, Any]]:
        """
        Get all metamodel classes.

        Returns:
            List of metamodel class definitions
        """
        response = adoit_request("2.0/metamodel/classes")
        response.raise_for_status()
        return response.json()

    def _fetch_entity_from_api(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch entity from API without caching."""
        response = adoit_request(f"2.0/entities/{entity_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _cache_entity(self, entity_data: Dict[str, Any]) -> Dict[str, Any]:
        """Store entity in cache and return it."""
        entity_id = entity_data.get('id', '').strip('{}')
        entity_type = entity_data.get('type', 'unknown')
        entity_name = entity_data.get('name', '')
        entity_modified_at = self._extract_entity_modified_at(entity_data)

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO entities
                   (id, type, name, data, retrieved_at, entity_modified_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entity_id,
                    entity_type,
                    entity_name,
                    json.dumps(entity_data),
                    datetime.datetime.now().isoformat(),
                    entity_modified_at
                )
            )
            conn.commit()

        return entity_data

    def _fetch_and_cache_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch entity from API and cache it."""
        entity_data = self._fetch_entity_from_api(entity_id)
        if entity_data:
            return self._cache_entity(entity_data)
        return None

    def get_entity(self, entity_id: str, force_refresh: bool = False, cache_ttl_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Get entity with intelligent caching.

        Args:
            entity_id: Entity ID to retrieve
            force_refresh: If True, bypass cache entirely (legacy behavior)
            cache_ttl_seconds: Time-to-live in seconds. If cache is older than this,
                              check for modifications. If None, always check modifications.
                              Default: 172800 (48 hours)

        Returns:
            Entity data or None if not found
        """
        if cache_ttl_seconds is None:
            cache_ttl_seconds = 172800  # 48 hours default

        # Force refresh bypasses all caching logic
        if force_refresh:
            return self._fetch_and_cache_entity(entity_id)

        # Try to get from cache
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM entities WHERE id = ?",
                (entity_id,)
            )
            row = cursor.fetchone()

            if not row:
                # Not in cache, fetch fresh
                return self._fetch_and_cache_entity(entity_id)

            # Check cache age
            retrieved_at = datetime.datetime.fromisoformat(row["retrieved_at"])
            cache_age_seconds = (datetime.datetime.now() - retrieved_at).total_seconds()

            if cache_age_seconds < cache_ttl_seconds:
                # Cache is fresh, trust it
                logger.debug(f"Cache hit (fresh): {entity_id} (age: {cache_age_seconds:.0f}s)")
                return json.loads(row["data"])

            # Cache is stale, verify entity hasn't changed
            logger.debug(f"Cache stale, checking modifications: {entity_id} (age: {cache_age_seconds:.0f}s)")
            fresh_entity = self._fetch_entity_from_api(entity_id)

            if not fresh_entity:
                # Entity deleted or not found, remove from cache
                cursor.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
                conn.commit()
                return None

            # Compare modification timestamps
            cached_modified_at = row["entity_modified_at"]
            fresh_modified_at = self._extract_entity_modified_at(fresh_entity)

            if fresh_modified_at and cached_modified_at and fresh_modified_at <= cached_modified_at:
                # Entity unchanged, just update retrieved_at
                logger.debug(f"Entity unchanged, touching cache: {entity_id}")
                cursor.execute(
                    "UPDATE entities SET retrieved_at = ? WHERE id = ?",
                    (datetime.datetime.now().isoformat(), entity_id)
                )
                conn.commit()
                return json.loads(row["data"])

            # Entity modified or missing timestamp, update cache
            logger.debug(f"Entity modified, updating cache: {entity_id}")
            return self._cache_entity(fresh_entity)
    
    def get_entities_by_type(self, entity_type: str, repo_id: str = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get entities of a specific type from cache or fetch from ADOit API.

        Args:
            entity_type: The type of entities to retrieve
            repo_id: The repository ID to search in (defaults to ADOIT_REPO_ID)
            force_refresh: Force refresh from API regardless of cache

        Returns:
            List of entity data dictionaries
        """
        if repo_id is None:
            repo_id = ADOIT_REPO_ID

        # Strip curly braces from repo_id if present (API returns IDs with braces but doesn't accept them)
        repo_id = repo_id.strip('{}') if repo_id else repo_id
        
        if not force_refresh:
            # Try to get from cache first
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM entities WHERE type = ?", (entity_type,))
                rows = cursor.fetchall()
                
                if rows:
                    # Entities found in cache
                    return [json.loads(row["data"]) for row in rows]
        
        # Prepare query parameters - className should be in filters array
        # Using quoted JSON keys to match the sorting hack expectations
        query = f'{{"filters":[{{"className":["{entity_type}"]}}]}}'
        q = {
            "query": query
        }

        # Fetch from API with pagination using correct endpoint
        response = adoit_request_paginated(f"2.0/repos/{repo_id}/search", q=q)
        response.raise_for_status()
        
        result_data = response.json()
        entities = result_data.get("items", [])
        
        # Store in cache
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for entity in entities:
                cursor.execute(
                    "INSERT OR REPLACE INTO entities (id, type, name, data, retrieved_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        entity.get("id", ""),
                        entity_type,
                        entity.get("name", ""),
                        json.dumps(entity),
                        datetime.datetime.now().isoformat()
                    )
                )
            conn.commit()
        
        return entities
    
    def get_relationships(self, entity_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get relationships for an entity from cache or fetch from API if not available or outdated.
        
        Args:
            entity_id: The ID of the entity to retrieve relationships for
            force_refresh: Force refresh from API regardless of cache
            
        Returns:
            List of relationship data dictionaries
        """
        if not force_refresh:
            # Try to get from cache first
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM relationships WHERE source_id = ? OR target_id = ?", 
                    (entity_id, entity_id)
                )
                rows = cursor.fetchall()
                
                if rows:
                    # Relationships found in cache
                    return [json.loads(row["data"]) for row in rows]
        
        # Fetch from API
        response = adoit_request(f"2.0/entities/{entity_id}/relations")
        if response.status_code == 404:
            return []
            
        response.raise_for_status()
        relations_data = response.json()
        relations = relations_data.get("relations", [])
        
        # Store in cache
        timestamp = datetime.datetime.now().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for relation in relations:
                relation_id = relation.get("id", "")
                source_id = relation.get("fromId", "")
                target_id = relation.get("toId", "")
                relation_type = relation.get("relationType", "unknown")
                
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO relationships 
                    (id, source_id, target_id, type, data, retrieved_at) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation_id,
                        source_id,
                        target_id,
                        relation_type,
                        json.dumps(relation),
                        timestamp
                    )
                )
            conn.commit()
        
        return relations

    def get_entities_by_filters(self, filters: List[Dict[str, Any]], repo_id: str = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get entities matching complex filter criteria from cache or fetch from ADOit API.

        Args:
            filters: List of filter dictionaries. Each filter can contain:
                     - className: List of class names to filter by
                     - attrName: Attribute name to filter by
                     - value: Value to match against
                     - op: Operator (OP_EQ, OP_LIKE, OP_NEMPTY, etc.)
            repo_id: The repository ID to search in (defaults to ADOIT_REPO_ID)
            force_refresh: Force refresh from API regardless of cache

        Returns:
            List of entity data dictionaries matching the filters

        Example:
            filters = [
                {"className": ["C_APPLICATION"]},
                {"attrName": "A_SPECIALISATION", "value": "Bus. App.", "op": "OP_EQ"}
            ]
            entities = api.get_entities_by_filters(filters)
        """
        if repo_id is None:
            repo_id = ADOIT_REPO_ID

        # Strip curly braces from repo_id if present
        repo_id = repo_id.strip('{}') if repo_id else repo_id

        # Create a cache key based on the filters
        import hashlib
        filter_key = hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()

        if not force_refresh:
            # Try to get from cache first
            # Note: For now we skip caching of filtered queries to keep it simple
            # Could be enhanced later with a dedicated filtered_queries table
            pass

        # Build query with filters
        query = json.dumps({"filters": filters})
        q = {
            "query": query
        }

        # Fetch from API with pagination using correct endpoint
        response = adoit_request_paginated(f"2.0/repos/{repo_id}/search", q=q)
        response.raise_for_status()

        result_data = response.json()
        entities = result_data.get("items", [])

        # Note: We don't cache filtered results in the entities table since they may
        # not represent the full set for a given type. Could add separate caching later.

        return entities

    def invalidate_cache(self, older_than: Optional[datetime.datetime] = None):
        """
        Invalidate cache entries.

        Args:
            older_than: Invalidate entries older than this datetime
        """
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            if older_than:
                # Convert datetime to ISO format string
                older_than_str = older_than.isoformat()
                cursor.execute("DELETE FROM entities WHERE retrieved_at < ?", (older_than_str,))
                cursor.execute("DELETE FROM relationships WHERE retrieved_at < ?", (older_than_str,))
            else:
                # Invalidate all cache
                cursor.execute("DELETE FROM entities")
                cursor.execute("DELETE FROM relationships")

            conn.commit()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            stats = {}

            # Total entities
            cursor.execute("SELECT COUNT(*) FROM entities")
            stats['total_entities'] = cursor.fetchone()[0]

            # Age distribution
            cursor.execute("""
                SELECT
                    COUNT(*) as count,
                    AVG((julianday('now') - julianday(retrieved_at)) * 86400) as avg_age_seconds
                FROM entities
            """)
            row = cursor.fetchone()
            stats['avg_cache_age_seconds'] = row[1] if row[1] else 0

            # Entities with modification timestamp
            cursor.execute("SELECT COUNT(*) FROM entities WHERE entity_modified_at IS NOT NULL")
            stats['entities_with_mod_timestamp'] = cursor.fetchone()[0]

            return stats

if __name__ == "__main__":
    api = AdoitApi()
    
    try:
        # Test the API connection by getting repos
        repos_response = api.get_repos()
        print(f"Raw API response type: {type(repos_response)}")
        print(f"Raw API response: {json.dumps(repos_response, indent=2)[:500]}")

        # Check if response is dict with 'repos' key
        if isinstance(repos_response, dict) and 'repos' in repos_response:
            repos = repos_response['repos']
        else:
            repos = repos_response

        print(f"\nFound {len(repos)} repositories:")
        for repo in repos:
            # Handle both string and dict responses
            if isinstance(repo, dict):
                print(f"  - {repo.get('name', 'Unnamed')} ({repo.get('id', 'No ID')})")
            else:
                print(f"  - {repo}")
        
        # Explore available endpoints if ADOIT_REPO_ID is set
        if ADOIT_REPO_ID:
            print(f"\nExploring repository: {ADOIT_REPO_ID}")

            # Try the modelgroups endpoint from rest_links
            repo_id_clean = ADOIT_REPO_ID.strip('{}')
            print(f"\nTrying modelgroups endpoint...")
            try:
                modelgroups_response = adoit_request(f"2.0/repos/{repo_id_clean}/modelgroups/root")
                modelgroups_response.raise_for_status()
                modelgroups_data = modelgroups_response.json()
                print(f"Modelgroups response (first 1000 chars): {json.dumps(modelgroups_data, indent=2)[:1000]}")
            except Exception as e:
                print(f"Modelgroups error: {e}")

            print(f"\nRetrieving full metamodel...")
            try:
                metamodel = api.get_metamodel()
                print(f"Raw metamodel response type: {type(metamodel)}")
                print(f"Raw metamodel keys: {list(metamodel.keys()) if isinstance(metamodel, dict) else 'not a dict'}")

                # Show a sample of the classes from the full metamodel
                if isinstance(metamodel, dict) and 'classes' in metamodel:
                    sample_classes = metamodel['classes'][:5] if isinstance(metamodel['classes'], list) else []
                    print(f"Sample class from metamodel (first 500 chars): {json.dumps(sample_classes, indent=2)[:500]}")

                print(f"\nRetrieving metamodel classes list...")
                classes = api.get_metamodel_classes()
                print(f"Metamodel classes response type: {type(classes)}")
            except Exception as e:
                print(f"Metamodel error: {e}")
                classes = None

            # Try to extract class names
            class_list = []
            if classes:
                if isinstance(classes, dict) and 'classes' in classes:
                    class_list = classes['classes']
                elif isinstance(classes, list):
                    class_list = classes

            print(f"\nFound {len(class_list)} metamodel classes")
            if class_list:
                print("Fetching details for first 10 classes...")
                class_names = []
                for i, cls in enumerate(class_list[:10]):
                    if isinstance(cls, dict):
                        class_id = cls.get('id', '').strip('{}')
                        meta_name = cls.get('metaName', 'Unknown')
                        if class_id:
                            try:
                                # Fetch full class details
                                class_response = adoit_request(f"2.0/metamodel/classes/{class_id}")
                                if class_response.status_code == 200:
                                    class_data = class_response.json()
                                    display_names = class_data.get('displayNames', [])
                                    if display_names and len(display_names) > 0:
                                        class_name = display_names[0].get('value', meta_name)
                                        class_names.append(class_name)
                                        print(f"  - {class_name} (metaName: {meta_name})")
                                    else:
                                        class_names.append(meta_name)
                                        print(f"  - {meta_name}")
                            except Exception as e:
                                print(f"  - {meta_name} (error fetching details: {e})")
                                class_names.append(meta_name)

            # Try searching with the actual class names from the metamodel
            print(f"\nTrying to search for entities with actual metamodel class names...")

            # Use the first few class names we found
            test_classes = class_names[:5] if 'class_names' in locals() and class_names else []
            if not test_classes and class_list:
                # Fallback to metaName from class_list
                test_classes = [cls.get('metaName', '') for cls in class_list[:5] if isinstance(cls, dict)]

            for class_name in test_classes:
                try:
                    print(f"\nTrying class: {class_name}")
                    entities = api.get_entities_by_type(class_name, ADOIT_REPO_ID)
                    print(f"✓ Found {len(entities)} {class_name} entities")

                    if entities and len(entities) > 0:
                        print(f"First 3 {class_name} entities:")
                        for entity in entities[:3]:
                            print(f"  - {entity.get('name', 'Unnamed')} ({entity.get('id', 'No ID')})")

                        # Get relationships for the first entity if available
                        if len(entities) > 0:
                            first_entity_id = entities[0].get('id')
                            first_entity_name = entities[0].get('name', 'Unnamed')
                            print(f"\nGetting relationships for {first_entity_name} ({first_entity_id})")
                            relations = api.get_relationships(first_entity_id)
                            print(f"Found {len(relations)} relationships")

                        break  # Found a valid class, stop searching
                except Exception as e:
                    print(f"✗ {class_name} failed: {e}")
    
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to ADOit API: {e}")
        sys.exit(1)