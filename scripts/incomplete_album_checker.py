#!/usr/bin/env python3
"""
Incomplete Album Checker

Checks albums in Music/Not_Owned against Lidarr to identify incomplete albums
and moves them to Music/Incomplete/{Artist} for better organization.
Also automatically moves single-track folders (likely singles) to keep 
Not_Owned focused on albums and EPs.

Name: Incomplete Album Checker
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: organization, lidarr, cleanup
Supports dry run: true
"""

import os
import sys
import json
import shutil
import logging
import argparse
import requests
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple

# Configure logging
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f'incomplete_album_checker_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)

# Supported audio formats
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus', '.wma', '.aac'}

class IncompleteAlbumChecker:
    """Main class for checking and organizing incomplete albums."""
    
    def __init__(self, music_dir: str = '/media/Not_Owned', incomplete_dir: str = '/media/Incomplete',
                 lidarr_url: str = None, lidarr_api_key: str = None, dry_run: bool = False, bidirectional: bool = True):
        """
        Initialize the Incomplete Album Checker.
        
        Args:
            music_dir: Root directory containing music files (Not_Owned) - default: /media/Not_Owned
            incomplete_dir: Directory to move incomplete albums to - default: /media/Incomplete
            lidarr_url: Lidarr base URL
            lidarr_api_key: Lidarr API key
            dry_run: If True, only simulate changes without applying them
            bidirectional: If True, also check Incomplete folder and move complete albums back to Not_Owned
        """
        self.music_dir = Path(music_dir).resolve()
        self.incomplete_dir = Path(incomplete_dir).resolve()
        self.lidarr_url = lidarr_url.rstrip('/') if lidarr_url else os.environ.get('LIDARR_URL', '').rstrip('/')
        self.lidarr_api_key = lidarr_api_key or os.environ.get('LIDARR_API_KEY', '')
        self.dry_run = dry_run
        self.bidirectional = bidirectional
        
        # File permission settings for services (configurable via env vars)
        self.target_uid = int(os.environ.get('TARGET_UID', '1000'))
        self.target_gid = int(os.environ.get('TARGET_GID', '1000'))
        self.file_mode = 0o644  # rw-r--r--
        self.dir_mode = 0o755   # rwxr-xr-x
        
        # Action history for tracking moves
        self.action_history = []
        
        # Statistics
        self.stats = {
            'albums_checked': 0,
            'incomplete_albums': 0,
            'moved_albums': 0,
            'errors': 0,
            'lidarr_matches': 0,
            'permissions_fixed': 0,
            'monitored_albums': 0,
            'unmonitored_albums': 0
        }
        
        # Cache for Lidarr data
        self.lidarr_albums = {}
        self.lidarr_artists = {}
        
        if not self.music_dir.exists():
            raise ValueError(f"Music directory does not exist: {music_dir}")
        
        # Create incomplete directory if it doesn't exist
        if not self.incomplete_dir.exists():
            logger.info(f"Creating incomplete directory: {self.incomplete_dir}")
            self.incomplete_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.lidarr_url or not self.lidarr_api_key:
            raise ValueError("Lidarr URL and API key are required")
        
        logger.info(f"Initialized Incomplete Album Checker")
        logger.info(f"  Music Directory: {self.music_dir} (scanning for albums)")
        logger.info(f"  Incomplete Directory: {self.incomplete_dir} (destination for incomplete albums)")
        logger.info(f"  Lidarr URL: {self.lidarr_url}")
        logger.info(f"  Target UID:GID: {self.target_uid}:{self.target_gid}")
        logger.info(f"  Dry Run: {self.dry_run}")
        logger.info(f"  Note: Using filesystem discovery, not Lidarr paths")
    
    def record_action(self, action_type: str, artist_name: str, album_name: str, 
                     source_path: str, destination_path: str, track_count: int = None):
        """
        Record an action in the history for tracking purposes.
        
        Args:
            action_type: Type of action ("moved_to_incomplete", "moved_to_complete")
            artist_name: Artist name
            album_name: Album name
            source_path: Source path (relative to base directory)
            destination_path: Destination path (relative to base directory)
            track_count: Number of tracks in the album
        """
        from datetime import datetime
        
        action = {
            'timestamp': datetime.now().isoformat(),
            'action': action_type,
            'artist': artist_name,
            'album': album_name,
            'source': source_path,
            'destination': destination_path,
            'track_count': track_count,
            'dry_run': self.dry_run
        }
        
        self.action_history.append(action)
        
        # Log the action
        if self.dry_run:
            logger.info(f"[DRY RUN] Action recorded: {action_type} - {artist_name} - {album_name}")
        else:
            logger.info(f"Action recorded: {action_type} - {artist_name} - {album_name}")
    
    def fix_permissions(self, path: Path) -> bool:
        """
        Fix file/directory permissions to work with configured target user/group.
        
        Args:
            path: Path to file or directory
            
        Returns:
            True if permissions were fixed successfully
        """
        try:
            if not path.exists():
                return False
            
            current_stat = path.stat()
            needs_fix = (current_stat.st_uid != self.target_uid or 
                        current_stat.st_gid != self.target_gid)
            
            # Check if we're running as root (required to change ownership)
            if needs_fix and os.geteuid() != 0:
                logger.warning(f"Cannot fix ownership for {path} - not running as root")
                logger.warning(f"Current: {current_stat.st_uid}:{current_stat.st_gid}, Target: {self.target_uid}:{self.target_gid}")
                return False
            
            if not self.dry_run and needs_fix:
                # Change ownership
                os.chown(path, self.target_uid, self.target_gid)
                
                # Set appropriate permissions
                if path.is_file():
                    path.chmod(self.file_mode)
                elif path.is_dir():
                    path.chmod(self.dir_mode)
                
                self.stats['permissions_fixed'] += 1
                logger.debug(f"Fixed permissions: {path} -> {self.target_uid}:{self.target_gid}")
                return True
            elif self.dry_run and needs_fix:
                logger.debug(f"[DRY RUN] Would fix permissions: {path} -> {self.target_uid}:{self.target_gid}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error fixing permissions for {path}: {e}")
            return False
    
    def make_lidarr_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """
        Make a request to Lidarr API.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response or None if failed
        """
        try:
            url = f"{self.lidarr_url}/api/v1/{endpoint}"
            headers = {'X-Api-Key': self.lidarr_api_key}
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Lidarr API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error making Lidarr request: {e}")
            return None
    
    def load_lidarr_data(self) -> bool:
        """
        Load artists and albums from Lidarr.
        
        Returns:
            True if data loaded successfully
        """
        logger.info("Loading data from Lidarr...")
        
        # Load artists
        artists_data = self.make_lidarr_request('artist')
        if artists_data is None:
            logger.error("Failed to load artists from Lidarr")
            return False
        
        for artist in artists_data:
            artist_name = artist.get('artistName', '').lower()
            self.lidarr_artists[artist_name] = artist
        
        logger.info(f"Loaded {len(self.lidarr_artists)} artists from Lidarr")
        
        # Load albums
        albums_data = self.make_lidarr_request('album')
        if albums_data is None:
            logger.error("Failed to load albums from Lidarr")
            return False
        
        for album in albums_data:
            artist_name = album.get('artist', {}).get('artistName', '').lower()
            album_title = album.get('title', '').lower()
            key = f"{artist_name}|||{album_title}"
            self.lidarr_albums[key] = album
        
        logger.info(f"Loaded {len(self.lidarr_albums)} albums from Lidarr")
        return True
    
    def find_album_folders(self, search_dir: Path = None) -> List[Path]:
        """
        Find all album folders in the specified directory.
        
        Args:
            search_dir: Directory to search in (defaults to self.music_dir)
        
        Returns:
            List of album folder paths
        """
        if search_dir is None:
            search_dir = self.music_dir
            
        album_folders = []
        
        # Look for artist/album structure
        for artist_dir in search_dir.iterdir():
            if not artist_dir.is_dir():
                continue
            
            for potential_album in artist_dir.iterdir():
                if not potential_album.is_dir():
                    continue
                
                # Check if this folder contains audio files
                audio_files = [f for f in potential_album.rglob('*') 
                              if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
                
                if audio_files:
                    album_folders.append(potential_album)
        
        return album_folders
    
    def parse_album_info(self, album_path: Path) -> Tuple[str, str, int]:
        """
        Parse artist and album name from folder path and count tracks.
        
        Args:
            album_path: Path to album folder
            
        Returns:
            Tuple of (artist_name, album_name, track_count)
        """
        try:
            artist_name = album_path.parent.name
            album_name = album_path.name
            
            # Remove year prefix if present (e.g., "[1975] Album Name" -> "Album Name")
            import re
            album_clean = re.sub(r'^\[\d{4}\]\s*', '', album_name)
            
            # Count audio files in the album folder
            audio_files = [f for f in album_path.rglob('*') 
                          if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
            
            return artist_name, album_clean, len(audio_files)
            
        except Exception as e:
            logger.error(f"Error parsing album info for {album_path}: {e}")
            return "Unknown", "Unknown", 0
    
    def check_album_completeness(self, artist_name: str, album_name: str, local_track_count: int) -> Optional[bool]:
        """
        Check if an album is complete by comparing with Lidarr data.
        
        Args:
            artist_name: Artist name
            album_name: Album name
            local_track_count: Number of tracks found locally
            
        Returns:
            True if complete, False if incomplete, None if not found in Lidarr
        """
        try:
            # Create lookup key
            artist_key = artist_name.lower()
            album_key = album_name.lower()
            lookup_key = f"{artist_key}|||{album_key}"
            
            # Try exact match first
            if lookup_key in self.lidarr_albums:
                album_data = self.lidarr_albums[lookup_key]
                expected_tracks = self._get_expected_track_count(album_data)
                
                # Log monitoring status
                is_monitored = album_data.get('monitored', False)
                album_status = album_data.get('status', 'Unknown')
                logger.debug(f"Lidarr album status: {artist_name} - {album_name} | Monitored: {is_monitored} | Status: {album_status} | Tracks: {local_track_count}/{expected_tracks}")
                
                # Track monitoring statistics
                if is_monitored:
                    self.stats['monitored_albums'] += 1
                else:
                    self.stats['unmonitored_albums'] += 1
                
                self.stats['lidarr_matches'] += 1
                logger.debug(f"Found exact match: {artist_name} - {album_name} ({local_track_count}/{expected_tracks} tracks)")
                
                return local_track_count >= expected_tracks
            
            # Try fuzzy matching
            for key, album_data in self.lidarr_albums.items():
                stored_artist, stored_album = key.split('|||', 1)
                
                if (self._similarity_match(artist_key, stored_artist) and 
                    self._similarity_match(album_key, stored_album)):
                    
                    expected_tracks = self._get_expected_track_count(album_data)
                    
                    # Log monitoring status for fuzzy matches too
                    is_monitored = album_data.get('monitored', False)
                    album_status = album_data.get('status', 'Unknown')
                    logger.debug(f"Lidarr album status (fuzzy): {artist_name} - {album_name} | Monitored: {is_monitored} | Status: {album_status} | Tracks: {local_track_count}/{expected_tracks}")
                    
                    # Track monitoring statistics
                    if is_monitored:
                        self.stats['monitored_albums'] += 1
                    else:
                        self.stats['unmonitored_albums'] += 1
                    
                    self.stats['lidarr_matches'] += 1
                    logger.debug(f"Found fuzzy match: {artist_name} - {album_name} -> {album_data.get('artist', {}).get('artistName', '')} - {album_data.get('title', '')} ({local_track_count}/{expected_tracks} tracks)")
                    
                    return local_track_count >= expected_tracks
            
            logger.debug(f"No Lidarr match found for: {artist_name} - {album_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error checking album completeness: {e}")
            return None
    
    def _get_expected_track_count(self, album_data: Dict) -> int:
        """
        Get the expected number of tracks for an album from Lidarr data.
        
        Args:
            album_data: Album data from Lidarr API
            
        Returns:
            Expected number of tracks
        """
        # Method 1: Try to get track count from album's total track count
        if 'trackCount' in album_data:
            return album_data['trackCount']
        
        # Method 2: Get detailed track information from the API
        album_id = album_data.get('id')
        if album_id:
            # Get detailed album info including all tracks
            detailed_album = self.make_lidarr_request(f'album/{album_id}')
            if detailed_album:
                # Count tracks from the detailed response
                if 'trackCount' in detailed_album:
                    logger.debug(f"Got track count from detailed album: {detailed_album['trackCount']}")
                    return detailed_album['trackCount']
                
                # If still no trackCount, try to get tracks directly
                tracks = self.make_lidarr_request('track', {'albumId': album_id})
                if tracks and isinstance(tracks, list):
                    track_count = len(tracks)
                    logger.debug(f"Counted tracks from track list: {track_count}")
                    return track_count
        
        # Method 3: Fallback to statistics (what Lidarr has, not ideal)
        stats_count = album_data.get('statistics', {}).get('trackCount', 0)
        if stats_count > 0:
            logger.warning(f"Using statistics.trackCount as fallback: {stats_count}")
            return stats_count
        
        # Method 4: Last resort - assume incomplete if we can't determine
        logger.warning(f"Could not determine expected track count for album: {album_data.get('title', 'Unknown')}")
        return 999  # High number to avoid false positives
    
    def _similarity_match(self, str1: str, str2: str, threshold: float = 0.8) -> bool:
        """
        Check if two strings are similar enough to be considered a match.
        
        Args:
            str1: First string
            str2: Second string
            threshold: Similarity threshold (0.0 to 1.0)
            
        Returns:
            True if strings are similar enough
        """
        # Simple similarity check - can be enhanced with more sophisticated algorithms
        if str1 == str2:
            return True
        
        # Check if one string is contained in the other
        if str1 in str2 or str2 in str1:
            return True
        
        # Basic character-based similarity
        longer = max(str1, str2, key=len)
        shorter = min(str1, str2, key=len)
        
        if len(longer) == 0:
            return True
        
        # Count matching characters
        matches = sum(1 for a, b in zip(longer, shorter) if a == b)
        similarity = matches / len(longer)
        
        return similarity >= threshold
    
    def move_album_to_incomplete(self, album_path: Path, artist_name: str, album_name: str = None, track_count: int = None) -> bool:
        """
        Move an incomplete album to the incomplete directory.
        
        Args:
            album_path: Path to the album folder
            artist_name: Artist name for organizing
            album_name: Album name for action history
            track_count: Number of tracks for action history
            
        Returns:
            True if moved successfully
        """
        try:
            # Get album name if not provided
            if album_name is None:
                album_name = album_path.name
            
            # Create target directory structure
            target_artist_dir = self.incomplete_dir / artist_name
            target_album_path = target_artist_dir / album_path.name
            
            if not self.dry_run:
                # Create directories
                target_artist_dir.mkdir(parents=True, exist_ok=True)
                self.fix_permissions(target_artist_dir)
                
                # Check if target already exists
                if target_album_path.exists():
                    logger.warning(f"Target already exists: {target_album_path}")
                    return False
                
                logger.info(f"Moving album from: {album_path}")
                logger.info(f"                to: {target_album_path}")
                
                # Move the album folder
                shutil.move(str(album_path), str(target_album_path))
                
                # Verify the move was successful
                if target_album_path.exists():
                    logger.info(f"✅ Move successful: {target_album_path}")
                else:
                    logger.error(f"❌ Move failed: {target_album_path} does not exist after move")
                
                # Fix permissions on moved content
                self.fix_permissions(target_album_path)
                for item in target_album_path.rglob('*'):
                    self.fix_permissions(item)
                
                logger.info(f"Moved incomplete album: {album_path.relative_to(self.music_dir)} -> {target_album_path.relative_to(self.incomplete_dir)}")
            else:
                logger.info(f"[DRY RUN] Would move: {album_path.relative_to(self.music_dir)} -> {target_artist_dir.name}/{album_path.name}")
            
            # Record the action in history
            self.record_action(
                action_type="moved_to_incomplete",
                artist_name=artist_name,
                album_name=album_name,
                source_path=str(album_path.relative_to(self.music_dir)),
                destination_path=f"{artist_name}/{album_path.name}",
                track_count=track_count
            )
            
            self.stats['moved_albums'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Error moving album {album_path}: {e}")
            self.stats['errors'] += 1
            return False
    
    def move_album_to_complete(self, album_path: Path, artist_name: str, album_name: str = None, track_count: int = None) -> bool:
        """
        Move a complete album from the incomplete directory back to the main music directory.
        
        Args:
            album_path: Path to the album folder in incomplete directory
            artist_name: Artist name for organizing
            album_name: Album name for action history
            track_count: Number of tracks for action history
            
        Returns:
            True if moved successfully
        """
        try:
            # Get album name if not provided
            if album_name is None:
                album_name = album_path.name
            
            # Create target directory structure in main music directory
            target_artist_dir = self.music_dir / artist_name
            target_album_path = target_artist_dir / album_path.name
            
            if not self.dry_run:
                # Create directories
                target_artist_dir.mkdir(parents=True, exist_ok=True)
                self.fix_permissions(target_artist_dir)
                
                # Check if target already exists
                if target_album_path.exists():
                    logger.warning(f"Target already exists: {target_album_path}")
                    return False
                
                logger.info(f"Moving complete album from: {album_path}")
                logger.info(f"                        to: {target_album_path}")
                
                # Move the album folder
                shutil.move(str(album_path), str(target_album_path))
                
                # Verify the move was successful
                if target_album_path.exists():
                    logger.info(f"✅ Move successful: {target_album_path}")
                else:
                    logger.error(f"❌ Move failed: {target_album_path} does not exist after move")
                
                # Fix permissions on moved content
                self.fix_permissions(target_album_path)
                for item in target_album_path.rglob('*'):
                    self.fix_permissions(item)
                
                logger.info(f"Moved complete album: {album_path.relative_to(self.incomplete_dir)} -> {target_album_path.relative_to(self.music_dir)}")
            else:
                logger.info(f"[DRY RUN] Would move complete: {album_path.relative_to(self.incomplete_dir)} -> {target_artist_dir.name}/{album_path.name}")
            
            # Record the action in history
            self.record_action(
                action_type="moved_to_complete",
                artist_name=artist_name,
                album_name=album_name,
                source_path=str(album_path.relative_to(self.incomplete_dir)),
                destination_path=f"{artist_name}/{album_path.name}",
                track_count=track_count
            )
            
            self.stats['moved_albums'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Error moving complete album {album_path}: {e}")
            self.stats['errors'] += 1
            return False
    
    def cleanup_empty_dirs(self) -> int:
        """
        Remove empty directories after moving albums.
        
        Returns:
            Number of directories removed
        """
        removed = 0
        
        # Cleanup main music directory
        removed += self._cleanup_empty_dirs_in_path(self.music_dir, "music")
        
        # Also cleanup incomplete directory if bidirectional is enabled
        if self.bidirectional:
            removed += self._cleanup_empty_dirs_in_path(self.incomplete_dir, "incomplete")
        
        return removed
    
    def _cleanup_empty_dirs_in_path(self, base_path: Path, path_type: str) -> int:
        """
        Helper method to cleanup empty directories in a specific path.
        
        Args:
            base_path: The base path to clean up
            path_type: Description for logging (e.g., "music", "incomplete")
            
        Returns:
            Number of directories removed
        """
        removed = 0
        
        # Walk bottom-up to handle nested empty directories
        for dirpath, dirnames, filenames in os.walk(base_path, topdown=False):
            current_dir = Path(dirpath)
            
            # Skip the root directory
            if current_dir == base_path:
                continue
            
            # Check if directory is empty
            try:
                if not any(current_dir.iterdir()):
                    if not self.dry_run:
                        current_dir.rmdir()
                        logger.info(f"Removed empty {path_type} directory: {current_dir.relative_to(base_path)}")
                    else:
                        logger.info(f"[DRY RUN] Would remove empty {path_type} directory: {current_dir.relative_to(base_path)}")
                    removed += 1
            except Exception as e:
                logger.debug(f"Could not remove {path_type} directory {current_dir}: {e}")
        
        return removed
    
    def check_and_organize_albums(self) -> None:
        """
        Main method to check all albums and move incomplete ones.
        """
        logger.info(f"\n{'='*60}")
        logger.info("Starting incomplete album check...")
        logger.info(f"{'='*60}\n")
        
        # Load Lidarr data
        if not self.load_lidarr_data():
            logger.error("Failed to load Lidarr data - aborting")
            return
        
        # Find all album folders
        album_folders = self.find_album_folders()
        logger.info(f"Found {len(album_folders)} album folders to check\n")
        
        # Check each album
        for i, album_path in enumerate(album_folders, 1):
            try:
                self.stats['albums_checked'] += 1
                
                if i % 50 == 0:
                    logger.info(f"Progress: {i}/{len(album_folders)} albums checked...")
                
                # Parse album information
                artist_name, album_name, track_count = self.parse_album_info(album_path)
                
                # Check if this folder only has 1 track (likely a single)
                if track_count == 1:
                    logger.info(f"Single track found: {artist_name} - {album_name} (1 track) - moving to incomplete")
                    self.stats['incomplete_albums'] += 1
                    self.stats['single_tracks'] = self.stats.get('single_tracks', 0) + 1
                    
                    # Move to incomplete directory (singles should not be in Not_Owned)
                    self.move_album_to_incomplete(album_path, artist_name, album_name, track_count)
                    continue
                
                # Check completeness against Lidarr for multi-track albums
                is_complete = self.check_album_completeness(artist_name, album_name, track_count)
                
                if is_complete is False:  # Explicitly incomplete
                    logger.info(f"Incomplete album found: {artist_name} - {album_name} ({track_count} tracks)")
                    self.stats['incomplete_albums'] += 1
                    
                    # Move to incomplete directory
                    self.move_album_to_incomplete(album_path, artist_name, album_name, track_count)
                
                elif is_complete is True:
                    logger.debug(f"Complete album: {artist_name} - {album_name} ({track_count} tracks)")
                
                else:  # Not found in Lidarr
                    logger.debug(f"Not in Lidarr: {artist_name} - {album_name} ({track_count} tracks)")
                
            except Exception as e:
                logger.error(f"Error processing {album_path}: {e}")
                self.stats['errors'] += 1
        
        # Check for complete albums in incomplete directory (bidirectional)
        if self.bidirectional:
            logger.info(f"\n{'='*60}")
            logger.info("Checking incomplete directory for complete albums...")
            logger.info(f"{'='*60}\n")
            
            incomplete_albums = self.find_album_folders(self.incomplete_dir)
            logger.info(f"Found {len(incomplete_albums)} albums in incomplete directory to check\n")
            
            for i, album_path in enumerate(incomplete_albums, 1):
                try:
                    if i % 50 == 0:
                        logger.info(f"Progress: {i}/{len(incomplete_albums)} incomplete albums checked...")
                    
                    # Parse album information
                    artist_name, album_name, track_count = self.parse_album_info(album_path)
                    
                    # Skip single tracks - they should stay in incomplete
                    if track_count == 1:
                        continue
                    
                    # Check completeness against Lidarr
                    is_complete = self.check_album_completeness(artist_name, album_name, track_count)
                    
                    if is_complete is True:  # Album is now complete
                        logger.info(f"Complete album found in incomplete: {artist_name} - {album_name} ({track_count} tracks)")
                        self.stats['complete_albums'] = self.stats.get('complete_albums', 0) + 1
                        
                        # Move back to main music directory
                        self.move_album_to_complete(album_path, artist_name, album_name, track_count)
                        
                except Exception as e:
                    logger.error(f"Error processing incomplete album {album_path}: {e}")
                    self.stats['errors'] += 1
        
        # Clean up empty directories
        logger.info("\nCleaning up empty directories...")
        removed_dirs = self.cleanup_empty_dirs()
        logger.info(f"Removed {removed_dirs} empty directories")
        
        # Print summary
        self.print_summary()
    
    def print_summary(self) -> None:
        """Print summary statistics."""
        logger.info(f"\n{'='*60}")
        logger.info("INCOMPLETE ALBUM CHECK COMPLETE - SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Albums checked:       {self.stats['albums_checked']:,}")
        logger.info(f"Incomplete albums:    {self.stats['incomplete_albums']:,}")
        logger.info(f"  - Single tracks:    {self.stats.get('single_tracks', 0):,}")
        logger.info(f"  - Incomplete albums: {self.stats['incomplete_albums'] - self.stats.get('single_tracks', 0):,}")
        logger.info(f"Albums moved:         {self.stats['moved_albums']:,}")
        
        if self.bidirectional and self.stats.get('complete_albums', 0) > 0:
            logger.info(f"Complete albums found: {self.stats.get('complete_albums', 0):,}")
        
        logger.info(f"Lidarr matches:       {self.stats['lidarr_matches']:,}")
        logger.info(f"  - Monitored:        {self.stats['monitored_albums']:,}")
        logger.info(f"  - Unmonitored:      {self.stats['unmonitored_albums']:,}")
        logger.info(f"Permissions fixed:    {self.stats['permissions_fixed']:,}")
        logger.info(f"Errors encountered:   {self.stats['errors']:,}")
        
        if self.stats['albums_checked'] > 0:
            incomplete_rate = (self.stats['incomplete_albums'] / self.stats['albums_checked']) * 100
            logger.info(f"Incomplete rate:      {incomplete_rate:.1f}%")
        
        if self.stats['lidarr_matches'] > 0:
            monitoring_rate = (self.stats['monitored_albums'] / self.stats['lidarr_matches']) * 100
            logger.info(f"Monitoring rate:      {monitoring_rate:.1f}%")
        
        # Print action history if any actions were taken
        if self.action_history:
            logger.info(f"\n{'='*60}")
            logger.info("ACTION HISTORY")
            logger.info(f"{'='*60}")
            
            for action in self.action_history:
                action_desc = "Moved to Incomplete" if action['action'] == 'moved_to_incomplete' else "Moved to Complete"
                dry_run_prefix = "[DRY RUN] " if action['dry_run'] else ""
                track_info = f" ({action['track_count']} tracks)" if action['track_count'] else ""
                
                logger.info(f"{dry_run_prefix}{action_desc}: {action['artist']} - {action['album']}{track_info}")
                logger.info(f"    From: {action['source']}")
                logger.info(f"    To:   {action['destination']}")
                logger.info(f"    Time: {action['timestamp']}")
                logger.info("")
        
        logger.info(f"{'='*60}\n")
    
    def save_action_history(self, output_file: str = None) -> None:
        """
        Save action history to a JSON file.
        
        Args:
            output_file: Output file path (default: action_history_YYYYMMDD_HHMMSS.json)
        """
        if not self.action_history:
            logger.info("No actions to save - action history is empty")
            return
        
        if output_file is None:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"action_history_{timestamp}.json"
        
        try:
            import json
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'metadata': {
                        'total_actions': len(self.action_history),
                        'dry_run': self.dry_run,
                        'bidirectional': self.bidirectional,
                        'music_dir': str(self.music_dir),
                        'incomplete_dir': str(self.incomplete_dir)
                    },
                    'actions': self.action_history
                }, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Action history saved to: {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to save action history: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check albums against Lidarr and move incomplete ones to separate folder",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--music-dir',
        default='/media/Not_Owned',
        help='Root directory containing music files (default: /media/Not_Owned)'
    )
    
    parser.add_argument(
        '--incomplete-dir',
        default='/media/Incomplete',
        help='Directory to move incomplete albums to (default: /media/Incomplete)'
    )
    
    parser.add_argument(
        '--lidarr-url',
        help='Lidarr base URL (default: LIDARR_URL env var)'
    )
    
    parser.add_argument(
        '--lidarr-api-key',
        help='Lidarr API key (default: LIDARR_API_KEY env var)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate changes without applying them'
    )
    
    parser.add_argument(
        '--bidirectional',
        action='store_true',
        default=True,
        help='Also check incomplete folder and move complete albums back (default: True)'
    )
    
    parser.add_argument(
        '--no-bidirectional',
        action='store_true',
        help='Disable bidirectional checking (only move incomplete albums to incomplete folder)'
    )
    
    parser.add_argument(
        '--save-history',
        help='Save action history to specified JSON file (optional filename)'
    )
    
    args = parser.parse_args()
    
    # Check for DRY_RUN environment variable (from web interface)
    if os.environ.get('DRY_RUN') == 'true':
        args.dry_run = True
    
    # Handle bidirectional flag
    bidirectional = args.bidirectional and not args.no_bidirectional
    
    try:
        checker = IncompleteAlbumChecker(
            music_dir=args.music_dir,
            incomplete_dir=args.incomplete_dir,
            lidarr_url=args.lidarr_url,
            lidarr_api_key=args.lidarr_api_key,
            dry_run=args.dry_run,
            bidirectional=bidirectional
        )
        
        checker.check_and_organize_albums()
        
        # Save action history if requested
        if args.save_history:
            if args.save_history == "":
                checker.save_action_history()  # Use default filename
            else:
                checker.save_action_history(args.save_history)  # Use specified filename
        
    except KeyboardInterrupt:
        logger.info("\n\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()