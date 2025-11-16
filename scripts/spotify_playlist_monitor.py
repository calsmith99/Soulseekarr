#!/usr/bin/env python3
"""
Spotify Playlist Monitor - Add missing artists to Lidarr for monitoring

This script can operate in two modes:
1. Batch Mode (default): Process all active playlists from settings
2. Single Mode: Process a specific playlist URL

For each playlist, the script:
1. Extracts songs from the Spotify playlist
2. Checks which songs are missing from Navidrome library  
3. Adds missing songs' artists to Lidarr for future release monitoring

Name: Spotify Playlist Monitor
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: spotify, playlist, lidarr, monitoring
Supports dry run: true

Requires:
- SPOTIFY_CLIENT_ID: Spotify app client ID
- SPOTIFY_CLIENT_SECRET: Spotify app client secret
- NAVIDROME_URL: Base Navidrome URL (e.g., http://localhost:4533)
- NAVIDROME_USERNAME: Navidrome username
- NAVIDROME_PASSWORD: Navidrome password
- LIDARR_URL: Lidarr base URL (e.g., http://localhost:8686)
- LIDARR_API_KEY: Lidarr API key

Usage:
    python spotify_playlist_monitor.py                                    # Process all active playlists
    python spotify_playlist_monitor.py --playlist-url "https://..."       # Process specific playlist
    python spotify_playlist_monitor.py --dry-run                          # Batch mode with dry run
"""

import os
import sys
import json
import argparse
import logging
import requests
import base64
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import re

# Add parent directory to path so we can import action_logger and lidarr_utils
sys.path.append(str(Path(__file__).parent.parent))

from slskd_utils import search_and_download_song

# Try to import settings
try:
    from settings import (
        get_navidrome_config, 
        get_lidarr_config, 
        get_slskd_config,
        get_spotify_client_id,
        get_spotify_client_secret
    )
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

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

