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

### Cache Management

```python
import datetime

# Invalidate all cache
api.invalidate_cache()

# Invalidate cache older than 7 days
api.invalidate_cache(older_than=datetime.datetime.now() - datetime.timedelta(days=7))
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