# Discord Bot Cog Design Architecture

## Executive Summary

This document describes the standardized architecture for Discord bot cogs in the thetower.lol project. All cogs extend `BaseCog` to inherit consistent functionality for settings management, data persistence, task tracking, and UI integration.

**Key Design Principles:**

- **BaseCog Foundation**: All cogs inherit from `BaseCog` for standardized functionality
- **Multi-Guild Isolation**: Complete data and configuration isolation per Discord server
- **Settings Integration**: Two-tier settings (guild-level + global bot owner) with automatic context detection
- **Modular UI Architecture**: UI components organized by function (core, user, admin, settings)
- **Task Management**: Background task lifecycle with tracking and error handling

**When to Use This Pattern:**

- All new cogs should follow this architecture
- Complex cogs requiring rich user interactions
- Features needing per-guild configuration and data isolation
- Systems requiring scheduled background processing

## Core Architecture

### BaseCog Integration

All cogs extend `BaseCog` (`src/thetower/bot/basecog.py`) to inherit standardized functionality:

```python
from thetower.bot.basecog import BaseCog

class MyCog(BaseCog, name="My Feature"):
    """Description of your cog."""

    # Settings view class for global /settings integration
    settings_view_class = MySettingsView

    # Define default settings as class attributes (automatically loaded by BaseCog)
    guild_settings = {
        "enabled": True,
        "notification_channel": None,
        "timeout_seconds": 60
    }

    global_settings = {
        "approved_admin_groups": ["Moderators", "Admins"]
    }

    def __init__(self, bot):
        super().__init__(bot)
        # BaseCog automatically:
        # - Registers cog on bot (bot.my_cog = self)
        # - Loads settings from class attributes
        # - Registers settings view with CogManager
        # - Registers UI/info extensions
        # - Sets up ready state tracking
```

**Inherited Capabilities:**

- **Automatic Registration**: Cogs are automatically registered on the bot (`bot.cog_name = cog_instance`)
- **Settings Management**: `get_setting()`, `set_setting()`, `ensure_settings_initialized()`
    - Settings declared as class attributes (`guild_settings`, `global_settings`)
    - Automatic guild context detection from `ctx` or `interaction`
    - Per-guild settings stored in `config.json` under `guilds/{guild_id}/{cog_name}/`
    - Global settings via `get_global_setting()`, `set_global_setting()`
- **Data Persistence**: `DataManager` for cog-specific data files
    - `self.load_data()`, `self.save_data_if_modified()`
    - Data directory: `{config_path}/cogs/{cog_name}/`
- **Task Tracking**: `task_tracker.task_context()` for monitoring background operations
- **Ready State**: `await self.ready.wait()` to ensure cog is initialized before operations
- **Logging**: `self.logger` pre-configured with cog name
- **Permission Checks**: `interaction_check()` and `cog_check()` with cog authorization

### Settings System Architecture

The bot uses a two-tier settings system managed by `ConfigManager`:

#### 1. Guild-Level Settings (Per-Server Configuration)

Guild settings are unique to each Discord server and accessed via `BaseCog` methods:

```python
# In a command or interaction handler
# BaseCog automatically detects guild_id from ctx or interaction
@app_commands.command(name="configure")
async def configure_slash(self, interaction: discord.Interaction) -> None:
    # Automatic context detection - just pass interaction
    timeout = self.get_setting("timeout_seconds", default=60, interaction=interaction)

    # Or explicit guild_id
    timeout = self.get_setting("timeout_seconds", default=60, guild_id=interaction.guild_id)

    # Set a setting
    self.set_setting("timeout_seconds", 120, interaction=interaction)
```

**Storage Structure in `config.json`:**

```json
{
    "guilds": {
        "123456789": {
            "my_cog": {
                "enabled": true,
                "notification_channel": 987654321,
                "timeout_seconds": 120
            }
        }
    }
}
```

#### 2. Global Settings (Bot Owner Configuration)

Global settings apply bot-wide and are typically restricted to bot owner access:

```python
class MyCog(BaseCog, name="My Feature"):
    # Define global settings as class attributes
    global_settings = {
        "approved_admin_groups": ["Moderators", "Admins"],
        "api_endpoint": "https://api.example.com"
    }

# In command handlers
@app_commands.command(name="admin_config")
async def admin_config(self, interaction: discord.Interaction) -> None:
    # Global settings don't need guild context
    admin_groups = self.get_global_setting("approved_admin_groups", default=[])
    self.set_global_setting("approved_admin_groups", ["SuperAdmins"])
```

