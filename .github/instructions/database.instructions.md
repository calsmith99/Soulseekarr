---
applyTo: '**'
---

# Database Structure and Patterns

## Overview

SoulSeekarr uses **SQLite 3** with **WAL (Write-Ahead Logging)** mode for persistent storage. The database is located at `/data/work/soulseekarr.db` inside the container and includes a version-based migration system.

**Key Characteristics:**
- **WAL Mode**: Allows concurrent reads and writes without blocking
- **60-Second Timeout**: Prevents "database is locked" errors
- **Row Factory**: `sqlite3.Row` for dict-like access to query results
- **Migration System**: `PRAGMA user_version` tracks schema version
- **Context Managers**: Automatic connection management with rollback on errors

---

## Database Schema (Version 8)

### 1. `script_executions` - Execution History

Tracks all script runs with persistent history that survives container restarts.

```sql
CREATE TABLE script_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id TEXT NOT NULL,                    -- Unique script identifier
    script_name TEXT NOT NULL,                  -- Display name
    start_time TIMESTAMP NOT NULL,              -- When execution started
    end_time TIMESTAMP,                         -- When execution completed (NULL if running)
    duration_seconds REAL,                      -- Total execution time
    status TEXT NOT NULL DEFAULT 'running',     -- 'running', 'completed', 'failed', 'stopped'
    return_code INTEGER,                        -- Exit code (0 = success)
    dry_run BOOLEAN DEFAULT FALSE,              -- Whether this was a dry run
    pid INTEGER,                                -- Process ID
    error_message TEXT,                         -- Error details if failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_script_executions_script_id ON script_executions(script_id);
CREATE INDEX idx_script_executions_start_time ON script_executions(start_time);
CREATE INDEX idx_script_executions_status ON script_executions(status);
```

**Common Queries:**
```python
# Get recent executions
db.get_recent_executions(limit=50)

# Get execution by ID
db.get_execution(execution_id)

# Create new execution
execution_id = db.create_execution(script_id, script_name, dry_run=False)

# Update execution status
db.update_execution(execution_id, status='completed', return_code=0, duration_seconds=45.2)
```

---

### 2. `script_logs` - Execution Output

Stores log output for each execution with batch insertion optimization.

```sql
CREATE TABLE script_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL,              -- FK to script_executions
    line_number INTEGER NOT NULL,               -- Sequential line number
    timestamp TIMESTAMP NOT NULL,               -- When line was logged
    content TEXT NOT NULL,                      -- Log line content
    log_level TEXT DEFAULT 'info',              -- 'info', 'warning', 'error'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES script_executions(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_script_logs_execution_id ON script_logs(execution_id);
CREATE INDEX idx_script_logs_timestamp ON script_logs(timestamp);
```

**Batch Insertion Pattern** (from `app.py`):
```python
# Buffer logs for performance
log_buffer = []
last_insert_time = time.time()
line_number = 0

for line in process.stdout:
    line_number += 1
    log_buffer.append({
        'execution_id': execution_id,
        'line_number': line_number,
        'timestamp': datetime.now().isoformat(),
        'content': line.strip(),
        'log_level': 'info'
    })
    
    # Insert when buffer reaches 100 lines or 1 second elapsed
    if len(log_buffer) >= 100 or (time.time() - last_insert_time) >= 1.0:
        db.add_script_logs_batch(log_buffer)
        log_buffer.clear()
        last_insert_time = time.time()

# Insert remaining logs
if log_buffer:
    db.add_script_logs_batch(log_buffer)
```

---

### 3. `script_configs` - Auto-Discovered Scripts

Metadata for all scripts discovered in the `scripts/` directory.

```sql
CREATE TABLE script_configs (
    script_id TEXT PRIMARY KEY,                 -- Unique identifier (filename without .py)
    name TEXT NOT NULL,                         -- Display name from docstring
    description TEXT,                           -- Description from docstring
    script_path TEXT NOT NULL,                  -- Full path to script
    supports_dry_run BOOLEAN DEFAULT TRUE,      -- Whether script supports --dry-run
    section TEXT DEFAULT 'commands',            -- 'commands' or 'tests'
    status TEXT DEFAULT 'active',               -- 'active' or 'inactive'
    status_message TEXT,                        -- Error message if inactive
    last_discovered TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    execution_count INTEGER DEFAULT 0,          -- Total executions
    last_execution_time TIMESTAMP,              -- Last run timestamp
    avg_duration_seconds REAL                   -- Average execution time
);
```

**Discovery Process** (from `app.py`):
```python
def scan_scripts_folder():
    """Auto-discover scripts and extract metadata from docstrings."""
    for script_file in scripts_dir.glob('*.py'):
        script_id = script_file.stem
        metadata = extract_script_metadata(script_file)
        
        db.upsert_script_config(
            script_id=script_id,
            name=metadata.get('name', script_id.replace('_', ' ').title()),
            description=metadata.get('description', ''),
            script_path=str(script_file),
            supports_dry_run=metadata.get('supports_dry_run', True),
            section=metadata.get('section', 'commands')
        )
```

