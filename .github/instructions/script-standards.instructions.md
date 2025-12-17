---
applyTo: '**'
---

# Script Development Standards

## Overview

All scripts in the `scripts/` directory are automatically discovered by SoulSeekarr and displayed in the web UI. Following these standards ensures proper integration, consistent user experience, and reliable operation.

---

## Required Docstring Metadata

Every script **MUST** include a docstring at the top of the file with standardized metadata for auto-discovery.

### Metadata Format

```python
#!/usr/bin/env python3
"""
[Brief description of what the script does - one or two sentences]

[Optional longer description with details about the script's functionality,
workflow, and any important notes about its operation]

Name: Display Name for UI
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: tag1, tag2, tag3, tag4
Supports dry run: true
"""
```

### Metadata Fields

| Field | Required | Values | Description |
|-------|----------|--------|-------------|
| **Name** | Yes | Any string | Display name shown in UI (e.g., "Organise Files") |
| **Author** | No | String | Script author (default: "SoulSeekarr") |
| **Version** | No | Semver | Version number (e.g., "1.0", "2.1.3") |
| **Section** | Yes | `commands` or `tests` | UI category (commands = production, tests = utilities) |
| **Tags** | Yes | Comma-separated | Keywords for categorization and search |
| **Supports dry run** | Yes | `true` or `false` | Whether script supports `--dry-run` flag |

### Example: Complete Docstring

```python
#!/usr/bin/env python3
"""
Organise Files v5.0 - Track Database Approach

Comprehensive music file organization using a track database approach.
Scans all directories first to build a complete picture, then organizes albums properly.

Directory Logic:
- Downloads: New files - metadata checked, files with incomplete metadata stay here
- Owned: Protected directory - only check for missing tracks, never move/delete  
- Not_Owned: Should contain only complete albums after processing
- Incomplete: Should contain incomplete albums and files with valid but incomplete metadata

Name: Organise Files
Author: SoulSeekarr
Version: 5.0
Section: commands
Tags: organization, lidarr, cleanup, duplicates, downloads, metadata, track-database
Supports dry run: true
"""
```

---

## Dry-Run Mode Implementation

**All scripts that modify files, APIs, or databases MUST support dry-run mode.**

### Standard Pattern

```python
import argparse

def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description='Script description')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Run without making actual changes')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No files will be moved or modified")
        logger.info("")
    
    # Use throughout script
    if some_condition:
        if dry_run:
            logger.info(f"[DRY RUN] Would move: {source} → {destination}")
        else:
            shutil.move(source, destination)
            logger.info(f"Moved: {source} → {destination}")
```

### Dry-Run in Service Clients

When using `LidarrClient` or `SlskdDownloader`, pass dry-run flag:

```python
from lidarr_utils import LidarrClient

# Initialize with dry-run
lidarr = LidarrClient(lidarr_url, lidarr_api_key, logger, dry_run=args.dry_run)

# Client methods automatically respect dry-run
lidarr.add_artist_with_future_monitoring("Artist Name")
# In dry-run: Logs "DRY RUN: Would add artist..."
# In normal mode: Actually adds artist
```

### Dry-Run Checklist

✅ All file operations (move, delete, rename)  
✅ All API calls (Lidarr, slskd, Navidrome)  
✅ All database modifications  
✅ Clear logging of what *would* happen  
✅ Flag passed to all helper functions/clients  

❌ Don't skip: Read operations (safe to run)  
❌ Don't skip: Progress reporting (needed for UI)  
❌ Don't skip: Logging (needed for verification)  

---

## Progress Reporting for UI Integration

The Flask app parses specific progress formats and broadcasts them via Server-Sent Events (SSE) to the web UI for real-time monitoring.

### Progress Format Patterns

#### Main Progress Line

Format: `PROGRESS: [current/total] percentage% - Processing: description`

```python
total = len(items)
for i, item in enumerate(items):
    percentage = int((i + 1) / total * 100)
    print(f"PROGRESS: [{i + 1}/{total}] {percentage}% - Processing: {item.artist} - {item.album}")
```

**Parsed by Flask as:**
```json
{
    "current": 123,
    "total": 456,
    "percentage": 67,
    "message": "Processing: Artist Name - Album Name"
}
```

#### Sub-Progress Line

Format: `PROGRESS_SUB: sub-task description`