**Storage Structure in `config.json`:**

```json
{
    "bot_owner_settings": {
        "my_cog": {
            "approved_admin_groups": ["SuperAdmins"],
            "api_endpoint": "https://api.example.com"
        }
    }
}
```

### Settings UI Integration

Cogs integrate with the global `/settings` command via `settings_view_class`:

```python
class MyCog(BaseCog, name="My Feature"):
    settings_view_class = MySettingsView  # Registers with CogManager

# Settings view receives SettingsViewContext
class MySettingsView(discord.ui.View):
    """Settings view for My Cog."""

    def __init__(self, context: SettingsViewContext):
        """Initialize using the unified constructor pattern."""
        super().__init__(timeout=900)
        self.cog = context.cog_instance
        self.context = context
        self.interaction = context.interaction
        self.is_bot_owner = context.is_bot_owner
        self.guild_id = context.guild_id

        # Add UI components (buttons, selects, etc.)
        self.add_item(MySettingButton(self.cog, self.guild_id))
```

**SettingsViewContext Properties:**

- `cog_instance`: Reference to the cog
- `interaction`: Discord interaction that opened settings
- `is_bot_owner`: Whether user is bot owner
- `guild_id`: Current guild ID (None for DMs)

**Benefits:**

- **Consistent UX**: Settings accessible through global `/settings` command
- **Discoverability**: All cog settings in one place
- **Automatic Integration**: `CogManager` handles registration and display

### UI Modularization by Function/Role

UI components are organized into logical modules based on **function and user role**:

```
my_cog/
├── cog.py           # Main cog class with commands
├── ui/
│   ├── __init__.py  # Clean API exports
│   ├── core.py      # Core business logic (forms, constants)
│   ├── user.py      # User-facing interaction flows
│   ├── admin.py     # Administrative interfaces (optional)
│   └── settings.py  # Settings management views
└── data/            # Cog-specific data files (optional)
```

#### Core Module (`core.py`)

- **Purpose**: Business logic and shared components
- **Contents**: Form modals, constants, reusable view components
- **Example**: Form classes, enums, validation logic

```python
# Example: Modal form for user input
class ConfigurationForm(discord.ui.Modal, title="Configuration"):
    timeout_input = discord.ui.TextInput(
        label="Timeout (seconds)",
        placeholder="60",
        default="60",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Validation and processing
        timeout = int(self.timeout_input.value)
        # Store via cog.set_setting()
```

#### User Module (`user.py`)

- **Purpose**: End-user interaction flows
- **Contents**: Views for regular users managing their own data
- **Example**: Management views, selection interfaces

```python
# Example: User management view
class ManagementView(discord.ui.View):
    def __init__(self, cog, user_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id

        # Add user-facing buttons
        self.add_item(CreateButton())
        self.add_item(ViewButton())
```

#### Admin Module (`admin.py` - Optional)

- **Purpose**: Administrative oversight and moderation
- **Contents**: Interfaces for administrators and moderators
- **Example**: Bulk management, audit views, override controls

#### Settings Module (`settings.py`)

- **Purpose**: Configuration and setup interfaces
- **Contents**: Views for configuring cog behavior via `/settings`
- **Example**: Per-guild settings toggles, channel selects, role selects

```python
# Example: Settings view integrated with global /settings
class MySettingsView(discord.ui.View):
    def __init__(self, context: SettingsViewContext):
        super().__init__(timeout=900)
        self.cog = context.cog_instance
        self.guild_id = context.guild_id

        # Add setting-specific UI components
        self.add_item(ChannelSelect(self.cog, self.guild_id))
        self.add_item(EnabledToggleButton(self.cog, self.guild_id))
```

**Modular Benefits:**

- **Separation of Concerns**: Each module has clear, focused responsibilities
- **Selective Implementation**: Simple cogs might only need `core` + `settings`
- **Team Development**: Multiple developers can work on different aspects
- **Reusability**: Components can be imported independently

## Component Breakdown

### Cog Initialization