---

### 4. `app_settings` - Configuration Storage

Key-value store for application settings with web UI management.

```sql
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,                       -- Setting key (e.g., 'CLEANUP_DAYS')
    value TEXT NOT NULL,                        -- Setting value
    description TEXT,                           -- Human-readable description
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Settings Priority** (highest to lowest):
1. Database (`app_settings` table)
2. Environment variables
3. Hardcoded defaults

**Usage Pattern:**
```python
from settings import get_setting, set_setting

# Get with fallback chain
cleanup_days = get_setting('CLEANUP_DAYS', default='30', env_fallback=True)
# Checks: DB → ENV → default

# Set in database
set_setting('CLEANUP_DAYS', '45', description='Days before file expiry')
```

---

### 5. `scheduled_jobs` - Internal Scheduler

Cron-like automation for periodic script execution.

```sql
CREATE TABLE scheduled_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id TEXT NOT NULL UNIQUE,             -- FK to script_configs
    script_name TEXT NOT NULL,
    script_path TEXT NOT NULL,
    enabled BOOLEAN DEFAULT FALSE,              -- Whether job is active
    interval_type TEXT NOT NULL DEFAULT 'minutes', -- 'minutes', 'hours', 'days'
    interval_value INTEGER NOT NULL DEFAULT 60, -- Interval amount
    next_run TIMESTAMP,                         -- When to run next
    last_run TIMESTAMP,                         -- Last execution time
    last_run_status TEXT,                       -- 'success', 'failed'
    last_run_duration REAL,                     -- Duration in seconds
    run_count INTEGER DEFAULT 0,                -- Total runs
    error_count INTEGER DEFAULT 0,              -- Total failures
    last_error TEXT,                            -- Last error message
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_scheduled_jobs_enabled ON scheduled_jobs(enabled);
CREATE INDEX idx_scheduled_jobs_next_run ON scheduled_jobs(next_run);
CREATE INDEX idx_scheduled_jobs_script_id ON scheduled_jobs(script_id);
```

**Scheduler Logic** (from `scheduler.py`):
```python
def _run_scheduler():
    """Check for due jobs every 60 seconds."""
    while self.running:
        try:
            due_jobs = self._get_due_jobs()
            for job in due_jobs:
                self._execute_job(job)
                self._calculate_next_run(job)
            time.sleep(60)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
```

---

### 6. `expiring_albums` - File Expiry Tracking

Tracks albums for potential cleanup with starred protection.

```sql
CREATE TABLE expiring_albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_key TEXT NOT NULL UNIQUE,             -- MBID or hash of artist+album
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    directory TEXT NOT NULL,                    -- Parent directory path
    file_count INTEGER NOT NULL,                -- Number of tracks
    total_size_mb REAL NOT NULL,                -- Total album size
    is_starred BOOLEAN DEFAULT FALSE,           -- Protected from deletion
    first_detected TIMESTAMP NOT NULL,          -- For age calculation
    last_seen TIMESTAMP NOT NULL,               -- Updated on each scan
    status TEXT DEFAULT 'pending',              -- 'pending', 'deleted', 'starred'
    deleted_at TIMESTAMP,
    album_art_url TEXT,                         -- Cover art URL
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_expiring_albums_status ON expiring_albums(status);
CREATE INDEX idx_expiring_albums_last_seen ON expiring_albums(last_seen);
```

**Usage in Scripts:**
```python
# Upsert album during scan
db.upsert_expiring_album({
    'album_key': album_mbid or hash(f"{artist}{album}"),
    'artist': artist,
    'album': album,
    'directory': str(album_dir),
    'file_count': len(tracks),
    'total_size_mb': total_size / (1024 * 1024),
    'is_starred': check_navidrome_starred(album_mbid),
    'first_detected': existing_first_detected or datetime.now(),
    'last_seen': datetime.now()
})
```

---

### 7. `album_tracks` - Track-Level Details

Individual file details for expiring albums with starred status.

```sql
CREATE TABLE album_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,                  -- FK to expiring_albums
    file_path TEXT NOT NULL,                    -- Full file path
    file_name TEXT NOT NULL,                    -- Filename only
    track_title TEXT,                           -- Track metadata
    track_number INTEGER,
    track_artist TEXT,
    file_size_mb REAL NOT NULL,
    days_old INTEGER NOT NULL,                  -- Age in days
    last_modified TIMESTAMP NOT NULL,
    is_starred BOOLEAN DEFAULT FALSE,           -- Track-level starred status
    navidrome_id TEXT,                          -- Navidrome track ID
    year INTEGER,                               -- Release year
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (album_id) REFERENCES expiring_albums(id) ON DELETE CASCADE,
    UNIQUE(album_id, file_path)
);

