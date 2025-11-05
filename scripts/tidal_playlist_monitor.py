#!/usr/bin/env python3
"""
Tidal Playlist Monitor - Add missing artists to Lidarr for monitoring

Name: Tidal Playlist Monitor
Author: SoulSeekarr Team
Version: 1.0.0
Section: Playlist Management
Tags: tidal, playlists, monitoring, artists
Supports dry run: Yes

This script can operate in two modes:
1. Batch Mode (default): Process all active playlists from settings
2. Single Mode: Process a specific playlist URL

For each playlist, the script:
1. Extracts songs from the Tidal playlist
2. Checks which songs are missing from Navidrome library  
3. Adds missing songs' artists to Lidarr for future release monitoring

Requires:
- TIDAL_ACCESS_TOKEN: Tidal access token (you can get this from browser dev tools)
- TIDAL_COUNTRY_CODE: Country code (e.g., 'US', 'UK', 'DE') - defaults to 'US'
- NAVIDROME_URL: Base Navidrome URL (e.g., http://localhost:4533)
- NAVIDROME_USERNAME: Navidrome username
- NAVIDROME_PASSWORD: Navidrome password
- LIDARR_URL: Lidarr base URL (e.g., http://localhost:8686)
- LIDARR_API_KEY: Lidarr API key

Usage:
    python tidal_playlist_monitor.py                                    # Process all active playlists
    python tidal_playlist_monitor.py --playlist-url "https://..."       # Process specific playlist
    python tidal_playlist_monitor.py --dry-run                          # Batch mode with dry run
"""

import os
import sys
import json
import argparse
import logging
import requests
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import re

# Add parent directory to path so we can import action_logger and lidarr_utils
sys.path.append(str(Path(__file__).parent.parent))
try:
    from action_logger import log_action
except ImportError as e:
    print(f"Warning: Could not import action_logger: {e}")
    def log_action(*args, **kwargs):
        pass  # No-op if action logger unavailable

try:
    from lidarr_utils import LidarrClient
except ImportError as e:
    print(f"Warning: Could not import lidarr_utils: {e}")
    LidarrClient = None

