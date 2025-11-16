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

Processing Flow:
1. Load all albums from Lidarr (monitored albums)
2. Scan ALL directories and build a comprehensive track database
3. Files with missing metadata stay in Downloads
4. For each album (both monitored and discovered):
   - Collect all tracks for that album from anywhere in the system
   - Determine if album is complete based on track count/Lidarr data
   - Move all tracks for that album to appropriate destination:
     * Complete albums → Not_Owned (with [YEAR] prefix)
     * Incomplete albums → Incomplete
     * Files missing metadata → remain in Downloads
5. Check Owned directory for missing tracks (read-only)
6. Cleanup empty directories

Name: Organise Files
Author: SoulSeekarr
Version: 5.0
Section: commands
Tags: organization, lidarr, cleanup, duplicates, downloads, metadata, track-database
Supports dry run: true
"""

import os
import sys
import re
import json
import shutil
import logging
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple

# Import required dependencies
try:
    import requests
except ImportError:
    print("❌ Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

try:
    from mutagen import File as MutagenFile
except ImportError:
    print("❌ Error: 'mutagen' package required. Install with: pip install mutagen")
    sys.exit(1)

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# Add the parent directory to Python path for imports
sys.path.append(str(Path(__file__).parent.parent))

# Import our modules
try:
    from database import get_db
    DATABASE_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Warning: Database not available - expiry tracking will be disabled: {e}")
    DATABASE_AVAILABLE = False

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settings import (
    get_lidarr_config, 
    get_target_uid, 
    get_target_gid, 
    is_dry_run,
    get_owned_directory,
    get_not_owned_directory,
    get_incomplete_directory,
    get_downloads_completed_directory
)

# Set up logging
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'organise_files_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Supported audio formats
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus', '.wma', '.aac', '.wav', '.aiff'}


class FileOrganiser:
    """Main class for organizing music files using track database approach."""
    
    def __init__(self, owned_dir: str = None, music_dir: str = None, 
                 incomplete_dir: str = None, downloads_dir: str = None,
                 lidarr_url: str = None, lidarr_api_key: str = None, 
                 dry_run: bool = False):
        """Initialize the File Organiser."""
        
        logger.info("=" * 60)
        logger.info("🎵 ORGANISE FILES v5.0 - Track Database Approach")
        logger.info("=" * 60)
        logger.info("")
        
        # Get directories from settings
        self.owned_dir = Path(owned_dir or get_owned_directory()).resolve()
        self.music_dir = Path(music_dir or get_not_owned_directory()).resolve()
        self.incomplete_dir = Path(incomplete_dir or get_incomplete_directory()).resolve()
        self.downloads_dir = Path(downloads_dir or get_downloads_completed_directory()).resolve()
        
        # Get Lidarr configuration
        lidarr_config = get_lidarr_config()
        self.lidarr_url = (lidarr_url.rstrip('/') if lidarr_url 
                          else lidarr_config.get('url', '').rstrip('/'))
        self.lidarr_api_key = lidarr_api_key or lidarr_config.get('api_key', '')
        
        self.dry_run = dry_run
        
        if self.dry_run:
            logger.info("🧪 DRY RUN MODE - No files will be moved or modified")
            logger.info("")
        
        # File permissions
        self.target_uid = get_target_uid()
        self.target_gid = get_target_gid()
        self.file_mode = 0o644
        self.dir_mode = 0o755
        
        # Statistics
        self.stats = {
            'files_scanned': 0,
            'files_missing_metadata': 0,
            'albums_discovered': 0,
            'albums_complete': 0,
            'albums_incomplete': 0,
            'albums_moved': 0,
            'files_moved': 0,
            'owned_albums_checked': 0,
            'owned_missing_tracks': 0,
            'errors': 0
        }
        
        # Lidarr cache
        self.lidarr_albums = {}
        self.lidarr_artists = {}
        
        # Database connection for expiry tracking
        self.db = None
        if DATABASE_AVAILABLE:
            try:
                self.db = get_db()
                logger.info("📊 Expiry tracking enabled")
            except Exception as e:
                logger.warning(f"⚠️ Expiry tracking disabled: {e}")
        
        # Validate configuration
        if not self.lidarr_url or not self.lidarr_api_key:
            raise ValueError("❌ Lidarr URL and API key are required")
        
        if not self.music_dir.exists():
            raise ValueError(f"❌ Not_Owned directory does not exist: {self.music_dir}")
        
        # Create directories if needed
        for dir_path, name in [(self.incomplete_dir, "Incomplete"), (self.downloads_dir, "Downloads")]:
            if not dir_path.exists():
                logger.info(f"📁 Creating {name} directory: {dir_path}")
                dir_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"📁 Configuration:")
        logger.info(f"   Downloads:  {self.downloads_dir}")
        logger.info(f"   Owned:      {self.owned_dir} (protected)")
        logger.info(f"   Not_Owned:  {self.music_dir}")
        logger.info(f"   Incomplete: {self.incomplete_dir}")
        logger.info(f"🔗 Lidarr:      {self.lidarr_url}")
        logger.info(f"👤 UID:GID:     {self.target_uid}:{self.target_gid}")
        logger.info(f"📝 Log file:    {log_file}")
        logger.info("")
        logger.info("✅ Initialization complete")
        logger.info("")
    
    def check_metadata_quality(self, file_path: Path) -> Tuple[bool, Dict[str, str], List[str]]:
        """
        Check if a file has sufficient metadata for organization.
        
        Returns:
            Tuple of (has_required_metadata, metadata_dict, missing_fields)
        """
        try:
            audio = MutagenFile(file_path)
            if not audio or not audio.tags:
                return False, {}, ['all tags']
            
            metadata = {}
            missing = []
            
            tags = audio.tags
            
            # Extract essential metadata (artist, album, title, year)
            for field in ['artist', 'album', 'title', 'date']:
                value = None
                
                # Try common tag names
                tag_variants = {
                    'artist': ['artist', 'ARTIST', 'TPE1', 'TAG:artist'],
                    'album': ['album', 'ALBUM', 'TALB', 'TAG:album'],
                    'title': ['title', 'TITLE', 'TIT2', 'TAG:title'],
                    'date': ['date', 'DATE', 'TDRC', 'TYER', 'year', 'YEAR', 'TAG:date']
                }
                
                for tag_key in tag_variants.get(field, [field]):
                    if tag_key in tags:
                        v = tags[tag_key]
                        value = str(v[0]) if isinstance(v, list) else str(v)
                        break
                
                if value and value.strip():
                    # Extract year from date field (handle formats like "2023-01-01" or "2023")
                    if field == 'date':
                        year_match = re.match(r'(\d{4})', value)
                        if year_match:
                            metadata['year'] = year_match.group(1)
                        else:
                            missing.append('year')
                    else:
                        metadata[field] = value.strip()
                else:
                    missing.append('year' if field == 'date' else field)
            
            has_required = len(missing) == 0
            return has_required, metadata, missing
            
        except Exception as e:
            return False, {}, [f'error: {str(e)}']
    
    def get_music_files(self, directory: Path) -> List[Path]:
        """Get all music files from directory recursively, excluding macOS metadata files."""
        music_files = []
        
        try:
            for root, dirs, files in os.walk(directory):
                for file in files:
                    # Skip macOS metadata files
                    if file.startswith('._'):
                        continue
                    
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in AUDIO_EXTENSIONS:
                        music_files.append(file_path)
        except Exception as e:
            logger.info(f"❌ Error scanning directory {directory}: {e}")
            
        return music_files
    
    def build_track_database(self) -> Dict[str, Dict]:
        """
        Scan all directories and build a comprehensive database of all tracks.
        
        Returns:
            Dict mapping album_key to {
                'tracks': List of (file_path, metadata, source_directory),
                'locations': Set of directories where tracks are found,
                'complete_tracks': int
            }
        """
        logger.info("🔍 Building comprehensive track database")
        
        track_database = defaultdict(lambda: {
            'tracks': [],
            'locations': set(),
            'complete_tracks': 0
        })
        
        # Directories to scan (excluding Owned for now as it's read-only)
        scan_dirs = [
            (self.downloads_dir, "Downloads"),
            (self.incomplete_dir, "Incomplete"),
            (self.music_dir, "Not_Owned")
        ]
        
        # Pre-scan to count total files for accurate progress
        logger.info("   📊 Counting files across all directories...")
        total_files = 0
        dir_file_counts = {}
        
        for dir_path, dir_name in scan_dirs:
            if dir_path.exists():
                files = self.get_music_files(dir_path)
                count = len(files)
                dir_file_counts[dir_name] = count
                total_files += count
                logger.info(f"      {dir_name}: {count:,} files")
            else:
                dir_file_counts[dir_name] = 0
                logger.info(f"      {dir_name}: 0 files (directory not found)")
        
        logger.info(f"   📈 Total: {total_files:,} files to process")
        self.stats['files_scanned'] = total_files
        
        if total_files == 0:
            logger.info("   ⚠️  No music files found in any directory")
            return {}
        
        logger.info("")
        
        # Main scanning with detailed progress
        files_processed = 0
        
        if TQDM_AVAILABLE:
            main_progress = tqdm(total=total_files, desc="PROGRESS_MAIN: Building track database", 
                               unit="file", ncols=100, position=0)
        
        for dir_path, dir_name in scan_dirs:
            if not dir_path.exists() or dir_file_counts[dir_name] == 0:
                continue
                
            logger.info(f"   🔍 Scanning {dir_name} ({dir_file_counts[dir_name]:,} files)...")
            music_files = self.get_music_files(dir_path)
            
            # Process files in this directory with sub-progress
            if TQDM_AVAILABLE:
                dir_progress = tqdm(music_files, 
                                  desc=f"PROGRESS_SUB: Scanning {dir_name}", 
                                  unit="file", ncols=100, position=1, leave=False)
            else:
                dir_progress = music_files
            
            valid_tracks_in_dir = 0
            missing_metadata_in_dir = 0
            
            for file_path in dir_progress:
                try:
                    # Check metadata quality
                    has_metadata, metadata, missing = self.check_metadata_quality(file_path)
                    
                    if has_metadata:
                        # Complete metadata - add to album database
                        artist = metadata['artist']
                        album = metadata['album'] 
                        year = metadata.get('year', 'Unknown')
                        album_key = f"{artist}|||{album}|||{year}"
                        
                        track_database[album_key]['tracks'].append((file_path, metadata, dir_name))
                        track_database[album_key]['locations'].add(dir_name)
                        track_database[album_key]['complete_tracks'] += 1
                        valid_tracks_in_dir += 1
                    else:
                        # Missing metadata - count but don't include in albums
                        missing_metadata_in_dir += 1
                        self.stats['files_missing_metadata'] += 1
                    
                    files_processed += 1
                    
                    # Update main progress bar
                    if TQDM_AVAILABLE:
                        main_progress.update(1)
                        # Update description with current stats
                        main_progress.set_postfix({
                            'Albums': len(track_database),
                            'Valid': files_processed - self.stats['files_missing_metadata'],
                            'Missing_MD': self.stats['files_missing_metadata']
                        })
                
                except Exception as e:
                    logger.warning(f"      ⚠️ Error processing {file_path}: {e}")
                    self.stats['errors'] += 1
                    files_processed += 1
                    if TQDM_AVAILABLE:
                        main_progress.update(1)
            
            # Summary for this directory
            total_in_dir = valid_tracks_in_dir + missing_metadata_in_dir
            if total_in_dir > 0:
                valid_pct = (valid_tracks_in_dir / total_in_dir) * 100
                logger.info(f"      ✅ {valid_tracks_in_dir:,} valid tracks ({valid_pct:.1f}%)")
                if missing_metadata_in_dir > 0:
                    missing_pct = (missing_metadata_in_dir / total_in_dir) * 100
                    logger.info(f"      ⚠️  {missing_metadata_in_dir:,} missing metadata ({missing_pct:.1f}%)")
        
        if TQDM_AVAILABLE:
            main_progress.close()
        
        self.stats['albums_discovered'] = len(track_database)
        
        logger.info("")
        logger.info(f"   📊 Database build complete:")
        logger.info(f"      Albums discovered: {len(track_database):,}")
        logger.info(f"      Valid tracks: {files_processed - self.stats['files_missing_metadata']:,}")
        logger.info(f"      Missing metadata: {self.stats['files_missing_metadata']:,}")
        
        # Show location distribution
        location_stats = defaultdict(int)
        cross_location_albums = 0
        
        for album_data in track_database.values():
            location_count = len(album_data['locations'])
            if location_count > 1:
                cross_location_albums += 1
            
            for location in album_data['locations']:
                location_stats[location] += 1
        
        logger.info("")
        logger.info("   📍 Album distribution by location:")
        for location, count in sorted(location_stats.items()):
            logger.info(f"      {location}: {count:,} albums")
        
        if cross_location_albums > 0:
            logger.info(f"   🔀 Albums spanning multiple locations: {cross_location_albums:,}")
            logger.info("      (These will be consolidated during organization)")
        
        return dict(track_database)
    
    def process_albums_from_track_database(self, track_database: Dict[str, Dict]) -> None:
        """
        Process all albums found in the track database and organize them properly.
        
        For each album:
        1. Determine if it's monitored by Lidarr
        2. Check completeness 
        3. Move all tracks to appropriate destination
        """
        total_albums = len(track_database)
        logger.info(f"📊 Processing {total_albums:,} discovered albums")
        
        if total_albums == 0:
            logger.info("   ⚠️  No albums to process")
            return
        
        # Pre-analyze albums for better progress tracking
        logger.info("   🔍 Analyzing albums for processing...")
        monitored_count = 0
        total_tracks_to_move = 0
        
        for album_key, album_data in track_database.items():
            artist, album_title, year = album_key.split('|||', 2)
            if self._find_lidarr_album(artist, album_title):
                monitored_count += 1
            total_tracks_to_move += len(album_data['tracks'])
        
        non_monitored_count = total_albums - monitored_count
        
        logger.info(f"      📈 Processing summary:")
        logger.info(f"         Monitored albums: {monitored_count:,}")
        logger.info(f"         Non-monitored albums: {non_monitored_count:,}")
        logger.info(f"         Total tracks to process: {total_tracks_to_move:,}")
        logger.info("")
        
        # Main processing with detailed progress
        albums_processed = 0
        
        if TQDM_AVAILABLE:
            main_progress = tqdm(total=total_albums, desc="PROGRESS_MAIN: Organizing albums", 
                               unit="album", ncols=100, position=0)
        
        for album_key, album_data in track_database.items():
            try:
                artist, album_title, year = album_key.split('|||', 2)
                tracks = album_data['tracks']
                locations = album_data['locations']
                track_count = len(tracks)
                
                if not tracks:
                    if TQDM_AVAILABLE:
                        main_progress.update(1)
                    continue
                
                # Check if this album is monitored by Lidarr
                lidarr_album = self._find_lidarr_album(artist, album_title)
                expected_tracks = 0
                
                if lidarr_album:
                    # Get expected track count from Lidarr
                    expected_tracks = lidarr_album.get('statistics', {}).get('trackCount')
                    if not expected_tracks:
                        expected_tracks = lidarr_album.get('trackCount', 0)
                    
                    # Try to get more accurate count from API
                    album_id = lidarr_album.get('id')
                    if album_id:
                        tracks_data = self._make_lidarr_request(f'track?albumId={album_id}')
                        if tracks_data:
                            expected_tracks = len(tracks_data)
                
                # Determine completeness
                if lidarr_album and expected_tracks > 0:
                    # Use Lidarr data for monitored albums
                    is_complete = track_count >= expected_tracks and track_count > 1
                    album_type = "monitored"
                else:
                    # Use heuristic for non-monitored albums  
                    is_complete = track_count >= 10 and track_count > 1
                    expected_tracks = 10  # Assume reasonable album size
                    album_type = "non-monitored"
                
                # Single track albums are always considered incomplete
                if track_count == 1:
                    is_complete = False
                
                # Log album processing (less verbose than before, let progress bar handle it)
                location_str = ", ".join(sorted(locations))
                if is_complete:
                    completion_status = f"Complete ({album_type})"
                    self.stats['albums_complete'] += 1
                    destination = self.music_dir
                else:
                    completion_status = f"Incomplete ({album_type})"
                    self.stats['albums_incomplete'] += 1
                    destination = self.incomplete_dir
                
                # Move all tracks for this album to the destination
                files_moved = 0
                if not self.dry_run:
                    files_moved = self._consolidate_album_tracks(tracks, destination, artist, album_title, year)
                    if files_moved > 0:
                        self.stats['albums_moved'] += 1
                        self.stats['files_moved'] += files_moved
                
                # Show detailed info for first few albums or if lots of files moved
                albums_processed += 1
                show_detail = (albums_processed <= 5 or files_moved >= 5 or 
                              len(locations) > 1 or track_count == 1)
                
                if show_detail:
                    logger.info(f"   {'✅' if is_complete else '⚠️ '} {completion_status}: {artist} - {album_title}")
                    logger.info(f"      📊 {track_count}/{expected_tracks} tracks from {location_str}")
                    if files_moved > 0:
                        logger.info(f"      📦 Moved {files_moved} files")
                
                # Track for expiry in new location
                if self.db and tracks:
                    track_tuples = [(Path(t[0]), t[1]) for t in tracks]
                    self.track_album_for_expiry(destination, album_key, track_tuples)
                    
                    # Show expiry info for first few albums or important cases (debug level)
                    if show_detail and not self.dry_run:
                        try:
                            with self.db.get_connection() as conn:
                                # Set timeout to prevent database locks
                                conn.execute("PRAGMA busy_timeout = 5000")  # 5 seconds
                                cursor = conn.cursor()
                                cursor.execute("""
                                    SELECT first_detected 
                                    FROM expiring_albums WHERE album_key = ?
                                """, (album_key,))
                                expiry_info = cursor.fetchone()
                                if expiry_info:
                                    logger.debug(f"      📅 First detected: {expiry_info['first_detected']}")
                        except Exception as e:
                            logger.debug(f"      ⚠️ Could not get expiry info: {e}")
                            pass  # Don't let expiry info errors break the main flow
                
                # Update progress bar with current stats
                if TQDM_AVAILABLE:
                    main_progress.update(1)
                    main_progress.set_postfix({
                        'Complete': self.stats['albums_complete'],
                        'Incomplete': self.stats['albums_incomplete'], 
                        'Moved': self.stats['albums_moved'],
                        'Files': self.stats['files_moved']
                    })
                
            except Exception as e:
                logger.info(f"   ❌ Error processing album {album_key}: {e}")
                self.stats['errors'] += 1
                if TQDM_AVAILABLE:
                    main_progress.update(1)
        
        if TQDM_AVAILABLE:
            main_progress.close()
        
        logger.info("")
        logger.info(f"   📊 Album organization complete:")
        logger.info(f"      Complete albums: {self.stats['albums_complete']:,}")
        logger.info(f"      Incomplete albums: {self.stats['albums_incomplete']:,}")
        logger.info(f"      Albums moved: {self.stats['albums_moved']:,}")
        logger.info(f"      Files moved: {self.stats['files_moved']:,}")
        if self.stats['errors'] > 0:
            logger.info(f"      Errors encountered: {self.stats['errors']:,}")

    def load_lidarr_data(self) -> bool:
        """Load artists and albums from Lidarr."""
        logger.info("🔗 Loading data from Lidarr...")
        
        # Load artists
        logger.info("   📥 Fetching artists from Lidarr API...")
        artists_data = self._make_lidarr_request('artist')
        if artists_data is None:
            logger.info("❌ Failed to load artists from Lidarr")
            return False
        
        logger.info(f"   📊 Processing {len(artists_data)} artists...")
        if TQDM_AVAILABLE:
            artist_iter = tqdm(artists_data, desc="PROGRESS_SUB: Processing artists", unit="artist", ncols=100)
        else:
            artist_iter = artists_data
        
        for artist in artist_iter:
            artist_name = artist.get('artistName', '').lower()
            self.lidarr_artists[artist_name] = artist
        
        logger.info(f"   ✅ Loaded {len(self.lidarr_artists)} artists")
        
        # Load albums
        logger.info("   📥 Fetching albums from Lidarr API...")
        albums_data = self._make_lidarr_request('album')
        if albums_data is None:
            logger.info("❌ Failed to load albums from Lidarr")
            return False
        
        logger.info(f"   📊 Processing {len(albums_data)} albums...")
        if TQDM_AVAILABLE:
            album_iter = tqdm(albums_data, desc="PROGRESS_SUB: Processing albums", unit="album", ncols=100)
        else:
            album_iter = albums_data
        
        for album in album_iter:
            artist_name = album.get('artist', {}).get('artistName', '').lower()
            album_title = album.get('title', '').lower()
            key = f"{artist_name}|||{album_title}"
            self.lidarr_albums[key] = album
        
        logger.info(f"   ✅ Loaded {len(self.lidarr_albums)} albums")
        return True
    
    def check_owned_directory(self) -> None:
        """Check Owned directory for missing tracks (read-only)."""
        if not self.owned_dir.exists():
            logger.info("⏭️  Owned directory does not exist, skipping")
            return
        
        logger.info("🔍 Checking Owned directory")
        
        # Count albums first for progress tracking
        logger.info("   📊 Scanning for album folders...")
        album_folders = self._find_album_folders(self.owned_dir)
        total_albums = len(album_folders)
        
        logger.info(f"   📁 Found {total_albums:,} album folders to verify")
        
        if total_albums == 0:
            logger.info("   ⚠️  No album folders found in Owned directory")
            return
        
        incomplete_count = 0
        albums_processed = 0
        
        if TQDM_AVAILABLE:
            progress = tqdm(album_folders, desc="PROGRESS_SUB: Verifying owned albums", 
                          unit="album", ncols=100)
        else:
            progress = album_folders
        
        for album_path in progress:
            try:
                self.stats['owned_albums_checked'] += 1
                albums_processed += 1
                
                artist, album, track_count = self._parse_album_info(album_path)
                
                # Check against Lidarr if available
                lidarr_album = self._find_lidarr_album(artist, album)
                
                if lidarr_album:
                    expected_tracks = lidarr_album.get('statistics', {}).get('trackCount', 0)
                    if track_count < expected_tracks:
                        missing_count = expected_tracks - track_count
                        incomplete_count += 1
                        
                        # Show details for first few incomplete albums
                        if incomplete_count <= 5:
                            logger.info(f"   ⚠️  Incomplete: {artist} - {album}")
                            logger.info(f"      📊 {track_count}/{expected_tracks} tracks ({missing_count} missing)")
                
                # Update progress postfix with current stats
                if TQDM_AVAILABLE:
                    progress.set_postfix({
                        'Checked': albums_processed,
                        'Complete': albums_processed - incomplete_count,
                        'Incomplete': incomplete_count
                    })
                    
            except Exception as e:
                logger.info(f"   ❌ Error checking {album_path}: {e}")
                self.stats['errors'] += 1
        
        self.stats['owned_missing_tracks'] = incomplete_count
        complete_count = self.stats['owned_albums_checked'] - incomplete_count
        
        logger.info("")
        logger.info(f"   📊 Owned directory verification complete:")
        logger.info(f"      Albums checked: {self.stats['owned_albums_checked']:,}")
        logger.info(f"      Complete albums: {complete_count:,}")
        if incomplete_count > 0:
            logger.info(f"      Incomplete albums: {incomplete_count:,}")
            if incomplete_count > 5:
                logger.info(f"         (showing details for first 5, {incomplete_count-5} more found)")
    
    def cleanup_empty_directories(self) -> int:
        """Remove empty directories with progress tracking."""
        logger.info("🧹 Cleaning up empty directories")
        
        # Scan first to count empty directories for progress tracking
        empty_dirs = []
        for base_dir in [self.music_dir, self.incomplete_dir, self.downloads_dir]:
            if not base_dir.exists():
                continue
            
            logger.info(f"   🔍 Scanning {base_dir.name} for empty directories...")
            for dirpath, dirnames, filenames in os.walk(base_dir, topdown=False):
                current_dir = Path(dirpath)
                
                if current_dir == base_dir:
                    continue
                
                try:
                    if not any(current_dir.iterdir()):
                        empty_dirs.append(current_dir)
                except:
                    pass
        
        total_empty = len(empty_dirs)
        logger.info(f"   📊 Found {total_empty} empty directories to remove")
        
        if total_empty == 0:
            logger.info("   ✅ No empty directories found")
            return 0
        
        removed = 0
        
        if TQDM_AVAILABLE and total_empty > 5:  # Only show progress for larger cleanup jobs
            progress = tqdm(empty_dirs, desc="PROGRESS_SUB: Removing empty dirs", 
                          unit="dir", ncols=100)
        else:
            progress = empty_dirs
        
        for current_dir in progress:
            try:
                if not self.dry_run:
                    current_dir.rmdir()
                removed += 1
                
                # Log first few removals
                if removed <= 5:
                    logger.info(f"      🗑️  Removed: {current_dir}")
            except Exception as e:
                if removed <= 3:  # Don't spam with too many error messages
                    logger.warning(f"      ⚠️ Could not remove {current_dir}: {e}")
        
        if removed > 5:
            logger.info(f"      ... and {removed - 5} more directories removed")
        
        logger.info(f"   ✅ Cleanup complete: {removed}/{total_empty} directories removed")
        return removed
    
    def track_album_for_expiry(self, directory: Path, album_key: str, tracks: List[Tuple[Path, Dict]]):
        """Track an album for expiry monitoring in the database, preserving first_detected timestamp."""
        if not self.db or not tracks:
            return
        
        try:
            # Parse album info
            artist, album, year = album_key.split('|||', 2)
            
            # Calculate file statistics
            total_size = 0
            file_count = len(tracks)
            
            for file_path, metadata in tracks:
                if file_path.exists():
                    stat = file_path.stat()
                    total_size += stat.st_size / (1024 * 1024)  # MB
            
            # Check if album already exists in database to preserve first_detected timestamp
            existing_album = None
            try:
                with self.db.get_connection() as conn:
                    # Set a timeout to prevent database locks
                    conn.execute("PRAGMA busy_timeout = 10000")  # 10 seconds
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, first_detected FROM expiring_albums WHERE album_key = ?", (album_key,))
                    existing = cursor.fetchone()
                    if existing:
                        existing_album = existing
            except Exception as e:
                logger.warning(f"   ⚠️ Could not check existing album in database: {e}")
                # Continue without existing album info
            
            if existing_album:
                logger.debug(f"   📊 Updating existing album: {artist} - {album}")
                logger.debug(f"      Preserving first_detected timestamp from database")
            else:
                logger.debug(f"   📊 Adding new album: {artist} - {album}")
                logger.debug(f"      Will use current timestamp as first_detected")
            
            # Prepare album data - only essential tracking info
            # Frontend/cleanup scripts will determine expiry policy
            album_data = {
                'album_key': album_key,
                'artist': artist,
                'album': album,
                'directory': str(directory),
                'oldest_file_days': 0,  # Legacy field, not used for expiry
                'days_until_expiry': 0,  # Legacy field, not used - frontend calculates
                'file_count': file_count,
                'total_size_mb': round(total_size, 2),
                'is_starred': False,  # Will be updated by other scripts
                'status': 'pending'
            }
            
            # Store in database (upsert will preserve first_detected for existing albums)
            album_id = self.db.upsert_expiring_album(album_data)
            
            # Clear existing track records and add current ones
            self.db.clear_album_tracks(album_id)
            
            for file_path, metadata in tracks:
                if file_path.exists():
                    stat = file_path.stat()
                    
                    track_data = {
                        'file_path': str(file_path),
                        'file_name': file_path.name,
                        'track_title': metadata.get('title'),
                        'track_number': metadata.get('tracknumber'),
                        'track_artist': metadata.get('artist'),
                        'file_size_mb': round((stat.st_size / (1024 * 1024)), 2),
                        'days_old': 0,  # Legacy field, not used for expiry
                        'last_modified': datetime.fromtimestamp(stat.st_mtime),
                        'is_starred': False,
                        'navidrome_id': None
                    }
                    
                    self.db.add_album_track(album_id, track_data)
            
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to track album expiry: {e}")
    
    def print_summary(self) -> None:
        """Print summary statistics."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Files scanned:            {self.stats['files_scanned']}")
        logger.info(f"Files missing metadata:   {self.stats['files_missing_metadata']}")
        logger.info(f"Albums discovered:        {self.stats['albums_discovered']}")
        logger.info(f"Complete albums:          {self.stats['albums_complete']}")
        logger.info(f"Incomplete albums:        {self.stats['albums_incomplete']}")
        logger.info(f"Albums moved:             {self.stats['albums_moved']}")
        logger.info(f"Files moved:              {self.stats['files_moved']}")
        logger.info(f"Owned albums checked:     {self.stats['owned_albums_checked']}")
        logger.info(f"Owned missing tracks:     {self.stats['owned_missing_tracks']}")
        logger.info(f"Errors:                   {self.stats['errors']}")
        
        # Add expiry tracking summary if available
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    
                    # Get basic tracking statistics
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total_albums,
                            COUNT(CASE WHEN is_starred = 1 THEN 1 END) as starred,
                            MIN(first_detected) as earliest_detected,
                            MAX(first_detected) as latest_detected
                        FROM expiring_albums 
                        WHERE status = 'pending'
                    """)
                    tracking_stats = cursor.fetchone()
                    
                    if tracking_stats and tracking_stats['total_albums'] > 0:
                        logger.info("=" * 60)
                        logger.info("📊 EXPIRY TRACKING")
                        logger.info("=" * 60)
                        logger.info(f"Albums tracked for expiry: {tracking_stats['total_albums']:,}")
                        logger.info(f"Starred (protected):       {tracking_stats['starred']:,}")
                        
                        if tracking_stats['earliest_detected'] and tracking_stats['latest_detected']:
                            logger.info(f"First detection range:     {tracking_stats['earliest_detected']} to {tracking_stats['latest_detected']}")
                        
                        # Show some recently added albums
                        logger.info("")
                        logger.info("📅 Recently tracked albums:")
                        cursor.execute("""
                            SELECT artist, album, first_detected, directory
                            FROM expiring_albums 
                            WHERE status = 'pending'
                            ORDER BY first_detected DESC
                            LIMIT 3
                        """)
                        for row in cursor.fetchall():
                            dir_name = Path(row['directory']).name
                            logger.info(f"   📁 {row['artist']} - {row['album']}")
                            logger.info(f"      📅 Added: {row['first_detected']} in {dir_name}")
                        
                        logger.info("")
                        logger.info("💡 Expiry policy will be determined by frontend/cleanup scripts")
                        logger.info("💡 View detailed expiry info at: /library")
                        
            except Exception as e:
                logger.warning(f"   ⚠️ Could not get tracking summary: {e}")
        
        logger.info("=" * 60)
        logger.info("✅ Processing complete")
        logger.info(f"📝 Log file: {log_file}")
        logger.info("=" * 60)
    
    def run(self) -> None:
        """Main execution flow with enhanced progress tracking."""
        start_time = datetime.now()
        
        # Step 1: Load Lidarr data
        step_start = datetime.now()
        logger.info("PROGRESS: [1/5] 20% - Loading Lidarr monitored albums")
        if not self.load_lidarr_data():
            logger.info("❌ Failed to load Lidarr data - aborting")
            return
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Step 2: Build comprehensive track database from all directories
        step_start = datetime.now()
        logger.info("PROGRESS: [2/5] 40% - Building comprehensive track database")
        track_database = self.build_track_database()
        
        if not track_database:
            logger.info("⚠️  No albums found to process - exiting early")
            self.print_summary()
            return
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Step 3: Process all albums and organize them properly
        step_start = datetime.now()
        logger.info("PROGRESS: [3/5] 60% - Organizing albums from track database")
        self.process_albums_from_track_database(track_database)
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Step 4: Check Owned directory (read-only)
        step_start = datetime.now()
        logger.info("PROGRESS: [4/5] 80% - Checking Owned directory")
        self.check_owned_directory()
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Step 5: Cleanup
        step_start = datetime.now()
        logger.info("PROGRESS: [5/5] 100% - Cleanup")
        empty_dirs_removed = self.cleanup_empty_directories()
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Print summary with total time
        total_duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"🕐 Total processing time: {total_duration:.1f}s")
        
        # Performance insights
        if self.stats['files_scanned'] > 0:
            files_per_sec = self.stats['files_scanned'] / total_duration
            logger.info(f"📊 Performance: {files_per_sec:.1f} files/second")
        
        if self.stats['albums_discovered'] > 0:
            albums_per_sec = self.stats['albums_discovered'] / total_duration  
            logger.info(f"📊 Performance: {albums_per_sec:.1f} albums/second")
        
        self.print_summary()
    
    # Helper methods
    
    def _find_lidarr_album(self, artist: str, album_title: str) -> Optional[Dict]:
        """Find album in Lidarr data using exact matching."""
        lookup_key = f"{artist.lower().strip()}|||{album_title.lower().strip()}"
        return self.lidarr_albums.get(lookup_key)
    
    def _consolidate_album_tracks(self, tracks: List[Tuple], 
                                   destination_dir: Path, artist: str, album: str, year: str) -> int:
        """
        Move all tracks for an album to the destination directory.
        
        Returns:
            Number of files actually moved
        """
        # Create destination structure with year prefix
        safe_artist = self._sanitize_filename(artist)
        album_with_year = f"[{year}] {album}" if year != 'Unknown' else album
        safe_album = self._sanitize_filename(album_with_year)
        
        target_artist_dir = destination_dir / safe_artist
        target_album_dir = target_artist_dir / safe_album
        
        target_album_dir.mkdir(parents=True, exist_ok=True)
        self._fix_permissions(target_artist_dir)
        self._fix_permissions(target_album_dir)
        
        files_moved = 0
        
        for file_path, metadata, source_dir in tracks:
            file_path = Path(file_path)  # Ensure it's a Path object
            target_file = target_album_dir / file_path.name
            
            # Skip if already in correct location
            if file_path.resolve() == target_file.resolve():
                continue
            
            if target_file.exists():
                logger.info(f"      ⚠️  Target exists, skipping: {file_path.name}")
                continue
            
            try:
                shutil.move(str(file_path), str(target_file))
                self._fix_permissions(target_file)
                files_moved += 1
            except Exception as e:
                logger.info(f"      ❌ Failed to move {file_path.name}: {e}")
                self.stats['errors'] += 1
        
        if files_moved > 0:
            logger.info(f"      📦 Moved {files_moved} files to {target_album_dir}")
        
        return files_moved
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename for filesystem."""
        if not name:
            return "Unknown"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip('. ')
    
    def _fix_permissions(self, path: Path) -> None:
        """Fix file/directory permissions."""
        if self.dry_run or os.geteuid() != 0:
            return
        
        try:
            os.chown(path, self.target_uid, self.target_gid)
            if path.is_file():
                path.chmod(self.file_mode)
            elif path.is_dir():
                path.chmod(self.dir_mode)
        except:
            pass
    
    def _make_lidarr_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make request to Lidarr API."""
        try:
            url = f"{self.lidarr_url}/api/v1/{endpoint}"
            headers = {'X-Api-Key': self.lidarr_api_key}
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.info(f"❌ Lidarr API error: {e}")
            return None
    
    def _find_album_folders(self, search_dir: Path) -> List[Path]:
        """Find all album folders in directory."""
        album_folders = []
        
        try:
            for artist_dir in search_dir.iterdir():
                if not artist_dir.is_dir():
                    continue
                
                for potential_album in artist_dir.iterdir():
                    if not potential_album.is_dir():
                        continue
                    
                    # Check if contains audio files
                    audio_files = [f for f in potential_album.iterdir() 
                                  if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
                    
                    if audio_files:
                        album_folders.append(potential_album)
        except:
            pass
        
        return album_folders
    
    def _parse_album_info(self, album_path: Path) -> Tuple[str, str, int]:
        """Parse artist, album name, and track count from path."""
        artist_name = album_path.parent.name
        album_name = album_path.name
        
        # Remove year prefix if present
        album_name = re.sub(r'^\[\d{4}\]\s*', '', album_name)
        
        # Count audio files
        audio_files = [f for f in album_path.iterdir() 
                      if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
        
        return artist_name, album_name, len(audio_files)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Organize music files using track database approach"
    )
    
    parser.add_argument('--owned-dir', help='Owned music directory (protected)')
    parser.add_argument('--music-dir', help='Not_Owned music directory')
    parser.add_argument('--incomplete-dir', help='Incomplete albums directory')
    parser.add_argument('--downloads-dir', help='Downloads directory')
    parser.add_argument('--lidarr-url', help='Lidarr base URL')
    parser.add_argument('--lidarr-api-key', help='Lidarr API key')
    parser.add_argument('--dry-run', action='store_true', help='Simulate changes without applying them')
    
    args = parser.parse_args()
    
    # Check for DRY_RUN environment variable
    if is_dry_run():
        args.dry_run = True
    
    try:
        organiser = FileOrganiser(
            owned_dir=args.owned_dir,
            music_dir=args.music_dir,
            incomplete_dir=args.incomplete_dir,
            downloads_dir=args.downloads_dir,
            lidarr_url=args.lidarr_url,
            lidarr_api_key=args.lidarr_api_key,
            dry_run=args.dry_run
        )
        
        organiser.run()
        
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.info(f"\n❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()