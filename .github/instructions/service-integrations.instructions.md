---
applyTo: '**'
---

# Service Integration Patterns

## Overview

SoulSeekarr integrates with multiple external services for music management. This document provides detailed usage patterns for each service integration.

---

## Lidarr Integration

### LidarrClient Class

Located in `lidarr_utils.py`, this class provides a reusable interface for all Lidarr API interactions.

### Initialization

```python
from lidarr_utils import LidarrClient

# Initialize client
client = LidarrClient(
    lidarr_url="http://localhost:8686",  # Or from settings
    lidarr_api_key="your_api_key",       # Or from settings
    logger=logger,                        # Logger instance
    dry_run=False                         # Enable for testing
)

# Test connection
if client.test_connection():
    logger.info("Connected to Lidarr successfully")
```

### Configuration Caching

The client caches expensive configuration queries for performance:

```python
# These are cached after first call
quality_profiles = client.get_quality_profiles()
# Returns: [{'id': 1, 'name': 'Lossless'}, ...]

root_folders = client.get_root_folders()
# Returns: [{'id': 1, 'path': '/music/', 'accessible': True}, ...]

metadata_profiles = client.get_metadata_profiles()
# Returns: [{'id': 1, 'name': 'Standard'}, ...]
```

**Caching Details:**
- Configuration fetched once per client instance
- Stored in private instance variables (`_quality_profiles`, etc.)
- No expiration - recreate client to refresh
- Safe to call repeatedly without performance penalty

### Adding Artists with Monitoring

**Primary Method:** `add_artist_with_future_monitoring()`

This method intelligently adds artists with "future releases only" monitoring:

```python
success = client.add_artist_with_future_monitoring(
    artist_name="Artist Name",
    quality_profile_name=None,     # Uses first available if None
    root_folder_path=None,         # Uses first available if None
    metadata_profile_name=None,    # Uses first available if None
    search_for_existing=True,      # Search for existing albums after adding
    monitored_initial=True,        # Monitor the artist initially
    search_on_add=False            # Don't auto-search (we control this)
)
```

**Workflow:**
1. Search MusicBrainz for artist metadata (exact name match)
2. Check if artist already exists in Lidarr
3. Resolve configuration (quality profile, root folder, metadata profile)
4. Add artist to Lidarr via API
5. Get artist's album list
6. Mark all past releases as unmonitored
7. Mark all future releases as monitored
8. Optionally trigger search for monitored albums

**Dry-Run Behavior:**
```python
client = LidarrClient(..., dry_run=True)
client.add_artist_with_future_monitoring("Artist Name")
# Logs: "DRY RUN: Would add artist 'Artist Name' (MBID: xxx)"
# Returns: True (simulated success)
# No actual API calls for modifications
```

### MusicBrainz Integration

The client includes sophisticated MusicBrainz searching:

```python
mb_artist = client.search_musicbrainz_artist("Jay-Z")
# Returns: {
#     'musicbrainz_id': 'f82bcf78-5b69-4622-a5ef-73800768d9ac',
#     'name': 'JAY-Z',
#     'sort_name': 'JAY-Z',
#     'disambiguation': 'US rapper',
#     'type': 'Person',
#     'score': 100
# }
```

**Matching Logic:**
1. **Exact match**: Name matches exactly (case-insensitive)
2. **Close match**: Handles variations like:
   - Spaces vs hyphens: "Jay Z" ↔ "Jay-Z"
   - Special characters: "Björk" ↔ "Bjork"
3. **High score match**: Score ≥ 90 (MusicBrainz confidence)

**Rate Limiting:**
- Built-in delay between requests (respects MusicBrainz guidelines)
- User-Agent header: `LidarrUtils/1.0`

### Album Operations

```python
# Get albums for artist
albums = client.get_artist_albums(artist_id=123)
# Returns: [{'id': 456, 'title': 'Album Name', 'monitored': True}, ...]

# Set monitoring status
client.set_album_monitoring(
    album_id=456,
    monitored=False
)
# In dry-run: Logs "DRY RUN: Would set album monitoring..."

# Search for missing albums
client.search_for_missing_albums(artist_id=123)
# Triggers Lidarr to search for monitored missing albums
```

### Artist Lookup

