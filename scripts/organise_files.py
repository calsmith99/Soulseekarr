#!/usr/bin/env python3
"""
Organise Files

Comprehensive music file organization across all directories (Downloads, Owned, Not_Owned, Incomplete).
Processes downloaded files, removes duplicates, and ensures proper album organization.

Directory Logic:
- Downloads: New files - metadata checked, files with incomplete metadata stay here
- Owned: Protected directory - only check for missing tracks, never move/delete  
- Not_Owned: Should contain only complete albums after processing
- Incomplete: Should contain incomplete albums and files with valid but incomplete metadata

Processing Flow:
1. Scan Downloads folder and check metadata quality
2. Files with missing metadata (artist/album/title) stay in Downloads
3. Files with complete metadata are organized based on Lidarr album completeness
4. Check Owned directory for missing tracks (read-only)
5. Remove duplicates across directories (Owned takes precedence)
6. Organize Not_Owned and Incomplete (complete vs incomplete albums)

Name: Organise Files
Author: SoulSeekarr
Version: 4.0
Section: commands
Tags: organization, lidarr, cleanup, duplicates, downloads, metadata
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
    """Main class for organizing music files across all directories."""
    
    def __init__(self, owned_dir: str = None, music_dir: str = None, 
                 incomplete_dir: str = None, downloads_dir: str = None,
                 lidarr_url: str = None, lidarr_api_key: str = None, 
                 dry_run: bool = False):
        """Initialize the File Organiser."""
        
        logger.info("=" * 60)
        logger.info("🎵 ORGANISE FILES")
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
        
        # Tracking
        self.action_history = []
        self.owned_missing_tracks = {}
        
        # Statistics
        self.stats = {
            'downloads_processed': 0,
            'files_missing_metadata': 0,
            'albums_checked': 0,
            'incomplete_albums': 0,
            'complete_albums': 0,
            'moved_albums': 0,
            'duplicates_removed': 0,
            'owned_albums_checked': 0,
            'owned_missing_tracks': 0,
            'errors': 0
        }
        
        # Lidarr cache
        self.lidarr_albums = {}
        self.lidarr_artists = {}
        
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
            
            # Extract essential metadata (artist, album, title)
            for field in ['artist', 'album', 'title']:
                value = None
                
                # Try common tag names
                for tag_key in [field, field.upper(), f'TAG:{field}']:
                    if tag_key in tags:
                        v = tags[tag_key]
                        value = str(v[0]) if isinstance(v, list) else str(v)
                        break
                
                # Try ID3 frames for MP3
                if not value:
                    id3_map = {'artist': 'TPE1', 'album': 'TALB', 'title': 'TIT2'}
                    if id3_map.get(field) in tags:
                        value = str(tags[id3_map[field]])
                
                if value and value.strip():
                    metadata[field] = value.strip()
                else:
                    missing.append(field)
            
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
    
    def process_downloads_folder(self) -> None:
        """Process files in Downloads folder based on metadata quality."""
        if not self.downloads_dir.exists():
            logger.info("⏭️  Downloads directory does not exist, skipping")
            return
        
        logger.info("🔍 Scanning Downloads folder")
        music_files = self.get_music_files(self.downloads_dir)
        
        if not music_files:
            logger.info("   No music files found in Downloads")
            return
        
        logger.info(f"   Found {len(music_files)} music files")
        logger.info("")
        
        # Check metadata for each file
        files_with_metadata = []
        files_without_metadata = []
        
        if TQDM_AVAILABLE:
            files_iter = tqdm(music_files, desc="PROGRESS_SUB: Checking metadata", unit="file", ncols=100)
        else:
            files_iter = music_files
        
        for file_path in files_iter:
            has_metadata, metadata, missing = self.check_metadata_quality(file_path)
            
            if has_metadata:
                files_with_metadata.append((file_path, metadata))
            else:
                files_without_metadata.append((file_path, missing))
                self.stats['files_missing_metadata'] += 1
        
        logger.info(f"   ✅ {len(files_with_metadata)} files with complete metadata")
        logger.info(f"   ⚠️  {len(files_without_metadata)} files with incomplete metadata (staying in Downloads)")
        
        if files_without_metadata:
            logger.info("")
            logger.info("   Files missing metadata:")
            for file_path, missing in files_without_metadata[:10]:  # Show first 10
                logger.info(f"      {file_path.name} - missing: {', '.join(missing)}")
            if len(files_without_metadata) > 10:
                logger.info(f"      ... and {len(files_without_metadata) - 10} more")
        
        # Group files with complete metadata by album
        if files_with_metadata:
            logger.info("")
            logger.info("   📦 Grouping files by album...")
            albums = self._group_files_by_album(files_with_metadata)
            logger.info(f"   Found {len(albums)} albums")
            
            # Process each album
            self._process_download_albums(albums)
        
        self.stats['downloads_processed'] = len(files_with_metadata)
    
    def _group_files_by_album(self, files_with_metadata: List[Tuple[Path, Dict]]) -> Dict[str, List[Tuple[Path, Dict]]]:
        """Group files by artist and album."""
        albums = defaultdict(list)
        
        for file_path, metadata in files_with_metadata:
            artist = metadata['artist']
            album = metadata['album']
            album_key = f"{artist}|||{album}"
            albums[album_key].append((file_path, metadata))
        
        return dict(albums)
    
    def _process_download_albums(self, albums: Dict[str, List[Tuple[Path, Dict]]]) -> None:
        """Process albums from downloads and move to appropriate directory."""
        if TQDM_AVAILABLE:
            albums_iter = tqdm(albums.items(), desc="PROGRESS_SUB: Processing albums", unit="album", ncols=100)
        else:
            albums_iter = albums.items()
        
        for album_key, files in albums_iter:
            try:
                artist, album = album_key.split('|||', 1)
                track_count = len(files)
                
                # Check album completeness against Lidarr
                is_complete = self.check_album_completeness(artist, album, track_count)
                
                if is_complete is None:
                    # Not in Lidarr - move to incomplete for manual review
                    logger.info(f"   📂 Not in Lidarr: {artist} - {album} → Incomplete")
                    destination = self.incomplete_dir
                elif is_complete:
                    # Complete album - move to Not_Owned
                    logger.info(f"   ✅ Complete: {artist} - {album} → Not_Owned")
                    destination = self.music_dir
                else:
                    # Incomplete album - move to Incomplete
                    logger.info(f"   ⚠️  Incomplete: {artist} - {album} ({track_count} tracks) → Incomplete")
                    destination = self.incomplete_dir
                
                # Move files to destination
                if not self.dry_run:
                    self._move_files_to_album_folder(files, destination, artist, album)
                
            except Exception as e:
                logger.info(f"   ❌ Error processing album {album_key}: {e}")
                self.stats['errors'] += 1
    
    def _move_files_to_album_folder(self, files: List[Tuple[Path, Dict]], 
                                     destination_dir: Path, artist: str, album: str) -> None:
        """Move files to the appropriate album folder."""
        # Create destination structure
        safe_artist = self._sanitize_filename(artist)
        safe_album = self._sanitize_filename(album)
        
        target_artist_dir = destination_dir / safe_artist
        target_album_dir = target_artist_dir / safe_album
        
        target_album_dir.mkdir(parents=True, exist_ok=True)
        self._fix_permissions(target_artist_dir)
        self._fix_permissions(target_album_dir)
        
        # Move each file
        for file_path, metadata in files:
            target_file = target_album_dir / file_path.name
            
            if target_file.exists():
                logger.info(f"      ⚠️  Target exists, skipping: {file_path.name}")
                continue
            
            shutil.move(str(file_path), str(target_file))
            self._fix_permissions(target_file)
    
    def load_lidarr_data(self) -> bool:
        """Load artists and albums from Lidarr."""
        logger.info("🔗 Loading data from Lidarr...")
        
        # Load artists
        artists_data = self._make_lidarr_request('artist')
        if artists_data is None:
            logger.info("❌ Failed to load artists from Lidarr")
            return False
        
        for artist in artists_data:
            artist_name = artist.get('artistName', '').lower()
            self.lidarr_artists[artist_name] = artist
        
        logger.info(f"   Loaded {len(self.lidarr_artists)} artists")
        
        # Load albums
        albums_data = self._make_lidarr_request('album')
        if albums_data is None:
            logger.info("❌ Failed to load albums from Lidarr")
            return False
        
        for album in albums_data:
            artist_name = album.get('artist', {}).get('artistName', '').lower()
            album_title = album.get('title', '').lower()
            key = f"{artist_name}|||{album_title}"
            self.lidarr_albums[key] = album
        
        logger.info(f"   Loaded {len(self.lidarr_albums)} albums")
        return True
    
    def check_album_completeness(self, artist: str, album: str, local_tracks: int) -> Optional[bool]:
        """
        Check if album is complete against Lidarr.
        
        Returns:
            True if complete, False if incomplete, None if not in Lidarr
        """
        lookup_key = f"{artist.lower()}|||{album.lower()}"
        
        if lookup_key in self.lidarr_albums:
            album_data = self.lidarr_albums[lookup_key]
            expected_tracks = album_data.get('trackCount', 999)
            return local_tracks >= expected_tracks
        
        # Try fuzzy match
        for key, album_data in self.lidarr_albums.items():
            stored_artist, stored_album = key.split('|||', 1)
            if self._fuzzy_match(artist.lower(), stored_artist) and self._fuzzy_match(album.lower(), stored_album):
                expected_tracks = album_data.get('trackCount', 999)
                return local_tracks >= expected_tracks
        
        return None
    
    def check_owned_directory(self) -> None:
        """Check Owned directory for missing tracks (read-only)."""
        if not self.owned_dir.exists():
            logger.info("⏭️  Owned directory does not exist, skipping")
            return
        
        logger.info("🔍 Checking Owned directory")
        
        album_folders = self._find_album_folders(self.owned_dir)
        logger.info(f"   Found {len(album_folders)} albums")
        
        if not album_folders:
            return
        
        if TQDM_AVAILABLE:
            albums_iter = tqdm(album_folders, desc="PROGRESS_SUB: Checking owned", unit="album", ncols=100)
        else:
            albums_iter = album_folders
        
        for album_path in albums_iter:
            try:
                self.stats['owned_albums_checked'] += 1
                
                artist, album, track_count = self._parse_album_info(album_path)
                is_complete = self.check_album_completeness(artist, album, track_count)
                
                if is_complete is False:
                    logger.info(f"   ⚠️  Missing tracks: {artist} - {album} ({track_count} tracks)")
                    self.owned_missing_tracks[str(album_path)] = {
                        'artist': artist,
                        'album': album,
                        'tracks': track_count
                    }
                    self.stats['owned_missing_tracks'] += 1
                    
            except Exception as e:
                logger.info(f"   ❌ Error checking {album_path}: {e}")
                self.stats['errors'] += 1
        
        if self.owned_missing_tracks:
            logger.info(f"   Found {len(self.owned_missing_tracks)} incomplete albums in Owned")
    
    def organize_albums(self) -> None:
        """Organize albums in Not_Owned and Incomplete directories."""
        logger.info("📊 Organizing albums")
        
        # Process Not_Owned directory
        logger.info("   Checking Not_Owned directory...")
        not_owned_albums = self._find_album_folders(self.music_dir)
        logger.info(f"   Found {len(not_owned_albums)} albums")
        
        if not_owned_albums:
            self._process_album_folders(not_owned_albums, "Not_Owned")
        
        # Process Incomplete directory
        logger.info("")
        logger.info("   Checking Incomplete directory...")
        incomplete_albums = self._find_album_folders(self.incomplete_dir)
        logger.info(f"   Found {len(incomplete_albums)} albums")
        
        if incomplete_albums:
            self._process_album_folders(incomplete_albums, "Incomplete")
    
    def _process_album_folders(self, album_folders: List[Path], source_dir_name: str) -> None:
        """Process a list of album folders."""
        if TQDM_AVAILABLE:
            albums_iter = tqdm(album_folders, desc=f"PROGRESS_SUB: Processing {source_dir_name}", unit="album", ncols=100)
        else:
            albums_iter = album_folders
        
        for album_path in albums_iter:
            try:
                self.stats['albums_checked'] += 1
                
                artist, album, track_count = self._parse_album_info(album_path)
                
                # Skip single tracks
                if track_count == 1:
                    continue
                
                # Check completeness
                is_complete = self.check_album_completeness(artist, album, track_count)
                
                if source_dir_name == "Not_Owned" and is_complete is False:
                    # Incomplete album in Not_Owned - move to Incomplete
                    logger.info(f"      ⚠️  Incomplete: {artist} - {album} → Incomplete")
                    self.stats['incomplete_albums'] += 1
                    if not self.dry_run:
                        self._move_album(album_path, self.incomplete_dir, artist)
                        
                elif source_dir_name == "Incomplete" and is_complete is True:
                    # Complete album in Incomplete - move to Not_Owned
                    logger.info(f"      ✅ Complete: {artist} - {album} → Not_Owned")
                    self.stats['complete_albums'] += 1
                    if not self.dry_run:
                        self._move_album(album_path, self.music_dir, artist)
                
            except Exception as e:
                logger.info(f"      ❌ Error: {e}")
                self.stats['errors'] += 1
    
    def cleanup_empty_directories(self) -> int:
        """Remove empty directories."""
        logger.info("🧹 Cleaning up empty directories")
        
        removed = 0
        for base_dir in [self.music_dir, self.incomplete_dir, self.downloads_dir]:
            if not base_dir.exists():
                continue
            
            for dirpath, dirnames, filenames in os.walk(base_dir, topdown=False):
                current_dir = Path(dirpath)
                
                if current_dir == base_dir:
                    continue
                
                try:
                    if not any(current_dir.iterdir()):
                        if not self.dry_run:
                            current_dir.rmdir()
                        removed += 1
                except:
                    pass
        
        logger.info(f"   Removed {removed} empty directories")
        return removed
    
    def print_summary(self) -> None:
        """Print summary statistics."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Downloads processed:      {self.stats['downloads_processed']}")
        logger.info(f"Files missing metadata:   {self.stats['files_missing_metadata']}")
        logger.info(f"Albums checked:           {self.stats['albums_checked']}")
        logger.info(f"Owned albums checked:     {self.stats['owned_albums_checked']}")
        logger.info(f"Owned missing tracks:     {self.stats['owned_missing_tracks']}")
        logger.info(f"Incomplete albums found:  {self.stats['incomplete_albums']}")
        logger.info(f"Complete albums found:    {self.stats['complete_albums']}")
        logger.info(f"Albums moved:             {self.stats['moved_albums']}")
        logger.info(f"Errors:                   {self.stats['errors']}")
        logger.info("=" * 60)
        logger.info(f"📝 Log file: {log_file}")
        logger.info("=" * 60)
    
    def run(self) -> None:
        """Main execution flow."""
        # Step 1: Load Lidarr data
        logger.info("PROGRESS: [1/5] 20% - Loading Lidarr data")
        if not self.load_lidarr_data():
            logger.info("❌ Failed to load Lidarr data - aborting")
            return
        logger.info("")
        
        # Step 2: Process Downloads
        logger.info("PROGRESS: [2/5] 40% - Processing Downloads folder")
        self.process_downloads_folder()
        logger.info("")
        
        # Step 3: Check Owned directory
        logger.info("PROGRESS: [3/5] 60% - Checking Owned directory")
        self.check_owned_directory()
        logger.info("")
        
        # Step 4: Organize albums
        logger.info("PROGRESS: [4/5] 80% - Organizing albums")
        self.organize_albums()
        logger.info("")
        
        # Step 5: Cleanup
        logger.info("PROGRESS: [5/5] 100% - Cleanup")
        self.cleanup_empty_directories()
        
        # Print summary
        self.print_summary()
    
    # Helper methods
    
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
    
    def _fuzzy_match(self, str1: str, str2: str, threshold: float = 0.8) -> bool:
        """Simple fuzzy string matching."""
        if str1 == str2:
            return True
        if str1 in str2 or str2 in str1:
            return True
        
        longer = max(str1, str2, key=len)
        shorter = min(str1, str2, key=len)
        
        if len(longer) == 0:
            return True
        
        matches = sum(1 for a, b in zip(longer, shorter) if a == b)
        return (matches / len(longer)) >= threshold
    
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
    
    def _move_album(self, album_path: Path, destination_dir: Path, artist: str) -> None:
        """Move album to destination directory."""
        target_artist_dir = destination_dir / artist
        target_album_path = target_artist_dir / album_path.name
        
        if target_album_path.exists():
            return
        
        target_artist_dir.mkdir(parents=True, exist_ok=True)
        self._fix_permissions(target_artist_dir)
        
        shutil.move(str(album_path), str(target_album_path))
        
        self._fix_permissions(target_album_path)
        for item in target_album_path.rglob('*'):
            self._fix_permissions(item)
        
        self.stats['moved_albums'] += 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Organize music files across all directories with metadata checking"
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
