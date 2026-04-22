# Supervisor Worker Documentation

## Overview

The Supervisor Worker is a proposed enhancement to the Tower system (ndsimpson/thetower-main), consolidating multiple background services (e.g., recalc worker, Zendesk queue, live bracket cache) into a single, concurrent queue/cache manager. This simplifies deployment, monitoring, and resource management while allowing extensible job handling without core code changes.

## Core Architecture

- **Single Entry Point**: A Django management command (`supervisor_worker.py`) runs as a long-lived process, similar to existing commands like `process_recalc_queue.py`.
- **Queue Abstraction**: Uses a shared `BackgroundJob` model for all job types, replacing scattered flags (e.g., `needs_recalc`).
- **Polling Loop**: Continuously checks and processes jobs, with concurrency for efficiency.
- **Error Handling**: Includes retries, timeouts, and logging for robustness.

## Concurrent Job Processing

To handle multiple jobs simultaneously:

- **Threading/Multiprocessing**: Uses `concurrent.futures.ThreadPoolExecutor` for a configurable pool (default 4 threads) to process jobs concurrently.
- **Job Isolation**: Each thread handles one job, with atomic DB operations to avoid race conditions.
- **Monitoring**: Tracks active jobs, errors, and throughput via the service status page.
- **Shutdown**: Graceful handling of SIGTERM for clean exits.

## Extensibility (Plugin System)

The worker is designed to be updated without modifying the core supervisor each time a new job type is added:

- **Registry Pattern**: A global `JOB_HANDLERS` dict maps job types (e.g., 'recalc') to handler functions.
- **Auto-Discovery**: Scans Django apps for `jobs.py` modules at startup, registering handlers dynamically.
- **Convention**: Each app's `jobs.py` defines handlers and calls `register_job_handler()`.

### Example Handler Registration

In `src/thetower/backend/tourney_results/jobs.py`:

```python
from ..jobs.registry import register_job_handler

def handle_recalc(job):
    # Recalc logic here
    pass

def register():
    register_job_handler("recalc", handle_recalc)
```

## Implementation Outline

1. **Job Model**: `BackgroundJob` with fields like `job_type`, `payload` (JSON), `status`, `retries`.
2. **Supervisor Command**: Loops with a thread pool, submitting jobs via `executor.submit()`.
3. **Handlers**: Job-specific functions that parse payloads and perform work.
4. **Testing**: Use `--one-shot` for batch processing or `--max-workers 1` for sequential mode.

## Benefits & Considerations

- **Pros**: Easier maintenance, shared resources, consistent handling.
- **Cons**: Potential DB bottlenecks; mitigate with limits and monitoring.
- **Migration**: Update existing services to enqueue jobs instead of direct processing.

For setup or code examples, refer to the management command and registry modules.
