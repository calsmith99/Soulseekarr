#!/usr/bin/env python3
"""
Navidrome Starred Albums Monitor - Smart album monitoring and owned album cleanup

This script:
1. Fetches all starred albums from Navidrome
2. Checks if albums are already owned (exist in OWNED_MUSIC_PATH folder)
3. For owned albums: Unstars them from Navidrome and unmonitors them in Lidarr
4. For non-owned albums: Finds them in Lidarr and sets them to be monitored
5. Extracts unique artists from starred albums
6. Searches for each artist in MusicBrainz to get proper metadata
7. Adds missing artists to Lidarr for monitoring (with future releases only)

Name: Starred Albums Monitor
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: navidrome, starred, monitoring, lidarr
Supports dry run: true

Usage:
    python navidrome_starred_albums_monitor.py                    # Process all starred albums
    python navidrome_starred_albums_monitor.py --dry-run          # Dry run mode

Requirements:
    - Navidrome server access (configured via environment variables)
    - Lidarr server access (configured via environment variables)
    - MusicBrainz API access for artist lookup
    - Access to owned music folder (configured via OWNED_MUSIC_PATH)

Environment Variables:
    NAVIDROME_URL - Navidrome server URL
    NAVIDROME_USERNAME - Navidrome username
    NAVIDROME_PASSWORD - Navidrome password
    LIDARR_URL - Lidarr server URL
    LIDARR_API_KEY - Lidarr API key
    OWNED_MUSIC_PATH - Path to owned music folder (default: /media/Owned)
"""

import os
import sys
import json
import argparse
import logging
import requests
import hashlib
import time
import random
import string
from pathlib import Path
from datetime import datetime
import re

# Add parent directory to path so we can import action_logger and lidarr_utils
sys.path.append(str(Path(__file__).parent.parent))
try:
    from action_logger import log_action
except ImportError as e:
    print(f"Warning: Could not import action_logger: {e}")
    def log_action(*args, **kwargs):
        pass

try:
    from lidarr_utils import LidarrClient
except ImportError as e:
    print(f"Warning: Could not import lidarr_utils: {e}")
    LidarrClient = None

