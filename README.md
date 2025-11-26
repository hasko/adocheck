# ADOcheck

Python scripts to access the ADOit API and run consistency checks against ArchiMate data.

## Features

- Connect to ADOit API with HMAC-SHA512 token-based authentication
- Retrieve metamodel information (classes, relations, attributes)
- Search and retrieve ArchiMate entities by type
- Cache ArchiMate data in SQLite database with timestamps
- Run consistency checks against the graph data (dangling relationships, orphaned entities)
- Selectively invalidate cached data based on timestamps
- Automatic pagination support for large result sets

## Setup

1. Install dependencies with uv:

```bash
uv pip install -r requirements.txt
```

2. Configure your credentials by creating a `.env` file (copy from `.env.example`):

```bash
ADOIT_URL=https://your-adoit-instance.example.com
ADOIT_API_ID=your_api_id
ADOIT_API_SECRET=your_api_secret
ADOIT_REPO_ID=your_repository_id  # Can be with or without curly braces

# Optional: Configure proxy if needed
# HTTP_PROXY=http://proxy.example.com:8080
# HTTPS_PROXY=http://proxy.example.com:8080
# NO_PROXY=localhost,127.0.0.1
```

3. Run the scripts:

```bash
# Test API connection and retrieve sample data
uv run adoit_api.py

# Run consistency checks
uv run consistency_check.py
```

## API Usage Examples

### Get Repositories

```python
from adoit_api import AdoitApi

api = AdoitApi()
repos = api.get_repos()
for repo in repos['repos']:
    print(f"{repo['name']}: {repo['id']}")
```

### Get Metamodel Classes

```python
# Get all metamodel classes
classes = api.get_metamodel_classes()
for cls in classes['classes']:
    print(f"{cls['metaName']}: {cls['id']}")
```

### Search for Entities

```python
# Search for entities of a specific type
entities = api.get_entities_by_type("C_APPLICATION", force_refresh=True)
print(f"Found {len(entities)} applications")
for entity in entities[:5]:
    print(f"  - {entity['name']} ({entity['id']})")
```

### Get Entity Relationships

```python
# Get relationships for a specific entity
entity_id = "{some-entity-id}"
relations = api.get_relationships(entity_id)
print(f"Found {len(relations)} relationships")
```

### Advanced Filtering

```python
# Search for entities with multiple attribute filters
filters = [
    {"className": ["C_APPLICATION"]},
    {"attrName": "A_CUST_SPECIALISATION", "value": "Bus. App.", "op": "OP_EQ"},
    {"attrName": "A_LIFECYCLE_STATE", "value": "In production", "op": "OP_EQ"}
]
entities = api.get_entities_by_filters(filters)
print(f"Found {len(entities)} matching entities")
```

### Cache Management

```python
import datetime

# Invalidate all cache
api.invalidate_cache()

# Invalidate cache older than 7 days
api.invalidate_cache(older_than=datetime.datetime.now() - datetime.timedelta(days=7))
```

## Capability Mapper

The capability mapper module (`capability_mapper.py`) maps business applications to top-level capabilities by traversing relationship chains using BFS graph traversal.

### Basic Usage

Map all business applications (C_APPLICATION_COMPONENT with Specialisation "Bus. App.") to top-level capabilities:

```bash
# Basic usage with default settings
uv run python3 capability_mapper.py

# Specify output location
uv run python3 capability_mapper.py --output data/my_capability_mapping.json

# Use custom application specialisation filter
uv run python3 capability_mapper.py --app-specialisation "Infrastructure App."

# Manually specify target capability IDs
uv run python3 capability_mapper.py \
  --target-capability-ids \
    "78d13953-a310-46bd-b954-0d7f4da18cd3" \
    "0148a1c2-9007-40ce-ae23-0bc7809d970e" \
    "ee9b8536-1930-43d5-858f-e3e136517fe2"

# Dry run (discovery only, no mapping)
uv run python3 capability_mapper.py --dry-run

# Use cached data for faster execution
uv run python3 capability_mapper.py --use-cache

# Adjust parallel workers for relationship fetching
uv run python3 capability_mapper.py --parallel-workers 15

# Debug mode
uv run python3 capability_mapper.py --log-level DEBUG
```

### Output Format

The capability mapper generates a JSON report with:
- Metadata (generation time, filters, statistics)
- Mappings grouped by capability (with path details)
- Unmapped applications list
- Coverage statistics