```python
print(f"PROGRESS_SUB: Getting track listing for {album_name}...")
print(f"PROGRESS_SUB: Checking Navidrome starred status...")
```

**Use for:**
- Detailed steps within main progress
- API calls or long-running sub-operations
- Providing context without updating main counter

#### Legacy Format (Still Supported)

Format: `[current/total] Processing: description`

```python
print(f"[{i + 1}/{total}] Processing: {artist_name}")
```

### Complete Progress Example

```python
from tqdm import tqdm

albums = get_all_albums()
total = len(albums)

if TQDM_AVAILABLE:
    iterator = tqdm(albums, desc="Processing albums")
else:
    iterator = albums

for i, album in enumerate(iterator):
    percentage = int((i + 1) / total * 100)
    print(f"PROGRESS: [{i + 1}/{total}] {percentage}% - Processing: {album['artist']} - {album['name']}")
    
    # Sub-tasks
    print(f"PROGRESS_SUB: Fetching track listing from Lidarr...")
    tracks = lidarr.get_album_tracks(album['id'])
    
    print(f"PROGRESS_SUB: Checking for existing files...")
    existing = check_existing_files(tracks)
    
    # Update tqdm if available
    if TQDM_AVAILABLE:
        iterator.set_postfix(artist=album['artist'][:30])
```

---

## Progress Bars with tqdm

Use `tqdm` for terminal progress bars with graceful degradation if not available.

### Standard Pattern

```python
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Usage
items = get_items_to_process()

if TQDM_AVAILABLE:
    iterator = tqdm(items, desc="Processing items", unit="item")
else:
    iterator = items

for item in iterator:
    process_item(item)
    
    # Update postfix (only if tqdm available)
    if TQDM_AVAILABLE:
        iterator.set_postfix(current=item.name[:30])
```

### Multiple Progress Bars

```python
if TQDM_AVAILABLE:
    albums_bar = tqdm(albums, desc="Albums", position=0)
    tracks_bar = None
else:
    albums_bar = albums
    tracks_bar = None

for album in albums_bar:
    tracks = get_tracks(album)
    
    if TQDM_AVAILABLE:
        tracks_bar = tqdm(tracks, desc=f"  Tracks: {album.name}", 
                         position=1, leave=False)
        track_iterator = tracks_bar
    else:
        track_iterator = tracks
    
    for track in track_iterator:
        process_track(track)
    
    if tracks_bar:
        tracks_bar.close()
```

---

## Action Logging Integration

Use `ActionLogger` to log all significant actions for the Activity tab.

### Import and Setup

```python
from action_logger import (
    log_action,
    log_script_start,
    log_script_complete,
    log_file_operation,
    log_api_call,
    log_download
)

# At script start
start_time = time.time()
log_script_start("Organise Files", f"Parameters: {args}")
```

### Action Types

#### Script Lifecycle

```python
# At start
log_script_start("Script Name", "Parameters: --dry-run")

# At completion
duration = time.time() - start_time
try:
    # ... script work ...
    log_script_complete("Script Name", duration, success=True)
except Exception as e:
    log_script_complete("Script Name", duration, success=False, error=str(e))
```

#### File Operations

```python
# File move
log_file_operation(
    operation="move",
    source=str(source_path),
    target=str(dest_path),
    status="success",
    details=f"Moved complete album: {artist} - {album}"
)

# File delete
log_file_operation(
    operation="delete",
    source=str(file_path),
    target=None,
    status="success" if deleted else "failed",
    details=f"Deleted expired file: {file_name}"
)
```

#### API Calls

```python
log_api_call(
    service="Lidarr",
    endpoint="/api/v1/artist",
    method="POST",
    status="success",
    details=f"Added artist: {artist_name} (MBID: {mbid})"
)

log_api_call(
    service="Navidrome",
    endpoint="getStarred2",
    method="GET",
    status="success",
    details=f"Retrieved {len(starred)} starred albums"
)
```

#### Downloads

```python
log_download(
    item_name=f"{artist} - {title}",
    source="slskd",
    status="queued",
    details=f"Download queued with ID: {download_id}"
)
```

#### Generic Actions

```python
log_action(
    action_type="database_update",
    source="File Organiser",
    target="expiring_albums",
    details=f"Updated {count} album entries",
    status="success",
    duration=elapsed_time
)
```

---

## Graceful Interruption Handling