```python
class MyCog(BaseCog, name="My Feature"):
    settings_view_class = MySettingsView

    # Define default settings as class attributes (automatically loaded by BaseCog)
    guild_settings = {
        "enabled": True,
        "notification_channel": None,
        "timeout_seconds": 60,
        "max_items": 10
    }

    global_settings = {
        "api_key": None,
        "approved_admin_groups": []
    }

    def __init__(self, bot):
        super().__init__(bot)
        # BaseCog automatically:
        # - Registers cog on bot (bot.my_cog = self)
        # - Loads settings from class attributes
        # - Registers settings view with CogManager
        # - Registers UI/info extensions
        # - Sets up ready state tracking

        # Initialize cog-specific data structures
        self.cache = {}  # Guild-specific caches

    async def _initialize_cog_specific(self, tracker) -> None:
        """Initialize cog-specific functionality (called automatically by BaseCog)."""
        # Initialize guild settings for all guilds
        for guild in self.bot.guilds:
            self.ensure_settings_initialized(
                guild_id=guild.id,
                default_settings=self.guild_settings
            )

        # Start background tasks
        if not self.background_task.is_running():
            self.background_task.start()

    async def cog_unload(self):
        """Called when cog is unloaded."""
        # Stop background tasks
        if hasattr(self, 'background_task'):
            self.background_task.cancel()

        await super().cog_unload()
```

### Data Layer

**Guild-Specific Data Structures:**

```python
# Multi-guild data isolation
self.cache = {}  # {guild_id: guild_specific_data}

def _ensure_guild_initialized(self, guild_id: int) -> None:
    """Ensure guild-specific data structures exist."""
    # Initialize settings
    self.ensure_settings_initialized(
        guild_id=guild_id,
        default_settings=self.guild_settings
    )

    # Initialize cache
    if guild_id not in self.cache:
        self.cache[guild_id] = {
            "items": [],
            "last_update": None
        }
```

**Persistent Data with DataManager:**

```python
from thetower.bot.utils.data_management import DataManager

def __init__(self, bot):
    super().__init__(bot)
    self.data_manager = DataManager()

async def _initialize_cog_specific(self, tracker) -> None:
    """Initialize cog-specific functionality."""
    # Load persistent data from disk
    tracker.update_status("Loading data")
    data = await self.load_data()
    if data:
        self.cache = data.get("cache", {})
        self.logger.info("Loaded cached data")

async def save_cache(self):
    """Save cache to disk."""
    data = {"cache": self.cache}
    await self.save_data_if_modified(data)
```

### Task Layer

**Background Processing Tasks:**

```python
from discord.ext import tasks

@tasks.loop(hours=1)
async def periodic_cleanup(self) -> None:
    """Clean up expired data every hour."""
    if self.is_paused:
        return

    async with self.task_tracker.task_context("cleanup"):
        for guild_id in list(self.cache.keys()):
            # Clean up guild data
            await self._cleanup_guild_data(guild_id)

@periodic_cleanup.before_loop
async def before_periodic_cleanup(self):
    """Wait for cog to be ready before starting tasks."""
    await self.bot.wait_until_ready()
    await self.ready.wait()  # Wait for cog initialization

@tasks.loop(minutes=5)
async def sync_data(self) -> None:
    """Sync data from external source."""
    if self.is_paused:
        return

    async with self.task_tracker.task_context("sync"):
        # Perform sync operation
        await self._sync_external_data()
```

**Task Lifecycle Management:**

- Start tasks in `_initialize_cog_specific()` after bot and cog are ready
- Use `@task.before_loop` to wait for `bot.wait_until_ready()` and `self.ready.wait()`
- Cancel tasks in `cog_unload()`
- Wrap task bodies in `task_tracker.task_context()` for monitoring
- Check `self.is_paused` at the start of task loops

### Command Layer

**Slash Command Integration:**

```python
from discord import app_commands

@app_commands.command(name="mycommand", description="Do something useful")
@app_commands.guild_only()
async def my_slash(self, interaction: discord.Interaction, option: str) -> None:
    """Slash command handler with automatic permission checking."""
    # BaseCog.interaction_check() automatically validates:
    # 1. Cog is enabled for this guild
    # 2. User has required permissions
    # 3. Channel is authorized (if configured)

    # Access guild settings with automatic context detection
    enabled = self.get_setting("enabled", default=True, interaction=interaction)

    if not enabled:
        await interaction.response.send_message("Feature disabled", ephemeral=True)
        return

    # Process command
    await interaction.response.send_message("Success!", ephemeral=True)

@app_commands.command(name="admin")
@app_commands.guild_only()
async def admin_slash(self, interaction: discord.Interaction) -> None:
    """Admin-only command with role check."""
    # Check if user has admin role
    is_bot_owner = await self.bot.is_owner(interaction.user)
    is_admin = any(role.name == "Admin" for role in interaction.user.roles)

    if not (is_bot_owner or is_admin):
        await interaction.response.send_message("❌ Admin only", ephemeral=True)
        return

    # Admin functionality
    await interaction.response.send_message("Admin panel", ephemeral=True)
```