```python
# Find existing artist by MBID
artist = client.lookup_artist_in_lidarr(mbid="f82bcf78-...")
# Returns: {'id': 123, 'artistName': 'JAY-Z', ...} or None

# Find artist by name
artist = client.find_artist_by_name("JAY-Z")
# Returns: {'id': 123, ...} or None

# Normalize names for matching
normalized = client.normalize_artist_name("The Beatles")
# Returns: "beatles" (removes "the", lowercases, removes spaces)
```

### Wanted Albums (Cutoff Unmet)

```python
# Get albums not meeting quality cutoff
wanted = client.get_cutoff_unmet_albums()
# Returns: [{
#     'id': 789,
#     'title': 'Album Name',
#     'artist': {'artistName': 'Artist'},
#     'monitored': True,
#     'statistics': {'trackCount': 12}
# }, ...]
```

### Dry-Run Support

**All modification methods respect dry-run mode:**

```python
client = LidarrClient(..., dry_run=True)

# These log what would happen but make no changes:
client.add_artist_with_future_monitoring("Artist")
client.set_album_monitoring(album_id, False)
client.search_for_missing_albums(artist_id)

# Read operations still work normally:
artists = client.get_artists()  # Actual API call
albums = client.get_artist_albums(artist_id)  # Actual API call
```

---

## slskd Integration (Soulseek)

### SlskdDownloader Class

Located in `slskd_utils.py`, this class handles search and download operations with sophisticated matching logic.

### Initialization

```python
from slskd_utils import SlskdDownloader

downloader = SlskdDownloader(
    slskd_url="http://localhost:5030",
    slskd_api_key="your_api_key",
    logger=logger
)
```

**Note:** No dry-run mode at initialization - handled per-operation.

### Search Workflow

The complete search → match → download workflow:

#### 1. Start Search

```python
search_id = downloader._start_search("Artist Album")
# POST /api/v0/searches
# Returns: search_id string (e.g., "abc123def456")
```

#### 2. Wait for Results

```python
file_count = downloader._wait_for_search_results(search_id)
# Polls status every 4 seconds
# Checks: isComplete flag, file count, response count
# Timeout: 600 seconds (10 minutes)
# Early exit: If 300s elapsed and files > 0
```

**Status Logging:**
```
Search progress: 15 users, 147 files, complete: False (20s)
Search completed after 45s with 231 files
```

#### 3. Fetch Results

```python
results = downloader._get_search_results(search_id)
# GET /api/v0/searches/{search_id}/responses
# Returns: [{'username': '...', 'files': [...], 'uploadSpeed': ...}, ...]
```

#### 4. Find Best Match

**For Albums:**
```python
candidates = downloader._find_best_album_match(results, album_name="Album Name")
# Returns sorted list of album candidates with scores
```

**Scoring Algorithm:**
```python
score = 0
score += 10 if album_name matches directory
score += 40 if has FLAC files
score += len(audio_files)  # File count (prefer complete albums)
score += min(avg_bitrate // 32, 10)  # Bitrate bonus
score += min(upload_speed // 102400, 30)  # Speed bonus
score -= 20 if compilation (and not wanted)
```

**For Songs:**
```python
candidates = downloader._find_best_song_match(
    results,
    search_query="Artist Song",
    target_title="Song Title"
)
```

**Song Scoring:**
```python
score = 0
score += 50 if title exact match (normalized)
score += 100 if free upload slot
score += 50 if queue position < 5
score += min(bitrate / 320 * 30, 30)  # Prefer 320kbps MP3 or FLAC
score -= queue_position  # Penalize high queue
```

#### 5. Download

```python
result = downloader._download_files(username, files)
# POST /api/v0/transfers/downloads/{username}
# Queues download with slskd
# Returns: download info or False
```

### High-Level Methods

**Album Download:**
```python
success = downloader.search_and_download_album(
    artist="Artist Name",
    album="Album Name",
    dry_run=False
)
# Complete workflow: search → match → download
# Returns: True on success, False on failure
```

**Song Download:**
```python
success = downloader.search_and_download_song(
    artist="Artist Name",
    title="Song Title",
    dry_run=False
)
# Searches for specific track
# Returns: True on success, False on failure
```

