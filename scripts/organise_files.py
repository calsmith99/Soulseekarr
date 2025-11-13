#!/usr/bin/env python3
"""
Organise Files

Comprehensive music file organization across Owned, Not_Owned, and Incomplete directories.
Removes duplicate tracks (preferring highest quality), ensures proper album organization,
and maintains integrity of the Owned directory while organizing other collections.

Directory Logic:
- Owned: Protected directory - only check for missing tracks, never move/delete
- Not_Owned: Should contain only complete albums after processing
- Incomplete: Should contain only albums with missing tracks after processing

Duplicate Detection:
- Finds duplicate tracks with same name but different formats across all directories
- Prefers lossless formats (FLAC, ALAC) over lossy (MP3, AAC, OGG)
- Prefers higher bitrates within same format category
- Safely removes lower quality duplicates

Name: Organise Files
Author: SoulSeekarr
Version: 3.0
Section: commands
Tags: organization, lidarr, cleanup, duplicates, quality, owned, incomplete
Supports dry run: true
"""

import os
import sys
import re
import json
import shutil
import logging
import argparse
import requests
# For reading audio metadata
try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm = None

# Add parent directory to path to import settings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from settings import (
    get_lidarr_config, 
    get_target_uid, 
    get_target_gid, 
    is_dry_run,
    get_owned_directory,
    get_not_owned_directory,
    get_incomplete_directory
)
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple

# Configure logging
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f'organise_files_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

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