**Permission Integration:**

- `BaseCog.interaction_check()` automatically validates cog authorization
- Override `_check_additional_interaction_permissions()` for custom checks
- Use `PermissionManager` for channel/user authorization
- Bot owner checks via `await self.bot.is_owner(user)`
- Django group checks via `self.get_user_django_groups(user)` and `PermissionContext.has_django_group()`

### Django Permission Checking

BaseCog provides centralized methods for checking Django user groups to avoid code duplication:

```python
# Get all Django groups for a user (centralized method)
user_groups = await self.get_user_django_groups(interaction.user)

# Check specific group membership
if "Moderators" in user_groups:
    # User is a moderator
    pass

# Or use PermissionContext for efficient checking
permission_ctx = await self._fetch_user_permissions(interaction.user, interaction.guild)
if permission_ctx.has_django_group("Moderators"):
    # User has moderator permissions
    pass
```

**Available Methods:**

- `self.get_user_django_groups(user)`: Returns `List[str]` of Django group names for the user
- `PermissionContext.has_django_group(group_name)`: Check if user has specific Django group
- `PermissionContext.has_any_group(group_list)`: Check if user has any of the listed groups
- `PermissionContext.has_all_groups(group_list)`: Check if user has all of the listed groups

## Key Design Patterns

### Automatic Guild Context Detection

BaseCog methods automatically detect `guild_id` from context:

```python
# Method 1: Pass interaction (preferred)
@app_commands.command(name="config")
async def config_slash(self, interaction: discord.Interaction) -> None:
    # No need to extract guild_id manually
    timeout = self.get_setting("timeout_seconds", default=60, interaction=interaction)
    self.set_setting("timeout_seconds", 120, interaction=interaction)

# Method 2: Pass ctx (for text commands, rarely used)
async def config_text(self, ctx: commands.Context) -> None:
    timeout = self.get_setting("timeout_seconds", default=60, ctx=ctx)

# Method 3: Explicit guild_id (when no context available)
def _process_guild_data(self, guild_id: int):
    timeout = self.get_setting("timeout_seconds", default=60, guild_id=guild_id)
```

**How It Works:**

- BaseCog's `_extract_guild_id()` inspects the call stack
- Looks for `ctx`, `interaction`, or `guild_id` in calling scope
- Automatically uses `ctx.guild.id` or `interaction.guild_id`
- Raises `ValueError` if guild context cannot be determined

### Settings Initialization Pattern

Initialize settings on cog load and when guilds join:

```python
async def _initialize_cog_specific(self, tracker) -> None:
    """Initialize cog-specific functionality."""
    # Initialize settings for all current guilds
    for guild in self.bot.guilds:
        self.ensure_settings_initialized(
            guild_id=guild.id,
            default_settings=self.guild_settings
        )
        self._ensure_guild_data(guild.id)

@commands.Cog.listener()
async def on_guild_join(self, guild: discord.Guild):
    """Initialize settings when bot joins a new guild."""
    self.ensure_settings_initialized(
        guild_id=guild.id,
        default_settings=self.guild_settings
    )
    self._ensure_guild_data(guild.id)
```

### UI Organization Pattern

**Function-Based Separation:**

```
# core.py: Business logic, forms, constants
# - Shared across user types
# - Pure functionality, no permissions

# user.py: End-user interactions
# - Personal data management
# - Self-service workflows

# admin.py: Administrative functions (optional)
# - Oversight and moderation
# - Bulk operations

# settings.py: Configuration
# - Cog behavior setup
# - Integrates with global /settings
```

**Implementation Flexibility:**

- **Simple Cogs**: May only need `core` + `settings` modules
- **Complex Cogs**: Can implement all modules as needed
- **Progressive Enhancement**: Start simple, add modules as complexity grows