class SpotifyPlaylistMonitor:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        
        # Try to get configuration from settings module first
        if SETTINGS_AVAILABLE:
            try:
                # Get Spotify config
                self.spotify_client_id = get_spotify_client_id()
                self.spotify_client_secret = get_spotify_client_secret()
                
                # Get Navidrome config
                navidrome_config = get_navidrome_config()
                self.navidrome_url = navidrome_config.get('url')
                self.navidrome_username = navidrome_config.get('username')
                self.navidrome_password = navidrome_config.get('password')
                
                # Get Lidarr config
                lidarr_config = get_lidarr_config()
                self.lidarr_url = lidarr_config.get('url')
                self.lidarr_api_key = lidarr_config.get('api_key')
                
                # Get slskd config
                slskd_config = get_slskd_config()
                self.slskd_url = slskd_config.get('url')
                self.slskd_api_key = slskd_config.get('api_key')
            except Exception as e:
                print(f"Warning: Could not load from settings module: {e}")
                # Set to None to trigger fallback
                self.spotify_client_id = None
                self.spotify_client_secret = None
                self.navidrome_url = None
                self.navidrome_username = None
                self.navidrome_password = None
                self.lidarr_url = None
                self.lidarr_api_key = None
                self.slskd_url = None
                self.slskd_api_key = None
        else:
            # Set to None to trigger fallback
            self.spotify_client_id = None
            self.spotify_client_secret = None
            self.navidrome_url = None
            self.navidrome_username = None
            self.navidrome_password = None
            self.lidarr_url = None
            self.lidarr_api_key = None
            self.slskd_url = None
            self.slskd_api_key = None
        
        # Fall back to environment variables if settings not available
        if not self.spotify_client_id:
            self.spotify_client_id = os.environ.get('SPOTIFY_CLIENT_ID')
        if not self.spotify_client_secret:
            self.spotify_client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
        
        if not self.navidrome_url:
            self.navidrome_url = os.environ.get('NAVIDROME_URL')
        if not self.navidrome_username:
            self.navidrome_username = os.environ.get('NAVIDROME_USERNAME')
        if not self.navidrome_password:
            self.navidrome_password = os.environ.get('NAVIDROME_PASSWORD')
        
        if not self.lidarr_url:
            self.lidarr_url = os.environ.get('LIDARR_URL')
        if not self.lidarr_api_key:
            self.lidarr_api_key = os.environ.get('LIDARR_API_KEY')
        
        if not self.slskd_url:
            self.slskd_url = os.environ.get('SLSKD_URL')
        if not self.slskd_api_key:
            self.slskd_api_key = os.environ.get('SLSKD_API_KEY')

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
            self.spotify_token = None
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
            error_msg = f"❌ Failed to initialize Spotify Playlist Monitor: {e}"
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
            log_file = logs_dir / f"spotify_playlist_monitor_{timestamp}.log"
            
            # Setup logger
            self.logger = logging.getLogger('spotify_playlist_monitor')
            self.logger.setLevel(logging.INFO)
            
            # Clear any existing handlers
            self.logger.handlers.clear()
            
            # File handler
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.INFO)  # Reduced from DEBUG
            
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
            self.logger.info(f"Spotify Playlist Monitor logging started - Log file: {self.log_file_path}")
            
            print(f"📋 Detailed log file: {self.log_file_path}")
            
        except Exception as e:
            print(f"❌ Failed to setup logging: {e}")
            # Create a basic console logger as fallback
            self.logger = logging.getLogger('spotify_playlist_monitor')
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
        
        if not self.spotify_client_id:
            missing_vars.append('SPOTIFY_CLIENT_ID')
        if not self.spotify_client_secret:
            missing_vars.append('SPOTIFY_CLIENT_SECRET')
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
        
        self.logger.info(f"Configuration validated - Spotify: {self.spotify_client_id[:8]}..., "
                        f"Navidrome: {self.navidrome_url}, Lidarr: {self.lidarr_url}")
    
    def _log_environment_config(self):
        """Log environment configuration for debugging"""
        self.logger.debug("=== ENVIRONMENT CONFIGURATION ===")
        self.logger.debug(f"SPOTIFY_CLIENT_ID: {self.spotify_client_id[:8] if self.spotify_client_id else 'NOT SET'}...")
        self.logger.debug(f"SPOTIFY_CLIENT_SECRET: {'SET' if self.spotify_client_secret else 'NOT SET'} (length: {len(self.spotify_client_secret) if self.spotify_client_secret else 0})")
        self.logger.debug(f"NAVIDROME_URL: {self.navidrome_url}")
        self.logger.debug(f"NAVIDROME_USERNAME: {self.navidrome_username}")
        self.logger.debug(f"NAVIDROME_PASSWORD: {'SET' if self.navidrome_password else 'NOT SET'} (length: {len(self.navidrome_password) if self.navidrome_password else 0})")
        self.logger.debug(f"LIDARR_URL: {self.lidarr_url}")
        self.logger.debug(f"LIDARR_API_KEY: {self.lidarr_api_key[:8] if self.lidarr_api_key else 'NOT SET'}...")
        self.logger.debug(f"DRY_RUN: {self.dry_run}")
        self.logger.debug("=== END ENVIRONMENT CONFIGURATION ===")
    
    def authenticate_spotify(self):
        """Authenticate with Spotify and get access token"""
        import ssl
        import urllib3
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        try:
            auth_url = "https://accounts.spotify.com/api/token"
            
            # Encode client credentials
            client_creds = f"{self.spotify_client_id}:{self.spotify_client_secret}"
            client_creds_b64 = base64.b64encode(client_creds.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {client_creds_b64}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            data = {
                'grant_type': 'client_credentials'
            }
            
            if self.dry_run:
                print("    [DRY RUN] Authenticating with Spotify")
                self.logger.info("DRY RUN: Authenticating with Spotify")
                # For dry run, we still need real authentication to get playlist data
                # Only Lidarr operations will be skipped
            else:
                self.logger.info("Authenticating with Spotify...")
            
            # Create a session with retry strategy and SSL configuration
            session = requests.Session()
            
            # Configure retry strategy
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["POST"]
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
                    self.logger.info(f"Attempting Spotify authentication (attempt {i+1}/{len(ssl_configs)})")
                    
                    response = session.post(
                        auth_url, 
                        headers=headers, 
                        data=data, 
                        timeout=30,
                        **ssl_config
                    )
                    
                    if response.status_code == 200:
                        auth_response = response.json()
                        self.spotify_token = auth_response.get('access_token')
                        
                        if self.spotify_token:
                            print(f"    ✓ Spotify authentication successful")
                            self.logger.info("Spotify authentication successful")
                            return True
                        else:
                            msg = "No access token in Spotify response"
                            print(f"    ❌ {msg}")
                            self.logger.error(msg)
                            self.stats['errors'] += 1
                            return False
                    else:
                        error_msg = f"HTTP {response.status_code}: {response.text}"
                        self.logger.warning(f"Authentication attempt {i+1} failed: {error_msg}")
                        if i == len(ssl_configs) - 1:  # Last attempt
                            print(f"    ❌ Failed to authenticate with Spotify: {error_msg}")
                            self.logger.error(f"Failed to authenticate with Spotify: {error_msg}")
                            self.stats['errors'] += 1
                            return False
                        
                except (requests.exceptions.SSLError, 
                        requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout) as e:
                    error_msg = str(e)
                    self.logger.warning(f"Authentication attempt {i+1} failed with connection error: {error_msg}")
                    if i == len(ssl_configs) - 1:  # Last attempt
                        print(f"    ❌ Failed to connect to Spotify: {error_msg}")
                        self.logger.error(f"Failed to connect to Spotify: {error_msg}")
                        self.stats['errors'] += 1
                        return False
                    else:
                        self.logger.info(f"Retrying with different SSL configuration...")
                        
            return False
            
        except Exception as e:
            msg = f"Error authenticating with Spotify: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def _make_spotify_request(self, url, headers, method='GET', data=None, timeout=30):
        """Make a robust HTTPS request to Spotify API with SSL error handling"""
        import urllib3
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Create a session with retry strategy and SSL configuration
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
                # For dry run, we still need real authentication to get library data
                # Only Lidarr operations will be skipped
            else:
                self.logger.info("Authenticating with Navidrome...")
            
            # Log full authentication request details
            self.logger.debug(f"=== NAVIDROME AUTHENTICATION REQUEST ===")
            self.logger.debug(f"Auth URL: {auth_url}")
            self.logger.debug(f"Auth data: {{'username': '{self.navidrome_username}', 'password': '***'}}")
            self.logger.debug(f"Request timeout: 30s")
            
            response = requests.post(auth_url, json=auth_data, timeout=30)
            
            # Log full authentication response details
            self.logger.debug(f"=== NAVIDROME AUTHENTICATION RESPONSE ===")
            self.logger.debug(f"Status Code: {response.status_code}")
            self.logger.debug(f"Response URL: {response.url}")
            self.logger.debug(f"Response Headers: {dict(response.headers)}")
            
            try:
                response_text = response.text
                self.logger.debug(f"Full Response Body: {response_text}")
            except Exception as log_error:
                self.logger.warning(f"Could not log response body: {log_error}")
            
            if response.status_code == 200:
                auth_response = response.json()
                self.navidrome_token = auth_response.get('token')
                self.subsonic_salt = auth_response.get('subsonicSalt')
                self.subsonic_token = auth_response.get('subsonicToken')
                
                if self.navidrome_token and self.subsonic_salt and self.subsonic_token:
                    self.logger.debug(f"Extracted JWT token: {self.navidrome_token[:20]}...{self.navidrome_token[-10:] if len(self.navidrome_token) > 30 else self.navidrome_token}")
                    self.logger.debug(f"Extracted Subsonic salt: {self.subsonic_salt}")
                    self.logger.debug(f"Extracted Subsonic token: {self.subsonic_token}")
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
    
    def extract_playlist_id(self, playlist_url):
        """Extract playlist ID from Spotify URL"""
        try:
            # Handle different Spotify URL formats
            # https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd
            # https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd?si=...
            
            if 'playlist/' in playlist_url:
                playlist_id = playlist_url.split('playlist/')[1].split('?')[0].split('&')[0]
                return playlist_id
            else:
                raise ValueError("Invalid Spotify playlist URL format")
                
        except Exception as e:
            self.logger.error(f"Error extracting playlist ID from URL {playlist_url}: {e}")
            return None
    
    def get_playlist_songs(self, playlist_url):
        """Get all songs from Spotify playlist"""
        try:
            playlist_id = self.extract_playlist_id(playlist_url)
            if not playlist_id:
                return False
            
            headers = {
                'Authorization': f'Bearer {self.spotify_token}'
            }
            
            # Get playlist info
            playlist_url_api = f"https://api.spotify.com/v1/playlists/{playlist_id}"
            playlist_response = self._make_spotify_request(playlist_url_api, headers)
            
            if playlist_response.status_code != 200:
                self.logger.error(f"Failed to get playlist info: {playlist_response.status_code}")
                return False
            
            playlist_info = playlist_response.json()
            playlist_name = playlist_info.get('name', 'Unknown Playlist')
            self.current_playlist_name = playlist_name  # Store for playlist management
            print(f"📋 Processing playlist: {playlist_name}")
            self.logger.info(f"Processing playlist: {playlist_name}")
            
            # Get all tracks (handle pagination)
            tracks_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
            all_tracks = []
            
            while tracks_url:
                tracks_response = self._make_spotify_request(tracks_url, headers)
                
                if tracks_response.status_code != 200:
                    self.logger.error(f"Failed to get playlist tracks: {tracks_response.status_code}")
                    return False
                
                tracks_data = tracks_response.json()
                all_tracks.extend(tracks_data.get('items', []))
                tracks_url = tracks_data.get('next')  # Next page URL
            
            # Process tracks
            for item in all_tracks:
                track = item.get('track')
                if not track or track.get('type') != 'track':
                    continue
                
                song_info = {
                    'title': track.get('name', ''),
                    'artists': [artist.get('name', '') for artist in track.get('artists', [])],
                    'album': track.get('album', {}).get('name', ''),
                    'spotify_id': track.get('id', ''),
                    'duration_ms': track.get('duration_ms', 0)
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
    
    def get_navidrome_library(self):
        """Get all songs from Navidrome library using Subsonic API"""
        try:
            if not self.subsonic_token or not self.subsonic_salt:
                self.logger.error("No Subsonic authentication credentials available")
                return False

            # Use Subsonic API endpoints to get ALL songs from library
            # We'll use getAlbumList2 to get all albums, then getAlbum for each to get all songs
            api_endpoints = [
                # Option 1: Use search3 with better queries to get more comprehensive results
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
                # Option 2: Get all albums then fetch songs from each album
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
                
                # Build headers and params for Subsonic API (no Bearer token needed)
                headers = {}
                
                # Add required Subsonic API parameters
                params = endpoint['params'].copy()
                params.update({
                    'u': self.navidrome_username,  # Username
                    't': self.subsonic_token,      # Token (MD5 hash of password + salt)
                    's': self.subsonic_salt,       # Salt
                    'v': '1.16.1',                 # API version
                    'c': 'SpotifyPlaylistMonitor'  # Client name
                })
                
                songs_url = endpoint['url']
                
                self.logger.info(f"Fetching songs from Navidrome using {endpoint['method']} endpoint...")
                
                # Log comprehensive request details
                self.logger.debug(f"=== NAVIDROME SUBSONIC API REQUEST (Method: {endpoint['method']}) ===")
                self.logger.debug(f"Request URL: {songs_url}")
                self.logger.debug(f"Request Method: GET")
                self.logger.debug(f"Request Headers: {headers}")
                self.logger.debug(f"Request Params: {params}")
                self.logger.debug(f"Request Timeout: 60s")
                self.logger.debug(f"Current Subsonic Token: {self.subsonic_token}")
                self.logger.debug(f"Current Subsonic Salt: {self.subsonic_salt}")
                
                response = requests.get(songs_url, headers=headers, params=params, timeout=60)
                
                # Log comprehensive response details
                self.logger.debug(f"=== NAVIDROME SUBSONIC API RESPONSE (Method: {endpoint['method']}) ===")
                self.logger.debug(f"Response Status Code: {response.status_code}")
                self.logger.debug(f"Response URL: {response.url}")
                self.logger.debug(f"Response Headers: {dict(response.headers)}")
                self.logger.debug(f"Response Encoding: {response.encoding}")
                self.logger.debug(f"Response History: {response.history}")
                
                # Log response content with size check
                try:
                    response_text = response.text
                    if len(response_text) > 2000:
                        self.logger.debug(f"Response Body (first 1000 chars): {response_text[:1000]}")
                        self.logger.debug(f"Response Body (last 1000 chars): {response_text[-1000:]}")
                        self.logger.debug(f"Total response body length: {len(response_text)} characters")
                    else:
                        self.logger.debug(f"Full Response Body: {response_text}")
                    
                    if response.status_code != 200:
                        print(f"    ❌ Navidrome Subsonic API Error (method {endpoint['method']}): {response.status_code} - {response_text}")
                except Exception as log_error:
                    self.logger.warning(f"Could not log response body: {log_error}")
                
                # If this endpoint works, process the response
                if response.status_code == 200:
                    try:
                        data = response.json()
                        
                        # Check for Subsonic API error in response
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
                                    # Get songs from this album
                                    album_url = f"{self.navidrome_url}/rest/getAlbum"
                                    album_params = {
                                        'id': album_id,
                                        'f': 'json',
                                        'u': self.navidrome_username,
                                        't': self.subsonic_token,
                                        's': self.subsonic_salt,
                                        'v': '1.16.1',
                                        'c': 'SpotifyPlaylistMonitor'
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
                # Normalization function for consistent matching
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
                
                # Debug: Log some sample songs from library
                sample_songs = list(self.library_songs)[:10]
                self.logger.debug(f"Sample library songs: {sample_songs}")
                
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

    def get_subsonic_playlists_and_starred(self):
        """Get all songs from Subsonic playlists and starred songs"""
        try:
            playlist_songs = set()
            starred_songs = set()
            
            # Get playlists
            playlists_url = f"{self.navidrome_url}/rest/getPlaylists"
            playlists_params = {
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'SpotifyPlaylistMonitor'
            }
            
            self.logger.info("Fetching Subsonic playlists...")
            response = requests.get(playlists_url, params=playlists_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    playlists = subsonic_response.get('playlists', {}).get('playlist', [])
                    
                    self.logger.info(f"Found {len(playlists)} playlists, fetching songs...")
                    
                    # Get songs from each playlist
                    for playlist in playlists:
                        playlist_id = playlist.get('id')
                        if playlist_id:
                            playlist_detail_url = f"{self.navidrome_url}/rest/getPlaylist"
                            playlist_detail_params = {
                                'id': playlist_id,
                                'f': 'json',
                                'u': self.navidrome_username,
                                't': self.subsonic_token,
                                's': self.subsonic_salt,
                                'v': '1.16.1',
                                'c': 'SpotifyPlaylistMonitor'
                            }
                            
                            try:
                                playlist_response = requests.get(playlist_detail_url, params=playlist_detail_params, timeout=30)
                                if playlist_response.status_code == 200:
                                    playlist_data = playlist_response.json()
                                    playlist_info = playlist_data.get('subsonic-response', {}).get('playlist', {})
                                    songs = playlist_info.get('entry', [])
                                    
                                    for song in songs:
                                        # Use same normalization as library songs
                                        title = self.normalize_string(song.get('title', ''))
                                        artist = self.normalize_string(song.get('artist', ''))
                                        if artist and title:
                                            playlist_songs.add(f"{artist}|{title}")
                                            
                            except Exception as e:
                                self.logger.warning(f"Error fetching playlist {playlist_id}: {e}")
                                
            # Get starred songs
            starred_url = f"{self.navidrome_url}/rest/getStarred2"
            starred_params = {
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'SpotifyPlaylistMonitor'
            }
            
            self.logger.info("Fetching starred songs...")
            starred_response = requests.get(starred_url, params=starred_params, timeout=30)
            
            if starred_response.status_code == 200:
                starred_data = starred_response.json()
                subsonic_response = starred_data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    starred_info = subsonic_response.get('starred2', {})
                    songs = starred_info.get('song', [])
                    
                    for song in songs:
                        title = self.normalize_string(song.get('title', ''))
                        artist = self.normalize_string(song.get('artist', ''))
                        if artist and title:
                            starred_songs.add(f"{artist}|{title}")
            
            self.logger.info(f"Found {len(playlist_songs)} songs in playlists, {len(starred_songs)} starred songs")
            return playlist_songs, starred_songs
            
        except Exception as e:
            self.logger.error(f"Error getting playlists and starred songs: {e}")
            return set(), set()

    def normalize_string(self, s):
        """Normalize string for consistent matching (extracted as separate method)"""
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
        
        # Remove common patterns that appear in Spotify titles but not in file names
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
        
        # Log the cleaning if something changed
        if cleaned != title:
            self.logger.debug(f"Cleaned title: '{title}' -> '{cleaned}'")
        
        return cleaned
    
    def is_song_already_downloaded(self, song):
        """Check if a song already exists in the completed downloads folder"""
        try:
            downloads_dir = "/downloads/completed"
            
            if not os.path.exists(downloads_dir):
                self.logger.debug(f"Downloads directory does not exist: {downloads_dir}")
                return False
            
            # Normalize song info for comparison
            artist = self.normalize_string(song['artist'])
            title = self.normalize_string(song['title'])
            cleaned_title = self.normalize_string(self.clean_song_title(song['title']))
            
            # Common audio file extensions
            audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac']
            
            # Search through the downloads directory recursively
            for root, dirs, files in os.walk(downloads_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Delete macOS metadata files immediately
                    if file.startswith('._'):
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            pass  # Silently ignore errors for metadata files
                        continue
                    
                    # Check if it's an audio file
                    if not any(file.lower().endswith(ext) for ext in audio_extensions):
                        continue
                    
                    # Normalize filename for comparison
                    normalized_filename = self.normalize_string(file)
                    
                    # Check if both artist and title (or cleaned title) appear in the filename
                    if (artist in normalized_filename and 
                        (title in normalized_filename or cleaned_title in normalized_filename)):
                        self.logger.debug(f"Found existing download: {os.path.join(root, file)}")
                        return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking for existing download: {e}")
            return False  # If we can't check, assume it's not downloaded
    
    def is_song_currently_downloading(self, song):
        """Check if a song is currently being downloaded in slskd"""
        try:
            if not self.slskd_url or not self.slskd_api_key:
                return False
            
            # Get current downloads from slskd
            url = f"{self.slskd_url}/api/v0/transfers/downloads"
            headers = {'X-API-Key': self.slskd_api_key}
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                self.logger.debug(f"Failed to get downloads list: {response.status_code}")
                return False
            
            downloads = response.json()
            
            # Normalize song info for comparison
            artist = self.normalize_string(song['artist'])
            title = self.normalize_string(song['title'])
            cleaned_title = self.normalize_string(self.clean_song_title(song['title']))
            
            # Check if any active download matches this song
            for download in downloads:
                filename = download.get('filename', '')
                normalized_filename = self.normalize_string(filename)
                
                # Check if both artist and title (or cleaned title) appear in the filename
                if (artist in normalized_filename and 
                    (title in normalized_filename or cleaned_title in normalized_filename)):
                    # Check download state - consider these as "downloading"
                    state = download.get('state', '').lower()
                    if state in ['queued', 'initializing', 'requested', 'inprogress', 'completed']:
                        self.logger.debug(f"Song is currently downloading: {filename} (state: {state})")
                        return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking current downloads: {e}")
            return False  # If we can't check, assume it's not downloading
    
    def ensure_navidrome_playlist(self, playlist_name):
        """Ensure a playlist with the given name exists in Navidrome, create if it doesn't exist"""
        try:
            # Get all playlists
            playlists_url = f"{self.navidrome_url}/rest/getPlaylists"
            playlists_params = {
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'SpotifyPlaylistMonitor'
            }
            
            response = requests.get(playlists_url, params=playlists_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    playlists = subsonic_response.get('playlists', {}).get('playlist', [])
                    
                    # Check if playlist already exists
                    for playlist in playlists:
                        if playlist.get('name') == playlist_name:
                            playlist_id = playlist.get('id')
                            self.logger.info(f"Found existing playlist '{playlist_name}' with ID: {playlist_id}")
                            return playlist_id
                    
                    # Playlist doesn't exist, create it
                    if not self.dry_run:
                        create_url = f"{self.navidrome_url}/rest/createPlaylist"
                        create_params = {
                            'name': playlist_name,
                            'f': 'json',
                            'u': self.navidrome_username,
                            't': self.subsonic_token,
                            's': self.subsonic_salt,
                            'v': '1.16.1',
                            'c': 'SpotifyPlaylistMonitor'
                        }
                        
                        create_response = requests.get(create_url, params=create_params, timeout=30)
                        
                        if create_response.status_code == 200:
                            create_data = create_response.json()
                            create_subsonic = create_data.get('subsonic-response', {})
                            if create_subsonic.get('status') == 'ok':
                                playlist_info = create_subsonic.get('playlist', {})
                                playlist_id = playlist_info.get('id')
                                self.logger.info(f"Created new playlist '{playlist_name}' with ID: {playlist_id}")
                                print(f"✅ Created playlist: {playlist_name}")
                                return playlist_id
                            else:
                                error = create_subsonic.get('error', {})
                                self.logger.warning(f"Failed to create playlist '{playlist_name}': {error}")
                                return None
                        else:
                            self.logger.warning(f"Failed to create playlist '{playlist_name}': HTTP {create_response.status_code}")
                            return None
                    else:
                        self.logger.info(f"DRY RUN: Would create playlist '{playlist_name}'")
                        print(f"🔍 DRY RUN: Would create playlist: {playlist_name}")
                        return "dry-run-playlist-id"
                        
            self.logger.warning(f"Failed to get playlists: HTTP {response.status_code}")
            return None
            
        except Exception as e:
            self.logger.error(f"Error ensuring playlist '{playlist_name}': {e}")
            return None
    
    def get_navidrome_playlist_songs(self, playlist_id):
        """Get all songs currently in the specified Navidrome playlist"""
        try:
            if self.dry_run and playlist_id == "dry-run-playlist-id":
                return set()  # Return empty set for dry run
                
            playlist_detail_url = f"{self.navidrome_url}/rest/getPlaylist"
            playlist_detail_params = {
                'id': playlist_id,
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'SpotifyPlaylistMonitor'
            }
            
            response = requests.get(playlist_detail_url, params=playlist_detail_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    playlist_info = subsonic_response.get('playlist', {})
                    songs = playlist_info.get('entry', [])
                    
                    playlist_songs = set()
                    for song in songs:
                        title = song.get('title', '')
                        artist = song.get('artist', '')
                        if artist and title:
                            # Add both normalized original title and cleaned title variants
                            normalized_artist = self.normalize_string(artist)
                            normalized_title = self.normalize_string(title)
                            cleaned_title = self.normalize_string(self.clean_song_title(title))
                            
                            # Add original title version
                            playlist_songs.add(f"{normalized_artist}|{normalized_title}")
                            
                            # Add cleaned title version if different
                            if cleaned_title != normalized_title:
                                playlist_songs.add(f"{normalized_artist}|{cleaned_title}")
                    
                    self.logger.info(f"Found {len(playlist_songs)} song variants in playlist {playlist_id}")
                    return playlist_songs
                else:
                    error = subsonic_response.get('error', {})
                    self.logger.warning(f"Failed to get playlist songs: {error}")
                    return set()
            else:
                self.logger.warning(f"Failed to get playlist songs: HTTP {response.status_code}")
                return set()
                
        except Exception as e:
            self.logger.error(f"Error getting playlist songs: {e}")
            return set()
    
    def add_songs_to_navidrome_playlist(self, playlist_id, playlist_name, songs_to_add):
        """Add songs to the Navidrome playlist"""
        try:
            if not songs_to_add:
                self.logger.info("No songs to add to playlist")
                return True
                
            if self.dry_run:
                self.logger.info(f"DRY RUN: Would add {len(songs_to_add)} songs to playlist '{playlist_name}'")
                print(f"🔍 DRY RUN: Would add {len(songs_to_add)} songs to playlist")
                for song in songs_to_add:
                    print(f"   • {song['artist']} - {song['title']}")
                return True
            
            songs_added = 0
            songs_failed = 0
            
            for song in songs_to_add:
                # Find the song ID in the library
                song_id = self.find_song_id_in_library(song)
                
                if song_id:
                    # Add song to playlist
                    update_url = f"{self.navidrome_url}/rest/updatePlaylist"
                    update_params = {
                        'playlistId': playlist_id,
                        'songIdToAdd': song_id,
                        'f': 'json',
                        'u': self.navidrome_username,
                        't': self.subsonic_token,
                        's': self.subsonic_salt,
                        'v': '1.16.1',
                        'c': 'SpotifyPlaylistMonitor'
                    }
                    
                    response = requests.get(update_url, params=update_params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        subsonic_response = data.get('subsonic-response', {})
                        if subsonic_response.get('status') == 'ok':
                            songs_added += 1
                            self.logger.info(f"Added to playlist: {song['artist']} - {song['title']}")
                        else:
                            songs_failed += 1
                            error = subsonic_response.get('error', {})
                            self.logger.warning(f"Failed to add song to playlist: {song['artist']} - {song['title']} - {error}")
                    else:
                        songs_failed += 1
                        self.logger.warning(f"Failed to add song to playlist: {song['artist']} - {song['title']} - HTTP {response.status_code}")
                else:
                    songs_failed += 1
                    self.logger.warning(f"Could not find song ID for: {song['artist']} - {song['title']}")
            
            print(f"📋 Added {songs_added} songs to playlist '{playlist_name}'")
            if songs_failed > 0:
                print(f"⚠️  Failed to add {songs_failed} songs to playlist")
                
            self.logger.info(f"Playlist update complete - Added: {songs_added}, Failed: {songs_failed}")
            return songs_failed == 0
            
        except Exception as e:
            self.logger.error(f"Error adding songs to playlist: {e}")
            return False
    
    def find_song_id_in_library(self, song):
        """Find the song ID in the Navidrome library for a given song"""
        try:
            # Search for the song using cleaned title for better matching
            cleaned_title = self.clean_song_title(song['title'])
            search_url = f"{self.navidrome_url}/rest/search3"
            search_params = {
                'query': f'"{song["artist"]}" "{cleaned_title}"',
                'songCount': 10,
                'f': 'json',
                'u': self.navidrome_username,
                't': self.subsonic_token,
                's': self.subsonic_salt,
                'v': '1.16.1',
                'c': 'SpotifyPlaylistMonitor'
            }
            
            response = requests.get(search_url, params=search_params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})
                if subsonic_response.get('status') == 'ok':
                    search_result = subsonic_response.get('searchResult3', {})
                    songs = search_result.get('song', [])
                    
                    # Look for exact match - try both cleaned and original titles
                    target_artist = self.normalize_string(song['artist'])
                    target_title_cleaned = self.normalize_string(self.clean_song_title(song['title']))
                    target_title_original = self.normalize_string(song['title'])
                    
                    for found_song in songs:
                        found_artist = self.normalize_string(found_song.get('artist', ''))
                        found_title = self.normalize_string(found_song.get('title', ''))
                        
                        # Try matching with both cleaned and original titles
                        if (found_artist == target_artist and 
                            (found_title == target_title_cleaned or found_title == target_title_original)):
                            return found_song.get('id')
                    
                    # If no exact match, return first result if available
                    if songs:
                        self.logger.info(f"No exact match for {song['artist']} - {song['title']}, using closest match")
                        return songs[0].get('id')
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding song ID for {song['artist']} - {song['title']}: {e}")
            return None
    
    def find_missing_songs(self):
        """Find songs from playlist that are missing from library, playlists, or starred songs"""
        try:
            # Get playlist and starred songs for enhanced matching
            print("📋 Loading playlists and starred songs for enhanced matching...")
            playlist_songs, starred_songs = self.get_subsonic_playlists_and_starred()
            
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
                    search_keys = [f"{artist}|{title}", f"{artist}|{album}|{title}"]
                    
                    # Check in main library
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
                    
                    # Check in playlists (try both cleaned and original title)
                    if f"{artist}|{title}" in playlist_songs:
                        found = True
                        break
                    if f"{artist}|{original_title_normalized}" in playlist_songs:
                        found = True
                        break
                    
                    # Check in starred songs (try both cleaned and original title)
                    if f"{artist}|{title}" in starred_songs:
                        found = True
                        break
                    if f"{artist}|{original_title_normalized}" in starred_songs:
                        found = True
                        break
                
                # If not found with exact match, try fuzzy matching for better accuracy
                if not found:
                    for artist in artists:
                        # Try partial matching - check if key words exist
                        title_words = title.split()
                        if len(title_words) >= 2:  # Only for titles with multiple words
                            # Check in all sources: library, playlists, starred
                            all_sources = [
                                ('library', self.library_songs),
                                ('playlists', playlist_songs),
                                ('starred', starred_songs)
                            ]
                            
                            for source_name, source_songs in all_sources:
                                for lib_key in source_songs:
                                    if artist in lib_key:
                                        # Check if most significant words from title are in the library key
                                        significant_words = [w for w in title_words if len(w) > 2]  # Skip short words
                                        if len(significant_words) >= 2:
                                            matches = sum(1 for word in significant_words if word in lib_key)
                                            if matches >= len(significant_words) * 0.7:  # 70% of words match
                                                found = True
                                                break
                                if found:
                                    break
                            if found:
                                break
                
                if found:
                    self.stats['songs_in_library'] += 1
                else:
                    self.missing_songs.append(song)
                    self.stats['songs_missing'] += 1
                    self.logger.info(f"Missing song: {song['artists'][0]} - {song['title']} (normalized: {artists[0]}|{title})")
            
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
                'User-Agent': 'SpotifyPlaylistMonitor/1.0 (https://github.com/your-repo)'
            }
            
            self.logger.debug(f"Searching MusicBrainz for artist: {artist_name}")
            
            # MusicBrainz requires rate limiting (1 request per second)
            time.sleep(1)
            
            # Create a session with retry strategy and SSL configuration
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
                            
                            # If still no match, be very conservative - only accept if names are very similar
                            best_match = max(artists, key=lambda x: x.get('score', 0))
                            if best_match.get('score', 0) >= 95:
                                mb_name = best_match.get('name', '')
                                mb_id = best_match.get('id', '')
                                score = best_match.get('score', 0)
                                
                                # Additional check - the names should be reasonably similar
                                normalized_mb = self.normalize_string(mb_name)
                                normalized_search = self.normalize_string(artist_name)
                                
                                # Only return if names are very similar (avoid "Beck" -> "Rufus Beck" issues)
                                if (normalized_mb == normalized_search or 
                                    len(artist_name) >= 4 and artist_name.lower() in mb_name.lower()):
                                    self.logger.info(f"Found MusicBrainz match (verified): {mb_name} (ID: {mb_id}, Score: {score})")
                                    return {
                                        'name': mb_name,
                                        'mbid': mb_id,
                                        'score': score
                                    }
                                else:
                                    self.logger.warning(f"Skipping MusicBrainz result '{mb_name}' - too different from '{artist_name}'")
                        
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

    def add_track_to_wanted_queue(self, song_info):
        """Add specific track to Lidarr's wanted queue"""
        try:
            headers = {
                'X-Api-Key': self.lidarr_api_key,
                'Content-Type': 'application/json'
            }
            
            # First, we need to find the artist in Lidarr
            artist_name = song_info['artists'][0]  # Use primary artist
            search_url = f"{self.lidarr_url}/api/v1/search"
            search_params = {'term': artist_name}
            
            response = requests.get(search_url, headers=headers, params=search_params, timeout=30)
            
            if response.status_code != 200:
                self.logger.error(f"Failed to search for artist {artist_name} in Lidarr: {response.status_code}")
                return False
            
            search_results = response.json()
            
            # Find the artist
            artist_match = None
            for result in search_results:
                if result.get('artistName', '').lower() == artist_name.lower():
                    artist_match = result
                    break
            
            if not artist_match and search_results:
                artist_match = search_results[0]  # Take first result if no exact match
            
            if not artist_match:
                # Try MusicBrainz fallback
                self.logger.info(f"No Lidarr results for {artist_name}, searching MusicBrainz...")
                mb_result = self.search_musicbrainz_artist(artist_name)
                
                if mb_result:
                    # Try searching Lidarr again with the MusicBrainz ID
                    mb_search_params = {'term': f'mbid:{mb_result["mbid"]}'}
                    mb_response = requests.get(search_url, headers=headers, params=mb_search_params, timeout=30)
                    
                    if mb_response.status_code == 200:
                        mb_search_results = mb_response.json()
                        if mb_search_results:
                            artist_match = mb_search_results[0]
                            self.logger.info(f"Found artist in Lidarr using MusicBrainz ID: {mb_result['name']}")
                
                if not artist_match:
                    self.logger.warning(f"Could not find artist {artist_name} in Lidarr for track: {song_info['title']}")
                    return False
            
            # Now try to find the specific album/track
            # Fix: Handle nested structure from Lidarr search response
            foreign_artist_id = (
                artist_match.get('foreignId') or  # Top-level field
                artist_match.get('foreignArtistId') or  # Direct field (for compatibility)
                artist_match.get('artist', {}).get('foreignArtistId')  # Nested in artist object
            )
            
            if not foreign_artist_id:
                self.logger.warning(f"No foreign artist ID for {artist_name}")
                return False
            
            # Search for albums by this artist to find the one containing our track
            # FIXED: Get the actual artist database ID, not the search result ID
            artist_id = artist_match.get('artist', {}).get('id')
            
            if not artist_id:
                self.logger.warning(f"No artist database ID found in artist_match for {artist_name}")
                self.logger.warning(f"This can happen when artist is found via MusicBrainz ID but doesn't exist in Lidarr yet")
                self.logger.warning(f"artist_match structure: {artist_match}")
                self.logger.warning(f"Available IDs: search_id={artist_match.get('id')}, artist_id={artist_match.get('artist', {}).get('id')}")
                self.logger.info(f"Artist {artist_name} needs to be added to Lidarr first")
                # Skip this track for now - the artist will be added in the monitoring phase
                return False
            
            albums_url = f"{self.lidarr_url}/api/v1/album"
            albums_params = {'artistId': artist_id}
            
            self.logger.debug(f"=== ALBUM SEARCH FOR {artist_name} ===")
            self.logger.debug(f"Albums URL: {albums_url}")
            self.logger.debug(f"Albums params: {albums_params}")
            self.logger.debug(f"Using CORRECT artistId: {artist_id} (not search result id: {artist_match.get('id')})")
            self.logger.debug(f"Artist name: {artist_match.get('artist', {}).get('artistName', artist_name)}")
            
            albums_response = requests.get(albums_url, headers=headers, params=albums_params, timeout=30)
            
            self.logger.debug(f"Albums response status: {albums_response.status_code}")
            self.logger.debug(f"Albums response URL: {albums_response.url}")
            
            if albums_response.status_code == 200:
                albums = albums_response.json()
                self.logger.info(f"Found {len(albums)} albums for artist {artist_name} using correct artist ID")
                
                # Now we should get the right albums - but let's still verify
                if albums:
                    first_album_artist = albums[0].get('artist', {}).get('artistName', 'Unknown')
                    self.logger.debug(f"First album: '{albums[0].get('title', '')}' by '{first_album_artist}'")
                    
                    if first_album_artist.lower() != artist_name.lower():
                        self.logger.warning(f"Still getting wrong artist albums - expected {artist_name}, got {first_album_artist}")
                
                if not albums:
                    self.logger.warning(f"No albums found for artist {artist_name}")
                    return False
                
                # Look for album containing this track, or find the best matching album
                target_album = None
                
                song_title_norm = self.normalize_string(song_info['title'])
                song_album_norm = self.normalize_string(song_info['album'])
                
                # First, try to find exact album match
                for album in albums:
                    album_title_norm = self.normalize_string(album.get('title', ''))
                    if song_album_norm and album_title_norm == song_album_norm:
                        target_album = album
                        self.logger.info(f"Found exact album match: {album.get('title', '')}")
                        break
                
                # If no exact album match, look for albums that might contain this track
                if not target_album and albums:
                    # For singles/EPs, the song might be in a differently named album
                    # Try to find any album that could contain this track
                    for album in albums:
                        album_title = album.get('title', '')
                        # Check if song title appears in album title or vice versa
                        if (song_title_norm in self.normalize_string(album_title) or 
                            any(word in self.normalize_string(album_title) for word in song_title_norm.split() if len(word) > 3)):
                            target_album = album
                            self.logger.info(f"Found potential album match: {album_title}")
                            break
                    
                    # If still no match, take the most recent album
                    if not target_album:
                        # Sort by date and take the most recent
                        sorted_albums = sorted(albums, key=lambda x: x.get('releaseDate', '0000-01-01'), reverse=True)
                        if sorted_albums:
                            target_album = sorted_albums[0]
                            self.logger.warning(f"No album match found for song '{song_info['title']}' from album '{song_info['album']}', using most recent album: {target_album.get('title', '')} (released: {target_album.get('releaseDate', 'Unknown')})")
                            self.logger.debug(f"Target album artist: {target_album.get('artist', {}).get('artistName', 'Unknown')}")
                            self.logger.debug(f"Target album foreignAlbumId: {target_album.get('foreignAlbumId', 'None')}")
                            
                            # This seems wrong - let's see what albums we actually have
                            self.logger.warning(f"Album mismatch - song '{song_info['title']}' from album '{song_info['album']}' not found, using most recent album '{target_album.get('title', '')}' instead")
                
                if target_album:
                    if self.dry_run:
                        print(f"    [DRY RUN] Would queue album for track: {artist_name} - {song_info['title']} (album: {target_album.get('title', '')})")
                        self.logger.info(f"DRY RUN: Would queue album for track: {artist_name} - {song_info['title']} (album: {target_album.get('title', '')})")
                        return True
                    
                    # Queue the entire album for download
                    album_monitor_data = {
                        'id': target_album['id'],
                        'monitored': True
                    }
                    
                    album_update_url = f"{self.lidarr_url}/api/v1/album/{target_album['id']}"
                    album_response = requests.put(album_update_url, headers=headers, json=album_monitor_data, timeout=30)
                    
                    if album_response.status_code in [200, 201]:
                        print(f"    ✓ Queued album for download: {artist_name} - {target_album.get('title', '')} (contains: {song_info['title']})")
                        self.logger.info(f"Queued album for download: {artist_name} - {target_album.get('title', '')} (contains: {song_info['title']})")
                        return True
                    else:
                        self.logger.error(f"Failed to queue album: {album_response.status_code} - {album_response.text}")
                        return False
                else:
                    self.logger.warning(f"Could not find any suitable album for: {artist_name} - {song_info['title']}")
                    return False
            else:
                self.logger.error(f"Failed to get albums for artist {artist_name}: {albums_response.status_code}")
                return False
                
        except Exception as e:
            msg = f"Error adding track to wanted queue {song_info['artists'][0]} - {song_info['title']}: {e}"
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False

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
                    if not self.dry_run:
                        self.logger.warning(f"Failed to add artist {artist_name} to Lidarr")
                        self.stats['artists_failed'] += 1
                    else:
                        print(f"    [DRY RUN] Failed to process artist: {artist_name}")
                    
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
            # Initialize MusicBrainz result tracking
            self._last_mb_result = None
            
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
                self._last_mb_result = mb_result  # Store for potential use later
                
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
            
            # Prioritize MusicBrainz IDs - don't use Lidarr's internal 'id' field
            foreign_artist_id = (
                best_match.get('foreignArtistId') or 
                best_match.get('mbId') or 
                best_match.get('mbid')
            )
            
            # If we still don't have a foreign artist ID and we used MusicBrainz, use that
            if not foreign_artist_id and hasattr(self, '_last_mb_result') and self._last_mb_result:
                foreign_artist_id = self._last_mb_result.get('mbid')
                artist_name_to_add = self._last_mb_result.get('name') or artist_name_to_add
                self.logger.info(f"Using MusicBrainz fallback ID: {foreign_artist_id}")
            
            self.logger.info(f"Adding artist to Lidarr - Name: '{artist_name_to_add}', Foreign ID: '{foreign_artist_id}'")
            
            # Validate that we have a proper MusicBrainz ID (should be a UUID format)
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
    
    def manage_navidrome_playlist(self):
        """
        Manage the Navidrome playlist with the following workflow:
        1. Ensure the playlist exists (create if needed)
        2. Get current playlist contents to avoid duplicates
        3. Categorize Spotify playlist songs into:
           - Already in Navidrome playlist (skip)
           - In library but not in playlist (add to playlist)
           - Not in library at all (queue for download via slskd)
        4. Add found songs to the Navidrome playlist
        5. Queue missing songs for download
        """
        try:
            # Get the playlist name from the Spotify playlist
            if not hasattr(self, 'current_playlist_name') or not self.current_playlist_name:
                self.logger.warning("No playlist name available for Navidrome playlist creation")
                return False
            
            playlist_name = self.current_playlist_name
            
            # Step 1: Ensure playlist exists
            print(f"📋 Ensuring playlist '{playlist_name}' exists in Navidrome...")
            playlist_id = self.ensure_navidrome_playlist(playlist_name)
            
            if not playlist_id:
                print(f"❌ Failed to create or find playlist '{playlist_name}'")
                return False
            
            # Step 2: Get current playlist contents to avoid duplicates
            print(f"📋 Checking current playlist contents...")
            current_playlist_songs = self.get_navidrome_playlist_songs(playlist_id)
            
            # Step 3: Categorize songs into different actions
            songs_to_add = []           # Songs in library but not in playlist
            songs_to_queue = []         # Songs not in library - queue for download
            songs_already_in_playlist = 0
            
            print(f"📋 Analyzing {len(self.playlist_songs)} songs...")
            
            for song in self.playlist_songs:
                # Clean the title and normalize for comparison
                cleaned_title = self.clean_song_title(song['title'])
                normalized_artist = self.normalize_string(song['artists'][0] if song['artists'] else '')
                normalized_title = self.normalize_string(cleaned_title)
                original_title_normalized = self.normalize_string(song['title'])
                
                # Check with both cleaned and original title for playlist duplicates
                song_key_cleaned = f"{normalized_artist}|{normalized_title}"
                song_key_original = f"{normalized_artist}|{original_title_normalized}"
                
                # Debug logging for duplicate detection
                self.logger.debug(f"Checking song: {song['artists'][0] if song['artists'] else ''} - {song['title']}")
                self.logger.debug(f"  Cleaned key: {song_key_cleaned}")
                self.logger.debug(f"  Original key: {song_key_original}")
                self.logger.debug(f"  In playlist (cleaned): {song_key_cleaned in current_playlist_songs}")
                self.logger.debug(f"  In playlist (original): {song_key_original in current_playlist_songs}")
                
                # Check if song is already in playlist (avoid duplicates)
                if song_key_cleaned in current_playlist_songs or song_key_original in current_playlist_songs:
                    songs_already_in_playlist += 1
                    self.logger.info(f"Song already in playlist, skipping: {song['artists'][0] if song['artists'] else ''} - {song['title']}")
                    continue
                
                # Check if song is missing from library
                song_is_missing = False
                for missing_song in self.missing_songs:
                    missing_artist = self.normalize_string(missing_song['artists'][0] if missing_song['artists'] else '')
                    # Compare with both cleaned and original missing song title
                    missing_title_cleaned = self.normalize_string(self.clean_song_title(missing_song['title']))
                    missing_title_original = self.normalize_string(missing_song['title'])
                    
                    # Check if this matches either the cleaned or original titles
                    if (missing_artist == normalized_artist and 
                        (missing_title_cleaned == normalized_title or 
                         missing_title_original == normalized_title or
                         missing_title_cleaned == original_title_normalized or
                         missing_title_original == original_title_normalized)):
                        song_is_missing = True
                        # Add to queue for download
                        songs_to_queue.append({
                            'artist': song['artists'][0] if song['artists'] else '',
                            'title': song['title'],  # Keep original title for download
                            'album': song['album'],
                            'spotify_url': song.get('spotify_url', '')
                        })
                        break
                
                # If song is in library (not missing) and not already in playlist, add it
                if not song_is_missing:
                    songs_to_add.append({
                        'artist': song['artists'][0] if song['artists'] else '',
                        'title': song['title'],
                        'album': song['album'],
                        '_dedup_key': song_key_cleaned  # Add dedup key for final deduplication
                    })
            
            # Deduplicate songs_to_add by normalized key
            deduped_songs_to_add = {}
            for song in songs_to_add:
                key = song['_dedup_key']
                if key not in deduped_songs_to_add:
                    deduped_songs_to_add[key] = song
            songs_to_add = [
                {k: v for k, v in song.items() if k != '_dedup_key'}
                for song in deduped_songs_to_add.values()
            ]
            
            # Step 4: Add found songs to playlist
            if songs_to_add:
                print(f"📋 Adding {len(songs_to_add)} songs to playlist...")
                success = self.add_songs_to_navidrome_playlist(playlist_id, playlist_name, songs_to_add)
                
                # Update stats
                if hasattr(self, 'stats'):
                    self.stats['songs_added_to_playlist'] = len(songs_to_add)
            else:
                print(f"📋 No new songs to add to playlist")
                if hasattr(self, 'stats'):
                    self.stats['songs_added_to_playlist'] = 0
            
            # Step 5: Queue missing songs for download in slskd
            if songs_to_queue:
                print(f"📥 Queuing {len(songs_to_queue)} missing songs for download...")
                queued_count = self.queue_missing_songs_in_slskd(songs_to_queue)
                if hasattr(self, 'stats'):
                    self.stats['songs_queued_for_download'] = queued_count
            else:
                print(f"📥 No songs need to be queued for download")
                if hasattr(self, 'stats'):
                    self.stats['songs_queued_for_download'] = 0
            
            # Print summary
            print(f"📊 Playlist Management Summary:")
            print(f"   📋 Already in playlist: {songs_already_in_playlist}")
            print(f"   ✅ Added to playlist: {len(songs_to_add)}")
            print(f"   📥 Queued for download: {len(songs_to_queue)}")
            
            return True
                
        except Exception as e:
            self.logger.error(f"Error managing Navidrome playlist: {e}")
            return False
    
    def queue_missing_songs_in_slskd(self, songs_to_queue):
        """Queue missing songs for download in slskd"""
        try:
            if not songs_to_queue:
                return 0
            
            # Check if slskd is configured
            if not self.slskd_url or not self.slskd_api_key:
                self.logger.warning("slskd not configured - skipping song download queue")
                print("⚠️  slskd not configured - songs will not be queued for download")
                return 0
            
            if self.dry_run:
                self.logger.info(f"DRY RUN: Would queue {len(songs_to_queue)} songs in slskd")
                print(f"🔍 DRY RUN: Would queue {len(songs_to_queue)} songs for download:")
                for song in songs_to_queue:
                    print(f"   • {song['artist']} - {song['title']}")
                return len(songs_to_queue)
            
            # Check slskd connection
            if not self.check_slskd_connection():
                self.logger.warning("Cannot connect to slskd - skipping song download queue")
                print("⚠️  Cannot connect to slskd - songs will not be queued")
                return 0
            
            queued_count = 0
            failed_count = 0
            skipped_count = 0
            
            for i, song in enumerate(songs_to_queue, 1):
                try:
                    # Check if song already exists in completed downloads
                    if self.is_song_already_downloaded(song):
                        print(f"⏭️  Skipping song {i}/{len(songs_to_queue)} (already downloaded): {song['artist']} - {song['title']}")
                        self.logger.info(f"Song already downloaded, skipping: {song['artist']} - {song['title']}")
                        skipped_count += 1
                        continue
                    
                    # Check if song is currently downloading
                    if self.is_song_currently_downloading(song):
                        print(f"⏳ Skipping song {i}/{len(songs_to_queue)} (currently downloading): {song['artist']} - {song['title']}")
                        self.logger.info(f"Song currently downloading, skipping: {song['artist']} - {song['title']}")
                        skipped_count += 1
                        continue
                    
                    print(f"📥 Queuing song {i}/{len(songs_to_queue)}: {song['artist']} - {song['title']}")
                    
                    if self.queue_song_in_slskd(song):
                        queued_count += 1
                        self.logger.info(f"Successfully queued: {song['artist']} - {song['title']}")
                    else:
                        failed_count += 1
                        self.logger.warning(f"Failed to queue: {song['artist']} - {song['title']}")
                    
                    # Small delay between requests
                    if i < len(songs_to_queue):
                        time.sleep(2)
                        
                except Exception as e:
                    failed_count += 1
                    self.logger.error(f"Error queuing {song['artist']} - {song['title']}: {e}")
            
            if queued_count > 0:
                print(f"✅ Successfully queued {queued_count} songs for download")
            if skipped_count > 0:
                print(f"⏭️  Skipped {skipped_count} songs (already downloaded or downloading)")
            if failed_count > 0:
                print(f"⚠️  Failed to queue {failed_count} songs")
            
            return queued_count + skipped_count  # Return total "successful" operations
            
        except Exception as e:
            self.logger.error(f"Error queuing songs in slskd: {e}")
            return 0
    
    def check_slskd_connection(self):
        """Check slskd connection and login status"""
        try:
            if not self.slskd_url or not self.slskd_api_key:
                return False
            
            url = f"{self.slskd_url}/api/v0/application"
            headers = {'X-API-Key': self.slskd_api_key}
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return False
            
            app_data = response.json()
            server_info = app_data.get('server', {})
            is_logged_in = server_info.get('isLoggedIn', False)
            
            return is_logged_in
            
        except Exception as e:
            self.logger.error(f"Error checking slskd connection: {e}")
            return False
    
    def queue_song_in_slskd(self, song):
        """Queue a single song for download in slskd using shared utility."""
        try:
            if not self.slskd_url or not self.slskd_api_key:
                self.logger.warning("slskd configuration missing")
                return False
            
            # Get album information if available
            album = song.get('album') or song.get('album_name')
            
            # Use shared utility function with album info
            success = search_and_download_song(
                slskd_url=self.slskd_url,
                slskd_api_key=self.slskd_api_key,
                artist=song['artist'],
                title=song['title'],
                album=album,  # Include album for better matching
                logger=self.logger,
                dry_run=False
            )
            
            if success:
                if album:
                    self.logger.info(f"Successfully queued: {song['artist']} - {song['title']} (from {album})")
                else:
                    self.logger.info(f"Successfully queued: {song['artist']} - {song['title']}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error queuing song {song['artist']} - {song['title']}: {e}")
            return False
    
    def find_best_song_match(self, results, target_song):
        """Find the best matching song from search results with relaxed criteria"""
        try:
            target_artist = self.normalize_string(target_song['artist'])
            
            # Use cleaned title for better matching
            cleaned_title = self.clean_song_title(target_song['title'])
            target_title = self.normalize_string(cleaned_title)
            
            self.logger.info(f"Looking for matches - Artist: '{target_artist}', Title: '{target_title}'")
            if cleaned_title != target_song['title']:
                self.logger.info(f"  (Original title: {target_song['title']})")
            
            # Handle different result formats
            if not results:
                self.logger.info("No results provided to find_best_song_match")
                return None
            
            # Normalize results format
            if isinstance(results, list):
                user_responses = results
            elif isinstance(results, dict):
                user_responses = results.get('responses', results.get('users', [results] if 'username' in results else []))
            else:
                self.logger.warning(f"Unexpected results format: {type(results)}")
                return None
            
            candidates = []
            total_files = 0
            
            for user_response in user_responses:
                if not isinstance(user_response, dict):
                    continue
                    
                username = user_response.get('username', '')
                files = user_response.get('files', [])
                total_files += len(files)
                
                for file_info in files:
                    if not isinstance(file_info, dict):
                        continue
                        
                    filename = file_info.get('filename', '')
                    filesize = file_info.get('size', 0)
                    
                    # Check if it's an audio file
                    if not any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.ogg', '.wav']):
                        continue
                    
                    # Skip very small files (likely not full tracks)
                    if filesize < 1000000:  # 1MB minimum (was 500KB, now more lenient)
                        continue
                    
                    # Normalize filename for comparison
                    normalized_filename = self.normalize_string(filename)
                    
                    # Remove track numbers for better matching (e.g., "08 Mr. Kill Myself" -> "Mr. Kill Myself")
                    cleaned_filename = re.sub(r'^\d+\s*[-.]?\s*', '', normalized_filename)
                    
                    # Simple but effective scoring - any reasonable match gets considered
                    match_score = 0
                    
                    # Artist matching - be very lenient
                    artist_words = [word for word in target_artist.split() if len(word) > 2]
                    for word in artist_words:
                        if word in normalized_filename or word in cleaned_filename:
                            match_score += 10
                    
                    # Title matching - be very lenient
                    title_words = [word for word in target_title.split() if len(word) > 2]
                    for word in title_words:
                        if word in normalized_filename or word in cleaned_filename:
                            match_score += 15
                    
                    # Even if only ONE significant word matches, consider it
                    if match_score == 0:
                        # Last resort: check if ANY meaningful word from title appears
                        all_target_words = [word for word in (target_artist + ' ' + target_title).split() if len(word) > 3]
                        for word in all_target_words:
                            if word in normalized_filename or word in cleaned_filename:
                                match_score += 5
                                break
                    
                    # Quality bonuses
                    if '.flac' in filename.lower():
                        match_score += 3
                    elif '.mp3' in filename.lower():
                        match_score += 2
                    
                    # Size bonus (larger files likely better quality)
                    size_mb = filesize / (1024 * 1024)
                    if size_mb > 3:  # Reasonable song size
                        match_score += min(int(size_mb / 2), 5)
                    
                    # Accept ANY file with even minimal match
                    if match_score > 0:
                        candidates.append((match_score, username, filename, filesize))
                        self.logger.debug(f"  Candidate: {os.path.basename(filename)} (score: {match_score}, user: {username}, size: {size_mb:.1f}MB)")
            
            self.logger.info(f"Found {len(candidates)} potential matches out of {total_files} total files")
            
            if not candidates:
                self.logger.info("No candidates found - trying even more relaxed matching...")
                return self.find_any_audio_file(user_responses)
            
            # Sort by match score (highest first)
            candidates.sort(key=lambda x: x[0], reverse=True)
            
            # Log top candidates
            self.logger.info(f"Top 5 candidates:")
            for i, (score, username, filename, filesize) in enumerate(candidates[:5]):
                size_mb = filesize / (1024 * 1024) if filesize > 0 else 0
                self.logger.info(f"  {i+1}. {os.path.basename(filename)} (score: {score}, {size_mb:.1f}MB, user: {username})")
            
            # Store the file size for use in queue_file_for_download
            self._current_file_size = candidates[0][3]
            
            # Return the best match (username, filename)
            return (candidates[0][1], candidates[0][2])
            
        except Exception as e:
            self.logger.error(f"Error finding best song match: {e}")
            return None
    
    def find_any_audio_file(self, user_responses):
        """Find any reasonable audio file as last resort"""
        try:
            self.logger.info("Trying to find ANY suitable audio file...")
            
            # Handle different result formats  
            if isinstance(user_responses, list):
                responses = user_responses
            elif isinstance(user_responses, dict):
                responses = user_responses.get('responses', user_responses.get('users', [user_responses] if 'username' in user_responses else []))
            else:
                self.logger.warning(f"Unexpected user_responses format: {type(user_responses)}")
                return None
            
            for user_response in responses:
                if not isinstance(user_response, dict):
                    continue
                    
                username = user_response.get('username', '')
                files = user_response.get('files', [])
                
                for file_info in files:
                    if not isinstance(file_info, dict):
                        continue
                        
                    filename = file_info.get('filename', '')
                    filesize = file_info.get('size', 0)
                    
                    # Check if it's an audio file
                    if not any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.ogg', '.wav']):
                        continue
                    
                    # Must be a reasonable size
                    if filesize < 1000000:  # 1MB minimum
                        continue
                    
                    size_mb = filesize / (1024 * 1024)
                    if size_mb > 100:  # Skip huge files (probably not single songs)
                        continue
                    
                    self.logger.info(f"Found audio file: {os.path.basename(filename)} ({size_mb:.1f}MB) from {username}")
                    
                    # Store the file size
                    self._current_file_size = filesize
                    
                    return (username, filename)
            
            self.logger.info("No suitable audio files found")
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding any audio file: {e}")
            return None
    
    def find_loose_song_match(self, results, target_song):
        """Find song match with very loose criteria as fallback"""
        try:
            target_artist = self.normalize_string(target_song['artist'])
            target_title = self.normalize_string(target_song['title'])
            
            self.logger.info(f"Trying loose matching for: {target_artist} - {target_title}")
            
            # Try just looking for any audio file that contains key words
            key_words = []
            
            # Add significant words from artist name
            artist_words = [word for word in target_artist.split() if len(word) > 2]
            key_words.extend(artist_words)
            
            # Add significant words from title
            title_words = [word for word in target_title.split() if len(word) > 2]
            key_words.extend(title_words)
            
            self.logger.info(f"Looking for files containing any of: {key_words}")
            
            best_candidate = None
            best_word_matches = 0
            
            for user_response in results:
                username = user_response.get('username', '')
                files = user_response.get('files', [])
                
                for file_info in files:
                    filename = file_info.get('filename', '')
                    filesize = file_info.get('size', 0)
                    
                    # Check if it's an audio file
                    if not any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.ogg', '.wav']):
                        continue
                    
                    # Skip very small files
                    if filesize < 500000:
                        continue
                    
                    normalized_filename = self.normalize_string(filename)
                    
                    # Count how many key words appear in filename
                    word_matches = sum(1 for word in key_words if word in normalized_filename)
                    
                    if word_matches > best_word_matches:
                        best_word_matches = word_matches
                        best_candidate = (username, filename, filesize)  # Include filesize
                        self.logger.info(f"  Better match: {filename} ({word_matches} words matched)")
            
            if best_candidate and best_word_matches >= 1:
                self.logger.info(f"Found loose match with {best_word_matches} word matches")
                # Store the file size for use in queue_file_for_download
                self._current_file_size = best_candidate[2]
                return (best_candidate[0], best_candidate[1])  # Return username, filename
            
            self.logger.info("No matches found even with loose criteria")
            return None
            
        except Exception as e:
            self.logger.error(f"Error in loose song matching: {e}")
            return None
    
    def queue_file_for_download(self, username, filename, song):
        """Queue a specific file for download"""
        try:
            if not self.slskd_url or not self.slskd_api_key:
                self.logger.warning("slskd configuration missing")
                return False
            
            # Find the actual file size from the search results
            file_size = getattr(self, '_current_file_size', 0)
            
            # Queue the file for download
            enqueue_url = f"{self.slskd_url}/api/v0/transfers/downloads/{username}"
            enqueue_headers = {
                'Content-Type': 'application/json',
                'X-API-Key': self.slskd_api_key
            }
            
            # Create the file data structure with actual size
            file_data = [{
                "filename": filename,
                "size": file_size
            }]
            
            # Log what we're sending
            size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
            self.logger.info(f"Queueing file: {os.path.basename(filename)} ({size_mb:.1f}MB)")
            
            response = requests.post(enqueue_url, headers=enqueue_headers, json=file_data, timeout=30)
            
            if response.status_code in [200, 201]:  # Both 200 and 201 are success codes
                self.logger.info(f"Successfully queued: {os.path.basename(filename)}")
                return True
            else:
                self.logger.warning(f"Failed to queue file: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error queuing file for download: {e}")
            return False
    
    def debug_search_results(self, results, song, attempt_number):
        """Debug function to log search results for troubleshooting"""
        try:
            self.logger.info(f"=== DEBUG: Search Results for {song['artist']} - {song['title']} (Attempt {attempt_number}) ===")
            
            if not results:
                self.logger.info("No results provided to debug function")
                self.logger.info("=== END DEBUG ===")
                return
            
            # Handle different result formats
            if isinstance(results, list):
                user_responses = results
            elif isinstance(results, dict):
                # Handle case where results might be wrapped in a different structure
                user_responses = results.get('responses', results.get('users', [results] if 'username' in results else []))
            else:
                self.logger.info(f"Unexpected results type: {type(results)}")
                self.logger.info("=== END DEBUG ===")
                return
            
            total_users = len(user_responses)
            total_files = 0
            
            # Calculate total files
            for user_response in user_responses:
                if isinstance(user_response, dict):
                    files = user_response.get('files', [])
                    total_files += len(files)
            
            self.logger.info(f"Found {total_users} users with {total_files} total files")
            
            # Show details for first few users
            for i, user_response in enumerate(user_responses[:5]):  # Show first 5 users
                if not isinstance(user_response, dict):
                    continue
                    
                username = user_response.get('username', f'User{i}')
                files = user_response.get('files', [])
                
                self.logger.info(f"User {i+1}: {username} ({len(files)} files)")
                
                # Show first few files from each user
                for j, file_info in enumerate(files[:3]):
                    if not isinstance(file_info, dict):
                        continue
                        
                    filename = file_info.get('filename', f'file{j}')
                    filesize = file_info.get('size', 0)
                    size_mb = filesize / (1024 * 1024) if filesize > 0 else 0
                    
                    # Check if it's an audio file
                    is_audio = any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.ogg', '.wav'])
                    
                    self.logger.info(f"  File {j+1}: {os.path.basename(filename)} ({size_mb:.1f}MB) {'[AUDIO]' if is_audio else '[OTHER]'}")
                
                if len(files) > 3:
                    self.logger.info(f"  ... and {len(files) - 3} more files")
            
            if total_users > 5:
                self.logger.info(f"... and {total_users - 5} more users")
            
            self.logger.info("=== END DEBUG ===")
            
        except Exception as e:
            self.logger.error(f"Error in debug_search_results: {e}")
            self.logger.info("=== END DEBUG ===")
    
    def monitor_missing_songs(self):
        """Add artists from missing songs to Lidarr for monitoring future releases"""
        try:
            print(f"👥 Processing artists from {len(self.missing_songs)} missing songs...")
            self.logger.info(f"Processing artists from {len(self.missing_songs)} missing songs")
            
            # Log some examples of missing songs for debugging
            if self.missing_songs:
                print(f"📝 Example missing songs:")
                for song in self.missing_songs[:5]:  # Show first 5
                    print(f"   • {song['artists'][0]} - {song['title']}")
                if len(self.missing_songs) > 5:
                    print(f"   ... and {len(self.missing_songs) - 5} more")
            
            all_artists = set()
            artists_monitored = set()
            artists_already_exist = set()
            
            # Collect all unique artists from missing songs
            for song in self.missing_songs:
                for artist in song['artists']:
                    all_artists.add(artist)
            
            print(f"🎭 Adding {len(all_artists)} unique artists to Lidarr for future release monitoring...")
            self.logger.info(f"Adding {len(all_artists)} unique artists to Lidarr for future release monitoring")
            
            # Log which artists we're about to add
            if all_artists:
                print(f"📝 Artists to monitor:")
                for i, artist in enumerate(sorted(list(all_artists))[:10], 1):  # Show first 10
                    print(f"   {i}. {artist}")
                if len(all_artists) > 10:
                    print(f"   ... and {len(all_artists) - 10} more")
            
            for artist in all_artists:
                # Check if artist already exists before trying to add
                if self.lidarr_client and self.lidarr_client.artist_exists(artist):
                    artists_already_exist.add(artist)
                    if self.dry_run:
                        print(f"    ⏭️  Artist already monitored (would skip): {artist}")
                    else:
                        self.logger.debug(f"Artist already monitored: {artist}")
                    continue
                
                artist_success = self.add_artist_to_lidarr(artist)
                if artist_success:
                    artists_monitored.add(artist)
                else:
                    self.failed_artists.append(artist)
                    self.stats['artists_failed'] += 1
            
            if artists_already_exist:
                print(f"    ⏭️  {len(artists_already_exist)} artists already monitored (skipped)")
            print(f"    ✓ Successfully added {len(artists_monitored)} new artists for future release monitoring")
            if self.failed_artists:
                print(f"    ❌ Failed to add {len(self.failed_artists)} artists")
            
            self.stats['artists_added'] = len(artists_monitored)
            
            return True
            
        except Exception as e:
            msg = f"Error monitoring missing songs: {e}"
            print(f"    ❌ {msg}")
            self.logger.error(msg)
            self.stats['errors'] += 1
            return False
    
    def run(self, playlist_url):
        """Main function to process playlist"""
        print(f"🎵 Spotify Playlist Monitor")
        print("=" * 60)
        
        # Log start
        log_action("script_start", "Spotify Playlist Monitor started", {
            "playlist_url": playlist_url,
            "dry_run": self.dry_run
        })
        
        self.logger.info(f"Starting Spotify playlist monitoring - URL: {playlist_url}, Dry run: {self.dry_run}")
        
        if self.dry_run:
            print("🔍 Running in DRY RUN mode - no changes will be made to Lidarr")
            self.logger.info("Running in DRY RUN mode - no changes will be made to Lidarr")
        
        # Step 1: Authenticate with Spotify
        print()
        print("🔐 Step 1: Authenticating with Spotify...")
        if not self.authenticate_spotify():
            print("❌ Failed to authenticate with Spotify - aborting")
            return False
        
        # Step 2: Authenticate with Navidrome
        print()
        print("🔐 Step 2: Authenticating with Navidrome...")
        if not self.authenticate_navidrome():
            print("❌ Failed to authenticate with Navidrome - aborting")
            return False
        
        # Step 3: Get playlist songs
        print()
        print("📋 Step 3: Fetching playlist songs...")
        if not self.get_playlist_songs(playlist_url):
            print("❌ Failed to get playlist songs - aborting")
            return False
        
        # Step 4: Get Navidrome library
        print()
        print("📚 Step 4: Loading Navidrome library...")
        if not self.get_navidrome_library():
            print("❌ Failed to load Navidrome library - aborting")
            return False
        
        # Step 5: Find missing songs
        print()
        print("🔍 Step 5: Finding missing songs...")
        if not self.find_missing_songs():
            print("❌ Failed to find missing songs - aborting")
            return False
        
        # Step 6: Manage Navidrome playlist - add found songs and queue missing ones
        print()
        print("📋 Step 6: Managing Navidrome playlist...")
        if not self.manage_navidrome_playlist():
            print("⚠️  Failed to manage Navidrome playlist (continuing with artist monitoring)")
        
        # Step 7: Add missing artists to Lidarr for monitoring
        print()
        print("👥 Step 7: Adding missing artists to Lidarr for monitoring...")
        if not self.monitor_missing_songs():
            print("❌ Failed to add missing artists for monitoring")
            return False
        
        # Print summary
        print()
        print("=" * 60)
        print("📊 Processing Summary:")
        print(f"   🎵 Playlist songs: {self.stats['playlist_songs']}")
        print(f"   ✅ Songs in library: {self.stats['songs_in_library']}")
        print(f"   ❌ Songs missing: {self.stats['songs_missing']}")
        print(f"   📋 Songs added to playlist: {self.stats['songs_added_to_playlist']}")
        print(f"   📥 Songs queued for download: {self.stats['songs_queued_for_download']}")
        print(f"   👥 Artists added for monitoring: {self.stats['artists_added']}")
        print(f"   ❌ Artists failed to add: {self.stats['artists_failed']}")
        print(f"   ⚠️  Errors: {self.stats['errors']}")
        
        # Show failed artists if any
        if self.failed_artists:
            print()
            print("❌ Failed to add these artists to Lidarr:")
            for artist in sorted(self.failed_artists):
                print(f"   • {artist}")
        
        # Log summary  
        self.logger.info(f"Processing complete - Playlist songs: {self.stats['playlist_songs']}, "
                        f"Missing: {self.stats['songs_missing']}, "
                        f"Artists added: {self.stats['artists_added']}, "
                        f"Errors: {self.stats['errors']}")
        
        if self.stats['errors'] == 0:
            print()
            print("✅ Spotify playlist monitoring complete!")
            self.logger.info("Spotify playlist monitoring completed successfully")
            
            log_action("script_complete", "Spotify Playlist Monitor completed successfully", {
                "stats": self.stats,
                "log_file": self.log_file_path
            })
        else:
            print()
            print("⚠️  Spotify playlist monitoring completed with errors!")
            self.logger.warning("Spotify playlist monitoring completed with errors")
            
            log_action("script_complete", "Spotify Playlist Monitor completed with errors", {
                "stats": self.stats,
                "errors": self.stats['errors'],
                "log_file": self.log_file_path
            })
        
        return self.stats['errors'] == 0

def main():
    parser = argparse.ArgumentParser(description='Monitor Spotify playlists and add missing songs to Lidarr')
    parser.add_argument('--playlist-url', help='Spotify playlist URL (if not provided, will process all active playlists)')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no changes to Lidarr)')
    parser.add_argument('--live', action='store_true', default=True, help='Run in LIVE mode (will make actual changes to Lidarr) - DEFAULT')
    
    args = parser.parse_args()
    
    # Use dry-run if explicitly requested, otherwise default to live mode
    dry_run_mode = args.dry_run
    
    print(f"🎵 Starting Spotify Playlist Monitor")
    
    if dry_run_mode:
        print("🔍 RUNNING IN DRY-RUN MODE - No changes will be made to Lidarr")
        print("💡 Remove --dry-run flag to make actual changes")
    else:
        print("⚠️  RUNNING IN LIVE MODE - Changes will be made to Lidarr!")
        print("💡 Use --dry-run flag to test without making changes")
    
    print(f"🔄 Dry Run: {dry_run_mode}")
    print()
    
    if args.playlist_url:
        # Process single playlist
        print(f"📋 Single Playlist Mode")
        print(f"📋 Playlist URL: {args.playlist_url}")
        playlists_to_process = [{'url': args.playlist_url, 'name': 'Manual'}]
    else:
        # Process all active playlists from settings
        print(f"� Batch Processing Mode - checking saved playlists")
        playlists_file = Path(__file__).parent.parent / 'work' / 'spotify_playlists.json'
        
        if not playlists_file.exists():
            print("❌ No saved playlists found. Add playlists through the settings page first.")
            sys.exit(1)
            
        with open(playlists_file, 'r') as f:
            all_playlists = json.load(f)
        
        # Filter to only active playlists
        playlists_to_process = [p for p in all_playlists if p.get('active', True)]
        
        if not playlists_to_process:
            print("ℹ️  No active playlists found. Enable playlists in settings to process them.")
            sys.exit(0)
            
        print(f"📋 Found {len(playlists_to_process)} active playlists to process")
    
    print(f"�🔄 Dry Run: {args.dry_run}")
    print()
    
    monitor = None
    overall_success = True
    total_stats = {
        'playlists_processed': 0,
        'total_songs': 0,
        'total_missing': 0,
        'total_artists_added': 0,
        'errors': 0
    }
    
    try:
        print("⚙️  Initializing monitor...")
        monitor = SpotifyPlaylistMonitor(dry_run=dry_run_mode)
        
        # Process each playlist
        for i, playlist in enumerate(playlists_to_process, 1):
            playlist_url = playlist['url']
            playlist_name = playlist.get('name', f'Playlist {i}')
            
            print(f"\n🎵 Processing playlist {i}/{len(playlists_to_process)}: {playlist_name}")
            print(f"📋 URL: {playlist_url}")
            
            try:
                success = monitor.run(playlist_url)
                
                if success:
                    print(f"✅ Successfully processed: {playlist_name}")
                else:
                    print(f"⚠️  Completed with errors: {playlist_name}")
                    overall_success = False
                
                # Accumulate stats
                total_stats['playlists_processed'] += 1
                total_stats['total_songs'] += monitor.stats.get('playlist_songs', 0)
                total_stats['total_missing'] += monitor.stats.get('songs_missing', 0)
                total_stats['total_artists_added'] += monitor.stats.get('artists_added', 0)
                total_stats['errors'] += monitor.stats.get('errors', 0)
                
                # Reset monitor stats for next playlist
                monitor.stats = {
                    'playlist_songs': 0,
                    'songs_in_library': 0,
                    'songs_missing': 0,
                    'songs_added_to_playlist': 0,
                    'songs_queued_for_download': 0,
                    'artists_added': 0,
                    'artists_failed': 0,
                    'songs_added': 0,
                    'errors': 0
                }
                monitor.playlist_songs = []
                monitor.library_songs = set()
                monitor.missing_songs = []
                
            except Exception as e:
                print(f"❌ Failed to process playlist {playlist_name}: {e}")
                monitor.logger.error(f"Failed to process playlist {playlist_name}: {e}")
                overall_success = False
                total_stats['errors'] += 1
        
        # Print final summary
        print(f"\n{'='*60}")
        print(f"🎵 SPOTIFY PLAYLIST MONITORING COMPLETE")
        print(f"{'='*60}")
        print(f"📊 Playlists processed: {total_stats['playlists_processed']}")
        print(f"🎵 Total songs found: {total_stats['total_songs']}")
        print(f"❓ Total songs missing: {total_stats['total_missing']}")
        print(f"👥 Total artists added: {total_stats['total_artists_added']}")
        print(f"❌ Errors: {total_stats['errors']}")
        
        if overall_success:
            print("✅ All playlists processed successfully!")
        else:
            print("⚠️  Some playlists had errors!")
            
        sys.exit(0 if overall_success else 1)
        
    except Exception as e:
        error_msg = f"❌ Fatal Error: {e}"
        print(error_msg)
        
        # Try to log to file if monitor was initialized
        if monitor and hasattr(monitor, 'logger') and monitor.logger:
            monitor.logger.error(error_msg)
            print(f"📋 Check log file: {monitor.log_file_path}")
        
        # Also try to log the action
        try:
            from action_logger import log_action
            log_action("script_error", "Spotify Playlist Monitor failed", {
                "error": str(e),
                "total_stats": total_stats
            })
        except:
            pass  # If action logging fails, don't crash
            
        sys.exit(1)

if __name__ == "__main__":
    main()