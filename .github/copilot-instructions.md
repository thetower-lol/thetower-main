# GitHub Copilot Instructions for thetower.lol

> **Note**: These instructions apply automatically when Copilot is asked to write or modify code. For general questions or explanations, normal responses apply.

## Project Overview

Multi-service platform for "The Tower" game tournament results and community management:

- **Django Backend** (`src/thetower/backend/`): SQLite database with tourney results, player moderation, REST API
- **Streamlit Web** (`src/thetower/web/`): Public/admin interfaces for visualizing tournament data and statistics
- **Background Services**: Automated result fetching, data imports, recalculation workers, live bracket generation

> **Discord Bot**: Moved to the `thetower-bot` repository (`github.com/ndsimpson/thetower-bot`). The bot depends on this package for Django models and tournament data.

## Copilot Coding Workflow

When writing or modifying code, follow these steps automatically:

1. **Write/modify** the code as requested
2. **Check Problems panel** for any errors or warnings in the affected files
3. **Fix all issues**:
    - Remove unused imports
    - Resolve unresolved imports
    - Remove trailing whitespace from all lines
    - Ensure no blank lines contain whitespace
    - Fix any linting errors shown in Problems panel
4. **Verify** the code adheres to project standards (150-char line length, type hints, etc.)
5. **Pause with `vscode_askQuestions`** — ask the user:
    - "Ready to commit?" with options: **Yes — commit now** / **No — keep working** / freeform (for instructions or feedback)
    - The user may run their linter while this question is pending, then click when ready

> **Staging discipline**: Before committing, check `git status` for untracked files (`??`) unrelated to the current change (loose docs, plans, skill files, etc.). If any exist, stage only the relevant paths — do **not** use `git add -A` blindly.

Apply this workflow for all code writing/modification requests unless explicitly told otherwise.

## Scope Management

When implementing features, maintain discipline to avoid scope creep:

### Implementation Focus

1. **Implement what was requested** - Focus on the user's actual ask, not theoretical improvements
2. **Match existing code patterns** - New code should match the rigor/safety level of surrounding code, not exceed it
3. **Pause before improvements** - After core functionality works, use `vscode_askQuestions` to ask before adding:
    - Extra error handling beyond existing code patterns
    - Performance optimizations not requested
    - Additional safety checks beyond codebase norms
    - Defensive coding for theoretical edge cases

### When to Stop

Task is complete when:

- ✅ User's specific request is implemented
- ✅ Code follows project standards (flake8, 150-char lines, type hints, formatting)
- ✅ No errors in Problems panel
- ✅ Doesn't break existing functionality

Do NOT continue adding without confirmation:

- ❌ Theoretical edge case handling not present in existing code
- ❌ "Production hardening" beyond what was asked
- ❌ Improvements to tangential code
- ❌ Extensive error handling if rest of codebase doesn't have it

### Review Expectations

When code review subagents find issues, distinguish between:

- **Blocking**: Breaks requested functionality or introduces real bugs
- **Critical**: Actual issues in new code (crashes, data corruption, security holes)
- **Suggestions**: Improvements beyond original scope

Only **Blocking** and **Critical** issues require fixes before completion. **Suggestions** should be noted but not automatically implemented unless explicitly requested.

## Interactive Questions

**Always use `vscode_askQuestions` tool for ANY interactive question during work**, including:

- Design decisions that affect implementation
- Clarification of requirements
- Configuration preferences
- Testing approach
- The mandatory "Ready to commit?" pause

**Never ask questions as plain text** - always use the tool for structured, actionable responses.

## Testing Policy

**Skip Test-Driven Development (TDD) for this project.** This is a Discord bot with complex external dependencies that make traditional unit testing impractical:

### Why No TDD

- **Discord API complexity**: Mocking Discord's WebSocket connections, event system, and API interactions provides minimal value
- **Live environment dependency**: Real testing requires an active Discord server and bot instance
- **Integration over isolation**: Most functionality depends on Discord.py's internals and live server state
- **Manual testing is practical**: Features can be verified immediately in a test Discord server

### Development Approach

**For Atlas, Sisyphus, and all subagents working on this project:**

- ❌ Do NOT write unit tests or test files
- ❌ Do NOT follow test-first development
- ❌ Do NOT require test coverage or test passing as acceptance criteria
- ✅ Implement features directly after planning
- ✅ Focus on code quality, type hints, error handling, and Discord API best practices
- ✅ Verify code compiles/runs without errors (use Problems panel)
- ✅ Document how to manually test each feature in commit messages or comments

