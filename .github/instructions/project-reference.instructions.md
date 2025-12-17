---
applyTo: '**'
---

# Project Reference and API Documentation

## Overview

This document provides comprehensive reference information for SoulSeekarr, including available scripts, Flask API endpoints, environment variables, and directory structure.

---

## Available Scripts

All scripts located in `/data/scripts/` directory, auto-discovered on container startup.

### Production Commands (Section: commands)

#### **organise_files.py** ✅
**Name**: Organise Files  
**Version**: 5.0  
**Tags**: organization, lidarr, cleanup, duplicates, downloads, metadata, track-database  
**Dry-Run**: Yes  

**Purpose**: Comprehensive music file organization using track database approach.

**Workflow**:
1. Scans all directories (Downloads, Owned, Not_Owned, Incomplete)
2. Validates metadata quality (requires albumartist, album, title, year, musicbrainz_albumid)
3. Matches against Lidarr monitored albums
4. Moves complete albums to Not_Owned with [YEAR] prefix
5. Moves incomplete albums to Incomplete
6. Files missing metadata stay in Downloads
7. Protected Owned directory (read-only)
8. Integrates with expiry database

---

#### **file_expiry_cleanup.py** ✅
**Name**: File Expiry Cleanup  
**Version**: 2.0  
**Tags**: cleanup, expiry, navidrome  
**Dry-Run**: Yes  

**Purpose**: Remove old files from Incomplete/Not_Owned based on age and starred status.

**Features**:
- Deletes files older than CLEANUP_DAYS (default 30)
- Checks Navidrome starred status (album AND track level)
- Skips owned music directory entirely
- Database integration for tracking
- Respects starred protection

---

#### **spotify_playlist_monitor.py** ✅
**Name**: Spotify Playlist Monitor  
**Version**: 2.0  
**Tags**: spotify, playlist, lidarr, monitoring  
**Dry-Run**: Yes  

**Purpose**: Sync Spotify playlists to library.

**Features**:
- Batch mode (all playlists) or single playlist mode
- Extracts songs from Spotify API
- Checks Navidrome for existing tracks
- Adds missing artists to Lidarr
- Queues downloads via slskd
- Database tracking for playlist_tracks

---

#### **tidal_playlist_monitor.py** ✅
**Name**: Tidal Playlist Monitor  
**Version**: 1.0  
**Tags**: tidal, playlist, lidarr, monitoring  
**Dry-Run**: Yes  

**Purpose**: Sync Tidal playlists to library (similar to Spotify monitor).

---

#### **navidrome_starred_albums_monitor.py** ✅
**Name**: Navidrome Starred Albums Monitor  
**Version**: 2.0  
**Tags**: navidrome, starred, monitoring, lidarr  
**Dry-Run**: Yes  

**Purpose**: Smart album monitoring based on Navidrome stars.

**Workflow**:
1. Fetches starred albums from Navidrome
2. Checks if albums exist in Owned directory
3. For owned albums: Unstars in Navidrome, unmonitors in Lidarr
4. For non-owned: Sets to monitored in Lidarr
5. Adds missing artists to Lidarr
6. MusicBrainz integration for accurate metadata

---

#### **queue_lidarr_monitored.py** ✅
**Name**: Queue Lidarr Monitored Albums  
**Version**: 2.0  
**Tags**: lidarr, slskd, downloads, automation, smart  
**Dry-Run**: Yes  

**Purpose**: Intelligent download manager for wanted albums.

**Features**:
- Fetches wanted albums from Lidarr (cutoff unmet)
- Gets complete track listings
- Checks owned music folder for existing files
- Checks download queue for duplicates
- Downloads only missing tracks
- Track-level precision
- Global DOWNLOADED_TRACKS deduplication
- Audio file filtering (FLAC/MP3, excludes cover art)

---

#### **listenbrainz_recommendations.py** ✅
**Name**: ListenBrainz Weekly Exploration  
**Version**: 1.0  
**Tags**: listenbrainz, recommendations, slskd, discovery, weekly-exploration  
**Dry-Run**: Yes  

**Purpose**: Queue albums from ListenBrainz Weekly Exploration.

**Features**:
- Fetches personalized recommendations
- Extracts unique albums
- Checks Lidarr library
- Queues missing albums in slskd
- Uses enhanced download logic

