# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ADOcheck is a collection of Python scripts designed to access the ADOit API and run consistency checks against ArchiMate data stored in ADOit. The project uses a SQLite database to cache data from ADOit to avoid repeated API calls and includes functionality to selectively invalidate cached data based on timestamps.

## Environment Setup

1. Dependencies are managed with UV:
   ```bash
   ./setup.sh  # Sets up environment and creates .env from .env.example
   ```

2. Alternatively, manually install dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```

3. Configure API credentials:
   - Copy `.env.example` to `.env`
   - Edit `.env` with ADOit API credentials:
     - `ADOIT_URL`: Your ADOit instance URL
     - `ADOIT_API_ID`: Your API identifier
     - `ADOIT_API_SECRET`: Your API secret key
     - `ADOIT_REPO_ID`: The repository ID to query
   - Optionally configure proxy settings if needed

## Architecture

The codebase is organized around these key components:

1. **ADOit API Client** (`adoit_api.py`):
   - Provides authentication and communication with the ADOit REST API
   - Implements intelligent TTL-based caching with modification timestamp checking
   - Handles data retrieval and cache invalidation
   - Database schema includes:
     - `entities` table: Stores ArchiMate entities with retrieval and modification timestamps
     - `relationships` table: Stores relationships between entities with timestamp information

   **Smart Caching**:
   - Configurable TTL (default: 48 hours, set via `ADOIT_CACHE_TTL` in `.env`)
   - Returns cached data immediately if within TTL period
   - For older cache entries, fetches from API and compares `DATE_OF_LAST_CHANGE` timestamps
   - Only updates cache if entity actually changed on server (reduces unnecessary writes)
   - Provides ~500x speedup for repeated report runs on stable data
   - Use `force_refresh=True` to bypass cache completely
   - Use `get_cache_stats()` to monitor cache effectiveness

2. **Consistency Checker** (`consistency_check.py`):
   - Implements various checks against the ArchiMate data
   - Uses the SQLite database to analyze data consistency
   - Current checks include:
     - Dangling relationships (where source or target entity doesn't exist)
     - Orphaned entities (entities with no relationships)
     - (More checks to be implemented)

3. **Report Generator** (`report_generator.py`):
   - Generates filtered reports of ADOit entities
   - Automatic metamodel introspection for attribute discovery
   - Supports complex multi-attribute filtering
   - JSON output with metadata and statistics

4. **Capability Mapper** (`capability_mapper.py`):
   - Maps business applications to top-level capabilities through relationship traversal
   - Uses BFS (Breadth-First Search) for guaranteed shortest paths
   - Parallel relationship fetching for performance (configurable workers)
   - Handles large-scale graph analysis (3000+ applications, 5000+ entities)
   - JSON output with detailed path information and statistics

   **Architecture**:
   - Graph stored as adjacency list: `{entity_id: [(target_id, rel_type), ...]}`
   - BFS guarantees shortest path, O(V + E) time complexity
   - Relationship type filtering based on ArchiMate semantics
   - In-memory entity cache to minimize API calls

   **Key Features**:
   - Automatic capability discovery (name-based API filtering)
   - Manual capability ID specification fallback
   - Relationship type whitelist (structural: composition, aggregation, realization; dependency: serving, access, influence)
   - Parallel workers (default: 10) for concurrent relationship fetching
   - Comprehensive error handling and progress logging
   - Dry-run mode for discovery without mapping

   **Usage Notes**:
   - Applications: Use `C_APPLICATION_COMPONENT` entities (not `C_APPLICATION`)
   - Specialisation attribute: `A_APPLICATION_COMPONENT_SPEC` (values like "App. Comp.")
   - Top-level capabilities: May be REPOSITORY_OBJECTS accessible via `/repos/{repo_id}/objects/{id}`
     - These have `metaName: C_CAPABILITY` but may not work with `/entities/{id}` endpoint
     - Use `--target-capability-ids` to manually specify if auto-discovery fails
   - Use `--use-cache` for faster repeated runs (but data may be stale)
   - Adjust `--parallel-workers` based on API rate limits and system resources

5. **OE Capability Report** (`oe_capability_report_hybrid.py`):
   - Analyzes business applications and their capability mappings using embedded relationship data
   - Extracts both organic (RC_REALIZATION) and aggregated (RC_CUST_AGGREGATED_*) capability links
   - Filters capabilities by GDM level (L1, L2, L3)
   - Tracks new vs deprecated "(do not use)" model statistics
   - Multiple output formats: JSON, HTML, and Markdown

   **Key Features**:
   - Uses search results with embedded RELATION attributes (no separate entity fetches)
   - GDM level extraction from capability names (e.g., "3.2 Hr" → L3, "2.2.1 IT Operations" → L2)
   - Capability caching to avoid redundant parsing
   - Identifies unmapped applications and applications without OEs
   - Tracks organic vs aggregated capability relationships
   - Priority-based organization: Applications without OE shown first, then sorted by mapping status

   **Output Formats**:
   - **JSON** (default): Complete structured data with metadata and statistics
   - **HTML** (`--html` flag): Interactive web report with collapsible sections, color-coded categories, and Chart.js visualizations
     - OE Assignment Distribution (doughnut chart)
     - Mapping Status Distribution (bar chart)
     - Model Migration Progress (doughnut chart)
     - Applications by OE (stacked horizontal bar chart)
   - **Markdown** (`--markdown` or `--md` flag): Clean markdown tables with emoji indicators

   **Priority Sorting**:
   Within each OE section, applications are sorted by priority (descending):
   - 🔴 Unmapped (no capabilities)
   - 🟠 Old Model Only (all deprecated)
   - 🟡 Mixed (Old + New) (partially migrated)
   - 🟢 New Model Only (fully migrated)

   **Usage**:
   ```bash
   # Generate all three formats
   python oe_capability_report_hybrid.py --html --markdown

   # Use cached data for faster runs
   python oe_capability_report_hybrid.py --use-cache --html --markdown

   # Custom output location
   python oe_capability_report_hybrid.py -o custom/path/report.json --html
   ```

   **Important Discovery**:
   - Capabilities are repository objects (`artefactType: "REPOSITORY_OBJECT"`), not entities
   - They cannot be fetched via `/entities/{id}` endpoint (returns 404)
   - Level must be parsed from capability name pattern `^\d+\.` where first digit = level
   - This avoids unnecessary API calls and improves performance

6. **Database Structure**:
   - Located in `data/adoit_cache.db` (auto-created)
   - Entities table: `id`, `type`, `name`, `data` (JSON), `retrieved_at`, `entity_modified_at`
   - Relationships table: `id`, `source_id`, `target_id`, `type`, `data` (JSON), `retrieved_at`
   - Smart caching with TTL and modification timestamp checking (see `ADOIT_CACHE_TTL` in `.env`)

## Common Commands

Run API client to test connection:
```bash
./adoit_api.py
```

Run consistency checks:
```bash
./consistency_check.py
```

Clear or invalidate the cache:
```python
from adoit_api import AdoitApi
import datetime