**Generic Search:**
```python
result = downloader.search_and_download(
    search_query="Artist Album",
    search_type="album",  # or "song", "any"
    target_name="Album Name",  # Optional for scoring
    dry_run=False
)
```

### Download Queue Management

```python
# Check if already downloading
is_queued = downloader.is_downloading_or_completed(
    artist="Artist Name",
    title="Song Title"
)
# Checks states: 'queued', 'initializing', 'requested', 'inprogress'
# Returns: True if already in queue (skip), False otherwise

# Get current downloads
downloads = downloader.get_downloads()
# GET /api/v0/transfers/downloads
# Returns: [{'filename': '...', 'state': '...', 'percentComplete': ...}, ...]

# Get completed downloads
completed = downloader.get_completed_downloads()
# Filters downloads with state: 'completed' or 'succeeded'
```

### Matching Helpers

```python
# Normalize strings for comparison
normalized = downloader._normalize_string("Artist - Song (feat. Other)")
# Returns: "artist song feat other" (lowercase, no special chars)

# Clean song titles
cleaned = downloader._clean_song_title("Song (feat. Artist) - Remix")
# Returns: "Song" (removes features, remix info, etc.)

# Check if file is audio
is_audio = downloader._is_audio_file("song.flac")
# Returns: True for .mp3, .flac, .m4a, .ogg, .opus, .wav
# Returns: False for .jpg, .png, .txt, .nfo, etc.

# Calculate average bitrate
avg = downloader._calculate_avg_bitrate([
    {'bitRate': 320000},
    {'bitRate': 256000}
])
# Returns: 288 (in kbps)
```

### Global Deduplication

The module maintains a global set to prevent duplicate downloads across searches:

```python
# In module scope
DOWNLOADED_TRACKS = set()

# Usage in searches
track_key = f"{artist}:{title}".lower()
if track_key in DOWNLOADED_TRACKS:
    logger.info(f"Already downloaded/queued: {artist} - {title}")
    continue

DOWNLOADED_TRACKS.add(track_key)
downloader.search_and_download_song(artist, title)
```

### Dry-Run Example

```python
# With dry-run enabled
result = downloader.search_and_download_album(
    artist="Artist",
    album="Album",
    dry_run=True
)
# Logs: "[DRY RUN] Would search slskd for: Artist Album"
# Returns: True (simulated success)
# No actual API calls made
```

---

## Navidrome Integration (Subsonic API)

### Authentication

**Always use Subsonic API with MD5 token authentication:**

```python
import hashlib
import random
import string
import requests

def get_subsonic_auth(username, password):
    """Generate Subsonic API authentication parameters."""
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    
    return {
        'u': username,
        't': token,
        's': salt,
        'v': '1.16.1',  # Subsonic API version
        'c': 'SoulSeekarr',  # Client name
        'f': 'json'  # Response format
    }

# Usage
navidrome_url = "http://localhost:4533"
auth_params = get_subsonic_auth("admin", "password")

# Make API call
response = requests.get(
    f"{navidrome_url}/rest/getStarred2",
    params=auth_params
)
```

### Common Operations

#### Get Starred Albums/Tracks

```python
def get_starred_albums(navidrome_url, username, password):
    """Get all starred albums and tracks."""
    auth = get_subsonic_auth(username, password)
    response = requests.get(f"{navidrome_url}/rest/getStarred2", params=auth)
    
    if response.status_code == 200:
        data = response.json()
        starred = data.get('subsonic-response', {}).get('starred2', {})
        
        albums = starred.get('album', [])
        songs = starred.get('song', [])
        
        return {
            'albums': albums,
            'songs': songs
        }
    return None
```

#### Star/Unstar Items

```python
def star_album(navidrome_url, username, password, album_id):
    """Star an album in Navidrome."""
    auth = get_subsonic_auth(username, password)
    params = {**auth, 'albumId': album_id}
    
    response = requests.get(f"{navidrome_url}/rest/star", params=params)
    return response.status_code == 200

def unstar_album(navidrome_url, username, password, album_id):
    """Unstar an album in Navidrome."""
    auth = get_subsonic_auth(username, password)
    params = {**auth, 'albumId': album_id}
    
    response = requests.get(f"{navidrome_url}/rest/unstar", params=params)
    return response.status_code == 200
```

#### Search Library