---

#### **deduplicate_tracks.py** ✅
**Name**: Deduplicate Tracks  
**Version**: 1.0  
**Tags**: duplicates, cleanup  
**Dry-Run**: Yes  

**Purpose**: Remove duplicate tracks based on metadata and file quality.

---

#### **log_cleanup.py** ✅
**Name**: Log Cleanup  
**Version**: 1.0  
**Tags**: maintenance, logs  
**Dry-Run**: Yes  

**Purpose**: Clean old log files to free up disk space.

---

### Utility Scripts (Section: tests)

#### **backup_first_seen.py** ✅
**Purpose**: Backup first_detected timestamps for expiry tracking.

#### **restore_first_seen.py** ✅
**Purpose**: Restore first_detected timestamps from backup.

#### **wipe_database.py** ⚠️
**Purpose**: Clear database tables (DANGEROUS - use with caution).

#### **fix_database_duplicates.py** ✅
**Purpose**: Fix duplicate entries in database tables.

---

## Flask API Endpoints

Base URL: `http://localhost:5000`

### Main Interface

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Main dashboard with script buttons |
| `/api/events` | GET | Server-Sent Events (SSE) for real-time updates |

---

### Script Execution

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/run_script/<script_id>` | POST | Start script execution (new API) | `dry_run` (bool), `args` (string) |
| `/run/<script_id>` | GET, POST | Start script execution (legacy) | Query params vary |
| `/stop/<script_id>` | GET | Stop running script | - |
| `/status/<script_id>` | GET | Get script execution status | - |
| `/output/<script_id>` | GET | Get script output | `lines` (int, default 100) |
| `/clear/<script_id>` | GET | Clear script output buffer | - |

**Example: Run Script**
```bash
curl -X POST http://localhost:5000/run_script/organise_files \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true, "args": ""}'
```

**Response:**
```json
{
  "success": true,
  "message": "Script started",
  "execution_id": 123,
  "script_id": "organise_files",
  "dry_run": true
}
```

---

### Execution Queue

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/api/queue/executions` | GET | Get recent executions | `limit` (int, default 50) |
| `/api/queue/stats/<script_id>` | GET | Get execution statistics | - |
| `/api/queue/execution/<execution_id>/stop` | POST | Stop execution | - |
| `/api/queue/execution/<execution_id>/logs` | GET | Get execution logs | `limit` (int, default 100) |
| `/api/queue/execution/<execution_id>/logs/clear` | DELETE | Clear logs | - |
| `/api/queue/execution/<execution_id>/logs/download` | GET | Download logs as file | - |

**Example: Get Execution Logs**
```bash
curl http://localhost:5000/api/queue/execution/123/logs?limit=50
```

**Response:**
```json
{
  "execution_id": 123,
  "logs": [
    {
      "line_number": 1,
      "timestamp": "2025-12-17T19:43:50",
      "content": "Starting file organisation...",
      "log_level": "info"
    }
  ]
}
```

---

### Cron/Scheduler

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/cron/queue` | GET | Get cron queue (legacy) | - |
| `/cron/add` | POST | Add to cron queue (legacy) | `script_id`, `dry_run` |
| `/cron/remove` | POST | Remove from cron queue | `script_id` |
| `/cron/start` | POST | Start cron queue processing | - |
| `/cron/stop` | POST | Stop cron queue processing | - |
| `/api/cron/status` | GET | Check cron runner status | - |
| `/api/cron/<script_id>/enable` | POST | Enable scheduled job | `interval_type`, `interval_value` |
| `/api/cron/<script_id>/disable` | POST | Disable scheduled job | - |
| `/api/cron/<script_id>/schedule` | PUT | Update schedule | `interval_type`, `interval_value` |
| `/api/cron/jobs` | GET | Get all scheduled jobs | - |

**Example: Enable Scheduler**
```bash
curl -X POST http://localhost:5000/api/cron/organise_files/enable \
  -H "Content-Type: application/json" \
  -d '{"interval_type": "hours", "interval_value": 6}'
```

---

### Activity

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/activity/history` | GET | Get action history from action_logger |