### Data Management Pattern

**Guild Isolation:**

```python
def _ensure_guild_data(self, guild_id: int) -> None:
    """Ensure guild-specific data structures exist."""
    # Settings initialization
    self.ensure_settings_initialized(
        guild_id=guild_id,
        default_settings=self.guild_settings
    )

    # Runtime data initialization
    if guild_id not in self.cache:
        self.cache[guild_id] = {
            "items": [],
            "timestamps": {}
        }
```

**Benefits:**

- **Complete Isolation**: No data leakage between servers
- **Scalability**: Supports hundreds of guilds efficiently
- **Flexibility**: Each guild can have different configurations

### Task Lifecycle Pattern

**Proper Task Management:**

```python
async def _initialize_cog_specific(self, tracker) -> None:
    """Initialize cog-specific functionality."""
    # Start background tasks after cog is ready
    if not self.periodic_task.is_running():
        self.periodic_task.start()

async def cog_unload(self) -> None:
    """Clean up tasks before unloading."""
    if hasattr(self, 'periodic_task'):
        self.periodic_task.cancel()
    await super().cog_unload()

@tasks.loop(hours=1)
async def periodic_task(self):
    """Background task with proper error handling."""
    if self.is_paused:
        return

    async with self.task_tracker.task_context("periodic_task"):
        # Task logic here
        pass

@periodic_task.before_loop
async def before_periodic_task(self):
    """Wait for bot to be ready before starting."""
    await self.bot.wait_until_ready()
    await self.ready.wait()  # Wait for cog initialization
```

**Benefits:**

- **Resource Management**: Prevents task leaks
- **Graceful Shutdown**: Proper cleanup on bot restart
- **Error Tracking**: Task errors logged via `task_tracker`
- **Monitoring**: Task history and statistics available

## Implementation Guide

### Creating a New Cog

**1. Create Cog Structure:**

```
src/thetower/bot/cogs/my_cog/
├── __init__.py
├── cog.py
└── ui/
    ├── __init__.py
    ├── core.py
    └── settings.py
```

**2. Implement Base Cog (`cog.py`):**

```python
from discord import app_commands
from discord.ext import tasks
from thetower.bot.basecog import BaseCog
from .ui.settings import MySettingsView

class MyCog(BaseCog, name="My Feature"):
    """Description of what this cog does."""

    settings_view_class = MySettingsView

    # Define default settings as class attributes (automatically loaded by BaseCog)
    guild_settings = {
        "enabled": True,
        "channel_id": None,
        "timeout": 60
    }

    global_settings = {
        "admin_groups": []
    }

    def __init__(self, bot):
        super().__init__(bot)
        # BaseCog automatically handles:
        # - Cog registration on bot (bot.my_cog = self)
        # - Settings loading from class attributes
        # - Settings view registration
        # - UI/info extension registration
        # - Ready state setup

        # Initialize data structures
        self.data = {}  # {guild_id: guild_data}

    async def _initialize_cog_specific(self, tracker) -> None:
        """Initialize cog-specific functionality (called automatically by BaseCog)."""
        # Initialize all guilds
        for guild in self.bot.guilds:
            self.ensure_settings_initialized(
                guild_id=guild.id,
                default_settings=self.guild_settings
            )

        # Start background tasks
        if not self.background_task.is_running():
            self.background_task.start()

    @app_commands.command(name="mycommand")
    @app_commands.guild_only()
    async def my_command(self, interaction: discord.Interaction):
        """Your command description."""
        # Automatic guild context detection
        enabled = self.get_setting("enabled", interaction=interaction)

        if not enabled:
            await interaction.response.send_message(
                "Feature disabled", ephemeral=True
            )
            return

        # Command logic here
        await interaction.response.send_message("Success!")

    @tasks.loop(hours=1)
    async def background_task(self):
        """Periodic background task."""
        if self.is_paused:
            return

        async with self.task_tracker.task_context("background_task"):
            # Task logic here
            pass

    @background_task.before_loop
    async def before_background_task(self):
        await self.bot.wait_until_ready()
        await self.ready.wait()

    async def cog_unload(self):
        """Cleanup when cog is unloaded."""
        if hasattr(self, 'background_task'):
            self.background_task.cancel()
        await super().cog_unload()

# Setup function required for cog loading
async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

**3. Implement Settings View (`ui/settings.py`):**

```python
import discord
from thetower.bot.ui.context import SettingsViewContext