```json
{
  "report_metadata": {
    "generated_at": "2025-11-26T19:00:00Z",
    "report_type": "application_capability_mapping",
    "statistics": {
      "total_applications": 150,
      "mapped_applications": 142,
      "unmapped_applications": 8,
      "coverage_percentage": 94.67,
      "average_path_length": 3.2
    }
  },
  "mappings_by_capability": {
    "Customer Centric Cluster": {
      "applications": [
        {
          "name": "Customer Portal",
          "path_length": 3,
          "path_details": [...]
        }
      ]
    }
  },
  "unmapped_applications": [...]
}
```

### Algorithm

- **Graph Traversal**: BFS (Breadth-First Search) for guaranteed shortest paths
- **Relationship Types**: Follows ArchiMate structural (composition, aggregation, realization) and dependency (serving, access, influence) relationships
- **Performance**: Parallel relationship fetching with configurable workers (default: 10)

## Report Generator

The report generator module (`report_generator.py`) provides functionality to generate filtered reports of ADOit entities with automatic attribute name discovery.

### Basic Usage

Generate a report of application components with specific attribute filters:

```bash
# Generate report with default filters (Specialisation: "Bus. App.", Lifecycle State: "In production")
uv run report_generator.py

# Specify custom filters
uv run report_generator.py --specialisation "Bus. App." --lifecycle "In production"

# Specify output path
uv run report_generator.py --output data/my_report.json

# Use a different class
uv run report_generator.py --class-name C_CUST_IT_DOMAIN
```

### List Available Attributes

Before generating a report, you can list all available attributes for a class:

```bash
# List all attributes for the default class (C_APPLICATION)
uv run report_generator.py --list-attributes

# List attributes for a specific class
uv run report_generator.py --class-name C_CUST_IT_DOMAIN --list-attributes
```

### Manual Attribute Mapping

If attribute discovery fails or you want to specify exact attribute names:

```bash
uv run report_generator.py \
  --manual-mapping "Specialisation" "RC_SPECIALIZATION" \
  --manual-mapping "Lifecycle State" "A_LIFECYCLE_STATE"
```

### Using as a Library

```python
from report_generator import ReportGenerator
from adoit_api import AdoitApi

# Initialize
api = AdoitApi()
generator = ReportGenerator(api)

# Generate a report
report_path = generator.run_report(
    class_name="C_APPLICATION",
    target_attributes={
        "Specialisation": "Bus. App.",
        "Lifecycle State": "In production"
    },
    output_path="data/application_report.json"
)

# List all available attributes
attributes = generator.list_all_attributes("C_APPLICATION")
for attr in attributes:
    print(f"{attr['displayName']} -> {attr['metaName']}")
```

### Report Output Format

The generated JSON report includes metadata and entity data:

```json
{
  "report_metadata": {
    "generated_at": "2025-11-26T17:30:00.123456",
    "filters_applied": {
      "class": "C_APPLICATION",
      "Specialisation": "Bus. App.",
      "Lifecycle State": "In production"
    },
    "total_count": 42
  },
  "applications": [
    {
      "id": "{entity-uuid}",
      "name": "Customer Portal",
      "specialisation": "Bus. App.",
      "lifecycle_state": "In production"
    }
  ]
}
```

## ADOit API Endpoints Used

This project uses the following ADOit REST API 2.0 endpoints:

- `GET /rest/2.0/repos` - List available repositories
- `GET /rest/2.0/metamodel` - Get metamodel information
- `GET /rest/2.0/metamodel/classes` - Get all metamodel classes
- `GET /rest/2.0/metamodel/classes/{class_id}` - Get specific class details
- `GET /rest/2.0/repos/{repo_id}/search` - Search for entities with filters
- `GET /rest/2.0/entities/{entity_id}/relations` - Get entity relationships
- `GET /rest/2.0/repos/{repo_id}/modelgroups/root` - Get model groups

### Search Query Format

The search endpoint accepts queries in this format:

```json
{
  "filters": [
    {"className": ["C_APPLICATION", "C_NOTE"]},
    {"attrName": "A_DESCRIPTION", "value": "text", "op": "OP_LIKE"}
  ]
}
```

Supported operators: `OP_EMPTY`, `OP_NEMPTY`, `OP_LIKE`, `OP_NLIKE`, `OP_EQ`, `OP_NEQ`, `OP_GR`, `OP_GR_EQ`, `OP_LE`, `OP_LE_EQ`, `OP_REGEX`, `OP_RANGE`, `OP_NRANGE`, `OP_CONTAINS_ANY`

## Known Issues

### Azure/Linux Locale Sorting Bug

On some Unix systems (particularly Azure/Linux), locale sorting with `en_US` is broken for special characters like `{` and `"`. This causes authentication failures when generating HMAC tokens.

**Workaround**: The code automatically detects this issue and applies a fix in the `get_token()` function. See `CLAUDE.md` for technical details.