#!/usr/bin/env python3
"""
File Expiry Cleanup - Remove old files from incomplete and not owned music directories

This script:
1. Checks files older than CLEANUP_DAYS in /media/Incomplete and /media/Not_Owned directories
2. Verifies files/albums are not starred in Navidrome before deletion
3. Logs all actions for tracking
4. Respects starred albums and individual tracks

Name: File Expiry Cleanup
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: cleanup, expiry, navidrome
Supports dry run: true

Uses Docker mounted music volumes:
- /media/Incomplete (mounted from /mnt/storage/Music/Incomplete)
- /media/Not_Owned (mounted from /mnt/storage/Music/Not_Owned)

Does NOT interact with downloads directories - only manages music library cleanup.
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
import requests
import hashlib
import random
import string
from time import sleep
import re

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

# Add parent directory to path so we can import action_logger
sys.path.append(str(Path(__file__).parent.parent))

# Try to import settings and database
try:
    from settings import get_navidrome_config, get_incomplete_directory, get_not_owned_directory
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

try:
    from database import get_db
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

from action_logger import log_action

class FileExpiryCleanup:
    def __init__(self, cleanup_days=None, dry_run=False):
        # Try to get configuration from settings module first
        if SETTINGS_AVAILABLE:
            try:
                navidrome_config = get_navidrome_config()
                self.navidrome_url = navidrome_config.get('url')
                self.navidrome_username = navidrome_config.get('username')
                self.navidrome_password = navidrome_config.get('password')
            except Exception as e:
                print(f"Warning: Could not load from settings module: {e}")
                navidrome_config = None
        else:
            navidrome_config = None
        
        # Fall back to environment variables if settings not available
        if not navidrome_config or not all([self.navidrome_url, self.navidrome_username, self.navidrome_password]):
            self.navidrome_url = os.environ.get('NAVIDROME_URL')
            self.navidrome_username = os.environ.get('NAVIDROME_USERNAME')
            self.navidrome_password = os.environ.get('NAVIDROME_PASSWORD')
        
        # Get directory paths from settings module or environment variables
        if SETTINGS_AVAILABLE:
            try:
                self.incomplete_dir = get_incomplete_directory()
                self.not_owned_dir = get_not_owned_directory()
            except Exception as e:
                print(f"Warning: Could not load directories from settings: {e}")
                self.incomplete_dir = os.environ.get('INCOMPLETE_DIRECTORY', '/media/Incomplete')
                self.not_owned_dir = os.environ.get('NOT_OWNED_DIRECTORY', '/media/Not_Owned')
        else:
            self.incomplete_dir = os.environ.get('INCOMPLETE_DIRECTORY', '/media/Incomplete')
            self.not_owned_dir = os.environ.get('NOT_OWNED_DIRECTORY', '/media/Not_Owned')
        
        # Get cleanup days from environment or parameter
        self.cleanup_days = cleanup_days or int(os.environ.get('CLEANUP_DAYS', '30'))
        
        # Set dry run mode - respect parameter, fall back to environment variable
        if dry_run:
            self.dry_run = True
        else:
            self.dry_run = os.environ.get('DRY_RUN', 'false').lower() == 'true'
        
        # Setup logging
        self.setup_logging()
        
        # Initialize stats
        self.stats = {
            'files_scanned': 0,
            'files_deleted': 0,
            'files_skipped_starred': 0,
            'files_skipped_recent': 0,
            'albums_skipped_starred': 0,
            'errors': 0
        }
        
        # Navidrome session
        self.navidrome_token = None
        self.subsonic_salt = None
        self.subsonic_token = None
        self.starred_albums = set()
        self.starred_tracks = set()
        self.starred_content_loaded = False  # Track if we successfully loaded starred content
        
        # Album expiry tracking for UI cache
        self.album_expiry_data = {}
        
        # Validate configuration
        self._validate_config()
    
    def setup_logging(self):
        """Setup detailed logging for this script run"""
        # Create logs directory if it doesn't exist
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"file_expiry_cleanup_{timestamp}.log"
        
        # Setup logger
        self.logger = logging.getLogger('file_expiry_cleanup')
        self.logger.setLevel(logging.INFO)
        
        # Clear any existing handlers
        self.logger.handlers.clear()
        
        # File handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        
        # Console handler  
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.log_file_path = str(log_file)
        self.logger.info(f"File Expiry Cleanup logging started - Log file: {self.log_file_path}")
        
        print(f"📋 Detailed log file: {self.log_file_path}")
    
    def _validate_config(self):
        """Validate required configuration"""
        missing_vars = []
        
        if not self.navidrome_url:
            missing_vars.append("NAVIDROME_URL")
        if not self.navidrome_username:
            missing_vars.append("NAVIDROME_USERNAME")
        if not self.navidrome_password:
            missing_vars.append("NAVIDROME_PASSWORD")
            
        if missing_vars:
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            self.logger.error(error_msg)
            self.logger.error("Please set these environment variables before running the script")
            raise ValueError(error_msg)
        
        self.logger.info(f"Configuration validated - Navidrome: {self.navidrome_url}, "
                        f"Cleanup days: {self.cleanup_days}, "
                        f"Incomplete: {self.incomplete_dir}, Not owned: {self.not_owned_dir}")
    
    def authenticate_navidrome(self):
        """Authenticate with Navidrome and generate Subsonic API credentials"""
        try:
            self.logger.info("Authenticating with Navidrome...")
            
            # Generate Subsonic API token and salt
            self.subsonic_salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            token_string = self.navidrome_password + self.subsonic_salt
            self.subsonic_token = hashlib.md5(token_string.encode()).hexdigest()
            
            # Test the credentials with a simple ping
            test_url = f"{self.navidrome_url}/rest/ping"
            params = {
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'FileExpiryCleanup',
                'f': 'json'
            }
            
            response = requests.get(test_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                
                if subsonic_response.get('status') == 'ok':
                    msg = "Successfully authenticated with Navidrome"
                    print(f"    ✓ {msg}")
                    self.logger.info(msg)
                    return True
                else:
                    error = subsonic_response.get('error', {})
                    msg = f"Navidrome authentication failed: {error.get('message', 'Unknown error')}"
                    print(f"    ⚠️  {msg}")
                    self.logger.error(msg)
                    return False
            else:
                msg = f"Failed to authenticate with Navidrome. Status: {response.status_code}"
                print(f"    ⚠️  {msg}")
                self.logger.error(f"{msg}, Response: {response.text}")
                return False
                
        except Exception as e:
            msg = f"Error authenticating with Navidrome: {e}"
            print(f"    ⚠️  {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def get_starred_content(self):
        """Get all starred albums and tracks from Navidrome using Subsonic API"""
        if not self.subsonic_token or not self.subsonic_salt:
            self.logger.error("No Navidrome Subsonic credentials available")
            return False
        
        try:
            # Use Subsonic API to get starred content
            starred_url = f"{self.navidrome_url}/rest/getStarred2"
            params = {
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'FileExpiryCleanup',
                'f': 'json'
            }
            
            self.logger.info(f"Fetching starred content from: {starred_url}")
            response = requests.get(starred_url, params=params, timeout=30)
            
            self.logger.info(f"Starred content API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                
                if subsonic_response.get('status') == 'ok':
                    starred_info = subsonic_response.get('starred2', {})
                    
                    # Get starred albums
                    starred_albums_data = starred_info.get('album', [])
                    if not isinstance(starred_albums_data, list):
                        starred_albums_data = [starred_albums_data] if starred_albums_data else []
                    
                    self.logger.info(f"Starred2 API returned {len(starred_albums_data)} albums")
                    
                    for album in starred_albums_data:
                        album_name = album.get('name', '').lower()
                        artist_name = album.get('artist', '').lower()
                        album_key = f"{artist_name} - {album_name}"
                        self.starred_albums.add(album_key)
                        # Log first few albums for debugging
                        if len(self.starred_albums) <= 5:
                            self.logger.debug(f"Starred album: '{artist_name}' - '{album_name}' -> key: '{album_key}'")
                    
                    self.logger.info(f"Found {len(self.starred_albums)} starred albums")
                    print(f"    ✓ Found {len(self.starred_albums)} starred albums")
                    
                    # Sample some starred albums for debugging
                    if self.starred_albums:
                        sample_albums = list(self.starred_albums)[:3]
                        self.logger.debug(f"Sample starred album keys: {sample_albums}")
                    
                    # Get starred tracks/songs
                    starred_tracks_data = starred_info.get('song', [])
                    if not isinstance(starred_tracks_data, list):
                        starred_tracks_data = [starred_tracks_data] if starred_tracks_data else []
                    
                    self.logger.info(f"Starred2 API returned {len(starred_tracks_data)} songs")
                    
                    for track in starred_tracks_data:
                        track_title = track.get('title', '').lower()
                        artist_name = track.get('artist', '').lower()
                        album_name = track.get('album', '').lower()
                        track_key = f"{artist_name} - {album_name} - {track_title}"
                        self.starred_tracks.add(track_key)
                    
                    self.logger.info(f"Found {len(self.starred_tracks)} starred tracks")
                    print(f"    ✓ Found {len(self.starred_tracks)} starred songs")
                    
                    # Mark that we successfully loaded starred content
                    self.starred_content_loaded = True
                    return True
                else:
                    error = subsonic_response.get('error', {})
                    msg = f"Subsonic API error: {error.get('message', 'Unknown error')}"
                    self.logger.error(msg)
                    return False
            else:
                self.logger.error(f"Failed to fetch starred content. Status: {response.status_code}, Response: {response.text[:200]}")
                return False
            
        except Exception as e:
            msg = f"Error getting starred content from Navidrome: {e}"
            print(f"    ⚠️  {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def get_album_art_url(self, artist, album):
        """Get album cover art URL from Navidrome using Subsonic API"""
        if not self.subsonic_token or not self.subsonic_salt:
            self.logger.debug(f"No Subsonic credentials available for album art: {artist} - {album}")
            return None
        
        try:
            # Search for the album using search3 endpoint
            search_url = f"{self.navidrome_url}/rest/search3"
            params = {
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'FileExpiryCleanup',
                'f': 'json',
                'query': f'"{artist}" "{album}"',  # Use quoted search for better matching
                'albumCount': 10
            }
            
            self.logger.debug(f"Searching for album art: {artist} - {album}")
            response = requests.get(search_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                
                if subsonic_response.get('status') == 'ok':
                    search_results = subsonic_response.get('searchResult3', {})
                    albums = search_results.get('album', [])
                    
                    if not isinstance(albums, list):
                        albums = [albums] if albums else []
                    
                    self.logger.debug(f"Found {len(albums)} album results for {artist} - {album}")
                    
                    # Try multiple matching strategies
                    matches = []
                    album_lower = album.lower().strip()
                    artist_lower = artist.lower().strip()
                    
                    for album_result in albums:
                        result_album = album_result.get('name', '').lower().strip()
                        result_artist = album_result.get('artist', '').lower().strip()
                        album_id = album_result.get('id')
                        
                        if not album_id:
                            continue
                            
                        self.logger.debug(f"Checking album ID {album_id}: '{result_artist}' - '{result_album}'")
                        
                        # Calculate match score
                        match_score = 0
                        
                        # Exact matches get highest score
                        if result_album == album_lower and result_artist == artist_lower:
                            match_score = 100
                        # Artist exact + album contains
                        elif result_artist == artist_lower and album_lower in result_album:
                            match_score = 80
                        # Album exact + artist contains  
                        elif result_album == album_lower and artist_lower in result_artist:
                            match_score = 80
                        # Both contain each other
                        elif album_lower in result_album and artist_lower in result_artist:
                            match_score = 60
                        # Partial matches
                        elif artist_lower in result_artist or result_artist in artist_lower:
                            if album_lower in result_album or result_album in album_lower:
                                match_score = 40
                        
                        if match_score > 0:
                            matches.append({
                                'id': album_id,
                                'score': match_score,
                                'artist': result_artist,
                                'album': result_album
                            })
                    
                    # Sort by match score and try each one
                    matches.sort(key=lambda x: x['score'], reverse=True)
                    
                    for match in matches[:3]:  # Try top 3 matches
                        album_id = match['id']
                        self.logger.debug(f"Trying album ID {album_id} with score {match['score']}")
                        
                        # Test if cover art is available for this album ID
                        cover_url = f"{self.navidrome_url}/rest/getCoverArt"
                        cover_params = {
                            'u': self.navidrome_username,
                            't': self.subsonic_token,
                            's': self.subsonic_salt,
                            'v': '1.16.1',
                            'c': 'FileExpiryCleanup',
                            'id': album_id,
                            'size': 300
                        }
                        
                        try:
                            # Test the cover art URL
                            test_response = requests.get(cover_url, params=cover_params, timeout=5)
                            
                            if test_response.status_code == 200:
                                # Check if response is actual image data
                                content_type = test_response.headers.get('Content-Type', '')
                                if content_type.startswith('image/'):
                                    # Build full URL with params for future use
                                    from urllib.parse import urlencode
                                    full_url = f"{cover_url}?{urlencode(cover_params)}"
                                    self.logger.debug(f"Successfully found album art for {artist} - {album} using ID {album_id}")
                                    return full_url
                                else:
                                    self.logger.debug(f"Album ID {album_id}: Response not an image ({content_type})")
                            else:
                                # Try to parse error response
                                try:
                                    error_content = test_response.text
                                    if 'Artwork not found' in error_content:
                                        self.logger.debug(f"Album ID {album_id}: No artwork available")
                                    elif 'Album not found' in error_content:
                                        self.logger.debug(f"Album ID {album_id}: Album no longer exists")
                                    else:
                                        self.logger.debug(f"Album ID {album_id}: HTTP {test_response.status_code}")
                                except:
                                    self.logger.debug(f"Album ID {album_id}: HTTP {test_response.status_code}")
                                    
                        except Exception as e:
                            self.logger.debug(f"Error testing album ID {album_id}: {e}")
                            continue
                    
                    self.logger.debug(f"No working album art found for {artist} - {album} (tried {len(matches)} matches)")
                else:
                    error = subsonic_response.get('error', {})
                    self.logger.debug(f"Subsonic API error for {artist} - {album}: {error.get('message', 'Unknown error')}")
            else:
                self.logger.debug(f"HTTP error searching for {artist} - {album}: {response.status_code}")
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Error getting album art for {artist} - {album}: {e}")
            return None
    
    def is_file_old_enough(self, file_path):
        """Check if file is older than cleanup_days"""
        try:
            file_stat = os.stat(file_path)
            file_age = datetime.now() - datetime.fromtimestamp(file_stat.st_mtime)
            cutoff_date = timedelta(days=self.cleanup_days)
            
            is_old = file_age > cutoff_date
            
            return is_old
            
        except Exception as e:
            self.logger.error(f"Error checking file age for {file_path}: {e}")
            return False
    
    def extract_music_info_from_path(self, file_path):
        """Extract artist, album, and track info from file path
        Expected format: /Artist/[year] Album/01. trackname.ext
        Or with subfolders: /Artist/[year] Album/CD1/01. trackname.ext
        """
        try:
            path_obj = Path(file_path)
            parts = list(path_obj.parts)
            
            # Debug logging
            self.logger.debug(f"Original path parts: {parts}")
            
            # Find the start of music structure by looking for known base directories
            music_start_idx = -1
            base_dirs = ['Incomplete', 'Not_Owned', 'Owned', 'Downloads', 'Completed']
            
            for i, part in enumerate(parts):
                if any(base_dir.lower() in part.lower() for base_dir in base_dirs):
                    music_start_idx = i + 1
                    break
            
            # If no base directory found, try to find after /media
            if music_start_idx == -1:
                for i, part in enumerate(parts):
                    if part.lower() == 'media' and i + 1 < len(parts):
                        music_start_idx = i + 1
                        break
            
            # Fallback: assume music structure starts after first few system directories
            if music_start_idx == -1:
                # Skip drive letters, common mount points
                for i, part in enumerate(parts):
                    if not (part.lower() in ['', 'media', 'mnt', 'storage', 'music'] or ':' in part):
                        music_start_idx = i
                        break
            
            if music_start_idx == -1 or music_start_idx >= len(parts) - 1:
                self.logger.debug(f"Could not find music structure start in {file_path}")
                return "", "", ""
            
            # Extract music parts (skip filename)
            music_parts = parts[music_start_idx:-1]  # Exclude the filename
            self.logger.debug(f"Music parts: {music_parts}")
            
            if len(music_parts) < 2:
                self.logger.debug(f"Not enough music parts: {music_parts}")
                return "", "", ""
                
            # Extract artist (first directory in music structure)
            artist = music_parts[0]
            
            # Extract album (second directory, may contain year)
            album_dir = music_parts[1]
            
            # Clean up album name - remove year brackets if present
            album = album_dir
            if album.startswith('[') and ']' in album:
                # Remove [year] prefix like "[2013] Album Name"
                album = album.split(']', 1)[1].strip()
            elif len(album) >= 4 and album[0:4].isdigit() and ' - ' in album:
                # Remove "year - " prefix like "2013 - Album Name"
                album = album.split(' - ', 1)[1].strip()
            elif len(album) >= 5 and album[0:4].isdigit() and album[4] == ' ':
                # Remove "year " prefix like "2013 Album Name"
                album = album[5:].strip()
            
            # Track title from filename
            track = path_obj.stem
            # Remove track numbers like "01. " or "1 - "
            if '. ' in track and len(track.split('.')[0].strip()) <= 3 and track.split('.')[0].strip().isdigit():
                track = track.split('.', 1)[1].strip()
            elif ' - ' in track and len(track.split(' - ')[0].strip()) <= 3 and track.split(' - ')[0].strip().isdigit():
                track = track.split(' - ', 1)[1].strip()
            
            self.logger.debug(f"Extracted from {file_path}: Artist='{artist}', Album='{album}', Track='{track}'")
            
            return artist, album, track
            
        except Exception as e:
            self.logger.error(f"Error extracting music info from path {file_path}: {e}")
            return "", "", ""
    
    def normalize_for_comparison(self, text):
        """Normalize text for comparison between filesystem and Navidrome data.
        
        This handles the common filesystem character replacements that occur
        when albums/artists are saved to disk.
        """
        if not text:
            return ""
        
        # Create a normalized version that maps filesystem characters back to originals
        normalized = text
        
        # Common filesystem character replacements
        replacements = {
            '_': '/',      # Horizons_West -> Horizons/West
            ' - ': ': ',   # Album - Subtitle -> Album: Subtitle  
            ' _ ': ' / ',  # Artist _ Album -> Artist / Album
        }
        
        for fs_char, original_char in replacements.items():
            normalized = normalized.replace(fs_char, original_char)
        
        return normalized.lower()

    def is_content_starred(self, file_path):
        """Check if the file or its album is starred in Navidrome"""
        try:
            # If we couldn't load starred content, be cautious and treat everything as starred
            if not self.starred_content_loaded:
                self.logger.warning(f"Starred content not loaded - treating file as starred for safety: {file_path}")
                return True
            
            # Try to get metadata from audio file first
            metadata = self.get_audio_metadata(file_path)
            
            if metadata:
                # Use metadata from audio file (most accurate)
                artist = metadata['artist'].lower()
                album = metadata['album'].lower()
                track = metadata.get('title', '').lower()
            else:
                # Fall back to filesystem path parsing
                artist_fs, album_fs, track_fs = self.extract_music_info_from_path(file_path)
                
                if not artist_fs:
                    # Can't determine if starred without artist info - be safe
                    self.logger.debug(f"No artist info extracted - treating as starred for safety: {file_path}")
                    return True
                
                # Use filesystem names and try normalization
                artist = artist_fs.lower()
                album = album_fs.lower()
                track = track_fs.lower()
                
                # Also try the normalized version for filesystem names
                artist_normalized = self.normalize_for_comparison(artist_fs)
                album_normalized = self.normalize_for_comparison(album_fs)
                track_normalized = self.normalize_for_comparison(track_fs)
            
            # Check if album is starred (direct comparison first)
            album_key = f"{artist} - {album}"
            if album_key in self.starred_albums:
                self.logger.debug(f"Album is starred: {album_key}")
                return True
            
            # If using filesystem names, also try normalized versions
            if not metadata:
                album_key_normalized = f"{artist_normalized} - {album_normalized}"
                if album_key_normalized in self.starred_albums:
                    self.logger.debug(f"Album is starred (normalized): {album_key_normalized}")
                    return True
            
            # Debug: log failed album matches
            self.logger.debug(f"Album NOT starred - checking '{album_key}' against {len(self.starred_albums)} starred albums")
            # Show similar album names for debugging
            for starred_key in self.starred_albums:
                if artist in starred_key:
                    self.logger.debug(f"  Found starred album for artist: '{starred_key}'")
            
            # Check if individual track is starred (direct comparison)
            track_key = f"{artist} - {album} - {track}"
            if track_key in self.starred_tracks:
                self.logger.debug(f"Track is starred: {track_key}")
                return True
            
            # If using filesystem names, also try normalized version
            if not metadata:
                track_key_normalized = f"{artist_normalized} - {album_normalized} - {track_normalized}"
                if track_key_normalized in self.starred_tracks:
                    self.logger.debug(f"Track is starred (normalized): {track_key_normalized}")
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking if content is starred for {file_path}: {e}")
            # On error, assume starred to be safe (don't delete)
            return True
    
    def get_audio_metadata(self, file_path):
        """Extract artist, album, and track metadata from audio file"""
        if not MutagenFile:
            self.logger.debug("Mutagen not available - falling back to filesystem parsing")
            return None
            
        try:
            audio = MutagenFile(file_path)
            if not audio or not audio.tags:
                self.logger.debug(f"No audio tags found in: {file_path}")
                return None
            
            tags = audio.tags
            metadata = {}
            
            # Extract essential metadata
            for field in ['albumartist', 'artist', 'album', 'title']:
                value = None
                
                # Try common tag names for FLAC and MP3
                tag_variants = {
                    'albumartist': ['albumartist', 'ALBUMARTIST', 'TPE2'],
                    'artist': ['artist', 'ARTIST', 'TPE1'],
                    'album': ['album', 'ALBUM', 'TALB'],
                    'title': ['title', 'TITLE', 'TIT2']
                }
                
                for tag_key in tag_variants.get(field, [field]):
                    if tag_key in tags:
                        v = tags[tag_key]
                        value = str(v[0]) if isinstance(v, list) else str(v)
                        break
                
                if value and value.strip():
                    metadata[field] = value.strip()
            
            # Use albumartist if available, fallback to artist
            if 'albumartist' in metadata:
                metadata['artist'] = metadata['albumartist']  # Override artist with albumartist
            
            # Must have artist and album at minimum
            if not metadata.get('artist') or not metadata.get('album'):
                self.logger.debug(f"Missing essential metadata (artist/album) in: {file_path}")
                return None
            
            self.logger.debug(f"Extracted metadata from {file_path}: {metadata['artist']} - {metadata['album']}")
            return metadata
            
        except Exception as e:
            self.logger.debug(f"Error extracting metadata from {file_path}: {e}")
            return None

    def get_navidrome_album_metadata(self, artist, album_path_name):
        """Get original album metadata from Navidrome using Subsonic API"""
        if not self.subsonic_token or not self.subsonic_salt:
            return None
        
        try:
            # Search for the album using search3 endpoint
            search_url = f"{self.navidrome_url}/rest/search3"
            params = {
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'FileExpiryCleanup',
                'f': 'json',
                'query': f"{artist}",  # Search by artist to find albums
                'albumCount': 50
            }
            
            response = requests.get(search_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                
                if subsonic_response.get('status') == 'ok':
                    search_result = subsonic_response.get('searchResult3', {})
                    albums = search_result.get('album', [])
                    
                    if albums:
                        # Look for the best match
                        for album_data in albums:
                            navidrome_album = album_data.get('name', '').lower()
                            navidrome_artist = album_data.get('artist', '').lower()
                            
                            # Check if this matches our filesystem album
                            if (navidrome_artist == artist.lower() and 
                                (navidrome_album == album_path_name.lower() or 
                                 self.normalize_for_comparison(navidrome_album) == album_path_name.lower() or
                                 self.normalize_for_comparison(album_path_name) == navidrome_album)):
                                
                                self.logger.debug(f"Found Navidrome metadata: {navidrome_artist} - {navidrome_album}")
                                return {
                                    'original_artist': album_data.get('artist', artist),
                                    'original_album': album_data.get('name', album_path_name),
                                    'navidrome_id': album_data.get('id'),
                                    'cover_art': album_data.get('coverArt')
                                }
                    
                    self.logger.debug(f"No Navidrome match found for: {artist} - {album_path_name}")
                    return None
                else:
                    self.logger.debug(f"Navidrome search failed: {subsonic_response.get('error', {})}")
                    return None
            else:
                self.logger.debug(f"Navidrome search request failed: {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.debug(f"Error getting Navidrome album metadata: {e}")
            return None

    def track_album_expiry(self, file_path):
        """Track album expiry data for UI display"""
        try:
            # Get file age in days
            file_stat = os.stat(file_path)
            file_age = datetime.now() - datetime.fromtimestamp(file_stat.st_mtime)
            days_old = file_age.days
            days_until_expiry = self.cleanup_days - days_old
            
            # Try to get metadata from audio file first
            metadata = self.get_audio_metadata(file_path)
            
            if metadata:
                # Use metadata from audio file (most reliable)
                artist = metadata['artist']
                album = metadata['album'] 
                track = metadata.get('title', '')
                source = "metadata"
                self.logger.debug(f"Using audio metadata: {artist} - {album}")
            else:
                # Fall back to filesystem path parsing
                artist, album, track = self.extract_music_info_from_path(file_path)
                source = "filesystem"
                self.logger.debug(f"Using filesystem parsing: {artist} - {album}")
            
            if not artist or not album:
                self.logger.debug(f"Could not extract artist/album info from {file_path}")
                return
                
            # Create album key using the extracted names
            album_key = f"{artist} - {album}"
            
            # Check if this file/album is starred
            is_starred = self.is_content_starred(file_path)
            
            # Get album directory path (Artist/Album)
            path_parts = Path(file_path).parts
            album_dir = None
            
            # Try to find the actual album directory in the path
            for i, part in enumerate(path_parts):
                # Look for artist match (case insensitive)
                if part.lower() == artist.lower() and i + 1 < len(path_parts):
                    # The next part should be the album directory
                    album_dir_full = path_parts[i + 1]
                    album_dir = f"{part}/{album_dir_full}"
                    break
            
            if not album_dir:
                # Fallback: construct directory path
                artist_fs, album_fs, _ = self.extract_music_info_from_path(file_path)
                if artist_fs and album_fs:
                    album_dir = f"{artist_fs}/{album_fs}"
                else:
                    album_dir = f"{artist}/{album}"
            
            # Track this album's expiry data using metadata names
            if album_key not in self.album_expiry_data:
                self.album_expiry_data[album_key] = {
                    'artist': artist,                    # Original artist name from metadata
                    'album': album,                      # Original album name from metadata  
                    'metadata_source': source,           # Track where the data came from
                    'oldest_file_days': days_old,
                    'days_until_expiry': days_until_expiry,
                    'file_count': 0,
                    'total_size_mb': 0,
                    'directory': album_dir,
                    'will_expire': days_until_expiry <= 0,
                    'is_starred': is_starred,  # Set starred status from the start
                    'tracks': []  # Track all files in album
                }
            else:
                # Update if this file is older
                if days_old > self.album_expiry_data[album_key]['oldest_file_days']:
                    self.album_expiry_data[album_key]['oldest_file_days'] = days_old
                    self.album_expiry_data[album_key]['days_until_expiry'] = days_until_expiry
                    self.album_expiry_data[album_key]['will_expire'] = days_until_expiry <= 0
                
                # Update starred status - if ANY file in album is starred, mark album as starred
                if is_starred:
                    self.album_expiry_data[album_key]['is_starred'] = True
            
            # Add file info
            self.album_expiry_data[album_key]['file_count'] += 1
            
            # Get file size
            try:
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                self.album_expiry_data[album_key]['total_size_mb'] += file_size_mb
            except:
                file_size_mb = 0
            
            # Store individual track information
            self.album_expiry_data[album_key]['tracks'].append({
                'file_path': str(file_path),
                'file_name': Path(file_path).name,
                'track_title': track if track else Path(file_path).stem,
                'file_size_mb': file_size_mb,
                'days_old': days_old,
                'last_modified': datetime.fromtimestamp(file_stat.st_mtime)
            })
            
        except Exception as e:
            self.logger.error(f"Error tracking album expiry for {file_path}: {e}")
    
    def save_album_expiry_cache(self):
        """Save album expiry data to database."""
        try:
            if not DATABASE_AVAILABLE:
                self.logger.error("Database not available - cannot save album expiry data")
                print("❌ Database not available - cannot save album expiry data")
                return
            
            # Log what we're about to save
            total_albums = len(self.album_expiry_data)
            total_tracks = sum(len(d.get('tracks', [])) for d in self.album_expiry_data.values())
            
            if total_albums == 0:
                self.logger.info("No albums to save to database")
                print("💾 No album expiry data to save (no albums found)")
                return
            
            print(f"💾 Saving {total_albums} albums with {total_tracks} tracks to database...")
            
            db = get_db()
            saved_count = 0
            
            # Create progress bar for album processing
            album_items = list(self.album_expiry_data.items())
            
            if TQDM_AVAILABLE:
                album_progress = tqdm(album_items,
                                    desc="PROGRESS_MAIN: Saving albums to database",
                                    unit="album", ncols=100, position=0)
            else:
                album_progress = album_items
            
            albums_with_art = 0
            
            try:
                for album_key, data in album_progress:
                    # Get album art URL from Navidrome
                    album_art_url = self.get_album_art_url(data['artist'], data['album'])
                    
                    if album_art_url:
                        albums_with_art += 1
                        if not TQDM_AVAILABLE:
                            print(f"    🎨 Found album art for {data['artist']} - {data['album']}")
                        self.logger.info(f"Found album art for {data['artist']} - {data['album']}")
                    else:
                        self.logger.debug(f"No album art found for {data['artist']} - {data['album']}")
                    
                    album_record = {
                        'album_key': album_key,
                        'artist': data['artist'],
                        'album': data['album'],
                        'directory': data['directory'],
                        'album_art_url': album_art_url,
                        'oldest_file_days': data['oldest_file_days'],
                        'days_until_expiry': data['days_until_expiry'],
                        'file_count': data['file_count'],
                        'total_size_mb': data['total_size_mb'],
                        'is_starred': data['is_starred'],
                        'cleanup_days': self.cleanup_days,
                        'status': 'starred' if data['is_starred'] else 'pending'
                    }
                    album_id = db.upsert_expiring_album(album_record)
                    
                    # Clear existing tracks for this album
                    db.clear_album_tracks(album_id)
                    
                    # Add all tracks
                    for track in data.get('tracks', []):
                        db.add_album_track(album_id, track)
                    
                    saved_count += 1
                    
                    # Update progress bar
                    if TQDM_AVAILABLE:
                        album_progress.set_postfix({
                            'Saved': saved_count,
                            'With_Art': albums_with_art,
                            'Art_%': f"{(albums_with_art/saved_count)*100:.1f}" if saved_count > 0 else "0"
                        })
                        
            finally:
                if TQDM_AVAILABLE:
                    album_progress.close()
                
            print(f"    ✅ Saved {saved_count} albums")
            print(f"    🎨 Found album art for {albums_with_art} albums ({(albums_with_art/saved_count)*100:.1f}%)" if saved_count > 0 else "")
            
            self.logger.info(f"Saved {saved_count} albums with {total_tracks} tracks to database")
            print(f"✅ Saved {saved_count} albums with all track details to database")
            
        except Exception as e:
            self.logger.error(f"Error saving album expiry data to database: {e}")
            print(f"❌ Error saving to database: {e}")

    def delete_file(self, file_path):
        """Delete a file with logging"""
        filename = Path(file_path).name
        
        if self.dry_run:
            print(f"    [DRY RUN] Would delete expired file: {filename}")
            self.logger.info(f"DRY RUN: Would delete {file_path}")
            self.stats['files_deleted'] += 1
            return True
        
        try:
            os.remove(file_path)
            print(f"    🗑️  Deleted expired file: {filename}")
            self.logger.info(f"Deleted expired file: {file_path}")
            log_action("file_delete", "Deleted expired file", {
                "file": filename,
                "path": str(file_path),
                "reason": "expired"
            })
            self.stats['files_deleted'] += 1
            return True
            
        except Exception as e:
            msg = f"Failed to delete {filename}: {e}"
            print(f"    ⚠️  {msg}")
            self.logger.error(f"Failed to delete {file_path}: {e}")
            self.stats['errors'] += 1
            return False
    
    def cleanup_directory(self, directory_path):
        """Clean up expired music files in a directory"""
        if not os.path.exists(directory_path):
            self.logger.warning(f"Music directory does not exist: {directory_path}")
            return
        
        print(f"🧹 Cleaning music directory: {directory_path}")
        self.logger.debug(f"Starting cleanup of music directory: {directory_path}")
        
        music_extensions = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.wav', '.opus'}
        
        # Pre-scan to count total music files for progress bar
        print("    📊 Scanning directory to count files...")
        total_music_files = 0
        all_file_paths = []
        
        for root, dirs, files in os.walk(directory_path):
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = Path(file_path).suffix.lower()
                
                # Count music files only (skip metadata files)
                if file_ext in music_extensions:
                    total_music_files += 1
                    all_file_paths.append(file_path)
        
        if total_music_files == 0:
            print("    ℹ️  No music files found in directory")
            return
            
        print(f"    📁 Found {total_music_files:,} music files to process")
        
        # Main processing with progress bar
        files_processed = 0
        
        if TQDM_AVAILABLE:
            progress = tqdm(all_file_paths, 
                          desc="PROGRESS_MAIN: Processing music files",
                          unit="file", ncols=100, position=0)
        else:
            progress = all_file_paths
        
        try:
            for file_path in progress:
                # Delete macOS metadata files immediately without tracking
                filename = os.path.basename(file_path)
                if filename.startswith('._'):
                    print(f"    🗑️  Removing macOS metadata file: {filename}")
                    self.logger.info(f"Removing macOS metadata file: {file_path}")
                    if not self.dry_run:
                        try:
                            os.remove(file_path)
                            self.logger.info(f"Deleted macOS metadata file: {file_path}")
                        except Exception as e:
                            self.logger.error(f"Error deleting macOS metadata file {file_path}: {e}")
                    continue
                
                files_processed += 1
                self.stats['files_scanned'] += 1
                
                # Always track album data for UI cache (regardless of age)
                self.track_album_expiry(file_path)
                
                # Check if file is old enough
                if not self.is_file_old_enough(file_path):
                    self.stats['files_skipped_recent'] += 1
                    if TQDM_AVAILABLE:
                        progress.set_postfix({
                            'Expired': self.stats['files_deleted'],
                            'Starred': self.stats['files_skipped_starred'],
                            'Recent': self.stats['files_skipped_recent']
                        })
                    continue
                
                # Check if content is starred
                if self.is_content_starred(file_path):
                    self.stats['files_skipped_starred'] += 1
                    if not TQDM_AVAILABLE:
                        print(f"    ⭐ Skipping starred content: {filename}")
                    self.logger.info(f"Skipping starred content: {file_path}")
                    if TQDM_AVAILABLE:
                        progress.set_postfix({
                            'Expired': self.stats['files_deleted'],
                            'Starred': self.stats['files_skipped_starred'],
                            'Recent': self.stats['files_skipped_recent']
                        })
                    continue
                
                # File is old enough and not starred - log and delete it
                if not TQDM_AVAILABLE:
                    print(f"    🗑️  Found expired file: {filename}")
                self.logger.info(f"Found expired file: {file_path}")
                self.delete_file(file_path)
                
                # Update progress bar stats
                if TQDM_AVAILABLE:
                    progress.set_postfix({
                        'Expired': self.stats['files_deleted'],
                        'Starred': self.stats['files_skipped_starred'],
                        'Recent': self.stats['files_skipped_recent']
                    })
                
                # Small delay to avoid overwhelming the system
                if not self.dry_run:
                        sleep(0.1)
        
        except Exception as e:
            msg = f"Error processing directory {directory_path}: {e}"
            self.logger.error(msg)
            self.stats['errors'] += 1
        
        finally:
            # Close progress bar
            if TQDM_AVAILABLE:
                progress.close()
        
        # Print summary
        files_found = self.stats['files_deleted'] + self.stats['files_skipped_starred']
        print(f"    📊 Processed {files_processed:,} files:")
        print(f"        🗑️  Expired files: {self.stats['files_deleted']}")
        print(f"        ⭐ Starred (protected): {self.stats['files_skipped_starred']}")
        print(f"        📅 Recent (safe): {self.stats['files_skipped_recent']}")
        
        # Only log summary if we found files to process
        if files_found > 0:
            self.logger.info(f"Processed {files_processed} files in {directory_path} - Found {files_found} old enough files")
    
    def remove_mac_metadata_files(self, root_dir):
        """Remove macOS metadata files (._* files) recursively"""
        if not os.path.exists(root_dir):
            return
        
        removed_count = 0
        try:
            for root, dirs, files in os.walk(root_dir):
                for file in files:
                    # Check if file starts with ._
                    if file.startswith('._'):
                        file_path = os.path.join(root, file)
                        try:
                            if self.dry_run:
                                print(f"    [DRY RUN] Would remove macOS metadata file: {file}")
                                self.logger.info(f"DRY RUN: Would remove macOS metadata file: {file_path}")
                            else:
                                os.remove(file_path)
                                print(f"    🍎 Removed macOS metadata file: {file}")
                                self.logger.info(f"Removed macOS metadata file: {file_path}")
                                log_action("mac_metadata_delete", "Removed macOS metadata file", {
                                    "file": file,
                                    "path": file_path
                                })
                            removed_count += 1
                        except Exception as e:
                            self.logger.error(f"Error removing macOS metadata file {file_path}: {e}")
        except Exception as e:
            self.logger.error(f"Error scanning for macOS metadata files in {root_dir}: {e}")
        
        if removed_count > 0:
            print(f"    ✓ Removed {removed_count} macOS metadata file{'s' if removed_count != 1 else ''}")
            self.logger.info(f"Removed {removed_count} macOS metadata files from {root_dir}")
    
    def remove_empty_directories(self, root_dir):
        """Remove empty directories recursively"""
        if not os.path.exists(root_dir):
            return
        
        removed_count = 0
        try:
            for root, dirs, files in os.walk(root_dir, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        if not os.listdir(dir_path):  # Directory is empty
                            if self.dry_run:
                                print(f"    [DRY RUN] Would remove empty directory: {dir_path}")
                                self.logger.info(f"DRY RUN: Would remove empty directory: {dir_path}")
                            else:
                                os.rmdir(dir_path)
                                print(f"    📁 Removed empty directory: {dir_path}")
                                self.logger.info(f"Removed empty directory: {dir_path}")
                                log_action("directory_delete", "Removed empty directory", {"path": dir_path})
                            removed_count += 1
                    except OSError:
                        pass  # Directory not empty or other error
        except Exception as e:
            self.logger.error(f"Error removing empty directories from {root_dir}: {e}")
        
        if removed_count > 0:
            self.logger.info(f"Removed {removed_count} empty directories from {root_dir}")
    
    def run(self):
        """Main cleanup function"""
        print(f"🧹 File Expiry Cleanup - Music files older than {self.cleanup_days} days")
        print("=" * 60)
        
        self.logger.info(f"Starting music file expiry cleanup - Cleanup days: {self.cleanup_days}, Dry run: {self.dry_run}")
        
        if self.dry_run:
            print("🔍 DRY RUN MODE - No changes will be made")
            print()
            self.logger.info("Running in DRY RUN mode - no changes will be made")
        
        log_action("script_start", "File Expiry Cleanup started", {
            "cleanup_days": self.cleanup_days,
            "incomplete_dir": self.incomplete_dir,
            "not_owned_dir": self.not_owned_dir,
            "dry_run": self.dry_run
        })
        
        # Step 1: Authenticate with Navidrome
        print()
        print("🔐 Step 1: Authenticating with Navidrome...")
        auth_success = self.authenticate_navidrome()
        
        if not auth_success:
            print("❌ Failed to authenticate with Navidrome - aborting cleanup")
            print("   Cannot proceed without verifying starred content")
            self.logger.error("Aborting cleanup - failed to authenticate with Navidrome")
            
            log_action("script_error", "File Expiry Cleanup aborted", {
                "reason": "navidrome_authentication_failed",
                "incomplete_dir": self.incomplete_dir,
                "not_owned_dir": self.not_owned_dir
            })
            
            return False
        
        # Step 2: Get starred content
        print()
        print("⭐ Step 2: Fetching starred content from Navidrome...")
        starred_success = self.get_starred_content()
        
        if not starred_success:
            print("⚠️  Failed to fetch starred content")
            if not self.dry_run:
                print("❌ Aborting cleanup to avoid deleting starred content")
                print("   Run with --dry-run to see what would be deleted")
                self.logger.error("Aborting cleanup - could not verify starred content and not in dry run mode")
                return False
            else:
                print("   Continuing in dry run mode - no files will be deleted")
                self.logger.warning("Continuing in dry run mode despite starred content fetch failure")
        else:
            # Print summary of starred content
            print(f"    ✓ Protection enabled for:")
            print(f"      📀 {len(self.starred_albums)} starred albums")
            print(f"      🎵 {len(self.starred_tracks)} starred songs")
        
        # Step 3: Clean music directories
        print()
        print("🧹 Step 3: Cleaning expired music files...")
        
        # Clean incomplete music directory
        if os.path.exists(self.incomplete_dir):
            self.cleanup_directory(self.incomplete_dir)
        else:
            print(f"    ⚠️  Incomplete music directory not found: {self.incomplete_dir}")
        
        # Clean not owned music directory
        if os.path.exists(self.not_owned_dir):
            self.cleanup_directory(self.not_owned_dir)
        else:
            print(f"    ⚠️  Not owned music directory not found: {self.not_owned_dir}")
        
        # Step 4: Remove macOS metadata files
        print()
        print("🍎 Step 4: Removing macOS metadata files...")
        self.remove_mac_metadata_files(self.incomplete_dir)
        self.remove_mac_metadata_files(self.not_owned_dir)
        
        # Step 5: Remove empty directories
        print()
        print("📁 Step 5: Removing empty music directories...")
        self.remove_empty_directories(self.incomplete_dir)
        self.remove_empty_directories(self.not_owned_dir)
        
        # Step 6: Save album expiry cache for UI
        print()
        print("💾 Step 6: Saving album expiry data to database...")
        self.save_album_expiry_cache()
        
        # Print summary
        print()
        print("=" * 60)
        print("📊 Cleanup Summary:")
        print(f"   🔍 Files scanned: {self.stats['files_scanned']}")
        if self.dry_run:
            print(f"   🗑️  Files would be deleted: {self.stats['files_deleted']}")
        else:
            print(f"   🗑️  Files deleted: {self.stats['files_deleted']}")
        print(f"   ⭐ Files skipped (starred): {self.stats['files_skipped_starred']}")
        print(f"   📅 Files skipped (recent): {self.stats['files_skipped_recent']}")
        print(f"   ❌ Errors: {self.stats['errors']}")
        
        # Log summary  
        action_verb = "would be deleted" if self.dry_run else "deleted"
        self.logger.info(f"Cleanup complete - Files scanned: {self.stats['files_scanned']}, "
                        f"Files {action_verb}: {self.stats['files_deleted']}, "
                        f"Starred skipped: {self.stats['files_skipped_starred']}, "
                        f"Recent skipped: {self.stats['files_skipped_recent']}, "
                        f"Errors: {self.stats['errors']}")
        
        if self.stats['errors'] == 0:
            print()
            print("✅ File expiry cleanup complete!")
            self.logger.info("File expiry cleanup completed successfully")
            
            log_action("script_complete", "File Expiry Cleanup completed successfully", {
                "stats": self.stats,
                "log_file": self.log_file_path
            })
        else:
            print()
            print("⚠️  File expiry cleanup completed with errors!")
            self.logger.warning("File expiry cleanup completed with errors")
            
            log_action("script_complete", "File Expiry Cleanup completed with errors", {
                "stats": self.stats,
                "log_file": self.log_file_path
            })
        
        return self.stats['errors'] == 0


def main():
    parser = argparse.ArgumentParser(description="File Expiry Cleanup - Remove old music files respecting starred content")
    parser.add_argument('--cleanup-days', type=int, help='Days after which music files are considered expired (default: from CLEANUP_DAYS env var or 30)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    
    args = parser.parse_args()
    
    # Pre-check for critical dependencies
    try:
        import requests
    except ImportError:
        print("✗ Error: requests library not found. Install with: pip install requests")
        sys.exit(1)
    
    try:
        cleanup = FileExpiryCleanup(
            cleanup_days=args.cleanup_days,
            dry_run=args.dry_run
        )
        success = cleanup.run()
        sys.exit(0 if success else 1)
        
    except ValueError as e:
        # Configuration errors
        print(f"✗ Configuration Error: {e}")
        print("\nMake sure to set the required environment variables:")
        print("  NAVIDROME_URL=http://your-navidrome-url:4533")
        print("  NAVIDROME_USERNAME=your-username")
        print("  NAVIDROME_PASSWORD=your-password")
        print("  CLEANUP_DAYS=30 (optional)")
        print("\nThe script will clean files from:")
        print("  /media/Incomplete (mounted from /mnt/storage/Music/Incomplete)")
        print("  /media/Not_Owned (mounted from /mnt/storage/Music/Not_Owned)")
        
        # Try to log to action logger
        try:
            log_action("script_error", "File Expiry Cleanup configuration error", {
                "error": str(e),
                "error_type": "configuration"
            })
        except:
            pass
        sys.exit(1)
    except Exception as e:
        error_msg = f"File Expiry Cleanup failed: {e}"
        print(f"✗ Unexpected Error: {e}")
        
        # Try to log to the detailed log if cleanup was created
        try:
            if 'cleanup' in locals():
                cleanup.logger.error(error_msg)
        except:
            pass
            
        try:
            log_action("script_error", "File Expiry Cleanup failed", {
                "error": str(e),
                "error_type": "runtime",
                "log_file": getattr(locals().get('cleanup'), 'log_file_path', 'Not created')
            })
        except:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()