class MySettingsView(discord.ui.View):
    """Settings view for My Cog."""

    def __init__(self, context: SettingsViewContext):
        super().__init__(timeout=900)
        self.cog = context.cog_instance
        self.guild_id = context.guild_id
        self.is_bot_owner = context.is_bot_owner

        # Add UI components
        self.add_item(EnabledToggle(self.cog, self.guild_id))
        self.add_item(ChannelSelect(self.cog, self.guild_id))

class EnabledToggle(discord.ui.Button):
    """Toggle button for enabled setting."""

    def __init__(self, cog, guild_id: int):
        enabled = cog.get_setting("enabled", guild_id=guild_id)
        label = "Enabled" if enabled else "Disabled"
        style = discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger

        super().__init__(label=label, style=style)
        self.cog = cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        # Toggle the setting
        current = self.cog.get_setting("enabled", guild_id=self.guild_id)
        self.cog.set_setting("enabled", not current, guild_id=self.guild_id)

        # Update UI
        await interaction.response.edit_message(
            content="Setting updated",
            view=self.view
        )

class ChannelSelect(discord.ui.ChannelSelect):
    """Channel select for channel_id setting."""

    def __init__(self, cog, guild_id: int):
        super().__init__(
            placeholder="Select notification channel",
            channel_types=[discord.ChannelType.text]
        )
        self.cog = cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        channel_id = self.values[0].id
        self.cog.set_setting("channel_id", channel_id, guild_id=self.guild_id)

        await interaction.response.send_message(
            f"Channel set to {self.values[0].mention}",
            ephemeral=True
        )
```

**4. Register in `__init__.py`:**

```python
from .cog import MyCog

__all__ = ['MyCog']
```

### Enabling the Cog

**1. Bot Owner Enables Cog:**

Use `/bot owner` command to enable the cog globally:

- Set `enabled: true` in bot owner settings
- Set `public: true` if all guilds can use it (or authorize specific guilds)

**2. Guild Owners Enable Cog:**

Once bot owner enables and authorizes, guild owners can enable via `/settings`:

- Navigate to "Manage Cogs"
- Enable the cog for their server

## Best Practices

### Settings Management

**DO:**

- Use `interaction=interaction` or `ctx=ctx` for automatic guild detection
- Initialize settings in `_initialize_cog_specific()` and `on_guild_join()`
- Define defaults in `guild_settings` and `global_settings` class attributes
- Use descriptive setting names (`notification_channel` not `channel`)

**DON'T:**

- Manually extract `guild_id` unless no context available
- Forget to initialize settings for new guilds
- Store guild-specific settings globally

### UI Design

**DO:**

- Use ephemeral responses for user privacy (`ephemeral=True`)
- Implement timeout handling (default 300-900 seconds)
- Provide clear user feedback for all actions
- Use consistent button/select styles across cogs

**DON'T:**

- Mix business logic with UI components
- Forget to handle interaction timeouts
- Make all responses public (consider privacy)

### Data Management

**DO:**

- Always isolate data by `guild_id`
- Use `DataManager` for persistent data
- Validate data before storing
- Clean up stale data periodically

**DON'T:**

- Share data between guilds without guild context
- Store sensitive data without encryption
- Let data structures grow unbounded

### Task Management

**DO:**

- Use `task_tracker.task_context()` for all operations
- Check `self.is_paused` at task start
- Wait for `bot.wait_until_ready()` and `self.ready.wait()` before loops
- Handle exceptions within tasks
- Cancel tasks in `cog_unload()`

**DON'T:**

- Start tasks before bot/cog is ready
- Forget to cancel tasks on unload
- Block with synchronous operations (use async alternatives)

### Code Organization

**DO:**

- Separate UI from business logic
- Use descriptive function/variable names
- Add docstrings to public methods
- Group related functionality in modules

**DON'T:**

- Create monolithic files (split into modules)
- Use cryptic abbreviations
- Skip documentation for complex logic

## Common Pitfalls

### Guild Context Errors

**Problem:** `ValueError: guild_id is required but could not be determined`

**Solutions:**

```python
# ❌ Wrong - no context
timeout = self.get_setting("timeout")

# ✅ Correct - pass interaction
timeout = self.get_setting("timeout", interaction=interaction)