**Response:**
```json
{
  "actions": [
    {
      "timestamp": "2025-12-17T19:43:50",
      "action_type": "file_move",
      "source": "Organise Files",
      "target": "/media/Not_Owned/[2020] Artist - Album",
      "details": "Moved complete album",
      "status": "success",
      "duration": "0.15s"
    }
  ]
}
```

---

### Scripts

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/scripts/available` | GET | Get all discovered scripts with metadata |

**Response:**
```json
{
  "commands": [
    {
      "script_id": "organise_files",
      "name": "Organise Files",
      "description": "Comprehensive music file organization",
      "supports_dry_run": true,
      "section": "commands",
      "execution_count": 42,
      "avg_duration_seconds": 45.2
    }
  ],
  "tests": []
}
```

---

### Library Management

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/library/expiring-albums` | GET | Get albums expiring soon | `days` (int, default 30), `include_starred` (bool, default false) |
| `/library/album/<album_key>/tracks` | GET | Get tracks for album | - |
| `/debug/albums` | GET | Debug endpoint for expiring albums | - |

**Example: Get Expiring Albums**
```bash
curl http://localhost:5000/library/expiring-albums?days=30&include_starred=false
```

**Response:**
```json
{
  "albums": [
    {
      "id": 123,
      "artist": "Artist Name",
      "album": "Album Name",
      "directory": "/media/Not_Owned/[2020] Artist - Album",
      "file_count": 12,
      "total_size_mb": 456.7,
      "days_old": 45,
      "is_starred": false,
      "first_detected": "2025-11-02T10:30:00"
    }
  ]
}
```

---

### Settings

#### Playlist Configs

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/settings/playlists` | GET | Get playlist configurations | - |
| `/settings/playlists` | POST | Add/update playlist | `service`, `playlist_id`, `name`, `sync_enabled` |
| `/settings/playlists/remove` | POST | Remove playlist | `playlist_id` |
| `/settings/playlists/sync-all` | POST | Sync all enabled playlists | - |
| `/settings/playlists/clear-all` | POST | Clear all playlists | - |

#### Connection Settings

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/api/settings/connections` | GET | Get all service connection configs | - |
| `/api/settings/connections/<service>` | PUT | Update service config | Service-specific fields |
| `/api/settings/connections/<service>/test` | POST | Test service connection | - |

**Example: Test Lidarr Connection**
```bash
curl -X POST http://localhost:5000/api/settings/connections/lidarr/test
```

**Response:**
```json
{
  "success": true,
  "message": "Connection successful",
  "version": "Lidarr v2.0.7.3849"
}
```

---

### OAuth/External Services

#### Spotify

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/oauth/spotify/status` | GET | Get Spotify OAuth status |
| `/api/oauth/spotify/authorize` | GET | Start Spotify OAuth flow |
| `/api/oauth/spotify/callback` | GET | Spotify OAuth callback |
| `/api/oauth/spotify/disconnect` | POST | Disconnect Spotify |

#### Tidal

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/oauth/tidal/status` | GET | Get Tidal OAuth status |
| `/api/oauth/tidal/authorize` | GET | Start Tidal OAuth flow |
| `/api/oauth/tidal/disconnect` | POST | Disconnect Tidal |

#### ListenBrainz

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/api/listenbrainz/save` | POST | Save ListenBrainz config | `username`, `token` |
| `/api/listenbrainz/test` | GET | Test ListenBrainz connection | - |
| `/api/listenbrainz/status` | GET | Get ListenBrainz status | - |

---

### Service Status

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | Health check endpoint |
| `/api/services/status` | GET | Get all service statuses (Navidrome, Lidarr, slskd) |
| `/api/services/refresh` | POST | Refresh service status cache |

**Example: Service Status**
```bash
curl http://localhost:5000/api/services/status
```

**Response:**
```json
{
  "navidrome": {
    "status": "ok",
    "message": "Connected",
    "last_check": "2025-12-17T19:43:50"
  },
  "lidarr": {
    "status": "ok",
    "message": "Connected to Lidarr v2.0.7.3849",
    "last_check": "2025-12-17T19:43:50"
  },
  "slskd": {
    "status": "ok",
    "message": "Connected",
    "last_check": "2025-12-17T19:43:50"
  }
}
```

---

### slskd Integration

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/api/slskd/downloads` | GET | Get current downloads | - |
| `/api/slskd/search/<search_id>/results` | GET | Get search results | - |

