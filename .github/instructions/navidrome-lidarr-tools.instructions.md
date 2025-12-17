---
applyTo: '**'
---

# SoulSeekarr Development Instructions

## Overview

SoulSeekarr is a Flask-based music management application that integrates with Navidrome (via Subsonic API), Lidarr, and slskd. The application runs exclusively in a Docker container on an Ubuntu server and provides a modern web UI for managing automated music organization scripts.

**Technology Stack:**
- **Backend**: Flask (Python 3.11), SQLite with WAL mode
- **Frontend**: Vanilla JavaScript, Server-Sent Events (SSE), Lidarr-inspired CSS
- **Container**: Docker (Alpine-based), managed via Portainer
- **Database**: SQLite at `/data/work/soulseekarr.db`
- **External APIs**: Subsonic (Navidrome), Lidarr, slskd, MusicBrainz, Spotify, Tidal, ListenBrainz

---

## ⚠️ CRITICAL: Development Workflow Constraints

### NEVER Run Python Locally

**You CANNOT execute Python commands directly on the developer's local machine.** The application runs exclusively inside a Docker container with specific volume mounts that don't exist locally.

❌ **DO NOT DO THIS:**
```bash
python scripts/organise_files.py --dry-run
python -c "import mutagen"
pip install requests
```

✅ **INSTEAD, DO THIS:**

1. **Make code changes** to files in the workspace (these files are mounted in the container at `/data`)
2. **Tell the user**: "Restart the `soulseekarr` container in Portainer and check the logs for [specific message]"
3. **Wait for user feedback** from container logs

### Portainer-Based Testing Workflow

The user manages the Docker container through **Portainer**. After making any code changes:

1. **Inform the user to restart the container:**
   ```
   Please restart the `soulseekarr` container in Portainer.
   
   After restart, check the logs for:
   - "Flask app started on port 5000"
   - "Database initialized successfully"
   - [Any other specific messages related to your changes]
   ```

2. **For script testing**, tell the user:
   ```
   The changes are ready to test. In the SoulSeekarr web UI (http://localhost:5000):
   1. Navigate to the [Script Name] command
   2. Click "Dry Run" to test without making changes
   3. Check the execution logs for [expected behavior]
   ```

3. **For database changes**, inform the user:
   ```
   Database migration added. Restart the container and verify in logs:
   - "Running migration to add [table/column]..."
   - "Successfully [description of change]"
   ```

---

## Project Architecture

### Directory Structure (Inside Container)

```
/data/                              # Application root (mounted from host)
├── app.py                          # Flask application entry point
├── database.py                     # Database manager with migrations
├── settings.py                     # Settings with DB + env fallback
├── lidarr_utils.py                 # Lidarr API client
├── slskd_utils.py                  # slskd downloader client
├── action_logger.py                # Centralized action logging
├── scheduler.py                    # Internal cron scheduler
├── entrypoint.sh                   # Container initialization
├── requirements.txt                # Python dependencies
├── docker-compose.yml              # Container definition
├── work/
│   ├── soulseekarr.db             # SQLite database (WAL mode)
│   └── *.json                     # Cache files
├── scripts/                        # Auto-discovered commands
│   ├── organise_files.py
│   ├── file_expiry_cleanup.py
│   ├── spotify_playlist_monitor.py
│   └── [other scripts...]
├── templates/
│   └── index.html                 # Web UI
└── static/
    └── [CSS/JS assets]

/media/Owned                        # Protected music library (read-only)
/media/Not_Owned                    # Complete albums (organized)
/media/Incomplete                   # Incomplete albums
/downloads/completed                # New downloads from slskd
/logs/                              # Script execution logs (mounted, writable)
```

### Volume Mounts

The Docker container mounts these directories:

| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| `/mnt/storage/Music/Owned` | `/media/Owned` | Protected music library (read-only in scripts) |
| `/mnt/storage/Music/Not_Owned` | `/media/Not_Owned` | Organized complete albums |
| `/mnt/storage/Music/Incomplete` | `/media/Incomplete` | Incomplete albums |
| `/mnt/storage/Downloads/slskd/completed` | `/downloads/completed` | New downloads |
| `/mnt/storage/AppData/navidrome-cleanup` | `/data` | Application code and database |
| `/mnt/storage/AppData/navidrome-cleanup/logs` | `/logs` | Script execution logs |

**Important:** These paths only exist inside the container. Local Python execution will fail because `/media/Owned` etc. don't exist on the developer's machine.

### Flask Application

- **Entry Point**: `app.py` (runs unbuffered: `python3 -u app.py`)
- **Port**: 5000 (mapped to host 5000)
- **Web UI**: http://localhost:5000
- **Real-time Updates**: Server-Sent Events (SSE) at `/api/events`

**Key Features:**
- Script execution with real-time log streaming
- Persistent execution history (survives container restarts)
- Internal scheduler for cron-like automation
- Settings management via web UI
- Service health monitoring (Navidrome, Lidarr, slskd)
- OAuth integrations (Spotify, Tidal, ListenBrainz)

### Database

- **Engine**: SQLite 3 with WAL (Write-Ahead Logging) mode
- **Location**: `/data/work/soulseekarr.db`
- **Migration System**: Version-based migrations in `database.py` (PRAGMA user_version)
- **Concurrency**: 60-second timeout, WAL mode for better read/write concurrency

For detailed database schema, see `.github/instructions/database.instructions.md`

---

## Service Integrations