class FileOrganiser:
    def check_folder_structure_with_metadata(self, search_dirs=None):
        """
        Check if files are in the correct folder structure based on MusicBrainz Picard metadata.
        Moves files to correct location unless running in dry run mode.
        Args:
            search_dirs: List of directories to check (default: [self.music_dir, self.incomplete_dir])
                        Note: Owned directory is excluded by default as it's protected
        """
        if MutagenFile is None:
            logger.error("mutagen is not installed. Please install mutagen to use metadata checking.")
            return

        if search_dirs is None:
            # Exclude owned directory by default as it's protected
            search_dirs = [self.music_dir, self.incomplete_dir]

        mismatches = []
        checked = 0
        moved_files = 0
        errors = 0
        
        for base_dir in search_dirs:
            if not base_dir.exists():
                continue
                
            logger.info(f"Checking folder structure in: {base_dir}")
            
            # Collect all audio files first for progress bar
            audio_files = [f for f in base_dir.rglob('*') 
                          if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
            
            logger.info(f"Found {len(audio_files)} audio files to check")
            
            # Create progress bar
            if TQDM_AVAILABLE:
                files_iter = tqdm(audio_files, desc="Checking metadata", unit="file", ncols=100)
            else:
                files_iter = audio_files
            
            for file_path in files_iter:
                checked += 1
                
                tags = self._extract_musicbrainz_metadata(file_path)
                if not tags:
                    logger.debug(f"No metadata found for: {file_path}")
                    continue
                
                # Get current path structure
                current_artist_dir = file_path.parent.parent.name
                current_album_dir = file_path.parent.name
                current_track_name = file_path.stem

                # Get metadata tags
                artist_tag = tags.get('artist')
                album_tag = tags.get('album')
                title_tag = tags.get('title')

                # Check if any tag is missing
                if not artist_tag or not album_tag:
                    logger.debug(f"Missing essential metadata for: {file_path}")
                    continue

                # Sanitize folder names for filesystem
                safe_artist = self._sanitize_filename(artist_tag)
                safe_album = self._sanitize_filename(album_tag)
                safe_title = self._sanitize_filename(title_tag) if title_tag else file_path.stem

                # Check if file is in correct location
                artist_mismatch = safe_artist != current_artist_dir
                album_mismatch = safe_album != current_album_dir
                title_mismatch = title_tag and safe_title != current_track_name

                if artist_mismatch or album_mismatch or title_mismatch:
                    # Calculate correct path
                    correct_artist_dir = base_dir / safe_artist
                    correct_album_dir = correct_artist_dir / safe_album
                    
                    # Use original filename if no title tag or if we're not renaming files
                    new_filename = file_path.name  # Keep original filename
                    correct_file_path = correct_album_dir / new_filename

                    mismatch_info = {
                        'file': str(file_path),
                        'artist_tag': artist_tag,
                        'album_tag': album_tag,
                        'title_tag': title_tag,
                        'current_artist': current_artist_dir,
                        'current_album': current_album_dir,
                        'current_title': current_track_name,
                        'correct_path': str(correct_file_path),
                        'artist_mismatch': artist_mismatch,
                        'album_mismatch': album_mismatch,
                        'title_mismatch': title_mismatch
                    }
                    mismatches.append(mismatch_info)

                    # Move file if not in dry run mode
                    if not self.dry_run:
                        try:
                            # Create target directories
                            correct_album_dir.mkdir(parents=True, exist_ok=True)
                            self.fix_permissions(correct_artist_dir)
                            self.fix_permissions(correct_album_dir)

                            # Check if target file already exists
                            if correct_file_path.exists():
                                logger.warning(f"Target file already exists, skipping: {correct_file_path}")
                                continue

                            # Move the file
                            shutil.move(str(file_path), str(correct_file_path))
                            self.fix_permissions(correct_file_path)
                            
                            logger.info(f"Moved: {file_path.relative_to(base_dir)} -> {correct_file_path.relative_to(base_dir)}")
                            moved_files += 1

                            # Record the action
                            self.record_action(
                                action_type="moved_for_metadata_compliance",
                                artist_name=artist_tag,
                                album_name=album_tag,
                                source_path=str(file_path),
                                destination_path=str(correct_file_path)
                            )

                        except Exception as e:
                            logger.error(f"Failed to move {file_path}: {e}")
                            errors += 1
                    else:
                        logger.info(f"[DRY RUN] Would move: {file_path.relative_to(base_dir)} -> {correct_file_path.relative_to(base_dir)}")

        # Clean up empty directories after moves
        if not self.dry_run and moved_files > 0:
            logger.info("Cleaning up empty directories after file moves...")
            removed_dirs = self.cleanup_empty_dirs()
            logger.info(f"Removed {removed_dirs} empty directories")

        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info("FOLDER STRUCTURE CHECK COMPLETE")
        logger.info(f"{'='*60}")
        logger.info(f"Files checked:        {checked:,}")
        logger.info(f"Mismatches found:     {len(mismatches):,}")
        logger.info(f"Files moved:          {moved_files:,}")
        if errors > 0:
            logger.warning(f"Errors encountered:   {errors:,}")
        
        if self.dry_run and mismatches:
            logger.info(f"[DRY RUN] Would move {len(mismatches)} files to correct locations")
        elif not mismatches:
            logger.info("All files are already in correct folder structure based on metadata")
            
        return moved_files

    def _sanitize_filename(self, name):
        """
        Sanitize a filename/folder name for filesystem compatibility.
        
        Args:
            name: String to sanitize
            
        Returns:
            Sanitized string safe for filesystem use
        """
        if not name:
            return "Unknown"
        
        # Replace invalid characters with underscores
        invalid_chars = '<>:"/\\|?*'
        sanitized = name
        
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Remove leading/trailing dots and spaces
        sanitized = sanitized.strip('. ')
        
        # Ensure it's not empty
        if not sanitized:
            return "Unknown"
            
        return sanitized

    def _extract_musicbrainz_metadata(self, file_path):
        """
        Extract artist, album, and title from audio file using mutagen.
        Returns dict with keys: artist, album, title
        """
        try:
            audio = MutagenFile(file_path)
            if not audio or not audio.tags:
                return None
            tags = {}
            # Try common tag keys
            for key in ['artist', 'album', 'title']:
                value = None
                for tag_key in [key, f'TAG:{key}', f'TIT2' if key=='title' else key.upper()]:
                    if tag_key in audio.tags:
                        v = audio.tags[tag_key]
                        if isinstance(v, list):
                            value = v[0]
                        else:
                            value = str(v)
                        break
                if not value:
                    # Try ID3 frames for MP3
                    if key == 'artist' and 'TPE1' in audio.tags:
                        value = str(audio.tags['TPE1'])
                    elif key == 'album' and 'TALB' in audio.tags:
                        value = str(audio.tags['TALB'])
                    elif key == 'title' and 'TIT2' in audio.tags:
                        value = str(audio.tags['TIT2'])
                if value:
                    tags[key] = value.strip()
            return tags if tags else None
        except Exception as e:
            logger.debug(f"Failed to extract metadata from {file_path}: {e}")
            return None
    """Main class for organizing music files across Owned, Not_Owned, and Incomplete directories."""
    
    def __init__(self, owned_dir: str = None, music_dir: str = None, 
                 incomplete_dir: str = None, lidarr_url: str = None, 
                 lidarr_api_key: str = None, dry_run: bool = False, bidirectional: bool = True):
        """
        Initialize the File Organiser.
        
        Args:
            owned_dir: Protected directory with owned music (never modified)
            music_dir: Directory containing music files to be organized (Not_Owned)
            incomplete_dir: Directory for incomplete albums
            lidarr_url: Lidarr base URL
            lidarr_api_key: Lidarr API key
            dry_run: If True, only simulate changes without applying them
            bidirectional: If True, also check Incomplete folder and move complete albums back
        """
        # Get directories from settings with fallback to parameters or defaults
        self.owned_dir = Path(owned_dir or get_owned_directory()).resolve()
        self.music_dir = Path(music_dir or get_not_owned_directory()).resolve()
        self.incomplete_dir = Path(incomplete_dir or get_incomplete_directory()).resolve()
        
        # Get Lidarr configuration from settings with fallback to parameters
        lidarr_config = get_lidarr_config()
        self.lidarr_url = (lidarr_url.rstrip('/') if lidarr_url 
                          else lidarr_config.get('url', '').rstrip('/'))
        self.lidarr_api_key = lidarr_api_key or lidarr_config.get('api_key', '')
        
        self.dry_run = dry_run
        self.bidirectional = bidirectional
        
        # File permission settings for services (configurable via settings/env vars)
        self.target_uid = get_target_uid()
        self.target_gid = get_target_gid()
        self.file_mode = 0o644  # rw-r--r--
        self.dir_mode = 0o755   # rwxr-xr-x
        
        # Action history for tracking moves
        self.action_history = []
        
        # Track missing tracks in Owned directory
        self.owned_missing_tracks = {}  # {album_path: [missing_track_names]}
        
        # Statistics
        self.stats = {
            'albums_checked': 0,
            'incomplete_albums': 0,
            'moved_albums': 0,
            'errors': 0,
            'lidarr_matches': 0,
            'permissions_fixed': 0,
            'monitored_albums': 0,
            'unmonitored_albums': 0,
            'duplicates_removed': 0,
            'duplicates_found_in_owned': 0,
            'owned_albums_checked': 0,
            'owned_missing_tracks': 0,
            'cross_directory_duplicates_removed': 0
        }
        
        # Cache for Lidarr data
        self.lidarr_albums = {}
        self.lidarr_artists = {}
        
        # Validate directories
        if not self.music_dir.exists():
            raise ValueError(f"Not_Owned directory does not exist: {music_dir}")
        
        if not self.owned_dir.exists():
            logger.warning(f"Owned directory does not exist: {owned_dir}")
        
        # Create incomplete directory if it doesn't exist
        if not self.incomplete_dir.exists():
            logger.info(f"Creating incomplete directory: {self.incomplete_dir}")
            self.incomplete_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.lidarr_url or not self.lidarr_api_key:
            raise ValueError("Lidarr URL and API key are required")
        
        logger.info(f"Initialized File Organiser")
        logger.info(f"  Owned Directory: {self.owned_dir} (protected - only check for missing tracks)")
        logger.info(f"  Not_Owned Directory: {self.music_dir} (will contain complete albums only)")
        logger.info(f"  Incomplete Directory: {self.incomplete_dir} (will contain incomplete albums only)")
        logger.info(f"  Lidarr URL: {self.lidarr_url}")
        logger.info(f"  Target UID:GID: {self.target_uid}:{self.target_gid}")
        logger.info(f"  Dry Run: {self.dry_run}")
    
    def get_audio_quality_score(self, file_path: Path) -> int:
        """
        Calculate a quality score for an audio file based on format and bitrate.
        Higher score means better quality.
        
        Returns:
            Quality score (higher = better)
        """
        ext = file_path.suffix.lower()
        
        # Format hierarchy (higher score = better quality)
        format_scores = {
            '.flac': 1000,      # Lossless
            '.alac': 950,       # Lossless (Apple)
            '.ape': 900,        # Lossless 
            '.wav': 850,        # Uncompressed
            '.aiff': 800,       # Uncompressed
            '.dsd': 950,        # High-res lossless
            '.m4a': 600,        # Usually AAC, can be lossless
            '.mp3': 500,        # Lossy
            '.aac': 450,        # Lossy  
            '.ogg': 400,        # Lossy
            '.opus': 350,       # Lossy
            '.wma': 300,        # Lossy
        }
        
        base_score = format_scores.get(ext, 100)
        
        # Try to get bitrate information from filename or metadata
        # Look for bitrate indicators in filename like "320kbps", "V0", "FLAC"
        filename = file_path.stem.lower()
        
        # Bitrate bonuses (for lossy formats)
        if ext in ['.mp3', '.aac', '.ogg', '.opus', '.wma']:
            if '320' in filename or '320k' in filename:
                base_score += 50
            elif 'v0' in filename or 'v2' in filename:
                base_score += 40
            elif '256' in filename:
                base_score += 30
            elif '192' in filename:
                base_score += 20
            elif '128' in filename:
                base_score += 10
        
        return base_score
    
    def find_duplicates_in_directory(self, directory: Path) -> Dict[str, List[Path]]:
        """
        Find duplicate audio files in a directory based on track name.
        
        Args:
            directory: Directory to scan for duplicates
            
        Returns:
            Dictionary mapping track names to list of file paths
        """
        duplicates = defaultdict(list)
        
        for file_path in directory.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in AUDIO_EXTENSIONS:
                # Extract track name (remove track number and extension)
                track_name = file_path.stem
                
                # Remove common track number patterns
                # Remove patterns like "01. ", "1 - ", "Track 01", etc.
                track_name = re.sub(r'^\d+[\.\-\s]*', '', track_name)
                track_name = re.sub(r'^track\s*\d+[\.\-\s]*', '', track_name, flags=re.IGNORECASE)
                
                # Normalize whitespace and case for comparison
                normalized_name = ' '.join(track_name.split()).lower()
                
                duplicates[normalized_name].append(file_path)
        
        # Only return entries with actual duplicates
        return {name: files for name, files in duplicates.items() if len(files) > 1}
    
    def remove_duplicate_tracks(self, directory: Path) -> int:
        """
        Remove duplicate tracks in a directory, keeping the highest quality version.
        
        Args:
            directory: Directory to process
            
        Returns:
            Number of files removed
        """
        duplicates = self.find_duplicates_in_directory(directory)
        removed_count = 0
        
        if not duplicates:
            return 0
        
        logger.info(f"Found {len(duplicates)} sets of duplicate tracks in {directory}")
        
        for track_name, file_list in duplicates.items():
            if len(file_list) < 2:
                continue
                
            # Sort by quality score (highest first)
            sorted_files = sorted(file_list, key=self.get_audio_quality_score, reverse=True)
            best_file = sorted_files[0]
            duplicates_to_remove = sorted_files[1:]
            
            logger.info(f"Duplicate track '{track_name}':")
            logger.info(f"  Keeping: {best_file.name} (score: {self.get_audio_quality_score(best_file)})")
            
            for duplicate in duplicates_to_remove:
                score = self.get_audio_quality_score(duplicate)
                logger.info(f"  Removing: {duplicate.name} (score: {score})")
                
                if not self.dry_run:
                    try:
                        duplicate.unlink()
                        removed_count += 1
                        self.record_action("removed_duplicate", 
                                         directory.parent.name if directory.parent.name != directory.root else "Unknown",
                                         directory.name,
                                         str(duplicate),
                                         f"Removed duplicate of {best_file.name}")
                    except Exception as e:
                        logger.error(f"Failed to remove duplicate {duplicate}: {e}")
                        self.stats['errors'] += 1
                else:
                    removed_count += 1
        
        if removed_count > 0:
            logger.info(f"Removed {removed_count} duplicate tracks from {directory}")
            self.stats['duplicates_removed'] += removed_count
        
        return removed_count
    
    def normalize_track_name(self, track_path: Path) -> str:
        """
        Normalize a track name for cross-directory comparison.
        Includes artist and album information to avoid false positives.
        
        Args:
            track_path: Path to the audio file
            
        Returns:
            Normalized track identifier including artist, album, and track name
        """
        # Extract artist and album from path
        # Assuming structure: /media/Directory/Artist/Album/Track.ext
        album_dir = track_path.parent
        artist_dir = album_dir.parent
        
        artist_name = artist_dir.name.lower()
        album_name = album_dir.name.lower()
        track_name = track_path.stem.lower()
        
        # Remove common track number patterns from track name
        # Remove patterns like "01. ", "1 - ", "Track 01", etc.
        track_name = re.sub(r'^\d+[\.\-\s]*', '', track_name)
        track_name = re.sub(r'^track\s*\d+[\.\-\s]*', '', track_name, flags=re.IGNORECASE)
        
        # Remove common album prefixes like "[2000]", "(Deluxe)", etc.
        album_name = re.sub(r'^\[\d{4}\]\s*', '', album_name)
        album_name = re.sub(r'\s*\([^)]*\)\s*', '', album_name)
        album_name = re.sub(r'\s*\[[^\]]*\]\s*', '', album_name)  # Remove [Remastered], etc.
        album_name = re.sub(r'\s*-\s*(deluxe|remaster|anniversary|edition|expanded).*$', '', album_name, flags=re.IGNORECASE)
        
        # Normalize whitespace
        artist_name = ' '.join(artist_name.split())
        album_name = ' '.join(album_name.split())
        track_name = ' '.join(track_name.split())
        
        # Create unique identifier: artist|album|track
        normalized_identifier = f"{artist_name}|{album_name}|{track_name}"
        
        return normalized_identifier
    
    def find_cross_directory_duplicates(self) -> Dict[str, Dict[str, List[Path]]]:
        """
        Find tracks that exist in multiple directories (Owned, Not_Owned, Incomplete).
        
        Returns:
            Dictionary mapping normalized track names to directories containing the track
        """
        all_tracks = {}
        
        # Scan all three directories
        directories_to_scan = [
            ('owned', self.owned_dir),
            ('not_owned', self.music_dir),
            ('incomplete', self.incomplete_dir)
        ]
        
        for dir_type, directory in directories_to_scan:
            if not directory.exists():
                continue
                
            logger.info(f"Scanning {dir_type} directory for tracks: {directory}")
            
            for file_path in directory.rglob('*'):
                if file_path.is_file() and file_path.suffix.lower() in AUDIO_EXTENSIONS:
                    normalized_name = self.normalize_track_name(file_path)
                    
                    if normalized_name not in all_tracks:
                        all_tracks[normalized_name] = {}
                    
                    if dir_type not in all_tracks[normalized_name]:
                        all_tracks[normalized_name][dir_type] = []
                    
                    all_tracks[normalized_name][dir_type].append(file_path)
        
        # Only return tracks that exist in multiple directories
        cross_directory_duplicates = {
            name: dirs for name, dirs in all_tracks.items() 
            if len(dirs) > 1
        }
        
        logger.info(f"Found {len(cross_directory_duplicates)} exact track matches across multiple directories")
        return cross_directory_duplicates
    
    def remove_cross_directory_duplicates(self) -> int:
        """
        Remove tracks from Not_Owned and Incomplete if they exist in Owned.
        Owned directory takes precedence and its files are never deleted.
        
        Returns:
            Number of files removed
        """
        logger.info("Checking for cross-directory duplicates...")
        duplicates = self.find_cross_directory_duplicates()
        removed_count = 0
        
        if not duplicates:
            logger.info("No cross-directory duplicates found")
            return 0
        
        for track_identifier, directories in duplicates.items():
            if 'owned' not in directories:
                # No conflict with owned directory, skip
                continue
            
            # Parse the track identifier to get readable info
            try:
                artist, album, track = track_identifier.split('|')
                readable_track = f"{artist} - {album} - {track}"
            except ValueError:
                readable_track = track_identifier
            
            owned_files = directories['owned']
            logger.info(f"Duplicate track found: {readable_track}")
            for owned_file in owned_files:
                logger.info(f"  Owned version: {owned_file}")
            
            # Remove from other directories
            for dir_type in ['not_owned', 'incomplete']:
                if dir_type in directories:
                    files_to_remove = directories[dir_type]
                    
                    for file_to_remove in files_to_remove:
                        # Double-check that this is actually the same track by comparing normalized data
                        owned_identifier = self.normalize_track_name(owned_files[0])
                        remove_identifier = self.normalize_track_name(file_to_remove)
                        
                        if owned_identifier != remove_identifier:
                            logger.warning(f"  Skipping removal - identifiers don't match: {owned_identifier} vs {remove_identifier}")
                            continue
                            
                        logger.info(f"  Removing duplicate from {dir_type}: {file_to_remove}")
                        
                        if not self.dry_run:
                            try:
                                file_to_remove.unlink()
                                removed_count += 1
                                
                                # Record the action
                                album_dir = file_to_remove.parent
                                artist_name = album_dir.parent.name if album_dir.parent.name != album_dir.root else "Unknown"
                                album_name = album_dir.name
                                
                                self.record_action("removed_cross_directory_duplicate", 
                                                 artist_name,
                                                 album_name,
                                                 str(file_to_remove),
                                                 f"Removed duplicate (exists in Owned)")
                                
                            except Exception as e:
                                logger.error(f"Failed to remove cross-directory duplicate {file_to_remove}: {e}")
                                self.stats['errors'] += 1
                        else:
                            removed_count += 1
        
        if removed_count > 0:
            logger.info(f"Removed {removed_count} cross-directory duplicate tracks")
            self.stats['cross_directory_duplicates_removed'] = self.stats.get('cross_directory_duplicates_removed', 0) + removed_count
        
        return removed_count
    
    def check_duplicates_in_owned(self, directory: Path) -> int:
        """
        Check for duplicate tracks in Owned directory, logging them but NOT removing them.
        The Owned directory should never have files deleted.
        
        Args:
            directory: Directory to check for duplicates
            
        Returns:
            Number of duplicate tracks found (for statistics)
        """
        duplicates = self.find_duplicates_in_directory(directory)
        duplicates_found = 0
        
        if not duplicates:
            return 0
        
        logger.info(f"Found {len(duplicates)} sets of duplicate tracks in OWNED directory: {directory}")
        logger.warning("NOTE: Duplicates in Owned directory will NOT be removed - logging only")
        
        for track_name, file_list in duplicates.items():
            if len(file_list) < 2:
                continue
                
            # Sort by quality score (highest first)
            sorted_files = sorted(file_list, key=self.get_audio_quality_score, reverse=True)
            best_file = sorted_files[0]
            duplicates_found += len(file_list) - 1
            
            logger.warning(f"OWNED DUPLICATE: '{track_name}':")
            logger.warning(f"  Best quality: {best_file.name} (score: {self.get_audio_quality_score(best_file)})")
            
            for i, duplicate in enumerate(sorted_files[1:], 1):
                score = self.get_audio_quality_score(duplicate)
                logger.warning(f"  Duplicate #{i}: {duplicate.name} (score: {score})")
            
            # Record for history tracking but don't mark as removed
            self.record_action("found_duplicate_in_owned", 
                             directory.parent.name if directory.parent.name != directory.root else "Unknown",
                             directory.name,
                             str(best_file),
                             f"Found {len(file_list)-1} duplicates in Owned directory")
        
        if duplicates_found > 0:
            logger.warning(f"Found {duplicates_found} duplicate tracks in OWNED directory {directory}")
            logger.warning("These duplicates were NOT removed - manual cleanup may be needed")
            self.stats['duplicates_found_in_owned'] = self.stats.get('duplicates_found_in_owned', 0) + duplicates_found
        
        return duplicates_found
    
    def resolve_cross_directory_duplicates(self) -> int:
        """
        Resolve duplicates across directories, prioritizing Owned > Not_Owned > Incomplete.
        Never delete from Owned directory.
        
        Returns:
            Number of files removed
        """
        return self.remove_cross_directory_duplicates()
    
    def check_owned_directory_completeness(self) -> None:
        """
        Check albums in Owned directory for missing tracks and record them.
        Never move or delete anything from Owned directory.
        """
        if not self.owned_dir.exists():
            logger.warning("Owned directory does not exist, skipping owned completeness check")
            return
        
        logger.info(f"\n{'='*60}")
        logger.info("Checking Owned directory for missing tracks...")
        logger.info(f"{'='*60}\n")
        
        owned_albums = self.find_album_folders(self.owned_dir)
        logger.info(f"Found {len(owned_albums)} albums in Owned directory")
        
        # Create progress bar for owned albums
        if TQDM_AVAILABLE:
            owned_albums_iter = tqdm(owned_albums, desc="Checking owned albums", unit="album", ncols=100)
        else:
            owned_albums_iter = owned_albums
        
        for album_path in owned_albums_iter:
            try:
                self.stats['owned_albums_checked'] += 1
                
                artist_name, album_name, track_count = self.parse_album_info(album_path)
                
                # Check for duplicates in Owned directory (log only, never remove)
                duplicates_found = self.check_duplicates_in_owned(album_path)
                
                # Check completeness against Lidarr
                is_complete = self.check_album_completeness(artist_name, album_name, track_count)
                
                if is_complete is False:  # Explicitly incomplete
                    logger.info(f"Owned album missing tracks: {artist_name} - {album_name} ({track_count} tracks)")
                    self.owned_missing_tracks[str(album_path)] = {
                        'artist': artist_name,
                        'album': album_name,
                        'current_tracks': track_count
                    }
                    self.stats['owned_missing_tracks'] += 1
                elif is_complete is True:
                    logger.debug(f"Complete owned album: {artist_name} - {album_name} ({track_count} tracks)")
                else:
                    logger.debug(f"Owned album not in Lidarr: {artist_name} - {album_name} ({track_count} tracks)")
                    
            except Exception as e:
                logger.error(f"Error checking owned album {album_path}: {e}")
                self.stats['errors'] += 1
        
        if self.owned_missing_tracks:
            logger.info(f"\nFound {len(self.owned_missing_tracks)} incomplete albums in Owned directory")
            logger.info("These will be checked against found tracks during organization")
        else:
            logger.info("\nAll albums in Owned directory appear complete")
    
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
                source_path=str(album_path),
                destination_path=str(target_album_path),
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
                source_path=str(album_path),
                destination_path=str(target_album_path),
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
            # Filter out macOS metadata files when checking if directory is empty
            actual_files = [f for f in filenames if not f.startswith('._')]
            
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
        Main method to organize files across all directories.
        
        Process:
        1. Check Owned directory for missing tracks (no modifications)
        2. Remove duplicates across all directories (preserve Owned)
        3. Organize Not_Owned and Incomplete directories
        4. Ensure Not_Owned has only complete albums
        5. Ensure Incomplete has only incomplete albums
        """
        logger.info(f"\n{'='*60}")
        logger.info("Starting comprehensive file organization...")
        logger.info(f"{'='*60}\n")
        
        # Load Lidarr data
        if not self.load_lidarr_data():
            logger.error("Failed to load Lidarr data - aborting")
            return
        
        # Step 1: Check Owned directory for completeness (read-only)
        self.check_owned_directory_completeness()
        
        # Step 2: Remove duplicates across all directories
        logger.info(f"\n{'='*60}")
        logger.info("Removing duplicate tracks across all directories...")
        logger.info(f"{'='*60}\n")
        self.resolve_cross_directory_duplicates()
        
        # Step 3: Remove duplicates within each directory
        for directory_name, directory_path in [('Not_Owned', self.music_dir), ('Incomplete', self.incomplete_dir)]:
            if directory_path.exists():
                logger.info(f"\nRemoving duplicates within {directory_name} directory...")
                removed = self.remove_duplicate_tracks(directory_path)
        
        # Step 4: Organize Not_Owned directory
        logger.info(f"\n{'='*60}")
        logger.info("Organizing Not_Owned directory...")
        logger.info(f"{'='*60}\n")
        
        album_folders = self.find_album_folders(self.music_dir)
        logger.info(f"Found {len(album_folders)} album folders in Not_Owned to check\n")
        
        # Create progress bar for album checking
        if TQDM_AVAILABLE:
            album_folders_iter = tqdm(album_folders, desc="Organizing albums", unit="album", ncols=100)
        else:
            album_folders_iter = album_folders
        
        # Check each album
        for album_path in album_folders_iter:
            try:
                self.stats['albums_checked'] += 1
                
                # Parse album information
                artist_name, album_name, track_count = self.parse_album_info(album_path)
                
                # Remove duplicate tracks before processing
                duplicates_removed = self.remove_duplicate_tracks(album_path)
                if duplicates_removed > 0:
                    # Re-count tracks after duplicate removal
                    artist_name, album_name, track_count = self.parse_album_info(album_path)
                    logger.info(f"After duplicate removal: {artist_name} - {album_name} now has {track_count} tracks")
                
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
            
            # Create progress bar for incomplete albums
            if TQDM_AVAILABLE:
                incomplete_albums_iter = tqdm(incomplete_albums, desc="Checking incomplete", unit="album", ncols=100)
            else:
                incomplete_albums_iter = incomplete_albums
            
            for album_path in incomplete_albums_iter:
                try:
                    
                    # Parse album information
                    artist_name, album_name, track_count = self.parse_album_info(album_path)
                    
                    # Remove duplicate tracks before processing
                    duplicates_removed = self.remove_duplicate_tracks(album_path)
                    if duplicates_removed > 0:
                        # Re-count tracks after duplicate removal
                        artist_name, album_name, track_count = self.parse_album_info(album_path)
                        logger.info(f"After duplicate removal: {artist_name} - {album_name} now has {track_count} tracks")
                    
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
    
    def remove_duplicates_only(self) -> None:
        """
        Process directories to only remove duplicate tracks without checking completeness.
        """
        logger.info(f"\n{'='*60}")
        logger.info("Starting duplicate removal process...")
        logger.info(f"{'='*60}\n")
        
        # Find all album folders in both directories
        all_folders = []
        
        # Add main music directory folders
        main_folders = self.find_album_folders()
        all_folders.extend(main_folders)
        logger.info(f"Found {len(main_folders)} album folders in main directory")
        
        # Add incomplete directory folders if it exists
        if self.incomplete_dir.exists():
            incomplete_folders = self.find_album_folders(self.incomplete_dir)
            all_folders.extend(incomplete_folders)
            logger.info(f"Found {len(incomplete_folders)} album folders in incomplete directory")
        
        logger.info(f"Total: {len(all_folders)} album folders to process\n")
        
        # Create progress bar for duplicate removal
        if TQDM_AVAILABLE:
            all_folders_iter = tqdm(all_folders, desc="Removing duplicates", unit="album", ncols=100)
        else:
            all_folders_iter = all_folders
        
        # Process each folder for duplicates
        for album_path in all_folders_iter:
            try:
                
                # Only remove duplicates, don't check completeness
                duplicates_removed = self.remove_duplicate_tracks(album_path)
                
            except Exception as e:
                logger.error(f"Error processing {album_path}: {e}")
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
        logger.info("FILE ORGANIZATION COMPLETE - SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Albums checked:       {self.stats['albums_checked']:,}")
        logger.info(f"Owned albums checked: {self.stats['owned_albums_checked']:,}")
        logger.info(f"Owned missing tracks: {self.stats['owned_missing_tracks']:,}")
        
        if self.stats.get('owned_missing_tracks', 0) > 0:
            logger.warning(f"Note: {self.stats['owned_missing_tracks']} albums in Owned directory have missing tracks")
        logger.info(f"Incomplete albums:    {self.stats['incomplete_albums']:,}")
        logger.info(f"  - Single tracks:    {self.stats.get('single_tracks', 0):,}")
        logger.info(f"  - Incomplete albums: {self.stats['incomplete_albums'] - self.stats.get('single_tracks', 0):,}")
        logger.info(f"Albums moved:         {self.stats['moved_albums']:,}")
        logger.info(f"Duplicates removed:   {self.stats['duplicates_removed']:,}")
        
        if self.stats.get('cross_directory_duplicates_removed', 0) > 0:
            logger.info(f"Cross-dir duplicates: {self.stats['cross_directory_duplicates_removed']:,} (from Not_Owned/Incomplete)")
        
        if self.stats.get('duplicates_found_in_owned', 0) > 0:
            logger.warning(f"Duplicates in Owned:  {self.stats['duplicates_found_in_owned']:,} (NOT removed)")
        
        if self.stats.get('complete_albums', 0) > 0:
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
                if action['action'] == 'moved_to_incomplete':
                    action_desc = "Moved to Incomplete"
                elif action['action'] == 'moved_to_complete':
                    action_desc = "Moved to Complete"
                elif action['action'] == 'found_duplicate_in_owned':
                    action_desc = "Found Duplicate in Owned"
                elif action['action'] == 'removed_duplicate':
                    action_desc = "Removed Duplicate"
                elif action['action'] == 'removed_cross_directory_duplicate':
                    action_desc = "Removed Cross-Directory Duplicate"
                else:
                    action_desc = action['action'].replace('_', ' ').title()
                
                dry_run_prefix = "[DRY RUN] " if action['dry_run'] else ""
                track_info = f" ({action['track_count']} tracks)" if action['track_count'] else ""
                
                logger.info(f"{dry_run_prefix}{action_desc}: {action['artist']} - {action['album']}{track_info}")
                
                # For move operations, show full paths
                if action['action'] in ['moved_to_incomplete', 'moved_to_complete']:
                    logger.info(f"    From: {action['source']}")
                    logger.info(f"    To:   {action['destination']}")
                elif action['action'] == 'found_duplicate_in_owned':
                    logger.info(f"    File: {action['source']}")
                    logger.info(f"    Note: {action['destination']}")
                elif action['action'] in ['removed_duplicate', 'removed_cross_directory_duplicate']:
                    logger.info(f"    Removed: {action['source']}")
                    logger.info(f"    Reason: {action['destination']}")
                else:
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
        description="Organize music files across Owned, Not_Owned, and Incomplete directories with duplicate removal",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--owned-dir',
        default='/media/Owned',
        help='Protected directory with owned music - only checked for missing tracks (default: /media/Owned)'
    )
    
    parser.add_argument(
        '--music-dir',
        default='/media/Not_Owned',
        help='Directory containing music files to organize (default: /media/Not_Owned)'
    )
    
    parser.add_argument(
        '--incomplete-dir',
        default='/media/Incomplete',
        help='Directory for incomplete albums (default: /media/Incomplete)'
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
    
    parser.add_argument(
        '--duplicates-only',
        action='store_true',
        help='Only remove duplicate tracks, skip album completeness checks'
    )
    
    parser.add_argument(
        '--check-folder-structure',
        action='store_true',
        help='Check and fix folder structure based on MusicBrainz Picard metadata'
    )
    
    args = parser.parse_args()
    
    # Check for DRY_RUN environment variable (from web interface)
    if is_dry_run():
        args.dry_run = True
    
    # Handle bidirectional flag
    bidirectional = args.bidirectional and not args.no_bidirectional
    
    try:
        checker = FileOrganiser(
            owned_dir=args.owned_dir,
            music_dir=args.music_dir,
            incomplete_dir=args.incomplete_dir,
            lidarr_url=args.lidarr_url,
            lidarr_api_key=args.lidarr_api_key,
            dry_run=args.dry_run,
            bidirectional=bidirectional
        )
        
        # Run appropriate method based on mode
        if args.duplicates_only:
            checker.remove_duplicates_only()
        elif args.check_folder_structure:
            checker.check_folder_structure_with_metadata()
        else:
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