-- Indexes
CREATE INDEX idx_album_tracks_album_id ON album_tracks(album_id);
CREATE INDEX idx_album_tracks_days_old ON album_tracks(days_old);
CREATE INDEX idx_album_tracks_is_starred ON album_tracks(is_starred);
CREATE INDEX idx_album_tracks_navidrome_id ON album_tracks(navidrome_id);
```

**Deletion Logic** (from `file_expiry_cleanup.py`):
```python
# Only delete if:
# 1. Album is not starred
# 2. Track is not starred (checked individually)
# 3. Age exceeds CLEANUP_DAYS
if not album['is_starred']:
    for track in album_tracks:
        if not track['is_starred'] and track['days_old'] > cleanup_days:
            if not dry_run:
                os.remove(track['file_path'])
                db.mark_track_deleted(track['id'])
```

---

### 8. `playlist_tracks` - Playlist Monitoring

Tracks from Spotify/Tidal playlists for automated downloading.

```sql
CREATE TABLE playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_id TEXT NOT NULL UNIQUE,            -- Spotify/Tidal track ID
    artist TEXT NOT NULL,
    title TEXT NOT NULL,
    album TEXT,
    year INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',     -- 'pending', 'downloading', 'found', 'failed'
    slskd_id TEXT,                              -- slskd download ID
    navidrome_id TEXT,                          -- Navidrome ID if found
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_playlist_tracks_spotify_id ON playlist_tracks(spotify_id);
CREATE INDEX idx_playlist_tracks_status ON playlist_tracks(status);
```

---

## Database Manager Usage

### Context Manager Pattern

**Always use context managers** for database operations to ensure proper connection handling:

```python
from database import get_db

db = get_db()

# Automatic connection management
with db.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM script_executions WHERE status = ?", ('running',))
    results = cursor.fetchall()
    
    # Access as dict
    for row in results:
        print(f"Script: {row['script_name']}, Started: {row['start_time']}")
    
    # Modifications auto-commit on success
    cursor.execute("UPDATE script_executions SET status = ? WHERE id = ?", ('completed', 123))
    conn.commit()
    
# Connection auto-closes, rollback on exception
```

### Common Operations

```python
# Execution tracking
execution_id = db.create_execution('organise_files', 'Organise Files', dry_run=False)
db.update_execution(execution_id, status='completed', return_code=0, duration_seconds=45.2)
db.mark_execution_stopped(execution_id)

# Log management
db.add_script_log(execution_id, line_number=1, content="Starting...", log_level='info')
db.add_script_logs_batch(log_buffer)  # Batch insert for performance
logs = db.get_execution_logs(execution_id, limit=100)

# Recent executions
executions = db.get_recent_executions(limit=50)
stats = db.get_execution_stats(script_id)

# Expiry albums
db.upsert_expiring_album(album_data)
albums = db.get_expiring_albums(days_threshold=30, include_starred=False)
tracks = db.get_album_tracks(album_id)

# Playlist tracks
db.add_playlist_track(spotify_id, artist, title, album)
db.update_playlist_track_status(spotify_id, 'downloading', slskd_id='abc123')
pending = db.get_pending_playlist_tracks()