### Navidrome (Subsonic API)

**Always use the Subsonic API** when interacting with Navidrome. Never use Navidrome's native API.

**Authentication**: MD5 token + salt method
```python
salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
token = hashlib.md5((password + salt).encode()).hexdigest()
```

**Common Operations:**
- `getStarred2` - Get starred albums/tracks
- `star`/`unstar` - Manage favorites
- `search3` - Search library
- `getAlbum`, `getSong` - Get metadata

**Configuration**: Retrieved from `settings.py` with fallback to environment variables.

### Lidarr

Use `LidarrClient` class from `lidarr_utils.py` for all Lidarr interactions.

See `.github/instructions/service-integrations.instructions.md` for detailed usage patterns.

### slskd (Soulseek Daemon)

Use `SlskdDownloader` class from `slskd_utils.py` for search and download operations.

See `.github/instructions/service-integrations.instructions.md` for detailed workflow.

---

## Configuration Management

**Settings Priority** (highest to lowest):
1. Database (`app_settings` table)
2. Environment variables
3. Hardcoded defaults

**Access Pattern:**
```python
from settings import get_lidarr_config, get_navidrome_config, get_slskd_config

# Service configurations
lidarr = get_lidarr_config()  # Returns {'url': '...', 'api_key': '...'}

# Directory paths
from settings import (
    get_owned_directory,        # /media/Owned
    get_not_owned_directory,    # /media/Not_Owned
    get_incomplete_directory,   # /media/Incomplete
    get_downloads_completed_directory  # /downloads/completed
)

# Individual settings with fallback
from settings import get_setting
cleanup_days = get_setting('CLEANUP_DAYS', default='30', env_fallback=True)
```

---

## Specialized Instruction Files

For detailed guidance on specific aspects of development, refer to:

- **Database Schema & Migrations**: `.github/instructions/database.instructions.md`
- **Script Development Standards**: `.github/instructions/script-standards.instructions.md`
- **Service Integration Patterns**: `.github/instructions/service-integrations.instructions.md`
- **Project Reference & API Endpoints**: `.github/instructions/project-reference.instructions.md`

---

## Common Development Tasks

### Adding a New Script

1. Create file in `scripts/` directory
2. Add required docstring metadata (see `script-standards.instructions.md`)
3. Implement dry-run support
4. Add progress reporting for UI integration
5. Integrate with `ActionLogger`
6. Tell user to restart container and check script appears in UI

### Modifying Database Schema

1. Add migration in `database.py` `create_tables()` method
2. Increment schema version: `PRAGMA user_version = N`
3. Handle graceful migration with try/except for existing columns
4. Tell user to restart container and check migration logs

### Testing Changes

**After making code changes, always instruct the user:**

```
Please restart the `soulseekarr` container in Portainer.

After restart, verify:
1. Container starts successfully
2. Check logs for "[specific success message]"
3. [Any additional verification steps]

If you see errors in the logs, please share them so I can help diagnose.
```

---

## Environment Variables Reference

See `docker-compose.yml` for complete list. Key variables:

**Service URLs & Authentication:**
- `NAVIDROME_URL`, `NAVIDROME_USERNAME`, `NAVIDROME_PASSWORD`
- `LIDARR_URL`, `LIDARR_API_KEY`
- `SLSKD_URL`, `SLSKD_API_KEY`

**Processing Settings:**
- `CLEANUP_DAYS` (default: 30) - Age threshold for file expiry
- `MAX_CONCURRENT_DOWNLOADS` (default: 3)
- `DOWNLOAD_TIMEOUT_MINUTES` (default: 30)

**Container Settings:**
- `PUID`, `PGID` - File ownership
- `TZ` - Timezone
- `PORT` - Flask port (default: 5000)

---

## Logging and Debugging

### Log Locations

1. **Container logs**: View in Portainer (stdout from Flask app)
2. **Script execution logs**: `/logs/[script_name]_[timestamp].log`
3. **Action history**: `/logs/action_history.json` (JSON array, max 1000 entries)
4. **Database execution logs**: `script_logs` table (persistent, survives restarts)

### Progress Monitoring

Scripts should output progress in this format for UI parsing:
```python
print(f"PROGRESS: [{current}/{total}] {percentage}% - Processing: {item}")
print(f"PROGRESS_SUB: Getting track listing...")
```

The Flask app parses these and broadcasts via SSE to the web UI.

---

## Best Practices Summary

✅ **DO:**
- Use `DatabaseManager` context managers for database operations
- Implement dry-run mode in all scripts that modify files/APIs
- Use `ActionLogger` to log all significant actions
- Fall back from database settings → environment variables → defaults
- Cache expensive API calls (quality profiles, root folders, etc.)
- Batch database insertions for performance (100 lines or 1 second intervals)
- Use progress bars with tqdm (with graceful degradation if not available)
- Handle interruptions gracefully with signal handlers

❌ **DON'T:**
- Run Python scripts locally outside the container
- Hardcode paths, credentials, or configuration
- Make API calls without checking dry-run mode
- Modify files in `/media/Owned` (protected directory)
- Block the main thread with long-running operations
- Execute commands with `python -c` or shell execution for testing

---

## Getting Help

When encountering issues, ask the user to provide:
1. Container logs from Portainer (last 50-100 lines)
2. Specific error messages
3. Script execution logs if relevant
4. Database state if relevant (query specific tables)

Then analyze the issue and provide solutions, always ending with instructions to restart the container and verify the fix.