Allow users to stop scripts cleanly with Ctrl+C or container stop.

### Signal Handler Pattern

```python
import signal
import sys

# Global flag
interrupted = False

def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    global interrupted
    interrupted = True
    logger.warning("")
    logger.warning("⚠️ Script interrupted by user")
    logger.warning("Finishing current operation and exiting...")

# Register handlers
signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Container stop

# Main processing loop
for item in items:
    if interrupted:
        logger.info("Stopped by user request")
        print_summary(interrupted=True)
        sys.exit(130)  # Standard exit code for SIGINT
    
    process_item(item)
```

### Cleanup on Interruption

```python
def cleanup_and_exit(interrupted=False):
    """Perform cleanup before exiting."""
    if interrupted:
        logger.info("")
        logger.info("=" * 60)
        logger.info("INTERRUPTED - Partial Results")
        logger.info("=" * 60)
    
    # Log statistics
    logger.info(f"Processed: {processed_count}/{total_count}")
    logger.info(f"Succeeded: {success_count}")
    logger.info(f"Failed: {failed_count}")
    
    # Close database connections
    if db:
        db.close()
    
    # Exit with appropriate code
    sys.exit(130 if interrupted else 0)

# Use in exception handler
try:
    main_processing()
except KeyboardInterrupt:
    cleanup_and_exit(interrupted=True)
except Exception as e:
    logger.error(f"Fatal error: {e}")
    cleanup_and_exit(interrupted=False)
```

---

## Settings Integration

Use `SettingsManager` for configuration with database → environment → default fallback.

### Import Pattern

```python
from settings import (
    get_lidarr_config,
    get_navidrome_config,
    get_slskd_config,
    get_owned_directory,
    get_not_owned_directory,
    get_incomplete_directory,
    get_downloads_completed_directory,
    get_setting,
    get_target_uid,
    get_target_gid,
    is_dry_run
)

# Service configs
lidarr_config = get_lidarr_config()
lidarr_url = lidarr_config.get('url')
lidarr_api_key = lidarr_config.get('api_key')

# Directory paths
owned_dir = Path(get_owned_directory())
not_owned_dir = Path(get_not_owned_directory())

# Individual settings with fallback
cleanup_days = int(get_setting('CLEANUP_DAYS', default='30', env_fallback=True))
max_downloads = int(get_setting('MAX_CONCURRENT_DOWNLOADS', default='3', env_fallback=True))
```

### Graceful Degradation

```python
try:
    from settings import get_lidarr_config
    SETTINGS_AVAILABLE = True
    lidarr_config = get_lidarr_config()
    lidarr_url = lidarr_config.get('url')
except ImportError:
    SETTINGS_AVAILABLE = False
    lidarr_url = None

# Fallback to environment
if not lidarr_url:
    lidarr_url = os.environ.get('LIDARR_URL')
    if not lidarr_url:
        logger.error("Lidarr URL not configured")
        sys.exit(1)
```

---

## Database Integration

Integrate with database for persistence where appropriate.

### Import Pattern

```python
try:
    from database import get_db
    DATABASE_AVAILABLE = True
    db = get_db()
except ImportError as e:
    logger.warning(f"Database not available: {e}")
    DATABASE_AVAILABLE = False
    db = None

# Usage
if DATABASE_AVAILABLE and db:
    db.upsert_expiring_album(album_data)
else:
    logger.debug("Database not available, skipping persistence")
```

### Batch Database Operations

For performance, batch inserts when processing many items:

```python
album_batch = []
BATCH_SIZE = 100

for album in albums:
    album_batch.append({
        'album_key': album.mbid,
        'artist': album.artist,
        'album': album.name,
        # ... other fields
    })
    
    # Insert every 100 albums
    if len(album_batch) >= BATCH_SIZE:
        if DATABASE_AVAILABLE and db:
            for album_data in album_batch:
                db.upsert_expiring_album(album_data)
        album_batch.clear()

# Insert remaining
if album_batch and DATABASE_AVAILABLE and db:
    for album_data in album_batch:
        db.upsert_expiring_album(album_data)
```

---

## Logging Setup

Use standard Python logging with file and console output.

### Standard Pattern

```python
import logging
from pathlib import Path
from datetime import datetime

# Determine log directory
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)

# Create timestamped log file
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'script_name_{timestamp}.log'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',  # Simple format for better UI readability
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)  # Both file and console
    ]
)
logger = logging.getLogger(__name__)

# Log file location
logger.info(f"Log file: {log_file}")
```

