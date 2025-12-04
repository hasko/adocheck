# ADOcheck Changelog

## 2025-12-04: Three-Level Hierarchical Organization (WIP)

### Overview
Implemented three-level organizational hierarchy for the OE capability report: Region > OE > LE.

### Problem
Report was grouping by what it called "OE" but was actually "LE" (Legal Entity). Needed proper hierarchical structure.

### Changes

#### 1. Added OE Extraction from LE Names
- New method: `extract_oe_from_le()`
- Parses Operating Entity from Legal Entity name prefix
- Examples:
  - "Allianz Technology Branch UK (DE1632)" → "Allianz Technology SE"
  - "Allianz Technology SE (DE1632)" → "Allianz Technology SE"
  - "AZS Germany" → "AZS Germany"

#### 2. Added Region Derivation
- New method: `derive_region_from_le()`
- Maps Legal Entities to regions based on geographic keywords
- Regions:
  - **IberoLatAm**: Latin America entities
  - **APAC**: Asia-Pacific (India, Singapore, Malaysia, etc.)
  - **Central Europe**: Germany, Austria, Switzerland, Czech, Slovakia, Poland, Hungary
  - **Standalone**: Large OEs that are their own "region"

#### 3. Fixed Terminology
- Renamed: `extract_oes_from_application()` → `extract_les_from_application()`
- Updated all references to use proper terminology (LE vs OE)

#### 4. Three-Level Data Structure
```
Region
  ├─ Operating Entity (OE)
  │  ├─ Legal Entity (LE)
  │  │  ├─ Application 1
  │  │  ├─ Application 2
  │  │  └─ ...
  │  └─ Legal Entity (LE)
  │     └─ Applications...
  └─ Operating Entity (OE)
     └─ ...
```

Statistics are aggregated at all three levels:
- LE level: Direct application counts
- OE level: Aggregated from all LEs within the OE
- Region level: Aggregated from all OEs within the region

### Results

**Hierarchical Breakdown:**
- 15 Regions (IberoLatAm, APAC, Central Europe + 12 standalone OEs)
- 18 Operating Entities (OEs)
- 35 Legal Entities (LEs)
- 191 Applications (production only)

**Example Hierarchy:**
```
APAC (Region)
├─ Allianz Technology SE (OE)
│  ├─ Allianz Technology Branch Singapore (DE1632) [LE] - 29 apps
│  ├─ Allianz Technology Branch India (DE1632) [LE] - XX apps
│  └─ Allianz Technology Branch Malaysia (DE1632) [LE] - XX apps
└─ AZS India (OE)
   └─ AZS India [LE] - XX apps
```

### Status

#### ✅ Completed
- JSON report generation with three-level hierarchy
- Core data structure refactoring
- Statistics aggregation at all three levels
- Proper terminology (Region/OE/LE)
- Testing and validation

#### ⏸️ Pending
- HTML generation: Update for nested collapsible sections (Region > OE > LE)
- Markdown generation: Update for three-level hierarchy
- Charts: Add region-level data visualization

### Files Modified
- `oe_capability_report_hybrid.py`: Core refactoring
- `CLAUDE.md`: Updated documentation
- `CHANGELOG.md`: This file

### Output
- JSON report: `data/oe_capability_report_hybrid.json` (2.7 MB)
- Fully functional three-level hierarchy
- Ready for HTML/Markdown template updates

---

## 2025-12-04: GDM Level Extraction Fix & Lifecycle Filtering

### Changes
1. Fixed GDM level extraction to count hierarchical segments (not first digit)
2. Added lifecycle state filter: only "In production" applications
3. Results: 98.4% new model adoption (441 → 191 apps)

### Impact
- Applications: 441 → 191 (only production)
- OEs: 55 → 35
- Organic new model: 94.6% → 98.4%
- Unmapped: 26 → 0

---

## 2025-12-04: Smart Caching & Initial Report

### Changes
1. Implemented TTL-based smart caching with modification timestamps
2. Generated first OE capability report
3. Added HTML/Markdown export with Chart.js visualizations
4. Priority-based sorting and Group Standards exclusion

### Impact
- Performance: 22+ hours → 2.5 minutes (~500x speedup)
- 441 applications analyzed
- 55 OEs
- 1,136 capability mappings
- 94.6% new model adoption