# Cleanup
db.cleanup_old_logs(days=30)  # Remove logs older than 30 days
db.mark_orphaned_executions()  # Mark stale "running" executions on startup
```

---

## Migration System

### Version-Based Migrations

Migrations use `PRAGMA user_version` to track schema version. Current version: **8**

**Migration Pattern** (in `database.py` `create_tables()` method):

```python
def create_tables(self, conn: sqlite3.Connection):
    cursor = conn.cursor()
    
    # Check current version
    cursor.execute("PRAGMA user_version")
    current_version = cursor.fetchone()[0]
    
    # Create all base tables
    cursor.execute("CREATE TABLE IF NOT EXISTS ...")
    
    # Apply migrations sequentially
    if current_version < 2:
        logger.info("Running migration to add album_art_url column...")
        try:
            cursor.execute("ALTER TABLE expiring_albums ADD COLUMN album_art_url TEXT")
            cursor.execute("PRAGMA user_version = 2")
            logger.info("Successfully added album_art_url column")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                cursor.execute("PRAGMA user_version = 2")
                logger.info("Column already exists, updating version")
            else:
                raise
    
    if current_version < 3:
        logger.info("Running migration to add track starred tracking...")
        columns_to_add = [
            ('is_starred', 'BOOLEAN DEFAULT FALSE'),
            ('navidrome_id', 'TEXT'),
            ('year', 'INTEGER')
        ]
        for col_name, col_type in columns_to_add:
            try:
                cursor.execute(f"ALTER TABLE album_tracks ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        
        # Add indexes for new columns
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_is_starred ON album_tracks(is_starred)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_tracks_navidrome_id ON album_tracks(navidrome_id)")
        cursor.execute("PRAGMA user_version = 3")
    
    conn.commit()
```

### Adding a New Migration

When modifying the schema:

1. **Increment the version number** at the end of migrations
2. **Use graceful fallbacks** for existing columns (check for "duplicate column" error)
3. **Log migration progress** for visibility in container logs
4. **Test migration** by:
   - Backing up database: `cp work/soulseekarr.db work/soulseekarr.db.backup`
   - Restarting container
   - Checking logs for migration messages
   - Verifying schema: `sqlite3 work/soulseekarr.db ".schema table_name"`

**Example: Adding a new column**

```python
if current_version < 9:
    logger.info("Running migration to add priority column...")
    try:
        cursor.execute("ALTER TABLE scheduled_jobs ADD COLUMN priority INTEGER DEFAULT 5")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_priority ON scheduled_jobs(priority)")
        cursor.execute("PRAGMA user_version = 9")
        logger.info("Successfully added priority column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            cursor.execute("PRAGMA user_version = 9")
        else:
            raise
```

---

## WAL Mode and Concurrency

### Configuration

WAL mode is enabled on every connection:

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")  # Optimize for WAL
```

### Benefits

- **Concurrent Reads**: Multiple readers don't block each other
- **Non-Blocking Writes**: Readers don't block writers (and vice versa)
- **Better Performance**: Reduced I/O for commits
- **Crash Recovery**: WAL log provides transaction recovery

### Timeout Handling

60-second timeout prevents "database is locked" errors:

```python
conn = sqlite3.connect(self.db_path, timeout=60.0)
```

This allows long-running queries to wait for locks instead of failing immediately.

---

## Best Practices

### ✅ DO:

1. **Use context managers** for all database operations
2. **Batch insert logs** (100 lines or 1 second intervals) for performance
3. **Use parameterized queries** to prevent SQL injection
4. **Check for existing columns** before adding in migrations
5. **Log migration progress** to container logs
6. **Use indexes** on frequently queried columns
7. **Clean up old data** periodically (execution logs, old executions)

### ❌ DON'T:

1. **Don't use string formatting** for SQL queries (use `?` placeholders)
2. **Don't forget to commit** after modifications
3. **Don't leave connections open** (use context managers)
4. **Don't add migrations without version checks**
5. **Don't modify schema directly** (always use migrations)
6. **Don't assume column exists** (check in migrations)

---

## Testing Database Changes

After modifying the database schema or adding migrations:

1. **Make your changes** to `database.py`
2. **Instruct the user:**
   ```
   Please restart the `soulseekarr` container in Portainer.
   
   After restart, check the logs for:
   - "Running migration to add [description]..."
   - "Successfully [description]"
   - "Database initialized successfully"
   
   If you see errors related to database migrations, please share them.
   ```

3. **Verify the migration** (user should run):
   ```bash
   # Check schema version
   sqlite3 /mnt/storage/AppData/navidrome-cleanup/work/soulseekarr.db "PRAGMA user_version"
   
   # View table schema
   sqlite3 /mnt/storage/AppData/navidrome-cleanup/work/soulseekarr.db ".schema table_name"
   ```

4. **Test functionality** through the web UI or script execution

---

## Common Database Queries

### Execution History

```python
# Get all running executions
with db.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM script_executions 
        WHERE status = 'running' 
        ORDER BY start_time DESC
    """)
    running = cursor.fetchall()

# Get execution statistics
cursor.execute("""
    SELECT 
        script_name,
        COUNT(*) as total_runs,
        AVG(duration_seconds) as avg_duration,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successes,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failures
    FROM script_executions
    WHERE start_time > datetime('now', '-30 days')
    GROUP BY script_name
""")
stats = cursor.fetchall()
```

### Expiring Albums

```python
# Get albums expiring soon (not starred, older than threshold)
cursor.execute("""
    SELECT * FROM expiring_albums
    WHERE status = 'pending'
    AND is_starred = FALSE
    AND julianday('now') - julianday(first_detected) > ?
    ORDER BY first_detected ASC
""", (cleanup_days,))
expiring = cursor.fetchall()

# Get tracks for deletion check
cursor.execute("""
    SELECT * FROM album_tracks
    WHERE album_id = ?
    AND is_starred = FALSE
    AND days_old > ?
""", (album_id, cleanup_days))
tracks = cursor.fetchall()
```

### Scheduled Jobs

```python
# Get due jobs
cursor.execute("""
    SELECT * FROM scheduled_jobs
    WHERE enabled = TRUE
    AND (next_run IS NULL OR next_run <= datetime('now'))
    ORDER BY next_run ASC
""")
due_jobs = cursor.fetchall()
```
