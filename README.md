# The Tower tourney results

- python3.13

## Setup

### Development Environment (Windows/with Git)

**Virtual Environment & Dependencies:**

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Linux/Mac
source /opt/venv/tower/bin/activate
```

**Install / development guidance:**

This project exposes a small core set of shared runtime dependencies and optional "extras" for feature areas. Pick the extras you need rather than installing everything by default.

```bash
# Install only the core package (shared runtime)
pip install -e .

# Install core + development tools
pip install -e ".[dev]"

# Install core + bot only
pip install -e ".[bot]"

# Install core + web (includes Django admin plugins and web utilities)
pip install -e ".[web]"

# Production install (bot + web extras)
pip install -e ".[bot,web]"
```

**Optional: Battle conditions predictor (separate package, requires repo access):**

```bash
# Install thetower-bcs from local workspace folder if available
pip install -e ../thetower-bcs

# Or install from git repository
pip install git+<thetower-bcs-repo-url>
```

### Production Environment (Pip-based)

**Production uses pip-installed packages (no git checkout needed):**

```bash
# Install from git repository
pip install git+https://github.com/ndsimpson/thetower-main.git

# Or install specific version/tag
pip install git+https://github.com/ndsimpson/thetower-main.git@v1.2.3

# Optional: Install battle conditions predictor (separate package)
pip install git+<thetower-bcs-repo-url>

# Extract Streamlit pages for web services (runs automatically via systemd ExecStartPre)
thetower-init-streamlit
```

**Production file locations:**

- Database: `/data/tower.sqlite3`
- Uploads: `/data/uploads/`
- Static files: `/data/static/`
- Bot configs: `/data/`
- Streamlit working directory: `/opt/thetower/` (extracted from installed package)
- Services: `/data/services/` (systemd service files)

### Bytecode Cache Management (Recommended)

To keep your project directories clean by centralizing Python bytecode files:

```bash
# Setup centralized bytecode caching
python scripts/manage_bytecode.py setup

# Check current configuration
python scripts/manage_bytecode.py status

# Clean up existing __pycache__ directories
python scripts/manage_bytecode.py cleanup

# For backward compatibility, setup can also be run without arguments
python scripts/manage_bytecode.py
```

This will:

- Install `sitecustomize.py` to your virtual environment
- Redirect all `.pyc` files to `.cache/python/` instead of creating `__pycache__` folders
- Keep your project structure clean for version control
- Preserve bytecode cache across virtual environment recreations

## Running

### Development (from source)

**Streamlit web interface:**

```bash
streamlit run src/thetower/web/pages.py
```

**Django admin:**

```bash
cd src/thetower/backend
python manage.py collectstatic  # Collect static files first
DEBUG=true python manage.py runserver
```

**Discord bot:**

```powershell
# Set environment variables (Windows PowerShell)
$env:DISCORD_TOKEN="..."; $env:DISCORD_APPLICATION_ID="..."; python -m thetower.bot.bot

# Linux/Mac
DISCORD_TOKEN="..." DISCORD_APPLICATION_ID="..." python -m thetower.bot.bot
```

**Background services:**

```bash
python -m thetower.backend.tourney_results.import.import_results
python -m thetower.backend.tourney_results.get_results
python -m thetower.backend.tourney_results.get_live_results
```

### Production (via systemd services)

Production runs via systemd service files located in `/data/services/`:

- **Web services:** `tower-public_site.service`, `tower-admin_site.service`, `tower-hidden_site.service`
- **Bot:** `discord_bot.service`
- **Data services:** `import_results.service`, `get_results.service`, `get_live_results.service`
- **Workers:** `tower-recalc_worker.service`, `generate_live_bracket_cache.service`

Services use the `thetower-web` and `thetower-bot` entry points installed via pip.

**Development file locations:**

- Database: Development uses local paths (configure via Django settings)
- Uploads: `./uploads/` or configure via settings

**Production file locations:**

- Database: `/data/tower.sqlite3`
- Uploads: `/data/uploads/`
- Static files: `/data/static/`
- Bot configs: `/data/`
- Streamlit working directory: `/opt/thetower/` (extracted via `thetower-init-streamlit`)

## Modern Package Management

This project uses `pyproject.toml` for dependency management instead of `requirements.txt`.
All configuration (pytest, black, isort, flake8) is consolidated in this single file.

**Entry points defined in pyproject.toml:**

- `thetower-web` - Streamlit web interface entry point
- `thetower-bot` - Discord bot entry point
- `thetower-init-streamlit` - Extract Streamlit pages from installed package (for production)

## Deployment Model

### Development (Git-based)

- Clone repository and install with `pip install -e .`
- Run directly from source via `python -m` or `streamlit run`
- Use local paths for data and configuration
- Manage via git (pull, commit, push)

### Production (Pip-based)

- Install via `pip install git+<repo-url>` (no git checkout needed)
- Run via systemd services using entry points (`thetower-web`, `thetower-bot`)
- Streamlit pages extracted to `/opt/thetower/` via `thetower-init-streamlit`
- All services use centralized `/data/` directory for database, uploads, configs
- Manage via admin web interface (hidden.thetower.lol) - package updates and service restarts handled through UI
- Update packages via pip, restart services to apply changes

**Why pip-based for production?**

- Cleaner deployment (no .git directory or development files)
- Version pinning via git tags for reproducible deployments
- Automatic extraction of only needed files via entry points
- Service restarts automatically re-extract updated Streamlit pages
- Standard Python package distribution workflow
- Updates managed through web admin interface with one-click package updates