```python
def search_navidrome(navidrome_url, username, password, query):
    """Search for artists, albums, and songs."""
    auth = get_subsonic_auth(username, password)
    params = {**auth, 'query': query}
    
    response = requests.get(f"{navidrome_url}/rest/search3", params=params)
    
    if response.status_code == 200:
        data = response.json()
        results = data.get('subsonic-response', {}).get('searchResult3', {})
        
        return {
            'artists': results.get('artist', []),
            'albums': results.get('album', []),
            'songs': results.get('song', [])
        }
    return None
```

#### Get Album Details

```python
def get_album_details(navidrome_url, username, password, album_id):
    """Get detailed information about an album."""
    auth = get_subsonic_auth(username, password)
    params = {**auth, 'id': album_id}
    
    response = requests.get(f"{navidrome_url}/rest/getAlbum", params=params)
    
    if response.status_code == 200:
        data = response.json()
        album = data.get('subsonic-response', {}).get('album', {})
        
        return {
            'id': album.get('id'),
            'name': album.get('name'),
            'artist': album.get('artist'),
            'artistId': album.get('artistId'),
            'songCount': album.get('songCount'),
            'duration': album.get('duration'),
            'year': album.get('year'),
            'genre': album.get('genre'),
            'starred': album.get('starred'),  # ISO timestamp if starred
            'songs': album.get('song', [])
        }
    return None
```

### Usage in Scripts

**Check if album is starred (for expiry protection):**

```python
def check_album_starred(album_mbid):
    """Check if album is starred in Navidrome."""
    from settings import get_navidrome_config
    
    config = get_navidrome_config()
    navidrome_url = config.get('url')
    username = config.get('username')
    password = config.get('password')
    
    # Search for album by MBID
    search_results = search_navidrome(navidrome_url, username, password, album_mbid)
    
    if search_results and search_results['albums']:
        album = search_results['albums'][0]
        
        # Check if starred field is present and not null
        return album.get('starred') is not None
    
    return False
```

**Get starred status for multiple items:**

```python
def get_all_starred(navidrome_url, username, password):
    """Get all starred items efficiently."""
    starred_data = get_starred_albums(navidrome_url, username, password)
    
    if not starred_data:
        return set(), set()
    
    # Extract IDs
    starred_album_ids = {album['id'] for album in starred_data['albums']}
    starred_song_ids = {song['id'] for song in starred_data['songs']}
    
    return starred_album_ids, starred_song_ids
```

---

## Settings Manager Integration

### SettingsManager Class

Located in `settings.py`, provides configuration with database → environment → default fallback.

### Service Configuration

```python
from settings import get_lidarr_config, get_navidrome_config, get_slskd_config

# Lidarr
lidarr_config = get_lidarr_config()
# Returns: {'url': 'http://...', 'api_key': '...'}

# Navidrome
navidrome_config = get_navidrome_config()
# Returns: {'url': 'http://...', 'username': '...', 'password': '...'}

# slskd
slskd_config = get_slskd_config()
# Returns: {'url': 'http://...', 'api_key': '...'}
```

**Fallback Chain:**
1. Check `app_settings` table (e.g., `LIDARR_URL`, `LIDARR_API_KEY`)
2. Check environment variables
3. Return None if not found

### Directory Paths

```python
from settings import (
    get_owned_directory,
    get_not_owned_directory,
    get_incomplete_directory,
    get_downloads_completed_directory
)

owned_dir = Path(get_owned_directory())  # /media/Owned
not_owned_dir = Path(get_not_owned_directory())  # /media/Not_Owned
incomplete_dir = Path(get_incomplete_directory())  # /media/Incomplete
downloads_dir = Path(get_downloads_completed_directory())  # /downloads/completed
```

### Individual Settings

```python
from settings import get_setting

# With all fallbacks
cleanup_days = get_setting('CLEANUP_DAYS', default='30', env_fallback=True)
# Checks: DB → ENV → default

# No environment fallback
api_key = get_setting('CUSTOM_API_KEY', default=None, env_fallback=False)
# Checks: DB → default only

# Store setting
from settings import set_setting
set_setting('CLEANUP_DAYS', '45', description='Days before file expiry')
```

### File Permissions