api = AdoitApi()

# Invalidate all cache
api.invalidate_cache()

# Invalidate cache older than 7 days
api.invalidate_cache(older_than=datetime.datetime.now() - datetime.timedelta(days=7))
```

## Development Notes

- Always use the `.env` file for credentials, never hardcode them
- The data directory is created automatically when needed
- The project is designed with a flat structure (scripts in root directory)
- When implementing new consistency checks, add them to the `run_all_checks` method in `ConsistencyChecker`
- ArchiMate data is a graph structure, so relationship integrity is important to validate

### Azure/Linux Locale Sorting Bug

The ADOit API authentication requires HMAC-SHA512 token generation with parameters sorted using `en_US` locale. However, on some Unix systems (particularly Azure/Linux), the locale sorting is broken for special characters like `{` and `"`, causing authentication failures.

**Solution**: The code includes a workaround in `get_token()` (adoit_api.py:75) that:
1. Detects if the sorting bug exists on the current system
2. For requests with a `query` parameter, removes the query value from its incorrectly sorted position
3. Manually inserts it at position 0 to match the expected server-side HMAC calculation

This fix is critical for the `/repos/{repo_id}/search` endpoint to work correctly.

## ADOit API Reference

### Authentication

The ADOit REST API uses HMAC-SHA512 token-based authentication. Each request must include these headers:

- `x-axw-rest-identifier`: Public identifier of the API key
- `x-axw-rest-guid`: Unique GUID for the request
- `x-axw-rest-timestamp`: UTC timestamp in milliseconds
- `x-axw-rest-token`: HMAC-SHA512 hash (Base64 encoded)

**Token Generation Algorithm**:
1. Collect all header names and values (identifier, guid, timestamp)
2. Collect all query parameter names and values
3. Add the secret key to the collection
4. Sort using `en_US` locale (with workaround for Azure bug)
5. Concatenate and convert to UTF-8 bytes
6. Create HMAC-SHA512 hash using secret key
7. Base64 encode the result

### Key API Endpoints

**Repositories**:
- `GET /rest/2.0/repos` - List all repositories
  - Returns: `{repos: [{id, name, rest_links}, ...]}`
  - Note: Repository IDs include curly braces `{uuid}` but must be used without braces in URLs

**Metamodel**:
- `GET /rest/2.0/metamodel` - Get metamodel overview
- `GET /rest/2.0/metamodel/classes` - List all classes
  - Returns: `{classes: [{id, metaName, visible, abstract}, ...]}`
  - `metaName` is the internal class name (e.g., `C_APPLICATION`)
- `GET /rest/2.0/metamodel/classes/{class_id}` - Get class details including displayNames

**Search**:
- `GET /rest/2.0/repos/{repo_id}/search?query={...}&range-start=0&range-end=200`
  - Query format: `{"filters":[{"className":["C_TYPE"]},{"attrName":"A_NAME","value":"val","op":"OP_EQ"}]}`
  - Keys must be quoted in JSON despite documentation showing unquoted format
  - Returns: `{items: [{id, name, type, attributes, ...}], hitsTotal, rangeStart, rangeEnd}`
  - Pagination: Use `range-start` and `range-end` parameters (range-end is exclusive)

**Entities**:
- `GET /rest/2.0/entities/{entity_id}` - Get single entity
- `GET /rest/2.0/entities/{entity_id}/relations` - Get entity relationships
  - Returns: `{relations: [{id, fromId, toId, relationType, ...}]}`

**Repository Objects**:
- `GET /rest/2.0/repos/{repo_id}/objects/{object_id}` - Get repository object
  - Some objects (like capabilities) have `artefactType: "REPOSITORY_OBJECT"`
  - These cannot be fetched via `/entities/{id}` endpoint (returns 404)
  - Must use the repository objects endpoint instead
  - Search results include these objects with embedded relationship data

### Implementation Notes

1. **Repository ID Format**: API returns IDs as `{uuid}` but URLs require just `uuid` (strip braces)
2. **Query Parameters**: Must be included in HMAC calculation in specific order
3. **Pagination**: The `adoit_request_paginated()` function automatically handles multi-page results
4. **Caching**: Entity and relationship data is cached in SQLite with TTL and modification timestamps
5. **Error Handling**: 401 errors typically indicate HMAC token calculation issues (check locale sorting)
6. **Repository Objects vs Entities**:
   - Search results can include both entity objects and repository objects
   - Check `artefactType` field: "REPOSITORY_OBJECT" requires `/repos/{id}/objects/{id}` endpoint
   - Capabilities are typically repository objects, not entities
   - Prefer using embedded relationship data from search results over separate entity fetches