class NavidromeStarredAlbumsMonitor:
    def __init__(self, dry_run=False):
        """Initialize the Navidrome Starred Albums Monitor
        
        This monitor will:
        1. Fetch starred albums from Navidrome
        2. Find and monitor those specific albums in Lidarr
        3. Add any missing artists to Lidarr for future monitoring
        """
        try:
            self.dry_run = dry_run
            
            # Initialize stats
            self.stats = {
                'starred_albums': 0,
                'unique_artists': 0,
                'artists_in_lidarr': 0,
                'artists_added_to_lidarr': 0,
                'artists_failed': 0,
                'albums_found_in_lidarr': 0,
                'albums_already_monitored': 0,
                'albums_set_to_monitored': 0,
                'albums_failed_to_monitor': 0,
                'albums_owned': 0,
                'albums_unstarred': 0,
                'albums_unmonitored': 0
            }
            
            # Initialize data storage
            self.starred_albums = []
            self.unique_artists = set()
            
            # Track failed artists
            self.failed_artists = []
            
            # Initialize logging
            self.log_file_path = None
            self.logger = None
            self._setup_logging()
            
            # Initialize APIs
            self._setup_navidrome_auth()
            self._setup_lidarr_connection()
            
            # Initialize Lidarr client
            if LidarrClient:
                self.lidarr_client = LidarrClient(
                    lidarr_url=self.lidarr_url,
                    lidarr_api_key=self.lidarr_api_key,
                    logger=self.logger,
                    dry_run=self.dry_run
                )
            else:
                self.lidarr_client = None
                self.logger.warning("LidarrClient not available, using fallback methods")
            
            self.logger.info("Navidrome Starred Albums Monitor initialized successfully")
            
        except Exception as e:
            error_msg = f"❌ Failed to initialize Navidrome Starred Albums Monitor: {e}"
            print(error_msg)
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(error_msg)
            raise

    def _setup_logging(self):
        """Set up logging configuration"""
        try:
            # Create logs directory
            logs_dir = Path(__file__).parent.parent / "logs"
            logs_dir.mkdir(exist_ok=True)
            
            # Create log file with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = logs_dir / f"navidrome_starred_albums_monitor_{timestamp}.log"
            
            # Store the log file path for later reference
            self.log_file_path = log_file
            
            # Configure logger
            self.logger = logging.getLogger('navidrome_starred_albums_monitor')
            self.logger.setLevel(logging.INFO)
            
            # Remove any existing handlers
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)
            
            # Create file handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            
            # Create console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # Create formatter
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            # Add handlers to logger
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
            
            self.logger.info(f"Navidrome Starred Albums Monitor logging started - Log file: {self.log_file_path}")
            
        except Exception as e:
            print(f"Failed to setup logging: {e}")
            # Create a basic logger that only logs to console
            self.logger = logging.getLogger('navidrome_starred_albums_monitor')
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _setup_navidrome_auth(self):
        """Set up Navidrome authentication"""
        try:
            self.navidrome_url = os.getenv('NAVIDROME_URL')
            self.navidrome_username = os.getenv('NAVIDROME_USERNAME')
            navidrome_password = os.getenv('NAVIDROME_PASSWORD')
            
            if not all([self.navidrome_url, self.navidrome_username, navidrome_password]):
                missing = []
                if not self.navidrome_url:
                    missing.append('NAVIDROME_URL')
                if not self.navidrome_username:
                    missing.append('NAVIDROME_USERNAME')
                if not navidrome_password:
                    missing.append('NAVIDROME_PASSWORD')
                raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
            
            # Generate Subsonic API token and salt
            self.subsonic_salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            token_string = navidrome_password + self.subsonic_salt
            self.subsonic_token = hashlib.md5(token_string.encode()).hexdigest()
            
            self.logger.info(f"Navidrome authentication configured for: {self.navidrome_url}")
            
        except Exception as e:
            self.logger.error(f"Failed to setup Navidrome authentication: {e}")
            raise

    def _setup_lidarr_connection(self):
        """Set up Lidarr connection"""
        try:
            self.lidarr_url = os.getenv('LIDARR_URL')
            self.lidarr_api_key = os.getenv('LIDARR_API_KEY')
            
            if not self.lidarr_url or not self.lidarr_api_key:
                missing = []
                if not self.lidarr_url:
                    missing.append('LIDARR_URL')
                if not self.lidarr_api_key:
                    missing.append('LIDARR_API_KEY')
                raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
            
            # Test Lidarr connection
            if not self.dry_run:
                headers = {'X-Api-Key': self.lidarr_api_key}
                test_url = f"{self.lidarr_url}/api/v1/system/status"
                response = requests.get(test_url, headers=headers, timeout=10)
                
                if response.status_code != 200:
                    raise ValueError(f"Failed to connect to Lidarr: HTTP {response.status_code}")
                
                system_status = response.json()
                self.logger.info(f"Connected to Lidarr: {system_status.get('appName', 'Unknown')} v{system_status.get('version', 'Unknown')}")
            else:
                self.logger.info("DRY RUN: Skipping Lidarr connection test")
            
        except Exception as e:
            self.logger.error(f"Failed to setup Lidarr connection: {e}")
            raise

    def get_starred_albums(self):
        """Get all starred albums from Navidrome"""
        try:
            self.logger.info("Fetching starred albums from Navidrome...")
            
            starred_url = f"{self.navidrome_url}/rest/getStarred2"
            starred_params = {
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'NavidromeStarredAlbumsMonitor'
            }
            
            response = requests.get(starred_url, params=starred_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    starred_info = subsonic_response.get('starred2', {})
                    albums = starred_info.get('album', [])
                    
                    self.starred_albums = []
                    artists_set = set()
                    
                    for album in albums:
                        album_info = {
                            'id': album.get('id'),
                            'name': album.get('name', ''),
                            'artist': album.get('artist', ''),
                            'artistId': album.get('artistId'),
                            'year': album.get('year'),
                            'songCount': album.get('songCount', 0),
                            'duration': album.get('duration', 0)
                        }
                        self.starred_albums.append(album_info)
                        
                        # Collect unique artists (don't split collaborations yet - we'll try full names first)
                        if album_info['artist']:
                            artist_name = album_info['artist'].strip()
                            if artist_name:
                                artists_set.add(artist_name)
                    
                    self.unique_artists = artists_set
                    self.stats['starred_albums'] = len(self.starred_albums)
                    self.stats['unique_artists'] = len(self.unique_artists)
                    
                    self.logger.info(f"Found {len(self.starred_albums)} starred albums from {len(self.unique_artists)} unique artists")
                    return True
                else:
                    error = subsonic_response.get('error', {})
                    self.logger.error(f"Navidrome API error: {error}")
                    return False
            else:
                self.logger.error(f"Failed to fetch starred albums: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error fetching starred albums: {e}")
            return False

    def normalize_album_title(self, title):
        """Normalize album title for comparison"""
        if not title:
            return ""
        
        # Convert to lowercase and normalize unicode
        import unicodedata
        normalized = unicodedata.normalize('NFKD', title.lower())
        
        # Remove common prefixes/suffixes and special characters
        normalized = normalized.strip()
        
        # Remove year patterns like "(2023)" or "[2023]"
        import re
        normalized = re.sub(r'\s*[\(\[]?\d{4}[\)\]]?\s*', ' ', normalized)
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        return normalized.strip()

    def find_album_in_lidarr(self, starred_album):
        """Find a starred album in Lidarr by searching through artist's albums"""
        try:
            if self.dry_run:
                return None
            
            artist_name = starred_album.get('artist', '').strip()
            album_name = starred_album.get('name', '').strip()
            
            if not artist_name or not album_name:
                self.logger.debug(f"Missing artist or album name: artist='{artist_name}', album='{album_name}'")
                return None
            
            # First, find the artist in Lidarr
            headers = {'X-Api-Key': self.lidarr_api_key}
            artists_url = f"{self.lidarr_url}/api/v1/artist"
            artists_response = requests.get(artists_url, headers=headers, timeout=30)
            
            if artists_response.status_code != 200:
                self.logger.warning(f"Failed to get artists from Lidarr: {artists_response.status_code}")
                return None
            
            artists = artists_response.json()
            
            # Find matching artist
            lidarr_artist = None
            normalized_starred_artist = self.normalize_artist_name(artist_name)
            
            for artist in artists:
                lidarr_artist_name = artist.get('artistName', '').strip()
                if self.normalize_artist_name(lidarr_artist_name) == normalized_starred_artist:
                    lidarr_artist = artist
                    break
            
            if not lidarr_artist:
                self.logger.debug(f"Artist '{artist_name}' not found in Lidarr")
                return None
            
            # Get albums for this artist
            albums_url = f"{self.lidarr_url}/api/v1/album"
            albums_params = {'artistId': lidarr_artist['id']}
            albums_response = requests.get(albums_url, headers=headers, params=albums_params, timeout=30)
            
            if albums_response.status_code != 200:
                self.logger.warning(f"Failed to get albums for artist '{artist_name}': {albums_response.status_code}")
                return None
            
            albums = albums_response.json()
            
            # Find matching album
            normalized_starred_album = self.normalize_album_title(album_name)
            
            for album in albums:
                lidarr_album_title = album.get('title', '').strip()
                normalized_lidarr_album = self.normalize_album_title(lidarr_album_title)
                
                if normalized_lidarr_album == normalized_starred_album:
                    self.logger.debug(f"Found exact album match: '{album_name}' -> '{lidarr_album_title}'")
                    return album
            
            # Try fuzzy matching for close matches
            from difflib import SequenceMatcher
            best_match = None
            best_score = 0.0
            
            for album in albums:
                lidarr_album_title = album.get('title', '').strip()
                normalized_lidarr_album = self.normalize_album_title(lidarr_album_title)
                
                score = SequenceMatcher(None, normalized_starred_album, normalized_lidarr_album).ratio()
                if score > best_score and score >= 0.8:  # 80% similarity threshold
                    best_score = score
                    best_match = album
            
            if best_match:
                self.logger.info(f"Found fuzzy album match ({best_score:.3f}): '{album_name}' -> '{best_match.get('title', '')}'")
                return best_match
            
            self.logger.debug(f"Album '{album_name}' by '{artist_name}' not found in Lidarr")
            return None
            
        except Exception as e:
            self.logger.warning(f"Error finding album '{album_name}' by '{artist_name}' in Lidarr: {e}")
            return None

    def set_album_monitored(self, album_data, monitored=True):
        """Set an album's monitoring status in Lidarr using reusable utilities"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                return self.lidarr_client.set_album_monitored(album_data, monitored)
            else:
                # Fallback to old method
                return self._set_album_monitored_fallback(album_data, monitored)
                
        except Exception as e:
            action = "monitor" if monitored else "unmonitor"
            self.logger.error(f"Error trying to {action} album '{album_data.get('title', 'Unknown')}': {e}")
            return False

    def _set_album_monitored_fallback(self, album_data, monitored=True):
        """Fallback method for setting album monitoring (original implementation)"""
        try:
            if self.dry_run:
                action = "monitor" if monitored else "unmonitor"
                self.logger.info(f"DRY RUN: Would {action} album '{album_data.get('title', 'Unknown')}'")
                return True
            
            album_id = album_data.get('id')
            album_title = album_data.get('title', 'Unknown')
            
            if not album_id:
                self.logger.error(f"Album '{album_title}' has no ID")
                return False
            
            # Update the album data
            updated_album = album_data.copy()
            updated_album['monitored'] = monitored
            
            headers = {
                'Content-Type': 'application/json',
                'X-Api-Key': self.lidarr_api_key
            }
            
            url = f"{self.lidarr_url}/api/v1/album/{album_id}"
            response = requests.put(url, headers=headers, json=updated_album, timeout=30)
            
            if response.status_code in [200, 202]:
                action = "monitored" if monitored else "unmonitored"
                self.logger.info(f"Successfully {action} album: '{album_title}'")
                return True
            else:
                action = "monitor" if monitored else "unmonitor"
                self.logger.warning(f"Failed to {action} album '{album_title}': HTTP {response.status_code}")
                if response.text:
                    self.logger.debug(f"Error response: {response.text[:200]}")
                return False
                
        except Exception as e:
            action = "monitor" if monitored else "unmonitor"
            self.logger.error(f"Error trying to {action} album '{album_data.get('title', 'Unknown')}': {e}")
            return False

    def get_owned_folder_path(self):
        """Get the owned folder path from environment variable"""
        return os.getenv('OWNED_MUSIC_PATH', '/media/Owned')

    def clean_name_for_matching(self, name):
        """Clean name for fuzzy matching (similar to clean_artist_name logic)"""
        if not name:
            return ""
        
        import re
        
        # Remove common prefixes
        name = name.strip()
        prefixes_to_remove = ['The ', 'A ']
        for prefix in prefixes_to_remove:
            if name.startswith(prefix):
                name = name[len(prefix):]
        
        # Remove year suffixes like "(2020)" or "[2020]" 
        name = re.sub(r'\s*[\[\(]\d{4}[\]\)]\s*$', '', name)
        
        # Remove common suffixes
        suffixes_to_remove = [' - Discography', ' Discography', ' (Complete)', ' [Complete]', 
                             ' - Remastered', ' (Remastered)', ' [Remastered]', ' (Deluxe)', 
                             ' [Deluxe]', ' - Deluxe', ' (Special Edition)', ' [Special Edition]']
        for suffix in suffixes_to_remove:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        
        # Normalize whitespace and case
        return ' '.join(name.lower().split())

    def is_album_owned(self, artist_name, album_name):
        """Check if album exists in the owned folder"""
        try:
            owned_path = self.get_owned_folder_path()
            
            if not os.path.exists(owned_path):
                self.logger.debug(f"Owned folder not found: {owned_path}")
                return False
            
            # Clean names for matching
            clean_artist = self.clean_name_for_matching(artist_name)
            clean_album = self.clean_name_for_matching(album_name)
            
            self.logger.debug(f"Checking ownership: '{clean_artist}' - '{clean_album}'")
            
            # Look for artist folder (with fuzzy matching)
            artist_folders = []
            for item in os.listdir(owned_path):
                item_path = os.path.join(owned_path, item)
                if os.path.isdir(item_path):
                    clean_folder_name = self.clean_name_for_matching(item)
                    if clean_folder_name == clean_artist:
                        artist_folders.append(item_path)
                        break
                    # Also check if artist name is a substring (for cases like "Jay-Z" vs "JAY Z")
                    elif clean_artist in clean_folder_name or clean_folder_name in clean_artist:
                        if abs(len(clean_artist) - len(clean_folder_name)) <= 3:  # Close match
                            artist_folders.append(item_path)
                            self.logger.debug(f"Found artist folder with fuzzy match: '{item}' for '{artist_name}'")
                            break
            
            if not artist_folders:
                self.logger.debug(f"Artist folder not found for: {artist_name}")
                return False
            
            # Check for album folder within artist folder
            artist_folder = artist_folders[0]
            for album_item in os.listdir(artist_folder):
                album_path = os.path.join(artist_folder, album_item)
                if os.path.isdir(album_path):
                    clean_album_folder = self.clean_name_for_matching(album_item)
                    if clean_album_folder == clean_album:
                        # Verify it has music files
                        if self._has_music_files(album_path):
                            self.logger.info(f"Found owned album: {artist_name} - {album_name} at {album_path}")
                            return True
                    # Fuzzy match for album names
                    elif clean_album in clean_album_folder or clean_album_folder in clean_album:
                        if abs(len(clean_album) - len(clean_album_folder)) <= 5:  # Allow more variance for album names
                            if self._has_music_files(album_path):
                                self.logger.info(f"Found owned album with fuzzy match: {artist_name} - {album_name} at {album_path}")
                                return True
            
            self.logger.debug(f"Album not found in owned folder: {artist_name} - {album_name}")
            return False
            
        except Exception as e:
            self.logger.warning(f"Error checking if album is owned '{artist_name} - {album_name}': {e}")
            return False

    def _has_music_files(self, folder_path):
        """Check if folder contains music files"""
        try:
            music_extensions = {'.mp3', '.flac', '.m4a', '.wav', '.ogg', '.wma', '.aac'}
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    if any(file.lower().endswith(ext) for ext in music_extensions):
                        return True
            return False
        except Exception as e:
            self.logger.debug(f"Error checking for music files in {folder_path}: {e}")
            return False

    def unstar_album_in_navidrome(self, album_id):
        """Remove star from album in Navidrome"""
        try:
            if self.dry_run:
                self.logger.info(f"DRY RUN: Would unstar album ID {album_id}")
                return True
            
            unstar_url = f"{self.navidrome_url}/rest/unstar"
            unstar_params = {
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'NavidromeStarredAlbumsMonitor',
                'albumId': album_id
            }
            
            response = requests.get(unstar_url, params=unstar_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    self.logger.info(f"Successfully unstarred album ID {album_id}")
                    return True
                else:
                    error = subsonic_response.get('error', {})
                    self.logger.error(f"Navidrome API error unstarring album {album_id}: {error}")
                    return False
            else:
                self.logger.error(f"Failed to unstar album {album_id}: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error unstarring album {album_id}: {e}")
            return False

    def monitor_starred_albums_specifically(self):
        """Monitor the specific albums that are starred in Navidrome"""
        try:
            if not self.starred_albums:
                self.logger.warning("No starred albums to process")
                return False
            
            print("🎯 Step 3: Finding and monitoring specific starred albums in Lidarr...")
            self.logger.info("Finding and monitoring specific starred albums in Lidarr")
            
            albums_found = 0
            albums_already_monitored = 0
            albums_set_to_monitored = 0
            albums_failed = 0
            
            for i, starred_album in enumerate(self.starred_albums, 1):
                try:
                    artist_name = starred_album.get('artist', 'Unknown')
                    album_name = starred_album.get('name', 'Unknown')
                    album_id = starred_album.get('id')
                    
                    print(f"🔍 Processing album {i}/{len(self.starred_albums)}: {artist_name} - {album_name}")
                    self.logger.info(f"Processing starred album {i}/{len(self.starred_albums)}: {artist_name} - {album_name}")
                    
                    # First check if the album is owned
                    is_owned = self.is_album_owned(artist_name, album_name)
                    
                    if is_owned:
                        self.stats['albums_owned'] += 1
                        print(f"   🏠 Album is owned - handling accordingly")
                        self.logger.info(f"Album is owned: {artist_name} - {album_name}")
                        
                        # Unstar the owned album from Navidrome
                        if album_id:
                            if self.unstar_album_in_navidrome(album_id):
                                self.stats['albums_unstarred'] += 1
                                print(f"   ⭐ Unstarred owned album: {album_name}")
                                self.logger.info(f"Unstarred owned album: {album_name}")
                            else:
                                print(f"   ❌ Failed to unstar owned album: {album_name}")
                                self.logger.warning(f"Failed to unstar owned album: {album_name}")
                        
                        # Find the album in Lidarr and unmonitor it if monitored
                        lidarr_album = self.find_album_in_lidarr(starred_album)
                        if lidarr_album:
                            is_monitored = lidarr_album.get('monitored', False)
                            if is_monitored:
                                if self.set_album_monitored(lidarr_album, False):  # Unmonitor
                                    self.stats['albums_unmonitored'] += 1
                                    print(f"   📵 Unmonitored owned album: {album_name}")
                                    self.logger.info(f"Unmonitored owned album: {album_name}")
                                else:
                                    print(f"   ❌ Failed to unmonitor owned album: {album_name}")
                                    self.logger.warning(f"Failed to unmonitor owned album: {album_name}")
                            else:
                                print(f"   ✅ Owned album already unmonitored: {album_name}")
                                self.logger.info(f"Owned album already unmonitored: {album_name}")
                        
                        continue  # Skip further processing for owned albums
                    
                    # Album is not owned, proceed with normal monitoring logic
                    # Find the album in Lidarr
                    lidarr_album = self.find_album_in_lidarr(starred_album)
                    
                    if lidarr_album:
                        albums_found += 1
                        
                        # Check if already monitored
                        is_monitored = lidarr_album.get('monitored', False)
                        
                        if is_monitored:
                            albums_already_monitored += 1
                            print(f"   ✅ Already monitored: {album_name}")
                            self.logger.info(f"Album already monitored: {album_name}")
                        else:
                            # Set to monitored
                            if self.set_album_monitored(lidarr_album, True):
                                albums_set_to_monitored += 1
                                print(f"   🎯 Set to monitored: {album_name}")
                                self.logger.info(f"Set album to monitored: {album_name}")
                            else:
                                albums_failed += 1
                                print(f"   ❌ Failed to monitor: {album_name}")
                                self.logger.warning(f"Failed to set album to monitored: {album_name}")
                    else:
                        print(f"   ⚠️  Not found in Lidarr: {album_name}")
                        self.logger.debug(f"Album not found in Lidarr: {artist_name} - {album_name}")
                    
                    # Small delay between requests
                    if i < len(self.starred_albums):
                        time.sleep(0.5)
                        
                except Exception as e:
                    albums_failed += 1
                    self.logger.error(f"Error processing starred album {artist_name} - {album_name}: {e}")
                    print(f"   ❌ Error processing: {album_name}")
            
            # Update stats
            self.stats['albums_found_in_lidarr'] = albums_found
            self.stats['albums_already_monitored'] = albums_already_monitored
            self.stats['albums_set_to_monitored'] = albums_set_to_monitored
            self.stats['albums_failed_to_monitor'] = albums_failed
            
            # Print summary
            print()
            print("🎯 Starred Albums Monitoring Summary:")
            print(f"   📋 Starred albums processed: {len(self.starred_albums)}")
            print(f"   🏠 Albums owned (unstarred/unmonitored): {self.stats['albums_owned']}")
            print(f"   ⭐ Albums unstarred: {self.stats['albums_unstarred']}")
            print(f"   📵 Albums unmonitored: {self.stats['albums_unmonitored']}")
            print(f"   🔍 Non-owned albums found in Lidarr: {albums_found}")
            print(f"   ✅ Already monitored: {albums_already_monitored}")
            print(f"   🎯 Set to monitored: {albums_set_to_monitored}")
            print(f"   ❌ Failed to monitor: {albums_failed}")
            print(f"   ⚠️  Not found in Lidarr: {len(self.starred_albums) - albums_found - self.stats['albums_owned']}")
            
            # Log the summary
            self.logger.info("🎯 Starred Albums Monitoring Summary:")
            self.logger.info(f"   📋 Starred albums processed: {len(self.starred_albums)}")
            self.logger.info(f"   🏠 Albums owned (unstarred/unmonitored): {self.stats['albums_owned']}")
            self.logger.info(f"   ⭐ Albums unstarred: {self.stats['albums_unstarred']}")
            self.logger.info(f"   📵 Albums unmonitored: {self.stats['albums_unmonitored']}")
            self.logger.info(f"   🔍 Non-owned albums found in Lidarr: {albums_found}")
            self.logger.info(f"   ✅ Already monitored: {albums_already_monitored}")
            self.logger.info(f"   🎯 Set to monitored: {albums_set_to_monitored}")
            self.logger.info(f"   ❌ Failed to monitor: {albums_failed}")
            self.logger.info(f"   ⚠️  Not found in Lidarr: {len(self.starred_albums) - albums_found - self.stats['albums_owned']}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in monitor_starred_albums_specifically: {e}")
            return False

    def normalize_artist_name(self, artist_name):
        """Normalize artist name for comparison using reusable utilities"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                return self.lidarr_client.normalize_artist_name(artist_name)
            else:
                # Fallback to old method
                return self._normalize_artist_name_fallback(artist_name)
                
        except Exception as e:
            self.logger.warning(f"Error normalizing artist name '{artist_name}': {e}")
            return artist_name.lower().strip() if artist_name else ""

    def _normalize_artist_name_fallback(self, artist_name):
        """Fallback method for normalizing artist names (original implementation)"""
        if not artist_name:
            return ""
        
        # Convert to lowercase
        normalized = artist_name.lower()
        
        # Replace different types of apostrophes and quotes with standard ones
        normalized = normalized.replace(''', "'")  # Curly apostrophe (U+2019)
        normalized = normalized.replace(''', "'")  # Another curly apostrophe (U+2018)
        normalized = normalized.replace('`', "'")  # Grave accent
        normalized = normalized.replace('´', "'")  # Acute accent
        normalized = normalized.replace('"', '"')  # Curly quote (U+201C)
        normalized = normalized.replace('"', '"')  # Another curly quote (U+201D)
        normalized = normalized.replace('–', '-')  # En dash
        normalized = normalized.replace('—', '-')  # Em dash
        
        # Remove all non-ASCII apostrophes and replace with standard apostrophe
        import unicodedata
        # Normalize unicode characters
        normalized = unicodedata.normalize('NFKD', normalized)
        
        # Replace any remaining non-standard apostrophes
        for char in normalized:
            if ord(char) > 127 and char in ["'", "'", "`", "´"]:
                normalized = normalized.replace(char, "'")
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        result = normalized.strip()
        
        # Debug logging for problematic characters
        if artist_name and ("'" in artist_name or "'" in artist_name):
            self.logger.debug(f"Apostrophe normalization: '{artist_name}' -> '{result}' (chars: {[ord(c) for c in artist_name if ord(c) > 127]})")
        
        return result

    def split_collaboration_artists(self, artist_string):
        """Split collaboration artists into individual artist names"""
        try:
            # Common collaboration separators
            separators = [
                ', ',     # Most common: "JAY Z, Linkin Park"
                ' & ',    # "Artist1 & Artist2"
                ' and ',  # "Artist1 and Artist2"
                ' feat. ', # "Artist1 feat. Artist2"
                ' featuring ', # "Artist1 featuring Artist2"
                ' vs. ',  # "Artist1 vs. Artist2"
                ' x ',    # "Artist1 x Artist2"
                ' with ', # "Artist1 with Artist2"
                ';',      # "Artist1;Artist2"
            ]
            
            artists = [artist_string]  # Start with the original string
            
            # Apply each separator
            for separator in separators:
                new_artists = []
                for artist in artists:
                    if separator in artist:
                        # Split and clean up
                        split_artists = [a.strip() for a in artist.split(separator)]
                        new_artists.extend(split_artists)
                    else:
                        new_artists.append(artist)
                artists = new_artists
            
            # Filter out empty strings and very short names (likely noise)
            filtered_artists = []
            for artist in artists:
                artist = artist.strip()
                if len(artist) > 1 and not artist.lower() in ['&', 'and', 'feat', 'featuring', 'vs', 'x', 'with']:
                    filtered_artists.append(artist)
            
            if len(filtered_artists) > 1:
                self.logger.info(f"Split collaboration '{artist_string}' into: {', '.join(filtered_artists)}")
            
            return filtered_artists if filtered_artists else [artist_string]
            
        except Exception as e:
            self.logger.warning(f"Error splitting artist string '{artist_string}': {e}")
            return [artist_string]

    def get_lidarr_artists(self):
        """Get all artists currently in Lidarr"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                artists = self.lidarr_client.get_artists()
                lidarr_artists = set()
                
                for artist in artists:
                    artist_name = artist.get('artistName', '').strip()
                    if artist_name:
                        normalized_name = self.lidarr_client.normalize_artist_name(artist_name)
                        lidarr_artists.add(normalized_name)
                
                self.logger.info(f"Found {len(lidarr_artists)} artists in Lidarr")
                return lidarr_artists
            else:
                # Fallback to old method
                return self._get_lidarr_artists_fallback()
                
        except Exception as e:
            self.logger.error(f"Error fetching Lidarr artists: {e}")
            return set()

    def _get_lidarr_artists_fallback(self):
        """Fallback method for getting Lidarr artists (original implementation)"""
        try:
            if self.dry_run:
                self.logger.info("DRY RUN: Skipping Lidarr artists fetch")
                return set()
            
            self.logger.info("Fetching existing artists from Lidarr...")
            
            headers = {'X-Api-Key': self.lidarr_api_key}
            url = f"{self.lidarr_url}/api/v1/artist"
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                artists = response.json()
                lidarr_artists = set()
                
                for artist in artists:
                    artist_name = artist.get('artistName', '').strip()
                    if artist_name:
                        normalized_name = self.normalize_artist_name(artist_name)
                        lidarr_artists.add(normalized_name)
                
                self.logger.info(f"Found {len(lidarr_artists)} artists in Lidarr")
                return lidarr_artists
            else:
                self.logger.error(f"Failed to fetch Lidarr artists: HTTP {response.status_code}")
                return set()
                
        except Exception as e:
            self.logger.error(f"Error fetching Lidarr artists: {e}")
            return set()

    def search_musicbrainz_artist(self, artist_name):
        """Search for artist in MusicBrainz using reusable utilities"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                return self.lidarr_client.search_musicbrainz_artist(artist_name)
            else:
                # Fallback to old method
                return self._search_musicbrainz_artist_fallback(artist_name)
                
        except Exception as e:
            self.logger.warning(f"Error searching MusicBrainz for {artist_name}: {e}")
            return None

    def _search_musicbrainz_artist_fallback(self, artist_name):
        """Fallback method for MusicBrainz search (original implementation)"""
        try:
            # Clean up artist name for search
            search_name = re.sub(r'[^\w\s-]', '', artist_name).strip()
            
            self.logger.debug(f"Searching MusicBrainz for: {search_name}")
            
            url = "https://musicbrainz.org/ws/2/artist"
            params = {
                'query': f'artist:"{search_name}"',
                'fmt': 'json',
                'limit': 10  # Increased limit for better matching
            }
            headers = {
                'User-Agent': 'NavidromeStarredAlbumsMonitor/1.0'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                artists = data.get('artists', [])
                
                # Look for exact match first
                for artist in artists:
                    if artist.get('name', '').lower() == artist_name.lower():
                        return {
                            'musicbrainz_id': artist.get('id'),
                            'name': artist.get('name'),
                            'sort_name': artist.get('sort-name'),
                            'disambiguation': artist.get('disambiguation', ''),
                            'type': artist.get('type', ''),
                            'score': artist.get('score', 0)
                        }
                
                # Look for close matches (handle cases like "JAY Z" vs "Jay-Z")
                for artist in artists:
                    artist_mb_name = artist.get('name', '').lower()
                    search_lower = artist_name.lower()
                    
                    # Handle common variations
                    if (artist_mb_name.replace('-', ' ') == search_lower.replace('-', ' ') or
                        artist_mb_name.replace(' ', '') == search_lower.replace(' ', '') or
                        artist_mb_name == search_lower.replace(' ', '-')):
                        
                        self.logger.info(f"Found close match for '{artist_name}': '{artist.get('name')}'")
                        return {
                            'musicbrainz_id': artist.get('id'),
                            'name': artist.get('name'),
                            'sort_name': artist.get('sort-name'),
                            'disambiguation': artist.get('disambiguation', ''),
                            'type': artist.get('type', ''),
                            'score': artist.get('score', 0)
                        }
                
                # If no exact/close match, return the first result with high score
                if artists and artists[0].get('score', 0) >= 90:
                    artist = artists[0]
                    self.logger.info(f"Using high-score match for '{artist_name}': '{artist.get('name')}' (score: {artist.get('score')})")
                    return {
                        'musicbrainz_id': artist.get('id'),
                        'name': artist.get('name'),
                        'sort_name': artist.get('sort-name'),
                        'disambiguation': artist.get('disambiguation', ''),
                        'type': artist.get('type', ''),
                        'score': artist.get('score', 0)
                    }
                
                return None
            else:
                self.logger.warning(f"MusicBrainz search failed for {artist_name}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.warning(f"Error searching MusicBrainz for {artist_name}: {e}")
            return None

    def add_artist_to_lidarr(self, artist_name, musicbrainz_data=None):
        """Add artist to Lidarr for monitoring using reusable utilities"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                return self.lidarr_client.add_artist_with_future_monitoring(
                    artist_name=artist_name,
                    musicbrainz_data=musicbrainz_data,
                    search_for_missing=False  # Don't search for existing albums
                )
            else:
                # Fallback to old method if LidarrClient not available
                return self._add_artist_to_lidarr_fallback(artist_name, musicbrainz_data)
                
        except Exception as e:
            self.logger.error(f"Error adding artist {artist_name} to Lidarr: {e}")
            return False

    def _add_artist_to_lidarr_fallback(self, artist_name, musicbrainz_data=None):
        """Fallback method for adding artist (original implementation)"""
        try:
            if self.dry_run:
                self.logger.info(f"DRY RUN: Would add artist to Lidarr: {artist_name}")
                return True
            
            # Get Lidarr configuration
            headers = {'X-Api-Key': self.lidarr_api_key}
            
            # Get quality profiles
            quality_url = f"{self.lidarr_url}/api/v1/qualityprofile"
            quality_response = requests.get(quality_url, headers=headers, timeout=10)
            
            if quality_response.status_code != 200:
                self.logger.error(f"Failed to get quality profiles: {quality_response.status_code}")
                return False
            
            quality_profiles = quality_response.json()
            quality_profile_id = quality_profiles[0]['id'] if quality_profiles else 1
            
            # Get root folders
            root_url = f"{self.lidarr_url}/api/v1/rootfolder"
            root_response = requests.get(root_url, headers=headers, timeout=10)
            
            if root_response.status_code != 200:
                self.logger.error(f"Failed to get root folders: {root_response.status_code}")
                return False
            
            root_folders = root_response.json()
            root_folder_path = root_folders[0]['path'] if root_folders else '/music'
            
            # Prepare artist data
            artist_data = {
                'artistName': musicbrainz_data['name'] if musicbrainz_data else artist_name,
                'foreignArtistId': musicbrainz_data['musicbrainz_id'] if musicbrainz_data else None,
                'qualityProfileId': quality_profile_id,
                'rootFolderPath': root_folder_path,
                'monitored': True,
                'albumFolder': True,
                'addOptions': {
                    'monitor': 'future',  # Only monitor future releases
                    'searchForMissingAlbums': False  # Don't search for existing albums
                }
            }
            
            # Remove None values
            artist_data = {k: v for k, v in artist_data.items() if v is not None}
            
            # Add artist to Lidarr
            add_url = f"{self.lidarr_url}/api/v1/artist"
            add_response = requests.post(add_url, headers=headers, json=artist_data, timeout=30)
            
            if add_response.status_code in [200, 201]:
                self.logger.info(f"Successfully added artist to Lidarr: {artist_name}")
                return True
            else:
                self.logger.warning(f"Failed to add artist {artist_name} to Lidarr: {add_response.status_code}")
                if add_response.text:
                    self.logger.debug(f"Error response: {add_response.text[:200]}")
                return False
                
        except Exception as e:
            self.logger.warning(f"Error adding artist {artist_name} to Lidarr: {e}")
            return False

    def verify_album_monitoring_status(self):
        """Verify album monitoring status for recently added artists"""
        try:
            if self.dry_run:
                self.logger.info("DRY RUN: Skipping album monitoring verification")
                print("🔍 DRY RUN: Skipping album monitoring verification")
                return True
            
            print("🔍 Checking album monitoring status in Lidarr...")
            
            headers = {'X-Api-Key': self.lidarr_api_key}
            
            # Get all artists
            artists_url = f"{self.lidarr_url}/api/v1/artist"
            artists_response = requests.get(artists_url, headers=headers, timeout=30)
            
            if artists_response.status_code != 200:
                self.logger.error(f"Failed to get artists from Lidarr: {artists_response.status_code}")
                return False
            
            artists = artists_response.json()
            
            # Focus on artists from our unique_artists list
            monitoring_stats = {
                'artists_checked': 0,
                'artists_monitoring_future': 0,
                'artists_monitoring_all': 0,
                'artists_not_monitoring': 0,
                'total_albums_monitored': 0,
                'total_albums_unmonitored': 0
            }
            
            print("📊 Album Monitoring Status:")
            
            # Check each artist from our starred albums
            for starred_artist_name in self.unique_artists:
                # Find this artist in Lidarr
                lidarr_artist = None
                for artist in artists:
                    if self.normalize_artist_name(artist.get('artistName', '')) == self.normalize_artist_name(starred_artist_name):
                        lidarr_artist = artist
                        break
                
                if not lidarr_artist:
                    continue  # Artist not in Lidarr
                
                monitoring_stats['artists_checked'] += 1
                artist_name = lidarr_artist.get('artistName', starred_artist_name)
                
                # Get albums for this artist
                albums_url = f"{self.lidarr_url}/api/v1/album"
                albums_params = {'artistId': lidarr_artist['id']}
                albums_response = requests.get(albums_url, headers=headers, params=albums_params, timeout=30)
                
                if albums_response.status_code == 200:
                    albums = albums_response.json()
                    
                    if albums:
                        monitored_albums = [album for album in albums if album.get('monitored', False)]
                        unmonitored_albums = [album for album in albums if not album.get('monitored', False)]
                        
                        monitoring_stats['total_albums_monitored'] += len(monitored_albums)
                        monitoring_stats['total_albums_unmonitored'] += len(unmonitored_albums)
                        
                        # Determine monitoring type based on album monitoring pattern
                        if len(monitored_albums) == 0:
                            monitoring_type = "NOT_MONITORING"
                            monitoring_stats['artists_not_monitoring'] += 1
                        elif len(monitored_albums) == len(albums):
                            monitoring_type = "ALL_ALBUMS"
                            monitoring_stats['artists_monitoring_all'] += 1
                        else:
                            monitoring_type = "SELECTIVE/FUTURE"
                            monitoring_stats['artists_monitoring_future'] += 1
                        
                        # Log detailed info for first few artists
                        if monitoring_stats['artists_checked'] <= 5:
                            self.logger.info(f"Artist: {artist_name}")
                            self.logger.info(f"  Total albums: {len(albums)}")
                            self.logger.info(f"  Monitored albums: {len(monitored_albums)}")
                            self.logger.info(f"  Monitoring type: {monitoring_type}")
                            
                            if monitored_albums:
                                recent_monitored = sorted(monitored_albums, key=lambda x: x.get('releaseDate', ''), reverse=True)[:3]
                                self.logger.info(f"  Recent monitored albums: {[album.get('title', 'Unknown') for album in recent_monitored]}")
                        
                        print(f"   🎵 {artist_name}: {len(monitored_albums)}/{len(albums)} albums monitored ({monitoring_type})")
                
                # Limit to first 10 artists to avoid spam
                if monitoring_stats['artists_checked'] >= 10:
                    break
            
            # Print summary
            print()
            print("📊 Monitoring Verification Summary:")
            print(f"   🎼 Artists checked: {monitoring_stats['artists_checked']}")
            print(f"   🔮 Future/Selective monitoring: {monitoring_stats['artists_monitoring_future']}")
            print(f"   📚 All albums monitoring: {monitoring_stats['artists_monitoring_all']}")
            print(f"   ❌ Not monitoring: {monitoring_stats['artists_not_monitoring']}")
            print(f"   ✅ Total albums monitored: {monitoring_stats['total_albums_monitored']}")
            print(f"   ⚪ Total albums unmonitored: {monitoring_stats['total_albums_unmonitored']}")
            
            # Log the summary
            self.logger.info("📊 Monitoring Verification Summary:")
            self.logger.info(f"   🎼 Artists checked: {monitoring_stats['artists_checked']}")
            self.logger.info(f"   🔮 Future/Selective monitoring: {monitoring_stats['artists_monitoring_future']}")
            self.logger.info(f"   📚 All albums monitoring: {monitoring_stats['artists_monitoring_all']}")
            self.logger.info(f"   ❌ Not monitoring: {monitoring_stats['artists_not_monitoring']}")
            self.logger.info(f"   ✅ Total albums monitored: {monitoring_stats['total_albums_monitored']}")
            self.logger.info(f"   ⚪ Total albums unmonitored: {monitoring_stats['total_albums_unmonitored']}")
            
            # Warning if too many artists are monitoring all albums
            if monitoring_stats['artists_monitoring_all'] > monitoring_stats['artists_monitoring_future']:
                print("⚠️  WARNING: More artists are monitoring ALL albums than just future releases!")
                print("⚠️  This could cause unwanted downloads of entire discographies.")
                self.logger.warning("More artists are monitoring ALL albums than just future releases - check configuration!")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error verifying album monitoring status: {e}")
            return False

    def monitor_starred_album_artists(self):
        """Main function to monitor starred album artists in Lidarr"""
        try:
            self.logger.info("Starting monitor_starred_album_artists method")
            print("🎵 Navidrome Starred Albums Monitor")
            print("=" * 50)
            
            if self.dry_run:
                print("🔍 DRY RUN MODE - No changes will be made")
                print()
            
            # Step 1: Get starred albums
            self.logger.info("Step 1: Fetching starred albums")
            print("📋 Fetching starred albums from Navidrome...")
            if not self.get_starred_albums():
                print("❌ Failed to fetch starred albums")
                self.logger.error("Failed to fetch starred albums")
                return False
            
            print(f"✅ Found {len(self.starred_albums)} starred albums from {len(self.unique_artists)} unique artists")
            print()
            
            # Step 2: Monitor specific starred albums
            self.logger.info("Step 2: Finding and monitoring specific starred albums")
            print("🎯 Finding and monitoring specific starred albums in Lidarr...")
            if not self.monitor_starred_albums_specifically():
                print("❌ Failed to monitor starred albums specifically")
                self.logger.error("Failed to monitor starred albums specifically")
                # Continue anyway - we can still add missing artists
            print()
            
            # Step 3: Get existing Lidarr artists
            self.logger.info("Step 3: Checking existing Lidarr artists")
            print("🎼 Checking existing artists in Lidarr...")
            lidarr_artists = self.get_lidarr_artists()
            print()
            
            # Step 4: Process each artist
            self.logger.info("Step 4: Processing artists")
            print("🔍 Processing artists...")
            artists_to_add = []
            artists_already_monitored = 0
            
            for artist_name in self.unique_artists:
                normalized_artist = self.normalize_artist_name(artist_name)
                self.logger.debug(f"Checking artist: '{artist_name}' -> normalized: '{normalized_artist}'")
                if normalized_artist in lidarr_artists:
                    artists_already_monitored += 1
                    self.logger.info(f"Artist already in Lidarr: {artist_name}")
                else:
                    artists_to_add.append(artist_name)
                    self.logger.debug(f"Artist not in Lidarr, will be added: {artist_name}")
            
            self.stats['artists_in_lidarr'] = artists_already_monitored
            
            print(f"📊 Artist Analysis:")
            print(f"   🎼 Total unique artists: {len(self.unique_artists)}")
            print(f"   ✅ Already in Lidarr: {artists_already_monitored}")
            print(f"   ➕ To be added: {len(artists_to_add)}")
            print()
            
            if not artists_to_add:
                print("🎉 All starred album artists are already monitored in Lidarr!")
                # Still show album monitoring results
                if self.stats['albums_found_in_lidarr'] > 0:
                    print(f"🎯 {self.stats['albums_set_to_monitored']} starred albums were set to monitored")
                return True
            
            # Step 5: Add missing artists
            print(f"➕ Adding {len(artists_to_add)} missing artists to Lidarr...")
            
            added_count = 0
            failed_count = 0
            
            for i, artist_name in enumerate(artists_to_add, 1):
                try:
                    print(f"🔍 Processing artist {i}/{len(artists_to_add)}: {artist_name}")
                    self.logger.info(f"Processing artist {i}/{len(artists_to_add)}: {artist_name}")
                    
                    # First, try to find the full artist name in MusicBrainz
                    musicbrainz_data = None
                    try:
                        musicbrainz_data = self.search_musicbrainz_artist(artist_name)
                    except Exception as mb_error:
                        self.logger.warning(f"MusicBrainz search failed for {artist_name}: {mb_error}")
                    
                    if musicbrainz_data:
                        # Found the full artist name, add it
                        self.logger.info(f"Found MusicBrainz data for {artist_name}: {musicbrainz_data['name']} (ID: {musicbrainz_data['musicbrainz_id']})")
                        
                        try:
                            if self.add_artist_to_lidarr(artist_name, musicbrainz_data):
                                added_count += 1
                                print(f"   ✅ Added: {artist_name}")
                            else:
                                failed_count += 1
                                print(f"   ❌ Failed: {artist_name}")
                                self.failed_artists.append(artist_name)
                        except Exception as add_error:
                            failed_count += 1
                            self.logger.error(f"Error adding artist {artist_name} to Lidarr: {add_error}")
                            print(f"   ❌ Error adding: {artist_name}")
                            self.failed_artists.append(artist_name)
                    else:
                        # Full artist name not found, try splitting into individual artists
                        self.logger.info(f"Full artist name '{artist_name}' not found in MusicBrainz, trying to split collaboration")
                        
                        try:
                            split_artists = self.split_collaboration_artists(artist_name)
                        except Exception as split_error:
                            self.logger.error(f"Error splitting collaboration '{artist_name}': {split_error}")
                            split_artists = [artist_name]  # Fallback to original name
                        
                        if len(split_artists) > 1:
                            # This is a collaboration, try adding individual artists
                            collaboration_success = 0
                            collaboration_failures = 0
                            
                            for split_artist in split_artists:
                                try:
                                    # Check if this individual artist is already in Lidarr
                                    normalized_split_artist = self.normalize_artist_name(split_artist)
                                    if normalized_split_artist in lidarr_artists:
                                        self.logger.info(f"Split artist already in Lidarr: {split_artist}")
                                        collaboration_success += 1
                                        continue
                                    
                                    # Search for individual artist
                                    split_mb_data = None
                                    try:
                                        split_mb_data = self.search_musicbrainz_artist(split_artist)
                                    except Exception as split_mb_error:
                                        self.logger.warning(f"MusicBrainz search failed for split artist {split_artist}: {split_mb_error}")
                                    
                                    if split_mb_data:
                                        self.logger.info(f"Found MusicBrainz data for split artist {split_artist}: {split_mb_data['name']} (ID: {split_mb_data['musicbrainz_id']})")
                                    else:
                                        self.logger.warning(f"No MusicBrainz data found for split artist {split_artist}, adding with original name")
                                    
                                    # Add individual artist to Lidarr
                                    try:
                                        if self.add_artist_to_lidarr(split_artist, split_mb_data):
                                            collaboration_success += 1
                                            print(f"   ✅ Added split artist: {split_artist}")
                                        else:
                                            collaboration_failures += 1
                                            print(f"   ❌ Failed split artist: {split_artist}")
                                            self.failed_artists.append(split_artist)
                                    except Exception as split_add_error:
                                        collaboration_failures += 1
                                        self.logger.error(f"Error adding split artist {split_artist} to Lidarr: {split_add_error}")
                                        print(f"   ❌ Error adding split artist: {split_artist}")
                                        self.failed_artists.append(split_artist)
                                    
                                    # Small delay between individual artist requests
                                    time.sleep(0.5)
                                
                                except Exception as split_processing_error:
                                    collaboration_failures += 1
                                    self.logger.error(f"Error processing split artist {split_artist}: {split_processing_error}")
                                    print(f"   ❌ Error processing split artist: {split_artist}")
                                    self.failed_artists.append(split_artist)
                            
                            if collaboration_success > 0:
                                added_count += 1
                                self.logger.info(f"Collaboration processed successfully: {collaboration_success}/{len(split_artists)} artists added for '{artist_name}'")
                                print(f"   ✅ Collaboration processed: {collaboration_success}/{len(split_artists)} artists added for '{artist_name}'")
                            else:
                                failed_count += 1
                                self.logger.warning(f"Collaboration failed: No artists added for '{artist_name}'")
                                print(f"   ❌ Collaboration failed: No artists added for '{artist_name}'")
                        else:
                            # Not a collaboration, add with original name
                            self.logger.warning(f"No MusicBrainz data found for {artist_name}, adding with original name")
                            
                            try:
                                if self.add_artist_to_lidarr(artist_name, None):
                                    added_count += 1
                                    print(f"   ✅ Added: {artist_name}")
                                else:
                                    failed_count += 1
                                    print(f"   ❌ Failed: {artist_name}")
                                    self.failed_artists.append(artist_name)
                            except Exception as final_add_error:
                                failed_count += 1
                                self.logger.error(f"Error adding artist {artist_name} to Lidarr: {final_add_error}")
                                print(f"   ❌ Error adding: {artist_name}")
                                self.failed_artists.append(artist_name)
                    
                    # Small delay between requests to be nice to APIs
                    if i < len(artists_to_add):
                        time.sleep(1)
                        
                except Exception as e:
                    failed_count += 1
                    self.logger.error(f"Error processing artist {artist_name}: {e}")
                    print(f"   ❌ Error: {artist_name} - {e}")
                    self.failed_artists.append(artist_name)
                    # Continue to next artist instead of breaking
            
            self.stats['artists_added_to_lidarr'] = added_count
            self.stats['artists_failed'] = failed_count
            
            # Print and log summary
            print()
            print("📊 Final Summary:")
            print(f"   📋 Starred albums processed: {self.stats['starred_albums']}")
            print(f"   🎼 Unique artists found: {self.stats['unique_artists']}")
            print(f"   ✅ Artists already in Lidarr: {self.stats['artists_in_lidarr']}")
            print(f"   ➕ Artists successfully added: {self.stats['artists_added_to_lidarr']}")
            print(f"   ❌ Artists failed to add: {self.stats['artists_failed']}")
            print()
            print("🎯 Album Monitoring Results:")
            print(f"   🏠 Albums owned (unstarred): {self.stats['albums_owned']}")
            print(f"   ⭐ Albums unstarred: {self.stats['albums_unstarred']}")
            print(f"   📵 Albums unmonitored: {self.stats['albums_unmonitored']}")
            print(f"   🔍 Non-owned albums found in Lidarr: {self.stats['albums_found_in_lidarr']}")
            print(f"   ✅ Albums already monitored: {self.stats['albums_already_monitored']}")
            print(f"   🎯 Albums set to monitored: {self.stats['albums_set_to_monitored']}")
            print(f"   ❌ Albums failed to monitor: {self.stats['albums_failed_to_monitor']}")
            
            # Log the summary as well
            self.logger.info("📊 Final Summary:")
            self.logger.info(f"   📋 Starred albums processed: {self.stats['starred_albums']}")
            self.logger.info(f"   🎼 Unique artists found: {self.stats['unique_artists']}")
            self.logger.info(f"   ✅ Artists already in Lidarr: {self.stats['artists_in_lidarr']}")
            self.logger.info(f"   ➕ Artists successfully added: {self.stats['artists_added_to_lidarr']}")
            self.logger.info(f"   ❌ Artists failed to add: {self.stats['artists_failed']}")
            self.logger.info("🎯 Album Monitoring Results:")
            self.logger.info(f"   🏠 Albums owned (unstarred): {self.stats['albums_owned']}")
            self.logger.info(f"   ⭐ Albums unstarred: {self.stats['albums_unstarred']}")
            self.logger.info(f"   📵 Albums unmonitored: {self.stats['albums_unmonitored']}")
            self.logger.info(f"   🔍 Non-owned albums found in Lidarr: {self.stats['albums_found_in_lidarr']}")
            self.logger.info(f"   ✅ Albums already monitored: {self.stats['albums_already_monitored']}")
            self.logger.info(f"   🎯 Albums set to monitored: {self.stats['albums_set_to_monitored']}")
            self.logger.info(f"   ❌ Albums failed to monitor: {self.stats['albums_failed_to_monitor']}")
            
            if self.failed_artists:
                print()
                print("❌ Failed Artists:")
                self.logger.info("❌ Failed Artists:")
                for artist in self.failed_artists:
                    print(f"   • {artist}")
                    self.logger.info(f"   • {artist}")
            
            self.logger.info("Navidrome Starred Albums Monitor completed successfully")
            
            # Step 6: Verify album monitoring status
            print()
            print("🔍 Step 6: Verifying album monitoring status...")
            self.verify_album_monitoring_status()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in monitor_starred_album_artists: {e}")
            return False

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Monitor starred album artists in Lidarr')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no changes made)')
    
    args = parser.parse_args()
    
    monitor = None
    try:
        # Initialize monitor
        print("🎵 Initializing Navidrome Starred Albums Monitor...")
        monitor = NavidromeStarredAlbumsMonitor(dry_run=args.dry_run)
        print("✅ Monitor initialized successfully")
        
        # Log action
        try:
            log_action(
                action="navidrome_starred_albums_monitor",
                details={
                    "dry_run": args.dry_run,
                    "log_file": str(monitor.log_file_path) if monitor.log_file_path else None
                }
            )
        except Exception as e:
            print(f"Warning: Could not log action: {e}")
        
        # Run the monitoring
        print("🔄 Starting monitoring process...")
        success = monitor.monitor_starred_album_artists()
        
        if success:
            print(f"\n✅ Navidrome Starred Albums Monitor completed successfully!")
            if monitor.log_file_path:
                print(f"📝 Full log available at: {monitor.log_file_path}")
        else:
            print(f"\n❌ Navidrome Starred Albums Monitor completed with errors!")
            if monitor.log_file_path:
                print(f"📝 Check log for details: {monitor.log_file_path}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️  Operation cancelled by user")
        if monitor and monitor.logger:
            monitor.logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        error_msg = f"\n❌ Unexpected error: {e}"
        print(error_msg)
        if monitor and monitor.logger:
            monitor.logger.error(f"Unexpected error in main: {e}", exc_info=True)
        else:
            print(f"Error occurred before logger was initialized: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()