```python
from settings import get_target_uid, get_target_gid

uid = get_target_uid()  # From PUID env var, default 1000
gid = get_target_gid()  # From PGID env var, default 1000

# Use for file operations
os.chown(file_path, uid, gid)
os.chmod(file_path, 0o644)
```

### Global Dry-Run

```python
from settings import is_dry_run

# Check global dry-run setting
if is_dry_run():
    logger.info("Global dry-run mode enabled")
```

---

## Integration Best Practices

### ✅ DO:

1. **Use provided clients** (`LidarrClient`, `SlskdDownloader`) instead of direct API calls
2. **Respect dry-run mode** in all modifying operations
3. **Use settings fallback chain** (DB → ENV → default)
4. **Cache expensive operations** (quality profiles, root folders)
5. **Log API calls** with action_logger for activity tracking
6. **Handle errors gracefully** with try/except and logging
7. **Use Subsonic API** for Navidrome (not native API)
8. **Normalize strings** for matching (handles spaces, special chars)
9. **Check existing state** before adding/modifying (avoid duplicates)
10. **Batch operations** where possible (multiple albums, tracks)

### ❌ DON'T:

1. **Don't make API calls without checking dry-run mode**
2. **Don't hardcode URLs or credentials**
3. **Don't skip error handling** (network issues are common)
4. **Don't use Navidrome native API** (use Subsonic)
5. **Don't assume exact string matches** (use normalization)
6. **Don't ignore rate limiting** (especially MusicBrainz)
7. **Don't fetch configuration repeatedly** (use caching)
8. **Don't mix authentication methods** (stick to Subsonic tokens)

---

## Common Integration Patterns

### Pattern 1: Add Artist to Lidarr from External Source

```python
from lidarr_utils import LidarrClient
from settings import get_lidarr_config

# Initialize
config = get_lidarr_config()
lidarr = LidarrClient(config['url'], config['api_key'], logger, dry_run=args.dry_run)

# Add with future monitoring
success = lidarr.add_artist_with_future_monitoring(
    artist_name="Discovered Artist",
    search_for_existing=True
)

if success:
    log_api_call("Lidarr", "/api/v1/artist", "POST", "success", 
                 f"Added artist: Discovered Artist")
```

### Pattern 2: Download Missing Album via slskd

```python
from slskd_utils import SlskdDownloader
from settings import get_slskd_config

# Initialize
config = get_slskd_config()
downloader = SlskdDownloader(config['url'], config['api_key'], logger)

# Check if already downloading
if not downloader.is_downloading_or_completed(artist, album):
    # Search and download
    success = downloader.search_and_download_album(
        artist=artist,
        album=album,
        dry_run=args.dry_run
    )
    
    if success:
        log_download(f"{artist} - {album}", "slskd", "queued", 
                    "Download started successfully")
```

### Pattern 3: Protect Starred Albums from Deletion

```python
from settings import get_navidrome_config

def is_album_starred(album_mbid):
    """Check if album is starred in Navidrome."""
    config = get_navidrome_config()
    auth = get_subsonic_auth(config['username'], config['password'])
    
    # Get all starred albums
    response = requests.get(
        f"{config['url']}/rest/getStarred2",
        params=auth
    )
    
    if response.status_code == 200:
        data = response.json()
        starred_albums = data.get('subsonic-response', {}).get('starred2', {}).get('album', [])
        
        # Check if our album is in the list
        for album in starred_albums:
            if album.get('musicBrainzId') == album_mbid:
                return True
    
    return False

# Usage in deletion logic
if not is_album_starred(album_mbid) and age_days > cleanup_threshold:
    delete_album_files(album_dir, dry_run=dry_run)
```

---

## Testing Service Integrations

After modifying service integration code:

1. **Make your changes** to the relevant file
2. **Instruct the user:**
   ```
   Please restart the `soulseekarr` container in Portainer.
   
   After restart, test the integration:
   1. Navigate to Settings > Connections in the web UI
   2. Click "Test Connection" for [Service Name]
   3. Verify the connection succeeds
   4. Run a script that uses the service in dry-run mode
   5. Check logs for proper API call formatting
   
   Share any error messages you see.
   ```

3. **Verify in logs:**
   - Connection success messages
   - API call formats (proper authentication, parameters)
   - Response handling (success/failure cases)
   - Dry-run behavior (no modifications made)
