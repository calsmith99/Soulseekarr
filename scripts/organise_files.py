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

# Supported audio formats - focusing on FLAC and MP3 only
AUDIO_EXTENSIONS = {'.mp3', '.flac'}


class FileOrganiser:
    """Main class for organizing music files using track database approach."""
    
    def __init__(self, owned_dir: str = None, music_dir: str = None, 
                 incomplete_dir: str = None, downloads_dir: str = None,
                 lidarr_url: str = None, lidarr_api_key: str = None, 
                 dry_run: bool = False, auto_mode: bool = False):
        """Initialize the File Organiser."""
        
        self.auto_mode = auto_mode
        
        if not auto_mode:
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
        
        if self.dry_run and not auto_mode:
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
            'files_quality_upgraded': 0,  # Files replaced with higher quality versions
            'files_quality_kept_existing': 0,  # Existing files kept due to better quality
            'owned_albums_checked': 0,
            'owned_missing_tracks': 0,
            'system_files_removed': 0,
            'empty_dirs_removed': 0,
            'source_dirs_removed': 0,  # Directories cleaned up during file moves
            'database_entries_removed': 0,  # Database entries for deleted files
            'errors': 0
        }
        
        # Track albums moved to complete (Not_Owned) directory
        self.complete_albums_moved = []
        
        # Track all file movements for summary
        self.move_summary = {
            'to_not_owned': [],      # Complete albums moved to Not_Owned
            'to_incomplete': [],     # Incomplete albums moved to Incomplete
            'files_by_destination': {'Not_Owned': 0, 'Incomplete': 0},
            'albums_by_destination': {'Not_Owned': 0, 'Incomplete': 0}
        }
        
        # Lidarr cache
        self.lidarr_albums = {}
        self.lidarr_artists = {}
        
        # Database connection for expiry tracking
        self.db = None
        if DATABASE_AVAILABLE:
            try:
                self.db = get_db()
                # Set database optimization settings for better concurrency
                with self.db.get_connection() as conn:
                    # Enable WAL mode for better concurrency
                    conn.execute("PRAGMA journal_mode = WAL")
                    # Set longer timeout globally
                    conn.execute("PRAGMA busy_timeout = 30000")  # 30 seconds
                    # Optimize for concurrent access
                    conn.execute("PRAGMA cache_size = 10000")  # 10MB cache
                    conn.execute("PRAGMA synchronous = NORMAL")  # Balance safety and speed
                    conn.commit()
                logger.info("📊 Expiry tracking enabled with optimized database settings")
            except Exception as e:
                logger.warning(f"⚠️ Expiry tracking disabled: {e}")
                self.db = None
        
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
            
            # Extract essential metadata (albumartist, album, title, year, musicbrainz_albumid)
            for field in ['albumartist', 'artist', 'album', 'title', 'date', 'musicbrainz_albumid']:
                value = None
                
                # Try common tag names for FLAC and MP3
                tag_variants = {
                    'albumartist': ['albumartist', 'ALBUMARTIST', 'TPE2'],
                    'artist': ['artist', 'ARTIST', 'TPE1'],
                    'album': ['album', 'ALBUM', 'TALB'],
                    'title': ['title', 'TITLE', 'TIT2'],
                    'date': ['date', 'DATE', 'TDRC', 'TYER', 'year', 'YEAR'],
                    'musicbrainz_albumid': ['musicbrainz_albumid', 'MUSICBRAINZ_ALBUMID', 'TXXX:MusicBrainz Album Id']
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
                    if field not in ['musicbrainz_albumid']: # MBID is optional
                        missing.append('year' if field == 'date' else field)
            
            # Use albumartist if available, fallback to artist
            if 'albumartist' in metadata:
                metadata['artist'] = metadata['albumartist']  # Override artist with albumartist
            elif 'artist' not in metadata:
                missing.append('artist')
            
            # Remove albumartist from metadata since we've used it to set artist
            metadata.pop('albumartist', None)
            
            # Extract track number (optional field)
            track_value = None
            track_variants = ['tracknumber', 'TRACKNUMBER', 'TRCK', 'TAG:tracknumber', 'trkn']
            for tag_key in track_variants:
                if tag_key in tags:
                    v = tags[tag_key]
                    track_value = str(v[0]) if isinstance(v, list) else str(v)
                    break
            
            if track_value and track_value.strip():
                # Handle track numbers like "1/12" or just "1"
                track_match = re.match(r'(\d+)', track_value)
                if track_match:
                    metadata['tracknumber'] = track_match.group(1)
            
            # Check if we have the essential fields
            essential_fields = ['artist', 'album', 'title']
            missing = [field for field in essential_fields if field not in metadata or not metadata[field].strip()]
            if 'year' not in metadata:
                missing.append('year')
            
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
            
            for i, file_path in enumerate(dir_progress, 1):
                if i % 100 == 0:
                    print(f"PROGRESS: {i}/{len(music_files)} - Scanning {dir_name}...")
                try:
                    # Check metadata quality
                    has_metadata, metadata, missing = self.check_metadata_quality(file_path)
                    
                    if has_metadata:
                        # Complete metadata - add to album database
                        # Use albumartist for organization to avoid featured artist splits
                        artist = metadata['artist']  # This is now albumartist if it was available
                        album = metadata['album'] 
                        year = metadata.get('year', 'Unknown')
                        
                        # Use MBID if available, otherwise hash
                        mb_album_id = metadata.get('musicbrainz_albumid')
                        if mb_album_id:
                            album_key = mb_album_id
                        else:
                            import hashlib
                            key_str = f"{artist}-{album}".lower().encode('utf-8')
                            album_key = f"local-{hashlib.md5(key_str).hexdigest()}"
                        
                        # Initialize metadata if new key
                        if 'artist' not in track_database[album_key]:
                            track_database[album_key]['artist'] = artist
                            track_database[album_key]['album'] = album
                            track_database[album_key]['year'] = year
                        
                        track_database[album_key]['tracks'].append((file_path, metadata, dir_name))
                        track_database[album_key]['locations'].add(dir_name)
                        track_database[album_key]['complete_tracks'] += 1
                        valid_tracks_in_dir += 1
                        
                        # Debug logging for featured artist issues (first few instances)
                        if 'feat' in file_path.stem.lower() and len([k for k in track_database.keys() if 'feat' not in k]) <= 3:
                            logger.info(f"      🎵 Featured track: {file_path.name}")
                            logger.info(f"         � Organized under: {artist} (avoiding artist split)")
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
            artist = album_data.get('artist')
            album_title = album_data.get('album')
            # Fallback for legacy keys if needed
            if not artist or not album_title:
                try:
                    artist, album_title, _ = album_key.split('|||', 2)
                except ValueError:
                    continue
                    
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
        
        for i, (album_key, album_data) in enumerate(track_database.items(), 1):
            if i % 10 == 0:
                print(f"PROGRESS: {i}/{total_albums} - Organizing albums...")
            try:
                artist = album_data.get('artist')
                album_title = album_data.get('album')
                year = album_data.get('year', 'Unknown')
                
                # Fallback for legacy keys
                if not artist or not album_title:
                    try:
                        artist, album_title, year = album_key.split('|||', 2)
                    except ValueError:
                        logger.warning(f"Skipping album with invalid key/data: {album_key}")
                        continue
                        
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
                        
                        # Track albums moved to complete directory
                        if is_complete and destination == self.music_dir:
                            album_info = {
                                'artist': artist,
                                'album': album_title,
                                'year': year,
                                'track_count': track_count,
                                'files_moved': files_moved
                            }
                            self.complete_albums_moved.append(album_info)
                            self.move_summary['to_not_owned'].append(album_info)
                            self.move_summary['files_by_destination']['Not_Owned'] += files_moved
                            self.move_summary['albums_by_destination']['Not_Owned'] += 1
                        elif not is_complete and destination == self.incomplete_dir:
                            album_info = {
                                'artist': artist,
                                'album': album_title,
                                'year': year,
                                'track_count': track_count,
                                'files_moved': files_moved
                            }
                            self.move_summary['to_incomplete'].append(album_info)
                            self.move_summary['files_by_destination']['Incomplete'] += files_moved
                            self.move_summary['albums_by_destination']['Incomplete'] += 1
                else:
                    # In dry-run mode, track what would be moved to complete
                    if is_complete and destination == self.music_dir:
                        album_info = {
                            'artist': artist,
                            'album': album_title,
                            'year': year,
                            'track_count': track_count,
                            'files_moved': track_count  # Estimate all tracks would be moved
                        }
                        self.complete_albums_moved.append(album_info)
                        self.move_summary['to_not_owned'].append(album_info)
                        self.move_summary['files_by_destination']['Not_Owned'] += track_count
                        self.move_summary['albums_by_destination']['Not_Owned'] += 1
                    elif not is_complete and destination == self.incomplete_dir:
                        album_info = {
                            'artist': artist,
                            'album': album_title,
                            'year': year,
                            'track_count': track_count,
                            'files_moved': track_count  # Estimate all tracks would be moved
                        }
                        self.move_summary['to_incomplete'].append(album_info)
                        self.move_summary['files_by_destination']['Incomplete'] += track_count
                        self.move_summary['albums_by_destination']['Incomplete'] += 1
                
                # Show detailed info for first few albums or if lots of files moved
                albums_processed += 1
                show_detail = (albums_processed <= 5 or files_moved >= 5 or 
                              len(locations) > 1 or track_count == 1)
                
                if show_detail:
                    logger.info(f"   {'✅' if is_complete else '⚠️ '} {completion_status}: {artist} - {album_title}")
                    logger.info(f"      📊 {track_count}/{expected_tracks} tracks from {location_str}")
                    if files_moved > 0:
                        logger.info(f"      📦 Moved {files_moved} files")
                
                # Track for expiry in new location (if database is available and working)
                if self.db and tracks and not self.dry_run:
                    track_tuples = [(Path(t[0]), t[1]) for t in tracks]
                    self.track_album_for_expiry(destination, album_key, track_tuples, artist=artist, album=album_title)
                    
                    # Show expiry info for first few albums or important cases (debug level)
                    if show_detail and not self.dry_run:
                        def get_expiry_info():
                            with self.db.get_connection() as conn:
                                # Set timeout to prevent database locks
                                conn.execute("PRAGMA busy_timeout = 15000")  # 15 seconds
                                cursor = conn.cursor()
                                cursor.execute("""
                                    SELECT first_detected 
                                    FROM expiring_albums WHERE album_key = ?
                                """, (album_key,))
                                expiry_info = cursor.fetchone()
                                if expiry_info:
                                    logger.debug(f"      📅 First detected: {expiry_info['first_detected']}")
                                return expiry_info
                        
                        # Use retry logic for expiry info lookup
                        self._execute_with_retry(f"expiry info lookup for {album_key}", get_expiry_info)
                
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
        
        for i, artist in enumerate(artist_iter, 1):
            if i % 50 == 0:
                print(f"PROGRESS: {i}/{len(artists_data)} - Processing artists...")
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
        
        for i, album in enumerate(album_iter, 1):
            if i % 50 == 0:
                print(f"PROGRESS: {i}/{len(albums_data)} - Processing albums...")
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
                
                if albums_processed % 10 == 0:
                    print(f"PROGRESS: {albums_processed}/{total_albums} - Verifying owned albums...")
                
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
    
    def cleanup_deleted_file_entries(self) -> int:
        """Remove database entries for files that no longer exist on disk."""
        if not self.db:
            logger.info("   ⚠️  Database not available - skipping cleanup")
            return 0
        
        logger.info("🗑️  Cleaning up database entries for deleted files")
        
        try:
            with self.db.get_connection() as conn:
                # Get all file entries from the album_tracks table (where individual files are tracked)
                cursor = conn.execute("SELECT id, file_path, album_id FROM album_tracks")
                all_entries = cursor.fetchall()
                
                if not all_entries:
                    logger.info("   ✅ No database entries found - nothing to clean")
                    return 0
                
                logger.info(f"   📊 Checking {len(all_entries)} database entries for deleted files")
                
                deleted_entries = []
                affected_albums = set()
                
                # Check each file entry
                for entry_id, file_path, album_id in all_entries:
                    if not Path(file_path).exists():
                        deleted_entries.append((entry_id, file_path))
                        affected_albums.add(album_id)
                
                if not deleted_entries:
                    logger.info("   ✅ All database entries point to existing files")
                    return 0
                
                logger.info(f"   🗑️  Found {len(deleted_entries)} entries for deleted files")
                
                # Remove entries for deleted files
                deleted_count = 0
                for entry_id, file_path in deleted_entries:
                    try:
                        if not self.dry_run:
                            conn.execute("DELETE FROM album_tracks WHERE id = ?", (entry_id,))
                        
                        deleted_count += 1
                        
                        # Log first few deletions
                        if deleted_count <= 5:
                            logger.info(f"      🗑️  Removed entry: {Path(file_path).name}")
                        
                    except Exception as e:
                        logger.warning(f"      ⚠️  Failed to remove entry for {file_path}: {e}")
                
                if deleted_count > 5:
                    logger.info(f"      ... and {deleted_count - 5} more entries removed")
                
                # Check for orphaned albums (albums with no remaining tracks)
                orphaned_albums = []
                if affected_albums:
                    logger.info(f"   📊 Checking {len(affected_albums)} potentially affected albums")
                    
                    for album_id in affected_albums:
                        cursor = conn.execute("SELECT COUNT(*) FROM album_tracks WHERE album_id = ?", (album_id,))
                        track_count = cursor.fetchone()[0]
                        
                        if track_count == 0:
                            # Get album info before deleting
                            cursor = conn.execute("SELECT artist, album FROM expiring_albums WHERE id = ?", (album_id,))
                            album_info = cursor.fetchone()
                            if album_info:
                                orphaned_albums.append((album_id, album_info[0], album_info[1]))
                
                # Remove orphaned album entries
                orphaned_count = 0
                if orphaned_albums:
                    logger.info(f"   🗑️  Found {len(orphaned_albums)} orphaned albums (no remaining tracks)")
                    
                    for album_id, artist, album in orphaned_albums:
                        try:
                            if not self.dry_run:
                                conn.execute("DELETE FROM expiring_albums WHERE id = ?", (album_id,))
                            
                            orphaned_count += 1
                            
                            # Log first few deletions
                            if orphaned_count <= 3:
                                logger.info(f"      🗑️  Removed album: {artist} - {album}")
                            
                        except Exception as e:
                            logger.warning(f"      ⚠️  Failed to remove album {artist} - {album}: {e}")
                    
                    if orphaned_count > 3:
                        logger.info(f"      ... and {orphaned_count - 3} more albums removed")
                
                # Commit changes if not dry run
                if not self.dry_run:
                    conn.commit()
                
                total_removed = deleted_count + orphaned_count
                logger.info(f"   ✅ Database cleanup: {deleted_count} tracks + {orphaned_count} albums = {total_removed} entries removed")
                self.stats['database_entries_removed'] = total_removed
                return total_removed
            
        except Exception as e:
            logger.error(f"   ❌ Error during database cleanup: {e}")
            return 0
    
    def cleanup_system_files_and_directories(self) -> Tuple[int, int]:
        """Remove macOS system files and empty directories with progress tracking."""
        logger.info("🧹 Cleaning up system files and empty directories")
        
        # First pass: Remove macOS system files
        logger.info("   🍎 Removing macOS system files (._* files)...")
        system_files = []
        for base_dir in [self.music_dir, self.incomplete_dir, self.downloads_dir]:
            if not base_dir.exists():
                continue
            
            for root, dirs, files in os.walk(base_dir):
                for file in files:
                    if file.startswith('._') or file == '.DS_Store':
                        system_files.append(Path(root) / file)
        
        system_files_removed = 0
        if system_files:
            logger.info(f"   � Found {len(system_files)} system files to remove")
            
            if TQDM_AVAILABLE and len(system_files) > 10:
                progress = tqdm(system_files, desc="PROGRESS_SUB: Removing system files", 
                              unit="file", ncols=100)
            else:
                progress = system_files
            
            for system_file in progress:
                try:
                    if not self.dry_run:
                        system_file.unlink()
                    system_files_removed += 1
                    
                    # Log first few removals
                    if system_files_removed <= 5:
                        logger.info(f"      🗑️  Removed: {system_file.name}")
                except Exception as e:
                    if system_files_removed <= 3:
                        logger.warning(f"      ⚠️ Could not remove {system_file}: {e}")
            
            if system_files_removed > 5:
                logger.info(f"      ... and {system_files_removed - 5} more system files removed")
            
            logger.info(f"   ✅ System file cleanup: {system_files_removed}/{len(system_files)} files removed")
        else:
            logger.info("   ✅ No system files found to remove")
        
        self.stats['system_files_removed'] = system_files_removed
        
        # Second pass: Remove empty directories
        logger.info("   📁 Removing empty directories...")
        empty_dirs = []
        for base_dir in [self.music_dir, self.incomplete_dir, self.downloads_dir]:
            if not base_dir.exists():
                continue
            
            for dirpath, dirnames, filenames in os.walk(base_dir, topdown=False):
                current_dir = Path(dirpath)
                
                if current_dir == base_dir:
                    continue
                
                try:
                    # Check if directory is empty (no files or subdirectories)
                    if not any(current_dir.iterdir()):
                        empty_dirs.append(current_dir)
                except:
                    pass
        
        empty_dirs_removed = 0
        if empty_dirs:
            logger.info(f"   📊 Found {len(empty_dirs)} empty directories to remove")
            
            if TQDM_AVAILABLE and len(empty_dirs) > 10:
                progress = tqdm(empty_dirs, desc="PROGRESS_SUB: Removing empty dirs", 
                              unit="dir", ncols=100)
            else:
                progress = empty_dirs
            
            for current_dir in progress:
                try:
                    if not self.dry_run:
                        current_dir.rmdir()
                    empty_dirs_removed += 1
                    
                    # Log first few removals
                    if empty_dirs_removed <= 5:
                        logger.info(f"      🗑️  Removed: {current_dir}")
                except Exception as e:
                    if empty_dirs_removed <= 3:
                        logger.warning(f"      ⚠️ Could not remove {current_dir}: {e}")
            
            if empty_dirs_removed > 5:
                logger.info(f"      ... and {empty_dirs_removed - 5} more directories removed")
            
            logger.info(f"   ✅ Directory cleanup: {empty_dirs_removed}/{len(empty_dirs)} directories removed")
        else:
            logger.info("   ✅ No empty directories found to remove")
        
        self.stats['empty_dirs_removed'] = empty_dirs_removed
        
        return system_files_removed, empty_dirs_removed
    
    def _execute_with_retry(self, operation_name: str, operation_func, max_retries: int = 3):
        """
        Execute database operation with retry logic and exponential backoff.
        
        Args:
            operation_name: Description of the operation for logging
            operation_func: Function to execute that returns a value or None
            max_retries: Maximum number of retry attempts
            
        Returns:
            Result from operation_func or None if all retries failed
        """
        import time
        
        for attempt in range(max_retries + 1):
            try:
                return operation_func()
            except Exception as e:
                error_msg = str(e).lower()
                
                # Check if it's a database lock error
                if 'database is locked' in error_msg or 'database locked' in error_msg:
                    if attempt < max_retries:
                        # Exponential backoff: 0.5s, 1s, 2s
                        wait_time = 0.5 * (2 ** attempt)
                        logger.debug(f"   ⏳ Database locked during {operation_name}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries + 1})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"   ❌ Database lock persisted for {operation_name} after {max_retries} retries")
                        return None
                else:
                    # Non-lock error, don't retry
                    logger.warning(f"   ⚠️ Failed {operation_name}: {e}")
                    return None
        
        return None
    
    def track_album_for_expiry(self, directory: Path, album_key: str, tracks: List[Tuple[Path, Dict]], artist: str = None, album: str = None):
        """Track an album for expiry monitoring in the database, preserving first_detected timestamp."""
        if not self.db or not tracks:
            return
        
        def perform_tracking():
            # Parse album info if not provided
            nonlocal artist, album
            if not artist or not album:
                try:
                    artist, album, _ = album_key.split('|||', 2)
                except ValueError:
                    # If we can't parse key and no info provided, try to get from first track
                    if tracks and tracks[0][1]:
                        artist = tracks[0][1].get('artist')
                        album = tracks[0][1].get('album')
                    
                    if not artist or not album:
                        logger.warning(f"Could not determine artist/album for expiry tracking: {album_key}")
                        return
            
            # Calculate file statistics
            total_size = 0
            file_count = len(tracks)
            
            for file_path, metadata in tracks:
                if file_path.exists():
                    stat = file_path.stat()
                    total_size += stat.st_size / (1024 * 1024)  # MB
            
            # Prepare album data
            album_data = {
                'album_key': album_key,
                'artist': artist,
                'album': album,
                'directory': str(directory),
                'file_count': file_count,
                'total_size_mb': round(total_size, 2),
                'is_starred': False,  # Will be updated by other scripts
                'status': 'pending'
            }
            
            # Prepare track data
            track_data_list = []
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
                    track_data_list.append(track_data)
            
            # Single database transaction to reduce lock contention
            with self.db.get_connection() as conn:
                # Set aggressive timeout for busy database
                conn.execute("PRAGMA busy_timeout = 30000")  # 30 seconds
                conn.execute("PRAGMA journal_mode = WAL")  # Enable WAL mode for better concurrency
                
                cursor = conn.cursor()
                
                # Check if album exists
                cursor.execute("SELECT id, first_detected FROM expiring_albums WHERE album_key = ?", (album_key,))
                existing = cursor.fetchone()
                
                now = datetime.now()
                
                if existing:
                    # Update existing album record
                    cursor.execute("""
                        UPDATE expiring_albums 
                        SET file_count = ?,
                            total_size_mb = ?, is_starred = ?, last_seen = ?, status = ?,
                            updated_at = ?
                        WHERE album_key = ?
                    """, (
                        album_data['file_count'], album_data['total_size_mb'],
                        album_data['is_starred'], now, album_data['status'],
                        now, album_key
                    ))
                    album_id = existing['id']
                    logger.debug(f"   📊 Updated existing album: {artist} - {album}")
                else:
                    # Insert new album record
                    cursor.execute("""
                        INSERT INTO expiring_albums 
                        (album_key, artist, album, directory, 
                         file_count, total_size_mb, is_starred, 
                         first_detected, last_seen, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        album_key, artist, album, str(directory),
                        album_data['file_count'], album_data['total_size_mb'],
                        album_data['is_starred'], now, now, album_data['status']
                    ))
                    album_id = cursor.lastrowid
                    logger.debug(f"   📊 Added new album: {artist} - {album}")
                
                # Clear existing tracks for this album
                cursor.execute("DELETE FROM album_tracks WHERE album_id = ?", (album_id,))
                
                # Batch insert all tracks
                if track_data_list:
                    track_values = []
                    for track_data in track_data_list:
                        track_values.append((
                            album_id, track_data['file_path'], track_data['file_name'],
                            track_data['track_title'], track_data['file_size_mb'],
                            track_data['days_old'], track_data['last_modified']
                        ))
                    
                    cursor.executemany("""
                        INSERT INTO album_tracks 
                        (album_id, file_path, file_name, track_title, file_size_mb, 
                         days_old, last_modified)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, track_values)
                
                conn.commit()
                return album_id
        
        # Execute with retry logic
        result = self._execute_with_retry(f"expiry tracking for {album_key}", perform_tracking)
        if result is None:
            logger.warning(f"   ⚠️ Failed to track album expiry after retries: {album_key}")
            
        return result
    
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
        logger.info(f"Files upgraded (quality): {self.stats['files_quality_upgraded']}")
        logger.info(f"Files kept (better):      {self.stats['files_quality_kept_existing']}")
        logger.info(f"Source dirs removed:      {self.stats['source_dirs_removed']}")
        logger.info(f"Owned albums checked:     {self.stats['owned_albums_checked']}")
        logger.info(f"Owned missing tracks:     {self.stats['owned_missing_tracks']}")
        logger.info(f"System files removed:     {self.stats['system_files_removed']}")
        logger.info(f"Empty dirs removed:       {self.stats['empty_dirs_removed']}")
        logger.info(f"Errors:                   {self.stats['errors']}")
        
        # Show movement summary by destination
        if any(self.move_summary['files_by_destination'].values()):
            logger.info("=" * 60)
            dry_run_prefix = "WOULD BE MOVED" if self.dry_run else "MOVED"
            logger.info(f"📦 FILES {dry_run_prefix} BY DESTINATION")
            logger.info("=" * 60)
            
            for destination, file_count in self.move_summary['files_by_destination'].items():
                if file_count > 0:
                    album_count = self.move_summary['albums_by_destination'][destination]
                    logger.info(f"📁 {destination}: {album_count} albums, {file_count} files")
        
        # Show albums moved to Incomplete
        if self.move_summary['to_incomplete']:
            logger.info("=" * 60)
            dry_run_prefix = "WOULD BE MOVED TO" if self.dry_run else "MOVED TO"
            logger.info(f"⚠️  ALBUMS {dry_run_prefix} INCOMPLETE")
            logger.info("=" * 60)
            logger.info(f"Total incomplete albums: {len(self.move_summary['to_incomplete'])}")
            logger.info("")
            
            for i, album in enumerate(self.move_summary['to_incomplete'], 1):
                year_str = f" [{album['year']}]" if album['year'] != 'Unknown' else ""
                logger.info(f"{i:2d}. {album['artist']} - {album['album']}{year_str}")
                files_text = f"{album['files_moved']} files {'would be ' if self.dry_run else ''}moved"
                logger.info(f"     📊 {album['track_count']} tracks, {files_text}")
                
                # Limit display to prevent log spam
                if i >= 20:
                    remaining = len(self.move_summary['to_incomplete']) - i
                    if remaining > 0:
                        logger.info(f"     ... and {remaining} more albums")
                    break
        
        # Show albums moved to complete directory
        if self.complete_albums_moved:
            logger.info("=" * 60)
            logger.info("� ALBUMS MOVED TO COMPLETE (NOT_OWNED)")
            logger.info("=" * 60)
            logger.info(f"Total complete albums: {len(self.complete_albums_moved)}")
            logger.info("")
            
            for i, album in enumerate(self.complete_albums_moved, 1):
                year_str = f" [{album['year']}]" if album['year'] != 'Unknown' else ""
                logger.info(f"{i:2d}. {album['artist']} - {album['album']}{year_str}")
                logger.info(f"     � {album['track_count']} tracks, {album['files_moved']} files moved")
                
                # Limit display to prevent log spam
                if i >= 20:
                    remaining = len(self.complete_albums_moved) - i
                    if remaining > 0:
                        logger.info(f"     ... and {remaining} more albums")
                    break
        
        # Show albums moved to complete directory
        if self.complete_albums_moved:
            logger.info("=" * 60)
            dry_run_prefix = "ALBUMS THAT WOULD BE MOVED TO" if self.dry_run else "ALBUMS MOVED TO"
            logger.info(f"💿 {dry_run_prefix} COMPLETE (NOT_OWNED)")
            logger.info("=" * 60)
            logger.info(f"Total complete albums: {len(self.complete_albums_moved)}")
            logger.info("")
            
            for i, album in enumerate(self.complete_albums_moved, 1):
                year_str = f" [{album['year']}]" if album['year'] != 'Unknown' else ""
                logger.info(f"{i:2d}. {album['artist']} - {album['album']}{year_str}")
                files_text = f"{album['files_moved']} files {'would be ' if self.dry_run else ''}moved"
                logger.info(f"     📊 {album['track_count']} tracks, {files_text}")
                
                # Limit display to prevent log spam
                if i >= 20:
                    remaining = len(self.complete_albums_moved) - i
                    if remaining > 0:
                        logger.info(f"     ... and {remaining} more albums")
                    break

        logger.info("=" * 60)
        logger.info("✅ Processing complete")
        logger.info(f"📝 Log file: {log_file}")
        logger.info("=" * 60)
    
    def run(self) -> None:
        """Main execution flow with enhanced progress tracking."""
        start_time = datetime.now()
        
        if self.auto_mode:
            logger.info("🔄 Starting organize files (auto mode)")
        
        # Step 1: Load Lidarr data
        step_start = datetime.now()
        if not self.auto_mode:
            logger.info("PROGRESS: [1/5] 20% - Loading Lidarr monitored albums")
        if not self.load_lidarr_data():
            logger.info("❌ Failed to load Lidarr data - aborting")
            return
        
        step_duration = (datetime.now() - step_start).total_seconds()
        if not self.auto_mode:
            logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
            logger.info("")
        
        # Step 2: Build comprehensive track database from all directories
        step_start = datetime.now()
        if not self.auto_mode:
            logger.info("PROGRESS: [2/5] 40% - Building comprehensive track database")
        track_database = self.build_track_database()
        
        if not track_database:
            if self.auto_mode:
                logger.info("ℹ️  No albums found to process")
            else:
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
        
        # Step 5: Database cleanup - remove entries for deleted files
        step_start = datetime.now()
        logger.info("PROGRESS: [5/6] 83% - Database cleanup")
        deleted_entries = self.cleanup_deleted_file_entries()
        
        step_duration = (datetime.now() - step_start).total_seconds()
        logger.info(f"   ⏱️  Step completed in {step_duration:.1f}s")
        logger.info("")
        
        # Step 6: System cleanup
        step_start = datetime.now()
        logger.info("PROGRESS: [6/6] 100% - System cleanup")
        system_files_removed, empty_dirs_removed = self.cleanup_system_files_and_directories()
        
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
        
        # Add cleanup stats
        if system_files_removed > 0 or empty_dirs_removed > 0 or deleted_entries > 0:
            cleanup_parts = []
            if deleted_entries > 0:
                cleanup_parts.append(f"{deleted_entries} database entries")
            if system_files_removed > 0:
                cleanup_parts.append(f"{system_files_removed} system files")
            if empty_dirs_removed > 0:
                cleanup_parts.append(f"{empty_dirs_removed} empty directories")
            logger.info(f"🧹 Cleanup: {', '.join(cleanup_parts)} removed")
        
        self.print_summary()
        
        # Clean up database connection to prevent lingering locks
        if self.db:
            try:
                self.db = None
                logger.debug("🔒 Database connection cleaned up")
            except Exception as e:
                logger.debug(f"⚠️ Error cleaning up database: {e}")
    
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
        source_directories_to_check = set()  # Track directories to check for emptiness
        
        for file_path, metadata, source_dir in tracks:
            file_path = Path(file_path)  # Ensure it's a Path object
            target_file = target_album_dir / file_path.name
            
            # Skip if already in correct location
            if file_path.resolve() == target_file.resolve():
                continue
            
            if target_file.exists():
                logger.info(f"      ⚠️  Duplicate found (skipping): {file_path.name}")
                logger.info(f"         Target exists: {target_file}")
                # Skip moving, do not delete source or target
                continue
            
            try:
                # Track the source directory before moving the file
                source_parent_dir = file_path.parent
                
                shutil.move(str(file_path), str(target_file))
                self._fix_permissions(target_file)
                files_moved += 1
                
                # Add source directory to list for cleanup check
                source_directories_to_check.add(source_parent_dir)
                
            except Exception as e:
                logger.info(f"      ❌ Failed to move {file_path.name}: {e}")
                self.stats['errors'] += 1
        
        # Check and clean up empty source directories after moving files
        directories_removed = 0
        for source_dir in source_directories_to_check:
            directories_removed += self._cleanup_empty_directory_chain(source_dir)
        
        # Update statistics
        self.stats['source_dirs_removed'] += directories_removed
        
        if files_moved > 0:
            logger.info(f"      📦 Moved {files_moved} files to {target_album_dir}")
            if directories_removed > 0:
                logger.info(f"      🗑️  Removed {directories_removed} empty source directories")
        
        return files_moved
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename for filesystem."""
        if not name:
            return "Unknown"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip('. ')
    
    def _cleanup_empty_directory_chain(self, directory: Path) -> int:
        """
        Check if directory is empty and remove it, then recursively check parent directories.
        Returns the number of directories removed.
        """
        if self.dry_run:
            return 0
            
        directories_removed = 0
        current_dir = directory
        
        # Don't remove base directories (Downloads, Not_Owned, Incomplete, Owned)
        protected_dirs = {
            self.downloads_dir,
            self.music_dir,
            self.incomplete_dir,
            self.owned_dir,
            self.downloads_dir.parent,  # In case these are subdirectories
            self.music_dir.parent,
            self.incomplete_dir.parent,
            self.owned_dir.parent
        }
        
        while current_dir and current_dir not in protected_dirs:
            try:
                # Check if directory exists and is empty
                if not current_dir.exists():
                    break
                
                # Check if directory is empty (no files or subdirectories)
                if any(current_dir.iterdir()):
                    break  # Directory is not empty, stop checking parent dirs
                
                # Directory is empty, try to remove it
                current_dir.rmdir()
                directories_removed += 1
                logger.debug(f"      🗑️  Removed empty directory: {current_dir}")
                
                # Move up to parent directory
                parent_dir = current_dir.parent
                if parent_dir == current_dir:  # Reached filesystem root
                    break
                current_dir = parent_dir
                
            except OSError as e:
                # Directory not empty or permission denied, stop here
                logger.debug(f"      ⚠️ Could not remove directory {current_dir}: {e}")
                break
            except Exception as e:
                logger.debug(f"      ❌ Error checking directory {current_dir}: {e}")
                break
        
        return directories_removed
    
    def _get_audio_quality_score(self, file_path: Path) -> float:
        """Calculate quality score for audio file comparison."""
        try:
            audio = MutagenFile(file_path)
            if not audio or not audio.info:
                return 0.0
            
            score = 0.0
            
            # Check if it's a lossless format
            file_ext = file_path.suffix.lower()
            lossless_formats = {'.flac', '.alac', '.wav', '.aiff', '.ape', '.wv'}
            is_lossless = file_ext in lossless_formats
            
            # Lossless formats get a huge boost
            if is_lossless:
                score += 100000
            
            # Bitrate contributes significantly
            bitrate = getattr(audio.info, 'bitrate', 0)
            if bitrate:
                score += bitrate / 1000  # Convert to kbps
            
            # Sample rate contributes
            sample_rate = getattr(audio.info, 'sample_rate', 0)
            if sample_rate:
                score += sample_rate / 100
            
            # File size as tiebreaker (larger usually means better quality)
            file_size = file_path.stat().st_size
            score += file_size / (1024 * 1024)  # Convert to MB
            
            return score
            
        except Exception as e:
            logger.warning(f"Could not get quality score for {file_path}: {e}")
            # Return file size as fallback
            try:
                return file_path.stat().st_size / (1024 * 1024)
            except:
                return 0.0
    
    def _compare_and_handle_duplicate(self, source_file: Path, target_file: Path) -> bool:
        """Compare two files and keep the better quality version.
        
        Returns True if source should replace target, False if target should be kept.
        """
        try:
            source_score = self._get_audio_quality_score(source_file)
            target_score = self._get_audio_quality_score(target_file)
            
            logger.info(f"      Quality comparison: {source_file.name}")
            logger.info(f"        Source score: {source_score:.2f}")
            logger.info(f"        Target score: {target_score:.2f}")
            
            if source_score > target_score:
                logger.info(f"      ✅ Source is better quality, will replace target")
                return True
            else:
                logger.info(f"      ❌ Target is better quality, keeping existing file")
                return False
                
        except Exception as e:
            logger.warning(f"Error comparing files {source_file} vs {target_file}: {e}")
            # Fallback: compare file sizes
            try:
                source_size = source_file.stat().st_size
                target_size = target_file.stat().st_size
                logger.info(f"      Fallback: comparing file sizes ({source_size} vs {target_size})")
                return source_size > target_size
            except:
                return False
    
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
    parser.add_argument('--auto-mode', action='store_true', 
                       help='Run in automated mode with reduced output for cron jobs')
    
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
            dry_run=args.dry_run,
            auto_mode=args.auto_mode
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