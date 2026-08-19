# PantryPilot: Architecture & Applied AI Implementation Guide

## 1. Project Overview & Objectives
**PantryPilot** (`pantry-pilot`) is a production-grade applied AI CLI tool and agentic pipeline designed to manage pantry inventory, query recipe sources (e.g., NYT Cooking, web search) based on target macros (Protein / Carb / Green) and practical constraints (time, health, flavor), and deterministically update/decrement pantry stock upon cooking.

### Core Learning Outcomes (Senior Engineering Principles):
1. **Deterministic State & Schema Enforcement:** Decoupling persistent storage (SQLite) from non-deterministic LLM operations using Pydantic / SQLModel.
2. **Decoupled Agentic Pipeline:** Structuring multi-stage agent workflows (extraction, search synthesis, tool retrieval, semantic resolution, and state mutation) rather than relying on brittle monolithic prompts.
3. **Semantic Entity Resolution & Unit Conversion:** Mapping natural language recipe ingredient quantities to exact database inventory records.
4. **Production Observability & Evals:** Implementing lightweight OpenTelemetry/tracing and deterministic evaluation suites for schema adherence, token budget, and task completion.
5. **Git Workflow & Architecture Decision Records (ADRs):** Iterative feature branching and documented trade-off analysis.

---

## 2. System Architecture & Component Breakdown

```text
+-------------------------------------------------------------+
|                         CLI Layer                           |
|            (Typer / Click: add, list, cook, suggest)        |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                     Orchestrator Pipeline                   |
|  - Stage 1: Inventory Fetch & Macro Filter (SQL Query)      |
|  - Stage 2: Query Synthesizer (Structured LLM Generation)   |
|  - Stage 3: Tool Execution / Web Scraper (Tavily/NYT Search)|
|  - Stage 4: Recipe Parser & Candidate Ranker                |
|  - Stage 5: Semantic Inventory Deduction & Atomic DB Commit |
+-------------------------------------------------------------+
            |                                   |
            v                                   v
+-----------------------+           +-------------------------+
|     LLM Interfaces    |           |   Data Layer (SQLite)   |
| - Pydantic Schemas    |           | - Ingredient Model      |
| - Instructor / SDK    |           | - Pantry Transaction Log|
+-----------------------+           +-------------------------+
```

---

## 3. Project Directory Structure

```text
pantry-pilot/
├── data/
│   └── pantry.db             # Local SQLite database
├── src/
│   ├── core/
│   │   ├── config.py         # App settings & env vars (Pydantic BaseSettings)
│   │   └── database.py       # Engine creation & session lifecycle
│   ├── models/
│   │   ├── db_models.py      # SQLModel / SQLAlchemy entities (Ingredient, Transaction)
│   │   └── schemas.py        # Pydantic validation models for structured LLM I/O
│   ├── services/
│   │   ├── pantry_service.py # Deterministic CRUD operations
│   │   ├── recipe_search.py  # External tool integration (Web / Tavily / Scrapers)
│   │   └── inventory_sync.py # Semantic deduction & fuzzy mapping engine
│   ├── pipeline/
│   │   └── orchestrator.py   # State machine / pipeline execution graph
│   └── cli.py                # Typer CLI entrypoint
├── tests/
│   ├── test_pantry_crud.py   # Unit tests for database transactions
│   └── test_inventory_sync.py# Evals for semantic mapping & schema adherence
├── docs/
│   └── adr/                  # Architecture Decision Records (ADRs)
├── pyproject.toml            # Dependency management (uv / poetry)
└── README.md
```

---

## 4. Phased Implementation Roadmap

### Phase 1: Persistent Data Layer & CLI Core
* Initialize project with `uv` or `poetry`.
* Define database models:
  * `Ingredient`: `id`, `name`, `category` (Protein, Carb, Green, Staple), `quantity`, `unit`, `is_perishable`, `updated_at`.
  * `PantryTransaction`: `id`, `ingredient_id`, `change_amount`, `reason` (Manual Add, Recipe Deduction), `created_at`.
* Build CLI commands: `pantry add`, `pantry list`, `pantry remove`.

### Phase 2: Structured Query Synthesizer & Web Tooling
* Define Pydantic schemas for search query formulation and recipe parsing.
* Implement structured recipe retrieval tool with domain constraints (NYT Cooking style, high-protein filters, time bounds).
* Parse raw recipe results into structured `RecipeCandidate` objects.

### Phase 3: Semantic Inventory Deduction Engine
* Build two-step deduction logic:
  1. *Semantic Resolver (LLM):* Matches recipe ingredient lines (e.g., "1 cup baby spinach") to exact database IDs and approximates quantity units.
  2. *Deterministic Transactor (Python/SQL):* Executes atomic SQLite transactions and handles low-stock/out-of-stock warnings.

### Phase 4: Evals, Observability & Packaging
* Implement automated evaluation scripts measuring:
  * Structured output validity (JSON/Pydantic adherence = 100%).
  * Inventory deduction precision.
  * Token cost and latency metrics.

---

## 5. Senior Engineering Mentorship Workflow (Git & Reviews)

* **Branch Cadence:**
  * `feat/01-pantry-db-crud`
  * `feat/02-structured-schemas-search`
  * `feat/03-semantic-inventory-sync`
  * `feat/04-orchestrator-cli-integration`
  * `feat/05-evals-and-telemetry`
* **ADR Review Standard:** Document why a specific design was chosen (e.g., SQLite over JSON flat file, SQLModel over raw SQLite, Instructor over open prompt string parsing).