# ✅ Correct - explicit guild_id
timeout = self.get_setting("timeout", guild_id=guild_id)
```

### Settings Not Persisting

**Problem:** Settings reset on bot restart

**Solution:** Ensure `ConfigManager.save_config()` is called (automatic in `set_setting()`)

### Task Not Starting

**Problem:** Background task doesn't run

**Solutions:**

```python
# ✅ Add before_loop to wait for ready
@my_task.before_loop
async def before_my_task(self):
    await self.bot.wait_until_ready()
    await self.ready.wait()

# ✅ Start in _initialize_cog_specific
async def _initialize_cog_specific(self, tracker) -> None:
    if not self.my_task.is_running():
        self.my_task.start()
```

### Interaction Timeout

**Problem:** "This interaction failed" after 3 seconds

**Solutions:**

```python
# ❌ Wrong - long operation blocks interaction
async def callback(self, interaction: discord.Interaction):
    result = await long_operation()  # Takes 5 seconds
    await interaction.response.send_message(result)

# ✅ Correct - defer then process
async def callback(self, interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await long_operation()
    await interaction.followup.send(result)
```

### Memory Leaks

**Problem:** Bot memory usage grows over time

**Solutions:**

- Clean up guild data in `on_guild_remove` listener
- Implement periodic cleanup tasks for stale data
- Use weak references for temporary caches
- Profile memory usage periodically

## Reference

### Key Files

- `src/thetower/bot/basecog.py`: Base class for all cogs (1000+ lines)
- `src/thetower/bot/utils/configmanager.py`: Settings management
- `src/thetower/bot/utils/cogmanager.py`: Cog loading and authorization
- `src/thetower/bot/utils/task_tracker.py`: Background task monitoring
- `src/thetower/bot/utils/data_management.py`: Data persistence
- `src/thetower/bot/ui/context.py`: SettingsViewContext definition

### Example Cogs

- `validation`: Simple cog with user verification
- `tourney_roles`: Complex cog with background tasks and Django integration
- `unified_advertise`: Full-featured cog with admin/user UIs
- `battle_conditions`: Cog with external API integration

## Cog Packages

Cogs can live in separate repositories and be installed as Python packages. CogManager discovers them via `importlib.metadata.entry_points()` group `"thetower.bot.cogs"`.

### Package Layout

Use a **flat** src layout — the cog package is the root, with no nested `cogs/` subdirectory. See `cog-tourney-reminder` and `VerifyCog` as canonical examples:

```
my-cog-repo/
├── pyproject.toml      # setuptools-scm versioning, entry point
├── setup.py            # dynamic URL injection
├── .flake8
├── .gitattributes
└── src/
    └── my_package/
        ├── __init__.py     # must contain async setup(bot)
        ├── _version.py     # generated by setuptools-scm
        ├── cog.py
        └── ui/
            ├── __init__.py
            ├── core.py
            └── settings.py
```

### Entry Point

The entry point value must point at the **top-level package** — not a sub-module. CogManager checks for `setup()` on the loaded module to determine it's a single-cog source:

```toml
[project.entry-points."thetower.bot.cogs"]
my_cog_name = "my_package"   # NOT "my_package.cogs"

[tool.setuptools_scm]
write_to = "src/my_package/_version.py"
```

### `__init__.py` Pattern

```python
async def setup(bot):
    from .cog import MyCog
    await bot.add_cog(MyCog(bot))
```

### Relative Imports

When porting a cog from `thetower.bot.cogs.validation` to an external package, update any absolute imports that referenced the old in-tree path to relative imports:

```python
# ❌ Old in-tree import
from thetower.bot.cogs.validation.ocr import is_available

# ✅ Correct relative import in external package
from .ocr import is_available          # from cog.py
from ..ocr import is_available         # from ui/core.py
```

### Discovery and Loading

```powershell
# Install locally for development
pip install -e C:\path\to\my-cog-repo

# Install from GitHub
pip install git+https://github.com/yourname/my-cog-repo.git

# After install, refresh in Discord:
# /settings → Bot Settings → Cog Management → Refresh Cog Sources
```

For private repositories see `docs/private_cog_deployment.md` for SSH deploy key setup.

This architecture provides a solid foundation for building sophisticated Discord bot features while maintaining code organization, scalability, and consistency across the codebase.