### Log Levels

```python
logger.debug("Detailed diagnostic information")     # Not shown by default
logger.info("Normal operation messages")            # Default level
logger.warning("Warning: potential issue")          # Highlighted in UI
logger.error("Error: operation failed")             # Red in UI
logger.critical("Critical: cannot continue")        # Fatal errors
```

---

## Complete Script Template

```python
#!/usr/bin/env python3
"""
[Brief description]

[Optional detailed description]

Name: Script Display Name
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: tag1, tag2, tag3
Supports dry run: true
"""

import os
import sys
import time
import signal
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Import dependencies with error handling
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import project modules
from settings import get_setting, get_lidarr_config
from action_logger import log_script_start, log_script_complete, log_action

try:
    from database import get_db
    DATABASE_AVAILABLE = True
    db = get_db()
except ImportError:
    DATABASE_AVAILABLE = False
    db = None

# Setup logging
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'script_name_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Graceful interruption
interrupted = False

def signal_handler(signum, frame):
    global interrupted
    interrupted = True
    logger.warning("\n⚠️ Script interrupted by user")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    """Main script execution."""
    # Parse arguments
    parser = argparse.ArgumentParser(description='Script description')
    parser.add_argument('--dry-run', action='store_true', help='Run without making changes')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    # Header
    logger.info("=" * 60)
    logger.info("SCRIPT NAME v1.0")
    logger.info("=" * 60)
    logger.info("")
    
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No changes will be made")
        logger.info("")
    
    # Log script start
    start_time = time.time()
    log_script_start("Script Name", f"Parameters: {'--dry-run' if dry_run else 'normal'}")
    
    try:
        # Main processing
        items = get_items_to_process()
        total = len(items)
        
        if TQDM_AVAILABLE:
            iterator = tqdm(items, desc="Processing")
        else:
            iterator = items
        
        for i, item in enumerate(iterator):
            if interrupted:
                logger.info("Stopped by user request")
                break
            
            # Progress for UI
            percentage = int((i + 1) / total * 100)
            print(f"PROGRESS: [{i + 1}/{total}] {percentage}% - Processing: {item.name}")
            
            # Process item
            process_item(item, dry_run=dry_run)
        
        # Success
        duration = time.time() - start_time
        log_script_complete("Script Name", duration, success=True)
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        
    except Exception as e:
        duration = time.time() - start_time
        log_script_complete("Script Name", duration, success=False, error=str(e))
        logger.error(f"❌ Error: {e}")
        raise


def process_item(item, dry_run=False):
    """Process a single item."""
    logger.info(f"Processing: {item.name}")
    
    if dry_run:
        logger.info(f"[DRY RUN] Would perform action on: {item.name}")
    else:
        # Actual processing
        perform_action(item)
        log_action(
            action_type="item_processed",
            source="Script Name",
            target=item.name,
            status="success"
        )


if __name__ == '__main__':
    main()
```

---

## Testing New Scripts

After creating or modifying a script:

1. **Save the script** in the `scripts/` directory
2. **Instruct the user:**
   ```
   Please restart the `soulseekarr` container in Portainer.
   
   After restart:
   1. Check the logs for "Discovered script: Script Name"
   2. Open http://localhost:5000
   3. Verify the script appears in the Commands section
   4. Click "Dry Run" to test without making changes
   5. Check execution logs for proper progress reporting
   
   Let me know if you see any errors or if the script doesn't appear.
   ```

3. **Verify script appears** in web UI
4. **Test dry-run mode** first
5. **Check logs** for proper formatting (progress, action logging)
6. **Run normally** after verification

---

## Common Patterns Summary

### ✅ DO:

- Include complete docstring metadata
- Implement dry-run mode for all modifying operations
- Use progress reporting (`PROGRESS:` format)
- Integrate with `ActionLogger`
- Handle graceful interruption
- Use tqdm with graceful degradation
- Batch database operations
- Use settings with fallback chain
- Log to both file and console

### ❌ DON'T:

- Hardcode configuration values
- Skip dry-run implementation
- Block without progress updates
- Ignore keyboard interrupts
- Assume dependencies exist
- Make API calls without dry-run checks
- Leave database connections open
- Use complex log formats (keep simple for UI parsing)