class TidalPlaylistMonitor:
    def __init__(self, dry_run=False):
        # Environment variables
        self.tidal_access_token = os.environ.get('TIDAL_ACCESS_TOKEN')
        self.tidal_country_code = os.environ.get('TIDAL_COUNTRY_CODE', 'US')
        self.navidrome_url = os.environ.get('NAVIDROME_URL')
        self.navidrome_username = os.environ.get('NAVIDROME_USERNAME')
        self.navidrome_password = os.environ.get('NAVIDROME_PASSWORD')
        self.lidarr_url = os.environ.get('LIDARR_URL')
        self.lidarr_api_key = os.environ.get('LIDARR_API_KEY')

        self.dry_run = dry_run

        # Initialize basic attributes first
        self.logger = None
        self.log_file_path = None

        try:
            # Setup logging
            self.setup_logging()

            # Initialize stats
            self.stats = {
                'playlist_songs': 0,
                'songs_in_library': 0,
                'songs_missing': 0,
                'songs_added_to_playlist': 0,
                'songs_queued_for_download': 0,
                'artists_added': 0,
                'artists_failed': 0,
                'errors': 0
            }

            # Track failed artists
            self.failed_artists = []

            # API tokens
            self.navidrome_token = None
            self.subsonic_salt = None
            self.subsonic_token = None

            # Data storage
            self.playlist_songs = []
            self.library_songs = set()
            self.missing_songs = []

            # Validate configuration
            self._validate_config()

            # Log environment configuration for debugging
            self._log_environment_config()

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
                if self.logger:
                    self.logger.warning("LidarrClient not available, using fallback methods")

        except Exception as e:
            error_msg = f"❌ Failed to initialize Tidal Playlist Monitor: {e}"
            print(error_msg)
            if self.logger:
                self.logger.error(error_msg)
            raise
    
    def setup_logging(self):
        """Setup logging to file and console"""
        try:
            # Create logs directory
            logs_dir = Path(__file__).parent.parent / 'logs'
            logs_dir.mkdir(exist_ok=True)
            
            # Create timestamped log file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = logs_dir / f"tidal_playlist_monitor_{timestamp}.log"
            
            # Setup logger
            self.logger = logging.getLogger('tidal_playlist_monitor')
            self.logger.setLevel(logging.INFO)
            
            # Clear any existing handlers
            self.logger.handlers.clear()
            
            # File handler
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)
            
            # Console handler  
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            
            # Formatter
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            # Add handlers
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
            
            self.log_file_path = str(log_file)
            self.logger.info(f"Tidal Playlist Monitor logging started - Log file: {self.log_file_path}")
            
            print(f"📋 Detailed log file: {self.log_file_path}")
            
        except Exception as e:
            print(f"❌ Failed to setup logging: {e}")
            # Create a basic console logger as fallback
            self.logger = logging.getLogger('tidal_playlist_monitor')
            self.logger.setLevel(logging.INFO)
            console_handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('%(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
            self.log_file_path = "Failed to create log file"
            raise
    
    def _validate_config(self):
        """Validate required configuration"""
        missing_vars = []
        
        if not self.tidal_access_token:
            missing_vars.append('TIDAL_ACCESS_TOKEN')
        if not self.navidrome_url:
            missing_vars.append('NAVIDROME_URL')
        if not self.navidrome_username:
            missing_vars.append('NAVIDROME_USERNAME')
        if not self.navidrome_password:
            missing_vars.append('NAVIDROME_PASSWORD')
        if not self.lidarr_url:
            missing_vars.append('LIDARR_URL')
        if not self.lidarr_api_key:
            missing_vars.append('LIDARR_API_KEY')
        
        if missing_vars:
            self.logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        self.logger.info(f"Configuration validated - Tidal: {self.tidal_access_token[:8]}..., "
                        f"Navidrome: {self.navidrome_url}, Lidarr: {self.lidarr_url}")
    
    def _log_environment_config(self):
        """Log environment configuration for debugging"""
        self.logger.debug("=== ENVIRONMENT CONFIGURATION ===")
        self.logger.debug(f"TIDAL_ACCESS_TOKEN: {self.tidal_access_token[:8] if self.tidal_access_token else 'NOT SET'}...")
        self.logger.debug(f"TIDAL_COUNTRY_CODE: {self.tidal_country_code}")
        self.logger.debug(f"NAVIDROME_URL: {self.navidrome_url}")
        self.logger.debug(f"NAVIDROME_USERNAME: {self.navidrome_username}")
        self.logger.debug(f"NAVIDROME_PASSWORD: {'SET' if self.navidrome_password else 'NOT SET'} (length: {len(self.navidrome_password) if self.navidrome_password else 0})")
        self.logger.debug(f"LIDARR_URL: {self.lidarr_url}")
        self.logger.debug(f"LIDARR_API_KEY: {self.lidarr_api_key[:8] if self.lidarr_api_key else 'NOT SET'}...")
        self.logger.debug(f"DRY_RUN: {self.dry_run}")
        self.logger.debug("=== END ENVIRONMENT CONFIGURATION ===")
    
    def _make_tidal_request(self, url, headers, method='GET', data=None, timeout=30):
        """Make a robust HTTPS request to Tidal API with error handling"""
        import urllib3
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Create a session with retry strategy
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        
        # Disable SSL warnings for problematic networks
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Try with different SSL configurations
        ssl_configs = [
            {'verify': True},  # Standard SSL verification
            {'verify': False},  # Disable SSL verification
        ]
        
        for i, ssl_config in enumerate(ssl_configs):
            try:
                if method.upper() == 'GET':
                    response = session.get(url, headers=headers, timeout=timeout, **ssl_config)
                elif method.upper() == 'POST':
                    response = session.post(url, headers=headers, data=data, timeout=timeout, **ssl_config)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                return response
                
            except (requests.exceptions.SSLError, 
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if i == len(ssl_configs) - 1:  # Last attempt
                    self.logger.error(f"Failed to connect to {url}: {e}")
                    raise
                else:
                    self.logger.warning(f"Request to {url} failed (attempt {i+1}), retrying with different SSL config: {e}")
        
        return None

    def extract_playlist_id(self, playlist_url):
        """Extract playlist ID from Tidal URL"""
        try:
            # Handle different Tidal URL formats
            # https://tidal.com/browse/playlist/00000000-0000-0000-0000-000000000000
            # https://listen.tidal.com/playlist/00000000-0000-0000-0000-000000000000
            
            if 'playlist/' in playlist_url:
                playlist_id = playlist_url.split('playlist/')[1].split('?')[0].split('&')[0]
                return playlist_id
            else:
                raise ValueError("Invalid Tidal playlist URL format")
                
        except Exception as e:
            self.logger.error(f"Error extracting playlist ID from URL {playlist_url}: {e}")
            return None
    
    def get_playlist_songs(self, playlist_url):
        """Get all songs from Tidal playlist"""
        try:
            playlist_id = self.extract_playlist_id(playlist_url)
            if not playlist_id:
                return False
            
            headers = {
                'Authorization': f'Bearer {self.tidal_access_token}',
                'X-Tidal-Token': self.tidal_access_token
            }
            
            # Get playlist info
            playlist_url_api = f"https://api.tidalhifi.com/v1/playlists/{playlist_id}"
            playlist_params = {'countryCode': self.tidal_country_code}
            playlist_response = self._make_tidal_request(playlist_url_api, headers, method='GET')
            
            if not playlist_response or playlist_response.status_code != 200:
                self.logger.error(f"Failed to get playlist info: {playlist_response.status_code if playlist_response else 'No response'}")
                return False
            
            playlist_info = playlist_response.json()
            playlist_name = playlist_info.get('title', 'Unknown Playlist')
            self.current_playlist_name = playlist_name  # Store for playlist management
            print(f"📋 Processing playlist: {playlist_name}")
            self.logger.info(f"Processing playlist: {playlist_name}")
            
            # Get all tracks (handle pagination)
            tracks_url = f"https://api.tidalhifi.com/v1/playlists/{playlist_id}/tracks"
            tracks_params = {
                'countryCode': self.tidal_country_code,
                'limit': 100,  # Maximum limit per request
                'offset': 0
            }
            
            all_tracks = []
            
            while True:
                tracks_response = self._make_tidal_request(tracks_url, headers, method='GET')
                
                if not tracks_response or tracks_response.status_code != 200:
                    self.logger.error(f"Failed to get playlist tracks: {tracks_response.status_code if tracks_response else 'No response'}")
                    return False
                
                tracks_data = tracks_response.json()
                items = tracks_data.get('items', [])
                
                if not items:
                    break  # No more tracks
                
                all_tracks.extend(items)
                
                # Check if there are more tracks
                if len(items) < tracks_params['limit']:
                    break  # Last page
                
                tracks_params['offset'] += tracks_params['limit']
            
            # Process tracks
            for track in all_tracks:
                if not track or track.get('type') != 'TRACK':
                    continue
                
                # Extract artist names
                artists = []
                for artist in track.get('artists', []):
                    artists.append(artist.get('name', ''))
                
                song_info = {
                    'title': track.get('title', ''),
                    'artists': artists,
                    'album': track.get('album', {}).get('title', ''),
                    'tidal_id': track.get('id', ''),
                    'duration': track.get('duration', 0)
                }
                
                self.playlist_songs.append(song_info)
            
            self.stats['playlist_songs'] = len(self.playlist_songs)
            print(f"    ✓ Found {len(self.playlist_songs)} songs in playlist")
            self.logger.info(f"Found {len(self.playlist_songs)} songs in playlist")
            
            return True
            
        except Exception as e:
            msg = f"Error getting playlist songs: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def authenticate_navidrome(self):
        """Authenticate with Navidrome and get access token"""
        try:
            auth_url = f"{self.navidrome_url}/auth/login"
            auth_data = {
                "username": self.navidrome_username,
                "password": self.navidrome_password
            }
            
            if self.dry_run:
                print("    [DRY RUN] Authenticating with Navidrome")
                self.logger.info("DRY RUN: Authenticating with Navidrome")
            else:
                self.logger.info("Authenticating with Navidrome...")
            
            response = requests.post(auth_url, json=auth_data, timeout=30)
            
            if response.status_code == 200:
                auth_response = response.json()
                self.navidrome_token = auth_response.get('token')
                self.subsonic_salt = auth_response.get('subsonicSalt')
                self.subsonic_token = auth_response.get('subsonicToken')
                
                if self.navidrome_token and self.subsonic_salt and self.subsonic_token:
                    print(f"    ✓ Navidrome authentication successful")
                    self.logger.info("Navidrome authentication successful")
                    return True
                else:
                    missing = []
                    if not self.navidrome_token:
                        missing.append("JWT token")
                    if not self.subsonic_salt:
                        missing.append("Subsonic salt")
                    if not self.subsonic_token:
                        missing.append("Subsonic token")
                    msg = f"Missing authentication data: {', '.join(missing)}"
                    print(f"    ❌ {msg}")
                    self.logger.error(msg)
                    return False
            else:
                msg = f"Failed to authenticate with Navidrome. Status: {response.status_code}, Response: {response.text}"
                print(f"    ❌ {msg}")
                self.logger.error(msg)
                return False
                
        except Exception as e:
            msg = f"Error authenticating with Navidrome: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def get_navidrome_library(self):
        """Get all songs from Navidrome library using Subsonic API"""
        try:
            if not self.subsonic_token or not self.subsonic_salt:
                self.logger.error("No Subsonic authentication credentials available")
                return False

            # Use Subsonic API to get all songs from library
            api_endpoints = [
                {
                    'url': f"{self.navidrome_url}/rest/search3",
                    'params': {
                        'query': '',  # Empty query to get everything
                        'songCount': 50000,  # Very large number to get all songs
                        'songOffset': 0,
                        'f': 'json'
                    },
                    'method': 'search3'
                },
                {
                    'url': f"{self.navidrome_url}/rest/getAlbumList2",
                    'params': {
                        'type': 'alphabeticalByName',
                        'size': 50000,  # Get all albums
                        'offset': 0,
                        'f': 'json'
                    },
                    'method': 'getAlbumList2'
                }
            ]
            
            all_songs = []
            
            # Try each endpoint until one works
            for i, endpoint in enumerate(api_endpoints):
                self.logger.debug(f"Trying Subsonic API endpoint {i+1}/{len(api_endpoints)}: {endpoint['method']}")
                
                # Build headers and params for Subsonic API
                headers = {}
                params = endpoint['params'].copy()
                params.update({
                    'u': self.navidrome_username,
                    't': self.subsonic_token,
                    's': self.subsonic_salt,
                    'v': '1.16.1',
                    'c': 'TidalPlaylistMonitor'
                })
                
                songs_url = endpoint['url']
                self.logger.info(f"Fetching songs from Navidrome using {endpoint['method']} endpoint...")
                
                response = requests.get(songs_url, headers=headers, params=params, timeout=60)
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        
                        subsonic_response = data.get('subsonic-response', {})
                        if subsonic_response.get('status') == 'failed':
                            error_info = subsonic_response.get('error', {})
                            error_msg = f"Subsonic API Error: {error_info.get('message', 'Unknown error')}"
                            self.logger.error(error_msg)
                            print(f"    ❌ {error_msg}")
                            continue
                        
                        self.logger.info(f"✅ Successfully connected using {endpoint['method']} endpoint")
                        
                        # Extract songs based on endpoint type
                        if endpoint['method'] == 'search3':
                            search_result = subsonic_response.get('searchResult3', {})
                            songs = search_result.get('song', [])
                            if songs:
                                all_songs.extend(songs)
                                self.logger.info(f"Retrieved {len(songs)} songs using {endpoint['method']}")
                                break
                            else:
                                self.logger.warning(f"No songs found using {endpoint['method']}")
                                continue
                        elif endpoint['method'] == 'getAlbumList2':
                            # Get all albums, then fetch songs from each album
                            album_list = subsonic_response.get('albumList2', {})
                            albums = album_list.get('album', [])
                            self.logger.info(f"Got {len(albums)} albums, fetching songs from each...")
                            
                            album_songs = []
                            for album in albums:
                                album_id = album.get('id')
                                if album_id:
                                    album_url = f"{self.navidrome_url}/rest/getAlbum"
                                    album_params = {
                                        'id': album_id,
                                        'f': 'json',
                                        'u': self.navidrome_username,
                                        't': self.subsonic_token,
                                        's': self.subsonic_salt,
                                        'v': '1.16.1',
                                        'c': 'TidalPlaylistMonitor'
                                    }
                                    
                                    try:
                                        album_response = requests.get(album_url, params=album_params, timeout=30)
                                        if album_response.status_code == 200:
                                            album_data = album_response.json()
                                            album_info = album_data.get('subsonic-response', {}).get('album', {})
                                            songs_in_album = album_info.get('song', [])
                                            if songs_in_album:
                                                album_songs.extend(songs_in_album)
                                        else:
                                            self.logger.warning(f"Failed to get album {album_id}: {album_response.status_code}")
                                    except Exception as e:
                                        self.logger.warning(f"Error fetching album {album_id}: {e}")
                            
                            if album_songs:
                                all_songs.extend(album_songs)
                                self.logger.info(f"Retrieved {len(album_songs)} songs from {len(albums)} albums using {endpoint['method']}")
                                break
                            else:
                                self.logger.warning(f"No songs found from albums using {endpoint['method']}")
                                continue
                            
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to parse JSON response from {endpoint['method']}: {e}")
                        continue
                        
                elif response.status_code == 401:
                    self.logger.warning(f"Endpoint {endpoint['method']} returned 401 - trying next endpoint if available")
                    continue
                else:
                    self.logger.warning(f"Endpoint {endpoint['method']} returned {response.status_code} - trying next endpoint if available")
                    continue
            
            # Process the songs if we got them
            if all_songs:
                # Create searchable set of songs
                for song in all_songs:
                    # Normalize for comparison
                    title = self.normalize_string(song.get('title', ''))
                    artist = self.normalize_string(song.get('artist', ''))
                    album = self.normalize_string(song.get('album', ''))
                    
                    # Create multiple keys for flexible matching
                    if artist and title:
                        self.library_songs.add(f"{artist}|{title}")
                    if artist and album and title:
                        self.library_songs.add(f"{artist}|{album}|{title}")
                
                print(f"    ✓ Loaded {len(all_songs)} songs from Navidrome library")
                self.logger.info(f"Loaded {len(all_songs)} songs from Navidrome library")
                return True
            else:
                self.logger.error("No songs retrieved from any Navidrome Subsonic API endpoint")
                return False
            
        except Exception as e:
            msg = f"Error getting Navidrome library: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False

    def normalize_string(self, s):
        """Normalize string for consistent matching"""
        import unicodedata
        if not s:
            return ""
        s = s.lower().strip()
        s = unicodedata.normalize('NFKD', s)
        # Handle common character replacements
        s = re.sub(r'[&]', 'and', s)
        s = re.sub(r"['']", '', s)  # Remove apostrophes
        s = re.sub(r'["""]', '', s)  # Remove quotes
        s = re.sub(r'[–—-]', ' ', s)  # Replace dashes with spaces
        s = re.sub(r'[^\w\s]', '', s)  # Remove all other special characters
        s = re.sub(r'\s+', ' ', s)  # Collapse multiple spaces
        s = s.strip()
        return s
    
    def clean_song_title(self, title):
        """Clean song title by removing featuring artists and other metadata"""
        if not title:
            return ""
        
        # Remove common patterns that appear in Tidal titles but not in file names
        cleaned = title
        
        # Remove featuring patterns (case insensitive)
        patterns_to_remove = [
            r'\s*\(ft\.?\s+[^)]+\)',      # (ft. Artist) or (ft Artist)
            r'\s*\(feat\.?\s+[^)]+\)',    # (feat. Artist) or (feat Artist)
            r'\s*\(featuring\s+[^)]+\)',  # (featuring Artist)
            r'\s*ft\.?\s+.*$',            # ft. Artist at end
            r'\s*feat\.?\s+.*$',          # feat. Artist at end
            r'\s*featuring\s+.*$',        # featuring Artist at end
            r'\s*\([^)]*remix[^)]*\)',    # Remove remix info
            r'\s*\([^)]*version[^)]*\)',  # Remove version info
            r'\s*\([^)]*edit[^)]*\)',     # Remove edit info
            r'\s*\([^)]*remaster[^)]*\)', # Remove remaster info
            r'\s*-\s*remaster.*$',        # Remove "- Remastered" at end
            r'\s*-\s*remix.*$',           # Remove "- Remix" at end
        ]
        
        for pattern in patterns_to_remove:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned
    
    def find_missing_songs(self):
        """Find songs from playlist that are missing from library"""
        try:
            for song in self.playlist_songs:
                # Clean the title to remove featuring info and metadata
                original_title = song['title']
                cleaned_title = self.clean_song_title(original_title)
                
                title = self.normalize_string(cleaned_title)
                artists = [self.normalize_string(artist) for artist in song['artists']]
                album = self.normalize_string(song['album'])
                found = False
                
                # Also try with original title if cleaned version doesn't match
                original_title_normalized = self.normalize_string(original_title)
                
                for artist in artists:
                    # Try with cleaned title first
                    if f"{artist}|{title}" in self.library_songs:
                        found = True
                        break
                    if f"{artist}|{album}|{title}" in self.library_songs:
                        found = True
                        break
                    
                    # If not found with cleaned title, try original title
                    if f"{artist}|{original_title_normalized}" in self.library_songs:
                        found = True
                        break
                    if f"{artist}|{album}|{original_title_normalized}" in self.library_songs:
                        found = True
                        break
                
                if found:
                    self.stats['songs_in_library'] += 1
                else:
                    self.missing_songs.append(song)
                    self.stats['songs_missing'] += 1
                    self.logger.info(f"Missing song: {song['artists'][0] if song['artists'] else 'Unknown'} - {song['title']}")
            
            print(f"    📊 Found {self.stats['songs_missing']} missing songs out of {self.stats['playlist_songs']} total")
            self.logger.info(f"Found {self.stats['songs_missing']} missing songs out of {self.stats['playlist_songs']} total")
            return True
        except Exception as e:
            msg = f"Error finding missing songs: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def search_musicbrainz_artist(self, artist_name):
        """Search MusicBrainz for artist and return artist info with MBID"""
        import urllib3
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        try:
            import time
            
            # MusicBrainz API endpoint
            mb_url = "https://musicbrainz.org/ws/2/artist"
            params = {
                'query': f'artist:"{artist_name}"',
                'fmt': 'json',
                'limit': 5
            }
            
            # Add User-Agent as required by MusicBrainz
            headers = {
                'User-Agent': 'TidalPlaylistMonitor/1.0 (https://github.com/your-repo)'
            }
            
            self.logger.debug(f"Searching MusicBrainz for artist: {artist_name}")
            
            # MusicBrainz requires rate limiting (1 request per second)
            time.sleep(1)
            
            # Create a session with retry strategy
            session = requests.Session()
            
            # Configure retry strategy
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"]
            )
            
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount("https://", adapter)
            
            # Disable SSL warnings for problematic networks
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # Try with different SSL configurations
            ssl_configs = [
                {'verify': True},  # Standard SSL verification
                {'verify': False},  # Disable SSL verification
            ]
            
            for i, ssl_config in enumerate(ssl_configs):
                try:
                    response = session.get(
                        mb_url, 
                        params=params, 
                        headers=headers, 
                        timeout=15,
                        **ssl_config
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        artists = data.get('artists', [])
                        
                        if artists:
                            # Look for exact matches first
                            for artist in artists:
                                mb_name = artist.get('name', '')
                                mb_id = artist.get('id', '')
                                score = artist.get('score', 0)
                                
                                # Exact match (case insensitive)
                                if mb_name.lower() == artist_name.lower():
                                    self.logger.info(f"Found exact MusicBrainz match: {mb_name} (ID: {mb_id}, Score: {score})")
                                    return {
                                        'name': mb_name,
                                        'mbid': mb_id,
                                        'score': score
                                    }
                            
                            # If no exact match, look for very close matches with high scores
                            for artist in artists:
                                mb_name = artist.get('name', '')
                                mb_id = artist.get('id', '')
                                score = artist.get('score', 0)
                                
                                # Normalize both names for comparison
                                normalized_mb = self.normalize_string(mb_name)
                                normalized_search = self.normalize_string(artist_name)
                                
                                # Very strict matching - names must be very similar AND high score
                                if (score >= 95 and 
                                    (normalized_mb == normalized_search or 
                                     artist_name.lower() in mb_name.lower() or 
                                     mb_name.lower() in artist_name.lower())):
                                    self.logger.info(f"Found close MusicBrainz match: {mb_name} (ID: {mb_id}, Score: {score})")
                                    return {
                                        'name': mb_name,
                                        'mbid': mb_id,
                                        'score': score
                                    }
                        
                        self.logger.debug(f"No good MusicBrainz matches found for: {artist_name}")
                        return None
                    elif response.status_code == 503:
                        self.logger.warning(f"MusicBrainz rate limited for {artist_name}, skipping")
                        return None
                    else:
                        error_msg = f"HTTP {response.status_code}: {response.text}"
                        self.logger.warning(f"MusicBrainz attempt {i+1} failed: {error_msg}")
                        if i == len(ssl_configs) - 1:  # Last attempt
                            self.logger.warning(f"MusicBrainz search failed for {artist_name}: {error_msg}")
                            return None
                        
                except (requests.exceptions.SSLError, 
                        requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout) as e:
                    error_msg = str(e)
                    self.logger.warning(f"MusicBrainz attempt {i+1} failed with connection error: {error_msg}")
                    if i == len(ssl_configs) - 1:  # Last attempt
                        self.logger.warning(f"Failed to connect to MusicBrainz for {artist_name}: {error_msg}")
                        return None
                    else:
                        self.logger.debug(f"Retrying MusicBrainz with different SSL configuration...")
            
            return None
                
        except Exception as e:
            self.logger.error(f"Error searching MusicBrainz for {artist_name}: {e}")
            return None

    def add_artist_to_lidarr(self, artist_name):
        """Add artist to Lidarr for monitoring future releases using reusable utilities"""
        try:
            if self.lidarr_client:
                # Use the new LidarrClient utility
                success = self.lidarr_client.add_artist_and_search_musicbrainz(
                    artist_name=artist_name,
                    future_monitoring=True,  # Only monitor future releases
                    search_for_missing=False  # Don't search for existing albums
                )
                
                if success:
                    if not self.dry_run:
                        print(f"    ✓ Added artist to Lidarr: {artist_name}")
                        self.stats['artists_added'] += 1
                    else:
                        print(f"    [DRY RUN] Would add artist to Lidarr: {artist_name}")
                else:
                    self.logger.warning(f"Failed to add artist {artist_name} to Lidarr")
                    self.stats['artists_failed'] += 1
                    
                return success
            else:
                # Fallback to old method if LidarrClient not available
                return self._add_artist_to_lidarr_fallback(artist_name)
                
        except Exception as e:
            self.logger.error(f"Error adding artist {artist_name} to Lidarr: {e}")
            self.stats['artists_failed'] += 1
            return False

    def _add_artist_to_lidarr_fallback(self, artist_name):
        """Fallback method for adding artist (original implementation)"""
        try:
            headers = {
                'X-Api-Key': self.lidarr_api_key,
                'Content-Type': 'application/json'
            }
            
            # Search for artist in Lidarr first
            search_url = f"{self.lidarr_url}/api/v1/search"
            search_params = {'term': artist_name}
            
            response = requests.get(search_url, headers=headers, params=search_params, timeout=30)
            
            if response.status_code != 200:
                self.logger.error(f"Failed to search for artist {artist_name} in Lidarr: {response.status_code}")
                return False
            
            search_results = response.json()
            
            # Find exact or best match
            best_match = None
            for result in search_results:
                if result.get('artistName', '').lower() == artist_name.lower():
                    best_match = result
                    break
            
            if not best_match and search_results:
                best_match = search_results[0]  # Take first result if no exact match
            
            # If no results in Lidarr, try MusicBrainz
            if not best_match:
                self.logger.info(f"No Lidarr results for {artist_name}, searching MusicBrainz...")
                mb_result = self.search_musicbrainz_artist(artist_name)
                
                if mb_result:
                    # Try searching Lidarr again with the MusicBrainz ID
                    mb_search_params = {'term': f'mbid:{mb_result["mbid"]}'}
                    
                    mb_response = requests.get(search_url, headers=headers, params=mb_search_params, timeout=30)
                    
                    if mb_response.status_code == 200:
                        mb_search_results = mb_response.json()
                        
                        if mb_search_results:
                            best_match = mb_search_results[0]
                            self.logger.info(f"Found artist in Lidarr using MusicBrainz ID: {mb_result['name']}")
                        else:
                            self.logger.info(f"MusicBrainz ID not found in Lidarr, using MusicBrainz data directly")
                            best_match = {
                                'artistName': mb_result['name'],
                                'foreignArtistId': mb_result['mbid'],
                                'name': mb_result['name'],
                                'mbId': mb_result['mbid']
                            }
                    else:
                        self.logger.error(f"Failed to search Lidarr with MusicBrainz ID: {mb_response.status_code}")
                        best_match = {
                            'artistName': mb_result['name'],
                            'foreignArtistId': mb_result['mbid'],
                            'name': mb_result['name'],
                            'mbId': mb_result['mbid']
                        }
                else:
                    self.logger.warning(f"No results found in Lidarr or MusicBrainz for artist: {artist_name}")
                    return False
            
            # Check if artist already exists
            artists_url = f"{self.lidarr_url}/api/v1/artist"
            existing_response = requests.get(artists_url, headers=headers, timeout=30)
            
            if existing_response.status_code == 200:
                existing_artists = existing_response.json()
                for existing in existing_artists:
                    if existing.get('artistName', '').lower() == artist_name.lower():
                        self.logger.info(f"Artist {artist_name} already exists in Lidarr")
                        return True
            
            # Add artist to Lidarr
            if self.dry_run:
                print(f"    [DRY RUN] Would add artist to Lidarr: {artist_name}")
                self.logger.info(f"DRY RUN: Would add artist to Lidarr: {artist_name}")
                return True
            
            # Get artist name and foreign artist ID, with fallbacks
            artist_name_to_add = best_match.get('artistName') or best_match.get('name') or artist_name
            
            # Prioritize MusicBrainz IDs
            foreign_artist_id = (
                best_match.get('foreignArtistId') or 
                best_match.get('mbId') or 
                best_match.get('mbid')
            )
            
            self.logger.info(f"Adding artist to Lidarr - Name: '{artist_name_to_add}', Foreign ID: '{foreign_artist_id}'")
            
            # Validate that we have a proper MusicBrainz ID
            if not foreign_artist_id or len(str(foreign_artist_id)) < 10:
                self.logger.warning(f"Invalid or missing MusicBrainz ID for artist '{artist_name_to_add}' - skipping")
                return False
            
            add_data = {
                'ArtistName': artist_name_to_add,
                'ForeignArtistId': foreign_artist_id,
                'QualityProfileId': 1,  # Default quality profile
                'MetadataProfileId': 1,  # Default metadata profile
                'Monitored': True,
                'RootFolderPath': '/music/Not_Owned',  # Root folder for monitored artists
                'AddOptions': {
                    'monitor': 'future',  # Only monitor future releases
                    'SearchForMissingAlbums': False  # Don't download existing albums
                }
            }
            
            add_response = requests.post(artists_url, headers=headers, json=add_data, timeout=30)
            
            if add_response.status_code in [200, 201]:
                print(f"    ✓ Added artist to Lidarr: {artist_name}")
                self.logger.info(f"Added artist to Lidarr: {artist_name}")
                self.stats['artists_added'] += 1
                return True
            else:
                self.logger.warning(f"Failed to add artist {artist_name} to Lidarr: {add_response.status_code}")
                self.stats['artists_failed'] += 1
                return False
                
        except Exception as e:
            msg = f"Error adding artist {artist_name} to Lidarr: {e}"
            self.logger.warning(msg)
            self.stats['artists_failed'] += 1
            return False
    
    def process_missing_artists(self):
        """Process missing songs and add their artists to Lidarr for monitoring"""
        try:
            if not self.missing_songs:
                print("📊 No missing songs to process")
                return True
            
            # Get unique artists from missing songs
            unique_artists = set()
            for song in self.missing_songs:
                # Add all artists for the song, not just the primary one
                for artist in song['artists']:
                    if artist and artist.strip():
                        unique_artists.add(artist.strip())
            
            if not unique_artists:
                print("📊 No artists found in missing songs")
                return True
            
            print(f"🎵 Processing {len(unique_artists)} unique artists from missing songs...")
            
            for i, artist_name in enumerate(sorted(unique_artists), 1):
                try:
                    print(f"🎵 Adding artist {i}/{len(unique_artists)}: {artist_name}")
                    success = self.add_artist_to_lidarr(artist_name)
                    
                    if success:
                        self.stats['artists_added'] += 1
                    else:
                        self.stats['artists_failed'] += 1
                        self.failed_artists.append(artist_name)
                    
                    # Small delay between requests to be respectful
                    if i < len(unique_artists):
                        time.sleep(1)
                        
                except Exception as e:
                    self.stats['artists_failed'] += 1
                    self.failed_artists.append(artist_name)
                    self.logger.error(f"Error processing artist {artist_name}: {e}")
            
            return True
            
        except Exception as e:
            msg = f"Error processing missing artists: {e}"
            print(f"❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def process_playlist(self, playlist_url):
        """Process a single Tidal playlist"""
        try:
            print(f"🎵 Processing Tidal playlist: {playlist_url}")
            self.logger.info(f"Processing Tidal playlist: {playlist_url}")
            
            # Step 1: Get playlist songs
            print("📋 Step 1: Extracting songs from Tidal playlist...")
            if not self.get_playlist_songs(playlist_url):
                return False
            
            # Step 2: Authenticate with Navidrome
            print("🔐 Step 2: Authenticating with Navidrome...")
            if not self.authenticate_navidrome():
                return False
            
            # Step 3: Get Navidrome library
            print("📚 Step 3: Loading Navidrome library...")
            if not self.get_navidrome_library():
                return False
            
            # Step 4: Find missing songs
            print("🔍 Step 4: Finding missing songs...")
            if not self.find_missing_songs():
                return False
            
            # Step 5: Add missing artists to Lidarr
            print("🎵 Step 5: Adding missing artists to Lidarr...")
            if not self.process_missing_artists():
                return False
            
            return True
            
        except Exception as e:
            msg = f"Error processing playlist: {e}"
            print(f"❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def print_summary(self):
        """Print processing summary"""
        try:
            print("\n" + "="*60)
            print("📊 TIDAL PLAYLIST MONITOR SUMMARY")
            print("="*60)
            print(f"📋 Playlist songs processed: {self.stats['playlist_songs']}")
            print(f"✅ Songs already in library: {self.stats['songs_in_library']}")
            print(f"❌ Songs missing from library: {self.stats['songs_missing']}")
            print(f"🎵 Artists added to Lidarr: {self.stats['artists_added']}")
            print(f"⚠️  Artists failed to add: {self.stats['artists_failed']}")
            print(f"💥 Total errors: {self.stats['errors']}")
            
            if self.failed_artists:
                print(f"\n⚠️  Failed to add {len(self.failed_artists)} artists:")
                for artist in self.failed_artists[:10]:  # Show first 10
                    print(f"   • {artist}")
                if len(self.failed_artists) > 10:
                    print(f"   ... and {len(self.failed_artists) - 10} more")
            
            print("="*60)
            print(f"📋 Detailed log file: {self.log_file_path}")
            print("="*60)
            
            # Log action for history
            action_data = {
                'playlist_songs': self.stats['playlist_songs'],
                'songs_in_library': self.stats['songs_in_library'],
                'songs_missing': self.stats['songs_missing'],
                'artists_added': self.stats['artists_added'],
                'artists_failed': self.stats['artists_failed'],
                'errors': self.stats['errors'],
                'failed_artists': self.failed_artists[:20]  # Limit for storage
            }
            
            log_action(
                'tidal_playlist_monitor',
                'batch' if not hasattr(self, 'single_playlist_mode') else 'single',
                action_data,
                dry_run=self.dry_run
            )
            
        except Exception as e:
            self.logger.error(f"Error printing summary: {e}")

def load_playlist_configs():
    """Load playlist configurations from JSON file"""
    try:
        config_file = Path(__file__).parent / 'playlist_configs.json'
        if config_file.exists():
            with open(config_file, 'r') as f:
                configs = json.load(f)
                return configs.get('tidal_playlists', [])
        else:
            # Create default config file if it doesn't exist
            default_config = {
                "tidal_playlists": [
                    {
                        "name": "My Tidal Playlist",
                        "url": "https://tidal.com/browse/playlist/00000000-0000-0000-0000-000000000000",
                        "enabled": True
                    }
                ]
            }
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            print(f"📝 Created default playlist config file: {config_file}")
            print("📝 Please edit the file to add your Tidal playlist URLs")
            return []
    except Exception as e:
        print(f"❌ Error loading playlist configs: {e}")
        return []

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Monitor Tidal playlists and add missing artists to Lidarr')
    parser.add_argument('--playlist-url', help='Process a specific playlist URL')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no actual changes)')
    
    args = parser.parse_args()
    
    try:
        monitor = TidalPlaylistMonitor(dry_run=args.dry_run)
        
        if args.playlist_url:
            # Single playlist mode
            monitor.single_playlist_mode = True
            print(f"🎵 Single playlist mode")
            success = monitor.process_playlist(args.playlist_url)
        else:
            # Batch mode - process all configured playlists
            print(f"🎵 Batch mode - processing all configured playlists")
            playlists = load_playlist_configs()
            
            if not playlists:
                print("❌ No playlists configured. Use --playlist-url for single playlist or configure playlists in playlist_configs.json")
                return 1
            
            enabled_playlists = [p for p in playlists if p.get('enabled', True)]
            
            if not enabled_playlists:
                print("❌ No enabled playlists found in configuration")
                return 1
            
            print(f"📋 Found {len(enabled_playlists)} enabled playlists")
            
            success = True
            for i, playlist in enumerate(enabled_playlists, 1):
                playlist_name = playlist.get('name', f'Playlist {i}')
                playlist_url = playlist.get('url', '')
                
                if not playlist_url:
                    print(f"⚠️  Skipping playlist '{playlist_name}' - no URL configured")
                    continue
                
                print(f"\n🎵 Processing playlist {i}/{len(enabled_playlists)}: {playlist_name}")
                playlist_success = monitor.process_playlist(playlist_url)
                
                if not playlist_success:
                    success = False
                
                # Small delay between playlists
                if i < len(enabled_playlists):
                    print("⏳ Waiting before next playlist...")
                    time.sleep(5)
        
        # Print final summary
        monitor.print_summary()
        
        return 0 if success else 1
        
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
        return 130
    except Exception as e:
        print(f"💥 Critical error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())