---

### Logs

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|-----------|
| `/logs` | GET | List all log files | - |
| `/logs/<filename>/download` | GET | Download log file | - |
| `/logs/<filename>/view` | GET | View log file in browser | - |
| `/logs/<filename>/tail` | GET | Tail log file (last 100 lines) | - |
| `/logs/delete-all` | POST | Delete all log files | - |

---

## Environment Variables

All environment variables can be overridden by database settings (`app_settings` table).

### Container Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | 1000 | User ID for file ownership |
| `PGID` | 1000 | Group ID for file ownership |
| `TZ` | UTC | Timezone |
| `PORT` | 5000 | Flask application port |
| `HOST` | 0.0.0.0 | Flask bind address |
| `FLASK_ENV` | production | Flask environment |

---

### Service URLs & Authentication

#### Navidrome

| Variable | Required | Description |
|----------|----------|-------------|
| `NAVIDROME_URL` | Yes | Navidrome server URL (e.g., http://192.168.1.x:4533) |
| `NAVIDROME_USERNAME` | Yes | Navidrome username |
| `NAVIDROME_PASSWORD` | Yes | Navidrome password |
| `USER` | No | Legacy alias for NAVIDROME_USERNAME |
| `PASS` | No | Legacy alias for NAVIDROME_PASSWORD |
| `BASE_URL` | No | Legacy alias for NAVIDROME_URL |

#### Lidarr

| Variable | Required | Description |
|----------|----------|-------------|
| `LIDARR_URL` | Yes | Lidarr server URL (e.g., http://192.168.1.x:8686) |
| `LIDARR_API_KEY` | Yes | Lidarr API key from Settings > General > Security |

#### slskd

| Variable | Required | Description |
|----------|----------|-------------|
| `SLSKD_URL` | Yes | slskd server URL (e.g., http://192.168.1.x:5030) |
| `SLSKD_API_KEY` | Yes | slskd API key from Settings > Application |

---

### Processing Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CLEANUP_DAYS` | 30 | Days before files expire (if not starred) |
| `MAX_CONCURRENT_DOWNLOADS` | 3 | Maximum simultaneous downloads from slskd |
| `DOWNLOAD_TIMEOUT_MINUTES` | 30 | Timeout for individual downloads |

---

### MusicBrainz Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ACOUSTID_API_KEY` | (empty) | AcoustID API key for audio fingerprinting |
| `MUSICBRAINZ_APP_NAME` | soulseekarr | Application name for MusicBrainz API |
| `MUSICBRAINZ_CONTACT_EMAIL` | admin@localhost | Contact email for MusicBrainz API |

**Note:** MusicBrainz dependencies are optional. Container degrades gracefully if installation fails.

---

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | INFO | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `VERBOSE_LOGGING` | false | Enable verbose logging for debugging |

---

### Memory Limits (Docker Compose)

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_LIMIT` | 1G | Container memory limit |

**Note:** Increased for MusicBrainz dependency compilation.

---

## Directory Mount Points

### Container → Host Mapping

| Container Path | Host Path (Example) | Purpose | Permissions |
|----------------|---------------------|---------|-------------|
| `/media/Owned` | `/mnt/storage/Music/Owned` | Protected music library | Read-only in scripts |
| `/media/Not_Owned` | `/mnt/storage/Music/Not_Owned` | Complete organized albums | Read-write |
| `/media/Incomplete` | `/mnt/storage/Music/Incomplete` | Incomplete albums | Read-write |
| `/downloads/completed` | `/mnt/storage/Downloads/slskd/completed` | New downloads from slskd | Read-write |
| `/data` | `/mnt/storage/AppData/navidrome-cleanup` | Application code & database | Read-write |
| `/logs` | `/mnt/storage/AppData/navidrome-cleanup/logs` | Script execution logs | Read-write |

**Important:** These paths only exist inside the Docker container. Local Python execution will fail.

---

## File Locations

### Inside Container

| Path | Purpose |
|------|---------|
| `/data/work/soulseekarr.db` | SQLite database (WAL mode) |
| `/data/work/*.json` | Cache files (Lidarr configs, playlists, etc.) |
| `/logs/[script]_[timestamp].log` | Script execution logs |
| `/logs/action_history.json` | Action logger history (max 1000 entries) |
| `/data/scripts/*.py` | Auto-discovered scripts |
| `/data/templates/index.html` | Web UI template |
| `/data/static/` | CSS/JS assets |

### On Host

| Path | Purpose |
|------|---------|
| `/mnt/storage/AppData/navidrome-cleanup/` | Application root (mounted as /data) |
| `/mnt/storage/AppData/navidrome-cleanup/work/soulseekarr.db` | Database |
| `/mnt/storage/AppData/navidrome-cleanup/logs/` | Logs |

---

## Common Configuration Tasks

### Add New Environment Variable

1. **Add to docker-compose.yml:**
   ```yaml
   environment:
     - NEW_SETTING=${NEW_SETTING:-default_value}
   ```

2. **Use in code with fallback:**
   ```python
   from settings import get_setting
   
   value = get_setting('NEW_SETTING', default='default_value', env_fallback=True)
   ```

3. **Restart container in Portainer**

---

### Override Setting via Database

Settings in database take precedence over environment variables:

```python
from settings import set_setting

set_setting('CLEANUP_DAYS', '45', description='Extended cleanup period')
# Now CLEANUP_DAYS=45 regardless of environment variable
```

Or via web UI: Settings > Connections

---

### Check Service Configuration

From container console or Python:

```python
from settings import get_lidarr_config, get_navidrome_config, get_slskd_config

lidarr = get_lidarr_config()
# {'url': 'http://...', 'api_key': '...'}

navidrome = get_navidrome_config()
# {'url': 'http://...', 'username': '...', 'password': '...'}

slskd = get_slskd_config()
# {'url': 'http://...', 'api_key': '...'}
```

---

## Server-Sent Events (SSE) Format

SSE endpoint: `/api/events`

**Event Types:**
- `status_update` - Script status and execution queue updates (every 1 second)

**Status Update Payload:**
```json
{
  "running_scripts": {
    "organise_files": {
      "running": true,
      "pid": 12345,
      "start_time": "2025-12-17T19:43:50",
      "dry_run": false,
      "progress": {
        "current": 123,
        "total": 456,
        "percentage": 27,
        "message": "Processing: Artist - Album"
      }
    }
  },
  "execution_queue": [
    {
      "scriptId": "organise_files",
      "name": "Organise Files",
      "startTime": "2025-12-17T19:43:50",
      "endTime": "2025-12-17T19:44:35",
      "status": "completed",
      "duration_seconds": 45.2,
      "dry_run": false,
      "execution_id": 123
    }
  ]
}
```

---

## Quick Reference: Common Workflows

### Run Script via API

```bash
# Dry run
curl -X POST http://localhost:5000/run_script/organise_files \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'

# Normal run
curl -X POST http://localhost:5000/run_script/organise_files \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'
```

### Check Script Status

```bash
curl http://localhost:5000/status/organise_files
```

### Get Recent Executions

```bash
curl http://localhost:5000/api/queue/executions?limit=20
```

### View Execution Logs

```bash
curl http://localhost:5000/api/queue/execution/123/logs?limit=100
```

### Test Service Connection

```bash
# Test all services
curl http://localhost:5000/api/services/status

# Test specific service
curl -X POST http://localhost:5000/api/settings/connections/lidarr/test
```

### Enable Scheduled Job

```bash
curl -X POST http://localhost:5000/api/cron/organise_files/enable \
  -H "Content-Type: application/json" \
  -d '{"interval_type": "hours", "interval_value": 6}'
```

### Get Expiring Albums

```bash
curl "http://localhost:5000/library/expiring-albums?days=30&include_starred=false"
```

---

## Testing API Changes

After modifying Flask routes or API endpoints:

1. **Make your changes** to `app.py`
2. **Instruct the user:**
   ```
   Please restart the `soulseekarr` container in Portainer.
   
   After restart:
   1. Check container logs for "Running on http://0.0.0.0:5000"
   2. Test the endpoint:
      curl http://localhost:5000/[your-endpoint]
   3. Verify the response format matches documentation
   
   Share the curl command output if you see errors.
   ```

3. **Verify in browser** at http://localhost:5000
4. **Check SSE updates** in browser developer console (Network tab)