### Verification Strategy

Instead of automated tests, verify implementation through:

1. **Static analysis**: Check Problems panel for errors, type checking, linting
2. **Manual Discord testing**: Run bot in test server and verify feature behavior
3. **Code review**: Focus on error handling, edge cases, and Discord API usage patterns
4. **Production monitoring**: Watch logs and user feedback after deployment

## Architecture & Structure

### Modern src/ Layout (Aug 2025 Restructure)

Reorganized from flat structure to modern `src/` layout with entry points in `pyproject.toml`:

```
src/thetower/
├── backend/          # Django project
│   ├── towerdb/     # Django settings (DJANGO_SETTINGS_MODULE="thetower.backend.towerdb.settings")
│   ├── tourney_results/  # Main app: models, views, import/export, background services
│   │   ├── import/  # CSV import logic
│   │   └── management/  # Django management commands
│   └── sus/         # Player moderation/ban system
└── web/             # Streamlit interfaces
    ├── pages.py     # Main entry point with page routing
    ├── admin/       # Admin interface (service status, migrations, codebase analysis)
    ├── live/        # Live tournament tracking and bracket visualization
    └── historical/  # Historical data analysis and player stats
```

### Django + Shared Database Pattern

- All components import Django: `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings"); django.setup()`
- Database: `/data/tower.sqlite3`
- Core models in `tourney_results/models.py`: `TourneyResult`, `TourneyRow`, `Role`, `PatchNew`, `BattleCondition`, `Avatar`, `Relic`
- Moderation in `sus/models.py`: `KnownPlayer`, `PlayerId`, `ModerationRecord`
- Always use Django ORM - never raw SQL
- SQLite timeout set to 60s in settings to handle concurrent access

> **Discord Bot**: Lives in `thetower-bot` repo and imports models from this package. Do not add bot-specific code here.

## Development Workflows

### Setup & Installation

```powershell
# Windows PowerShell - activate the shared venv (at org root)
& c:\Users\nicho\gitroot\thetower.lol\.venv\Scripts\Activate.ps1

# Install project with all dependencies
pip install -e .

# Optional dependency groups
pip install -e ".[dev]"   # pytest, black, isort, flake8
pip install -e ".[web]"   # Streamlit only

# Centralized bytecode caching (recommended - keeps __pycache__ out of git)
python scripts\manage_bytecode.py setup
python scripts\manage_bytecode.py status    # Check configuration
python scripts\manage_bytecode.py cleanup   # Clean existing __pycache__
```

### Running Components Locally

```powershell
# Streamlit web interface
streamlit run src\thetower\web\pages.py

# Django admin (for database management)
cd src\thetower\backend
python manage.py collectstatic  # Collect static files first
$env:DEBUG="true"; python manage.py runserver

# Background services (run as modules)
python -m thetower.backend.tourney_results.import.import_results
python -m thetower.backend.tourney_results.get_results
python -m thetower.backend.tourney_results.get_live_results
```

### Production Deployment

Production uses **pip-based deployment** (no git checkout):

**Installation**:

```bash
# Install main package from git repository
pip install git+https://github.com/ndsimpson/thetower-main.git

# Or install specific version/tag
pip install git+https://github.com/ndsimpson/thetower-main.git@v1.2.3

# Extract Streamlit pages to /opt/thetower/ (runs automatically via systemd ExecStartPre)
thetower-init-streamlit
```

**Service files**:

- Systemd reads service files from `/etc/systemd/system/` on the production server — that is the authoritative location.
- `/data/services/` is a folder on the **dev machine** that holds backup copies of the production service files in case they need to be restored. It is not used by systemd directly.
- To update a service: edit the file in `/etc/systemd/system/`, then run `systemctl daemon-reload` and `systemctl restart <service>`.

Service files in use:

- Web: `tower-public_site.service`, `tower-admin_site.service`, `tower-hidden_site.service`
- Data: `import_results.service`, `get_results.service`, `get_live_results.service`
- Workers: `tower-recalc_worker.service`, `generate_live_bracket_cache.service`

> **Bot services** (`discord_bot.service`, `tower-bot_site.service`) are managed from the `thetower-bot` repository.

**Environment variables** in service files:

- `DJANGO_SETTINGS_MODULE=thetower.backend.towerdb.settings`
- `HIDDEN_FEATURES=true` (enables admin features)
- `ANTHROPIC_API_KEY` (for AI features)

**Paths**:

- Database: `/data/tower.sqlite3`
- Uploads: `/data/uploads/`
- Static files: `/data/static/`
- Streamlit working directory: `/opt/thetower/` (extracted from installed package via `thetower-init-streamlit`)

**Deployment**: Code deployments are automated through the web admin interface (hidden.thetower.lol) - package updates and service restarts are handled through the UI (see [src/thetower/web/admin/](../src/thetower/web/admin/) for deployment tools).

## Project-Specific Conventions

### Python Standards

- **Line length**: 150 characters (NOT 79) - configured in black/flake8/isort
- **Formatting**: Run black/isort automatically, fix all flake8 issues
- **Imports**: Remove unused imports, resolve all import errors, organize per isort config
- **Whitespace**: No trailing whitespace, no whitespace on blank lines
- PEP 8 naming: 4-space indents, snake_case functions, CamelCase classes
- Import order: standard library → third-party → local (`from thetower.backend...`)
- Type hints on public functions/methods
- Docstrings with clear parameter and return descriptions

### Package Management

- **Use `pyproject.toml` exclusively** - no `requirements.txt`
- Dependencies: `[project.dependencies]` for core, `[project.optional-dependencies]` for dev/web
- Pin exact versions for reproducibility (e.g., `Django==5.2.4`)
- Entry points: `[project.scripts]` defines `thetower-web` command
- Update dependencies: Edit `pyproject.toml`, then `pip install -e .`

### Django Conventions

- **Models**: Use ForeignKey relationships, ColorField for colors, `simple_history` for audit trails
- **Settings**: Centralized in `towerdb/settings.py`, SECRET_KEY read from file, `/data/` for production
- **Migrations**: Always generate: `python manage.py makemigrations`
- **Admin**: Customize in `admin.py` for each app, register models with sensible list_display
- **Database**: SQLite with 60s timeout, shared across all services via `/data/tower.sqlite3`

### Logging

- Module-level: `logger = logging.getLogger(__name__)` consistently
- Format: `"%(asctime)s UTC [%(name)s] %(levelname)s: %(message)s"` with UTC timestamps
- Control: `LOG_LEVEL` environment variable (INFO/DEBUG/WARNING)

### Data Files & Paths

#### Production (Linux)

- **Data directory**: `/data/` - database, uploads, static
- **Database**: `/data/tower.sqlite3`
- **Uploads**: `/data/uploads/` (CSV files for import)
- **Static**: `/data/static/` (Django collectstatic output)
- **Bytecode cache**: `.cache/python/` (via `scripts/manage_bytecode.py setup`) - keeps `__pycache__` out of git

#### Local Development (Windows)

- **Django data directory**: `c:\data\django\` — maps to `/data/` on production
- **Results cache**: `c:\data\results_cache\`

## Critical Integration Points

### Streamlit ↔ Django

- Streamlit pages import Django: same `django.setup()` pattern
- Query models for visualization: `TourneyResult.objects.filter(...)`
- No direct database writes from Streamlit in production

### Background Services ↔ Django

- Services like `import_results.py` run as modules: `python -m thetower.backend.tourney_results.import.import_results`
- Schedule-based polling (via `schedule` library)
- Import CSV data from `/data/uploads/` into Django models

## Key Files Reference

- `src/thetower/backend/tourney_results/models.py`: Core database schema
- `src/thetower/backend/towerdb/settings.py`: Django configuration
- `scripts/manage_bytecode.py`: Centralized bytecode cache management (run `setup` first)
- `pyproject.toml`: All dependencies, build config, entry points, and tool settings (black, pytest, isort, flake8)
- `src/thetower/scripts/setup_streamlit.py`: Script to extract Streamlit pages for production deployment

## Windows PowerShell Environment

- Primary development OS is Windows with PowerShell
- Command chaining: Use `;` (NOT `&&`)
- Path separators: `\` in PowerShell commands, `/` for Python Path objects
- Environment variables: `$env:VAR_NAME="value"` (temporary) or `[System.Environment]::SetEnvironmentVariable()` (persistent)
- Virtual env activation: `c:\Users\nicho\gitroot\thetower.lol\.venv\Scripts\Activate.ps1`
- Common gotchas: PowerShell doesn't support `&&` chaining, use `;` instead for sequential commands

``
