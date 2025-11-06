#!/usr/bin/env python3
"""
Process Downloads Script

This script processes downloaded music files by:
1. Scanning the downloads directory for music files
2. Grouping files by album based on metadata
3. Checking album completeness against Lidarr
4. Moving complete albums to the main music library
5. Moving incomplete albums to the incomplete directory

Name: Process Downloads
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: downloads, organization, lidarr
Supports dry run: true
"""

import os
import sys
import json
import argparse
import logging
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Try to import optional dependencies
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None

# Add parent directory to path to import settings
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from settings import get_lidarr_config
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

# Set up basic logging immediately to catch early errors
try:
    # Create a basic logger first
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting process_downloads.py script...")
    
    # Try to create log directory and file with fallback for Windows
    try:
        # Try Docker path first (correct mount path)
        log_dir = Path('/logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f'process_downloads_{timestamp}.log'
        logger.info(f"Attempting to create log file at: {log_file}")
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not use Docker path /logs: {e}")
        # Fallback to Windows-compatible path
        log_dir = Path(__file__).parent.parent / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = log_dir / f'process_downloads_{timestamp}.log'
        logger.info(f"Using fallback log path: {log_file}")
        
    try:
        # Add file handler
        file_handler = logging.FileHandler(str(log_file))
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
        logger.info(f"Successfully set up file logging: {log_file}")
    except Exception as e:
        logger.warning(f"Could not set up file logging: {e}")
        logger.info("Continuing with console logging only")
        
except Exception as e:
    print(f"CRITICAL ERROR during basic logging setup: {e}")
    sys.exit(1)

# Import optional dependencies with error handling
try:
    logger.info("Importing requests...")
    import requests
    logger.info("Successfully imported requests")
except ImportError as e:
    logger.error("'requests' package not found. Install with: pip install requests")
    logger.error(f"Import error: {e}")
    sys.exit(1)

try:
    logger.info("Importing mutagen...")
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3NoHeaderError
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TDRC, TRCK
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    logger.info("Successfully imported mutagen")
except ImportError as e:
    logger.error("'mutagen' package not found. Install with: pip install mutagen")
    logger.error(f"Import error: {e}")
    sys.exit(1)

try:
    logger.info("Importing musicbrainzngs...")
    import musicbrainzngs
    logger.info("Successfully imported musicbrainzngs")
except ImportError as e:
    logger.error("'musicbrainzngs' package not found. Install with: pip install musicbrainzngs")
    logger.error(f"Import error: {e}")
    sys.exit(1)

try:
    logger.info("Importing pyacoustid for audio fingerprinting...")
    import acoustid
    logger.info("Successfully imported pyacoustid")
except ImportError as e:
    logger.error("'pyacoustid' package not found. Install with: pip install pyacoustid")
    logger.error(f"Import error: {e}")
    sys.exit(1)

logger.info("All imports successful, proceeding with script initialization...")

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class DownloadsProcessor:
    def __init__(self, dry_run=False, skip_metadata_fix=False):
        logger.info("Initializing DownloadsProcessor...")
        
        # Check for environment variable dry run override
        env_dry_run = os.environ.get('DRY_RUN', '').lower() == 'true'
        self.dry_run = dry_run or env_dry_run
        
        # Check for environment variable metadata fix override
        env_skip_metadata = os.environ.get('SKIP_METADATA_FIX', '').lower() == 'true'
        self.skip_metadata_fix = skip_metadata_fix or env_skip_metadata
        
        logger.info(f"Dry run mode: {self.dry_run}")
        logger.info(f"Skip metadata fix: {self.skip_metadata_fix}")
        
        # Check for required dependencies
        if not REQUESTS_AVAILABLE:
            logger.error("Missing required dependency: requests")
            logger.error("Install with: pip install requests")
            raise SystemExit(1)
        
        self.logger = logger  # Use the global logger
        self.setup_paths()
        self.setup_lidarr_config()
        if not self.skip_metadata_fix:
            self.setup_musicbrainz()
        self.setup_file_permissions()
        self.action_history = []
        
        # Initialize caches for API data
        self._artists_cache = None
        self._albums_cache = {}  # Will cache by artist_id
        
        logger.info("DownloadsProcessor initialization complete")
        
        
    def setup_paths(self):
        """Setup directory paths"""
        logger.info("Setting up directory paths...")
        # Use Docker container paths that match the mounted directories
        self.downloads_dir = Path('/downloads/completed')
        self.music_dir = Path('/media/Owned')  # Main music library for complete albums
        self.incomplete_dir = Path('/media/Incomplete')  # Incomplete albums
        self.not_owned_dir = Path('/media/Not_Owned')  # Alternative directory if needed
        self.work_dir = Path('/data/work')  # Application data mount
        
        # Ensure work directory exists
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Work directory ready: {self.work_dir}")
        except Exception as e:
            logger.error(f"Failed to create work directory {self.work_dir}: {e}")
            raise
            
    def setup_lidarr_config(self):
        """Setup Lidarr API configuration"""
        logger.info("Setting up Lidarr configuration...")
        
        if SETTINGS_AVAILABLE:
            # Get configuration from settings module
            lidarr_config = get_lidarr_config()
            self.lidarr_url = lidarr_config.get('url') or 'http://lidarr:8686'
            self.lidarr_api_key = lidarr_config.get('api_key')
            logger.info("Using Lidarr configuration from settings")
        else:
            # Fall back to environment variables
            self.lidarr_url = os.getenv('LIDARR_URL', 'http://lidarr:8686')
            self.lidarr_api_key = os.getenv('LIDARR_API_KEY')
            logger.info("Using Lidarr configuration from environment variables")
        
        logger.info(f"Lidarr URL: {self.lidarr_url}")
        
        if not self.lidarr_api_key:
            if SETTINGS_AVAILABLE:
                logger.error("Lidarr API key not configured in settings or LIDARR_API_KEY environment variable")
                logger.error("Please configure Lidarr in the web interface Settings page")
            else:
                logger.error("LIDARR_API_KEY environment variable not set")
                logger.error("Please set LIDARR_API_KEY environment variable to use this script")
            raise SystemExit(1)
            
        self.lidarr_headers = {'X-Api-Key': self.lidarr_api_key}
        logger.info("Lidarr configuration complete")
        
    def setup_musicbrainz(self):
        """Setup MusicBrainz API configuration"""
        logger.info("Setting up MusicBrainz configuration...")
        
        # Set user agent for MusicBrainz API requests (can use env vars for flexibility)
        app_name = os.getenv('MUSICBRAINZ_APP_NAME', 'navidrome-cleanup')
        app_version = os.getenv('MUSICBRAINZ_APP_VERSION', '1.0')
        contact_email = os.getenv('MUSICBRAINZ_CONTACT_EMAIL', 'admin@localhost')
        
        musicbrainzngs.set_useragent(app_name, app_version, contact_email)
        logger.info(f"MusicBrainz user agent set: {app_name}/{app_version}")
        
        # Set rate limiting to be respectful to MusicBrainz servers
        musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)
        logger.info("MusicBrainz rate limiting enabled")
        
        # Setup AcoustID for audio fingerprinting
        self.acoustid_api_key = os.getenv('ACOUSTID_API_KEY', 'cSpUJKpD')  # Default demo key
        if self.acoustid_api_key == 'cSpUJKpD':
            logger.warning("Using default AcoustID API key - consider getting your own from https://acoustid.org/")
        else:
            logger.info("Using custom AcoustID API key")
        
        logger.info("MusicBrainz configuration complete")
        
    def setup_file_permissions(self):
        """Setup file permission configuration for Docker environment"""
        logger.info("Setting up file permissions...")
        self.target_uid = int(os.environ.get('TARGET_UID', '1000'))
        self.target_gid = int(os.environ.get('TARGET_GID', '1000'))
        logger.info(f"Target UID/GID: {self.target_uid}/{self.target_gid}")
        
    def get_all_artists(self):
        """Get all artists from Lidarr with caching"""
        if self._artists_cache is not None:
            self.logger.debug(f"Using cached artists list ({len(self._artists_cache)} artists)")
            return self._artists_cache
            
        try:
            self.logger.info("Fetching artists list from Lidarr...")
            url = f"{self.lidarr_url}/api/v1/artist"
            response = requests.get(url, headers=self.lidarr_headers, timeout=30)
            response.raise_for_status()
            
            self._artists_cache = response.json()
            self.logger.info(f"Cached {len(self._artists_cache)} artists from Lidarr")
            return self._artists_cache
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching artists from Lidarr: {e}")
            return []
            
    def get_artist_albums(self, artist_id):
        """Get albums for an artist with caching"""
        if artist_id in self._albums_cache:
            self.logger.debug(f"Using cached albums for artist {artist_id}")
            return self._albums_cache[artist_id]
            
        try:
            url = f"{self.lidarr_url}/api/v1/album"
            params = {'artistId': artist_id}
            response = requests.get(url, headers=self.lidarr_headers, params=params, timeout=30)
            response.raise_for_status()
            
            albums = response.json()
            self._albums_cache[artist_id] = albums
            self.logger.debug(f"Cached {len(albums)} albums for artist {artist_id}")
            return albums
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching albums for artist {artist_id}: {e}")
            return []
        
    def get_music_files(self, directory):
        """Get all music files from directory recursively"""
        music_extensions = {'.mp3', '.flac', '.m4a', '.ogg', '.wav', '.wma'}
        music_files = []
        
        try:
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if Path(file).suffix.lower() in music_extensions:
                        music_files.append(Path(root) / file)
        except Exception as e:
            self.logger.error(f"Error scanning directory {directory}: {e}")
            
        return music_files
        
    def extract_metadata(self, file_path):
        """Extract metadata from music file"""
        try:
            audio_file = MutagenFile(file_path)
            if audio_file is None:
                return None
                
            metadata = {
                'artist': '',
                'album': '',
                'title': '',
                'track': '',
                'date': ''
            }
            
            # Handle different tag formats
            if hasattr(audio_file, 'tags') and audio_file.tags:
                tags = audio_file.tags
                
                # ID3 tags (MP3)
                if hasattr(tags, 'getall'):
                    metadata['artist'] = self.get_tag_value(tags, ['TPE1', 'TPE2', 'ARTIST'])
                    metadata['album'] = self.get_tag_value(tags, ['TALB', 'ALBUM'])
                    metadata['title'] = self.get_tag_value(tags, ['TIT2', 'TITLE'])
                    metadata['track'] = self.get_tag_value(tags, ['TRCK', 'TRACKNUMBER'])
                    metadata['date'] = self.get_tag_value(tags, ['TDRC', 'DATE', 'YEAR'])
                # Vorbis comments (FLAC, OGG)
                else:
                    for key in tags:
                        try:
                            # Handle different key formats
                            if isinstance(key, tuple) and len(key) >= 2:
                                # Special case: key is a tuple like ('TITLE', 'value')
                                key_str = str(key[0])  # Use first element as tag name
                                value_str = str(key[1]) # Use second element as value
                            elif isinstance(key, tuple) and len(key) == 1:
                                # Tuple with single element
                                key_str = str(key[0])
                                value = tags[key]
                                if isinstance(value, (list, tuple)) and value:
                                    value_str = str(value[0])
                                else:
                                    value_str = str(value) if value else ''
                            elif isinstance(key, list):
                                # List key (rare case)
                                key_str = str(key[0]) if key else ''
                                value = tags[key]
                                if isinstance(value, (list, tuple)) and value:
                                    value_str = str(value[0])
                                else:
                                    value_str = str(value) if value else ''
                            else:
                                # Normal string key
                                key_str = str(key)
                                value = tags[key]
                                if isinstance(value, (list, tuple)) and value:
                                    value_str = str(value[0])
                                else:
                                    value_str = str(value) if value else ''
                                
                            key_upper = key_str.upper()
                            
                            if key_upper in ['ARTIST', 'ALBUMARTIST']:
                                metadata['artist'] = value_str
                            elif key_upper == 'ALBUM':
                                metadata['album'] = value_str
                            elif key_upper == 'TITLE':
                                metadata['title'] = value_str
                            elif key_upper in ['TRACKNUMBER', 'TRACK']:
                                metadata['track'] = value_str
                            elif key_upper in ['DATE', 'YEAR']:
                                metadata['date'] = value_str
                                
                        except Exception as e:
                            # Log the specific error but continue processing
                            self.logger.debug(f"Error processing tag {key} for file {file_path}: {e}")
                            continue
                            
            return metadata
            
        except (ID3NoHeaderError, Exception) as e:
            self.logger.warning(f"Could not extract metadata from {file_path}: {e}")
            return None
            
    def get_tag_value(self, tags, tag_names):
        """Get tag value from list of possible tag names"""
        for tag_name in tag_names:
            try:
                if hasattr(tags, 'getall'):
                    values = tags.getall(tag_name)
                    if values:
                        # Handle different value types
                        value = values[0]
                        if isinstance(value, (list, tuple)) and value:
                            return str(value[0])
                        else:
                            return str(value)
                elif tag_name in tags:
                    value = tags[tag_name]
                    if isinstance(value, (list, tuple)) and value:
                        return str(value[0])
                    else:
                        return str(value) if value else ''
            except Exception as e:
                self.logger.debug(f"Error getting tag {tag_name}: {e}")
                continue
        return ''
        
    def fix_metadata_with_musicbrainz(self, file_path):
        """Fix metadata using audio fingerprinting and MusicBrainz lookup"""
        try:
            self.logger.info(f"Attempting to fix metadata for: {file_path}")
            
            # First extract current metadata for comparison
            current_metadata = self.extract_metadata(file_path)
            if not current_metadata:
                self.logger.warning(f"Could not extract current metadata from {file_path}")
                current_metadata = {}
                
            # Strategy 1: Use audio fingerprinting (most accurate)
            self.logger.debug("Trying audio fingerprinting identification...")
            fingerprint_metadata = self.identify_by_audio_fingerprint(file_path)
            if not fingerprint_metadata:
                self.logger.info(f"Skipping track due to failed audio fingerprinting: {file_path}")
                return False
            self.logger.info(f"Successfully identified track via audio fingerprinting: {fingerprint_metadata.get('artist')} - {fingerprint_metadata.get('title')}")
            return self.apply_metadata_to_file(file_path, fingerprint_metadata)
                
        except Exception as e:
            self.logger.error(f"Error fixing metadata for {file_path}: {e}")
            return False
            
    def identify_by_audio_fingerprint(self, file_path):
        """Identify track using audio fingerprinting via AcoustID"""
        try:
            self.logger.debug(f"Generating audio fingerprint for: {file_path}")
            
            # Generate fingerprint from audio file
            try:
                duration, fingerprint = acoustid.fingerprint_file(str(file_path))
                self.logger.debug(f"Generated fingerprint - Duration: {duration}s")
            except Exception as e:
                self.logger.warning(f"Could not generate fingerprint for {file_path}: {e}")
                return None
                
            # Look up fingerprint in AcoustID database
            self.logger.debug("Looking up fingerprint in AcoustID...")
            try:
                # Try different metadata request approach
                results = acoustid.lookup(
                    self.acoustid_api_key, 
                    fingerprint, 
                    duration, 
                    meta=['recordings', 'releasegroups', 'releases', 'tracks', 'artists']
                )
                
                # Debug: Print raw AcoustID response (minimal)
                self.logger.debug(f"AcoustID response status: {results.get('status', 'unknown')}")
                if 'error' in results:
                    self.logger.warning(f"AcoustID error: {results.get('error')}")
                # Only log full response for specific files (like 2Pac for debugging)
                if '2pac' in str(file_path).lower() or 'california love' in str(file_path).lower():
                    self.logger.debug(f"AcoustID raw response: {results}")
                else:
                    results_count = len(results.get('results', []))
                    self.logger.debug(f"AcoustID returned {results_count} results")
                
            except Exception as e:
                self.logger.warning(f"AcoustID lookup failed for {file_path}: {e}")
                return None
                
            if not results or 'results' not in results:
                self.logger.debug("No AcoustID results found")
                return None
                
            # Check if we got any results with recordings
            results_with_recordings = []
            for result in results.get('results', []):
                if result.get('recordings'):
                    results_with_recordings.append(result)
                    
            # If no results have recordings, try to get them using MusicBrainz lookup
            if not results_with_recordings:
                self.logger.debug("No recordings in AcoustID results, attempting MusicBrainz lookup for IDs...")
                results_with_recordings = self.enrich_acoustid_results_with_musicbrainz(results.get('results', []))
                
            if not results_with_recordings:
                self.logger.debug("No recordings found even after MusicBrainz enrichment")
                return None
                
            # Debug: Print results structure
            self.logger.debug(f"Processing {len(results_with_recordings)} enriched results")
            for i, result in enumerate(results_with_recordings[:3]):  # Show first 3 results
                score = result.get('score', 0)
                recordings = result.get('recordings', [])
                self.logger.debug(f"  Result {i+1}: score={score}, recordings={len(recordings)}")
                
            # Process results to find best match
            best_match = self.process_acoustid_results(results_with_recordings)
            
            if best_match:
                self.logger.info(f"Found acoustic match: {best_match}")
                return best_match
            else:
                self.logger.debug("No suitable matches found in AcoustID results")
                return None
                
        except Exception as e:
            self.logger.error(f"Error in audio fingerprint identification: {e}")
            return None
            
    def process_acoustid_results(self, results):
        """Process AcoustID results to extract best metadata match"""
        try:
            self.logger.debug(f"Processing {len(results)} AcoustID results...")
            
            best_metadata = None
            best_score = 0
            
            for i, result in enumerate(results):
                score = result.get('score', 0)
                
                # Only consider decent matches (lowered for debugging)
                if score < 0.5:
                    continue
                    
                # Extract recordings from result
                recordings = result.get('recordings', [])
                
                for j, recording in enumerate(recordings):
                    try:
                        metadata = self.extract_metadata_from_acoustid_recording(recording)
                        if metadata and score > best_score:
                            best_score = score
                            best_metadata = metadata
                            self.logger.debug(f"New best match (score {score:.3f}): {metadata.get('artist')} - {metadata.get('album')} - {metadata.get('title')}")
                            
                    except Exception as e:
                        self.logger.debug(f"Error processing recording {j+1}: {e}")
                        continue
                        
            if best_metadata:
                self.logger.info(f"Selected acoustic match (score {best_score:.3f}): {best_metadata.get('artist')} - {best_metadata.get('album')} - {best_metadata.get('title')}")
            else:
                self.logger.debug("No valid metadata found in any AcoustID results")
                
            return best_metadata
            
        except Exception as e:
            self.logger.error(f"Error processing AcoustID results: {e}")
            return None
            
    def extract_metadata_from_acoustid_recording(self, recording):
        """Extract metadata from AcoustID recording result"""
        try:
            # Only verbose logging for test cases
            is_debug_case = '2pac' in recording.get('title', '').lower() or any(
                '2pac' in artist.get('name', '').lower() for artist in recording.get('artists', [])
            )
            
            if is_debug_case:
                self.logger.debug(f"Extracting metadata from recording: {recording.get('title', 'Unknown')}")
            
            metadata = {}
            
            # Extract basic recording info
            metadata['title'] = recording.get('title', '')
            if is_debug_case:
                self.logger.debug(f"  Title: '{metadata['title']}'")
            
            # Extract artist information
            artists = recording.get('artists', [])
            if artists:
                # Use first artist or join multiple artists
                if len(artists) == 1:
                    metadata['artist'] = artists[0].get('name', '')
                else:
                    # Multiple artists - join them
                    artist_names = [artist.get('name', '') for artist in artists if artist.get('name')]
                    metadata['artist'] = ', '.join(artist_names)
                if is_debug_case:
                    self.logger.debug(f"  Artist: '{metadata['artist']}'")
            
            # Extract release (album) information - prioritize original albums over compilations
            releasegroups = recording.get('releasegroups', [])
            if is_debug_case:
                self.logger.debug(f"  Found {len(releasegroups)} release groups")
            if releasegroups:
                # Strategy: Find the best release group prioritizing original albums
                best_release = None
                original_albums = []
                non_compilation_albums = []
                compilation_albums = []
                
                for i, release in enumerate(releasegroups):
                    release_type = release.get('type', '')
                    secondarytypes = release.get('secondarytypes', [])
                    release_title = release.get('title', '')
                    is_compilation = 'Compilation' in secondarytypes
                    
                    if is_debug_case:
                        self.logger.debug(f"    Release {i+1}: '{release_title}' (type: {release_type}, compilation: {is_compilation})")
                    
                    # Categorize releases by priority
                    if release_type.lower() == 'album' and not is_compilation:
                        original_albums.append(release)
                    elif release_type.lower() in ['album', ''] and not is_compilation:
                        non_compilation_albums.append(release)
                    elif is_compilation:
                        compilation_albums.append(release)
                
                # Select best release in order of preference
                if original_albums:
                    best_release = original_albums[0]  # Prefer original albums
                    if is_debug_case:
                        self.logger.debug(f"    Selected original album: '{best_release.get('title', '')}'")
                elif non_compilation_albums:
                    best_release = non_compilation_albums[0]  # Then non-compilation albums
                    if is_debug_case:
                        self.logger.debug(f"    Selected non-compilation album: '{best_release.get('title', '')}'")
                elif compilation_albums:
                    # Try to find original release via MusicBrainz recording search before accepting compilations
                    if is_debug_case:
                        self.logger.debug(f"    All AcoustID results are compilations, searching for original recording...")
                    
                    # Get the first recording ID to search for original releases
                    recording_id = recording.get('id')
                    if recording_id:
                        original_release = self._search_original_recording_release(recording_id, is_debug_case)
                        if original_release:
                            best_release = original_release
                            if is_debug_case:
                                self.logger.debug(f"    Found original recording release: '{best_release.get('title', '')}'")
                        else:
                            # Only use compilations as last resort, but prefer earlier/official ones
                            # Sort by release date if available
                            sorted_compilations = sorted(compilation_albums, 
                                                       key=lambda x: x.get('releases', [{}])[0].get('date', {}).get('year', 9999))
                            best_release = sorted_compilations[0]
                            if is_debug_case:
                                self.logger.debug(f"    No original found, using compilation as fallback: '{best_release.get('title', '')}'")
                    else:
                        # No recording ID available, fall back to compilation
                        sorted_compilations = sorted(compilation_albums, 
                                                   key=lambda x: x.get('releases', [{}])[0].get('date', {}).get('year', 9999))
                        best_release = sorted_compilations[0]
                        if is_debug_case:
                            self.logger.debug(f"    No recording ID available, using compilation as fallback: '{best_release.get('title', '')}'")
                elif releasegroups:
                    best_release = releasegroups[0]  # Absolute fallback
                    if is_debug_case:
                        self.logger.debug(f"    Using first release as absolute fallback: '{best_release.get('title', '')}'")
                    
                if best_release:
                    metadata['album'] = best_release.get('title', '')
                    if is_debug_case:
                        self.logger.debug(f"  Album: '{metadata['album']}'")
                    
                    # Try to get release date from the best release
                    releases = best_release.get('releases', [])
                    if releases:
                        # Use the first release for date
                        first_release = releases[0]
                        release_date = first_release.get('date', {})
                        if isinstance(release_date, dict) and 'year' in release_date:
                            metadata['date'] = str(release_date['year'])
                            if is_debug_case:
                                self.logger.debug(f"  Date: '{metadata['date']}'")
                        elif isinstance(release_date, str):
                            try:
                                metadata['date'] = release_date[:4]  # Extract year
                                if is_debug_case:
                                    self.logger.debug(f"  Date: '{metadata['date']}'")
                            except:
                                if is_debug_case:
                                    self.logger.debug("  Could not extract year from date string")
                    else:
                        # Fallback: try first-release-date
                        first_release_date = best_release.get('first-release-date')
                        if first_release_date:
                            try:
                                metadata['date'] = first_release_date[:4]  # Extract year
                                if is_debug_case:
                                    self.logger.debug(f"  Date: '{metadata['date']}'")
                            except:
                                if is_debug_case:
                                    self.logger.debug("  Could not extract year from first-release-date")
                            
            # Validate that we have essential metadata
            if not metadata.get('title') or not metadata.get('artist'):
                if is_debug_case:
                    self.logger.debug(f"Incomplete metadata - title: '{metadata.get('title')}', artist: '{metadata.get('artist')}'")
                return None
                
            if is_debug_case:
                self.logger.debug(f"Successfully extracted metadata: {metadata}")
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error extracting metadata from AcoustID recording: {e}")
            return None
            
    def _search_original_recording_release(self, recording_id, is_debug_case=False):
        """Search MusicBrainz for original (non-compilation) releases of a recording"""
        try:
            if is_debug_case:
                self.logger.debug(f"    Searching MusicBrainz for original releases of recording {recording_id}")
            
            # Search MusicBrainz for the recording with release-group includes
            result = musicbrainzngs.get_recording_by_id(
                recording_id, 
                includes=['release-groups']
            )
            
            recording = result.get('recording', {})
            release_groups = recording.get('release-group-list', [])
            
            if is_debug_case:
                self.logger.debug(f"    Found {len(release_groups)} release groups for recording")
            
            # Look for non-compilation albums
            for release_group in release_groups:
                release_type = release_group.get('type', '')
                secondary_types = release_group.get('secondary-type-list', [])
                
                # Check if it's a compilation
                is_compilation = any(sec_type.get('name', '').lower() == 'compilation' 
                                   for sec_type in secondary_types)
                
                if is_debug_case:
                    title = release_group.get('title', '')
                    self.logger.debug(f"    Release group: '{title}' (type: {release_type}, compilation: {is_compilation})")
                
                # Prefer non-compilation albums
                if release_type.lower() == 'album' and not is_compilation:
                    if is_debug_case:
                        self.logger.debug(f"    Found original album: '{release_group.get('title', '')}'")
                    
                    # Convert to the format expected by our code
                    converted_release = {
                        'title': release_group.get('title', ''),
                        'type': release_group.get('type', ''),
                        'id': release_group.get('id', ''),
                        'first-release-date': release_group.get('first-release-date', ''),
                        'releases': []  # We could fetch this if needed
                    }
                    return converted_release
            
            if is_debug_case:
                self.logger.debug(f"    No original albums found for recording {recording_id}")
            return None
            
        except Exception as e:
            if is_debug_case:
                self.logger.debug(f"    Error searching for original recording release: {e}")
            return None
            
    def enrich_acoustid_results_with_musicbrainz(self, acoustid_results):
        """Enrich AcoustID results with MusicBrainz recording data"""
        try:
            enriched_results = []
            
            for result in acoustid_results:
                acoustid_id = result.get('id')
                score = result.get('score', 0)
                
                if not acoustid_id:
                    continue
                    
                self.logger.debug(f"Looking up MusicBrainz recordings for AcoustID: {acoustid_id}")
                
                try:
                    # Search for recordings that reference this AcoustID
                    mb_result = musicbrainzngs.search_recordings(
                        query=f'puid:{acoustid_id}',
                        limit=10
                    )
                    
                    recordings = mb_result.get('recording-list', [])
                    
                    if recordings:
                        # Add recordings to the result
                        enriched_result = {
                            'id': acoustid_id,
                            'score': score,
                            'recordings': recordings
                        }
                        enriched_results.append(enriched_result)
                        self.logger.debug(f"Found {len(recordings)} recordings for AcoustID {acoustid_id}")
                    else:
                        self.logger.debug(f"No MusicBrainz recordings found for AcoustID {acoustid_id}")
                        
                except Exception as e:
                    self.logger.debug(f"Error looking up MusicBrainz recordings for {acoustid_id}: {e}")
                    continue
                    
            self.logger.debug(f"Enriched {len(enriched_results)} results with MusicBrainz data")
            return enriched_results
            
        except Exception as e:
            self.logger.error(f"Error enriching AcoustID results: {e}")
            return []
            
    def search_musicbrainz_metadata(self, current_metadata, file_path):
        """Search MusicBrainz for improved metadata"""
        try:
            artist = current_metadata.get('artist', '').strip()
            album = current_metadata.get('album', '').strip()
            title = current_metadata.get('title', '').strip()
            
            # If we have filename but no title, try to extract title from filename
            if not title:
                title = Path(file_path).stem
                # Clean up common filename patterns
                title = title.replace('_', ' ')
                title = ' '.join(title.split())  # Normalize whitespace
                
            self.logger.debug(f"Searching MusicBrainz with: artist='{artist}', album='{album}', title='{title}'")
            
            # Strategy 1: Search by recording (track) if we have title and artist
            if title and artist:
                recordings = self.search_musicbrainz_recordings(artist, title)
                if recordings:
                    # Find the best matching recording
                    best_recording = self.find_best_recording_match(recordings, artist, album, title)
                    if best_recording:
                        return self.extract_metadata_from_recording(best_recording)
                        
            # Strategy 2: Search by album if we have artist and album
            if artist and album:
                releases = self.search_musicbrainz_releases(artist, album)
                if releases:
                    # Find the best matching release
                    best_release = self.find_best_release_match(releases, artist, album)
                    if best_release and title:
                        # Find the specific track in this release
                        track_metadata = self.find_track_in_release(best_release, title)
                        if track_metadata:
                            return track_metadata
                            
            return None
            
        except Exception as e:
            self.logger.error(f"Error searching MusicBrainz: {e}")
            return None
            
    def search_musicbrainz_recordings(self, artist, title, limit=10):
        """Search MusicBrainz for recordings by artist and title"""
        try:
            query = f'artist:"{artist}" AND recording:"{title}"'
            self.logger.debug(f"MusicBrainz recording query: {query}")
            
            result = musicbrainzngs.search_recordings(query=query, limit=limit)
            recordings = result.get('recording-list', [])
            
            self.logger.debug(f"Found {len(recordings)} recordings in MusicBrainz")
            return recordings
            
        except Exception as e:
            self.logger.error(f"Error searching MusicBrainz recordings: {e}")
            return []
            
    def search_musicbrainz_releases(self, artist, album, limit=10):
        """Search MusicBrainz for releases by artist and album"""
        try:
            query = f'artist:"{artist}" AND release:"{album}"'
            self.logger.debug(f"MusicBrainz release query: {query}")
            
            result = musicbrainzngs.search_releases(query=query, limit=limit)
            releases = result.get('release-list', [])
            
            self.logger.debug(f"Found {len(releases)} releases in MusicBrainz")
            return releases
            
        except Exception as e:
            self.logger.error(f"Error searching MusicBrainz releases: {e}")
            return []
            
    def find_best_recording_match(self, recordings, target_artist, target_album, target_title):
        """Find the best matching recording from search results"""
        try:
            def calculate_match_score(recording):
                score = 0
                
                # Check artist match
                artist_credits = recording.get('artist-credit', [])
                if artist_credits:
                    recording_artist = artist_credits[0].get('artist', {}).get('name', '')
                    if self.fuzzy_match_strings(recording_artist, target_artist):
                        score += 3
                        
                # Check title match
                recording_title = recording.get('title', '')
                if self.fuzzy_match_strings(recording_title, target_title):
                    score += 3
                    
                # Check if recording appears on target album
                releases = recording.get('release-list', [])
                for release in releases:
                    release_title = release.get('title', '')
                    if self.fuzzy_match_strings(release_title, target_album):
                        score += 2
                        break
                        
                return score
                
            # Find recording with highest score
            best_recording = None
            best_score = 0
            
            for recording in recordings:
                score = calculate_match_score(recording)
                if score > best_score:
                    best_score = score
                    best_recording = recording
                    
            if best_recording and best_score >= 3:  # Minimum threshold
                self.logger.debug(f"Selected recording with score {best_score}: {best_recording.get('title')}")
                return best_recording
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding best recording match: {e}")
            return None
            
    def find_best_release_match(self, releases, target_artist, target_album):
        """Find the best matching release from search results"""
        try:
            def calculate_match_score(release):
                score = 0
                
                # Check artist match
                artist_credits = release.get('artist-credit', [])
                if artist_credits:
                    release_artist = artist_credits[0].get('artist', {}).get('name', '')
                    if self.fuzzy_match_strings(release_artist, target_artist):
                        score += 3
                        
                # Check album title match
                release_title = release.get('title', '')
                if self.fuzzy_match_strings(release_title, target_album):
                    score += 3
                    
                return score
                
            # Find release with highest score
            best_release = None
            best_score = 0
            
            for release in releases:
                score = calculate_match_score(release)
                if score > best_score:
                    best_score = score
                    best_release = release
                    
            if best_release and best_score >= 3:  # Minimum threshold
                self.logger.debug(f"Selected release with score {best_score}: {best_release.get('title')}")
                return best_release
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding best release match: {e}")
            return None
            
    def find_track_in_release(self, release, target_title):
        """Find specific track in a release"""
        try:
            release_id = release.get('id')
            if not release_id:
                return None
                
            # Get full release info including track list
            release_info = musicbrainzngs.get_release_by_id(
                release_id, 
                includes=['recordings', 'artist-credits']
            )
            
            release_data = release_info.get('release', {})
            medium_list = release_data.get('medium-list', [])
            
            for medium in medium_list:
                track_list = medium.get('track-list', [])
                for track in track_list:
                    recording = track.get('recording', {})
                    track_title = recording.get('title', '')
                    
                    if self.fuzzy_match_strings(track_title, target_title):
                        # Found matching track
                        return self.extract_metadata_from_recording(recording, release_data, track)
                        
            return None
            
        except Exception as e:
            self.logger.error(f"Error finding track in release: {e}")
            return None
            
    def extract_metadata_from_recording(self, recording, release_data=None, track=None):
        """Extract improved metadata from MusicBrainz recording"""
        try:
            metadata = {}
            
            # Extract title
            metadata['title'] = recording.get('title', '')
            
            # Extract artist
            artist_credits = recording.get('artist-credit', [])
            if artist_credits:
                metadata['artist'] = artist_credits[0].get('artist', {}).get('name', '')
                
            # Extract album info from release data
            if release_data:
                metadata['album'] = release_data.get('title', '')
                
                # Extract date
                release_date = release_data.get('date')
                if release_date:
                    metadata['date'] = release_date[:4]  # Extract year
                    
            # Extract track number
            if track:
                metadata['track'] = str(track.get('position', ''))
                
            self.logger.debug(f"Extracted MusicBrainz metadata: {metadata}")
            return metadata
            
        except Exception as e:
            self.logger.error(f"Error extracting metadata from recording: {e}")
            return None
            
    def fuzzy_match_strings(self, str1, str2, threshold=0.8):
        """Simple fuzzy string matching for metadata comparison"""
        if not str1 or not str2:
            return False
            
        str1_clean = ''.join(c.lower() for c in str1 if c.isalnum())
        str2_clean = ''.join(c.lower() for c in str2 if c.isalnum())
        
        if not str1_clean or not str2_clean:
            return False
            
        # Check if one string is contained in the other
        if str1_clean in str2_clean or str2_clean in str1_clean:
            return True
            
        # Calculate simple similarity ratio
        longer = max(len(str1_clean), len(str2_clean))
        matches = sum(1 for a, b in zip(str1_clean, str2_clean) if a == b)
        ratio = matches / longer if longer > 0 else 0
        
        return ratio >= threshold
        
    def apply_metadata_to_file(self, file_path, metadata):
        """Apply improved metadata to music file"""
        try:
            if self.dry_run:
                self.logger.info(f"[DRY RUN] Would update metadata for: {file_path}")
                self.logger.info(f"[DRY RUN] New metadata: {metadata}")
                return True
                
            self.logger.info(f"Updating metadata for: {file_path}")
            
            # Load the file
            audio_file = MutagenFile(file_path)
            if audio_file is None:
                self.logger.error(f"Could not load audio file: {file_path}")
                return False
                
            # Apply metadata based on file type
            if file_path.suffix.lower() == '.mp3':
                success = self.apply_id3_metadata(audio_file, metadata)
            elif file_path.suffix.lower() == '.flac':
                success = self.apply_flac_metadata(audio_file, metadata)
            elif file_path.suffix.lower() in ['.m4a', '.mp4']:
                success = self.apply_mp4_metadata(audio_file, metadata)
            else:
                success = self.apply_generic_metadata(audio_file, metadata)
                
            if success:
                audio_file.save()
                self.logger.info(f"Successfully updated metadata for: {file_path}")
                return True
            else:
                self.logger.error(f"Failed to update metadata for: {file_path}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error applying metadata to {file_path}: {e}")
            return False
            
    def apply_id3_metadata(self, audio_file, metadata):
        """Apply metadata to ID3 tags (MP3)"""
        try:
            if not hasattr(audio_file, 'tags') or audio_file.tags is None:
                audio_file.add_tags()
                
            tags = audio_file.tags
            
            if metadata.get('title'):
                tags.add(TIT2(encoding=3, text=metadata['title']))
            if metadata.get('artist'):
                tags.add(TPE1(encoding=3, text=metadata['artist']))
            if metadata.get('album'):
                tags.add(TALB(encoding=3, text=metadata['album']))
            if metadata.get('date'):
                tags.add(TDRC(encoding=3, text=metadata['date']))
            if metadata.get('track'):
                tags.add(TRCK(encoding=3, text=metadata['track']))
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying ID3 metadata: {e}")
            return False
            
    def apply_flac_metadata(self, audio_file, metadata):
        """Apply metadata to FLAC tags"""
        try:
            if metadata.get('title'):
                audio_file['TITLE'] = metadata['title']
            if metadata.get('artist'):
                audio_file['ARTIST'] = metadata['artist']
            if metadata.get('album'):
                audio_file['ALBUM'] = metadata['album']
            if metadata.get('date'):
                audio_file['DATE'] = metadata['date']
            if metadata.get('track'):
                audio_file['TRACKNUMBER'] = metadata['track']
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying FLAC metadata: {e}")
            return False
            
    def apply_mp4_metadata(self, audio_file, metadata):
        """Apply metadata to MP4 tags"""
        try:
            if metadata.get('title'):
                audio_file['\xa9nam'] = metadata['title']
            if metadata.get('artist'):
                audio_file['\xa9ART'] = metadata['artist']
            if metadata.get('album'):
                audio_file['\xa9alb'] = metadata['album']
            if metadata.get('date'):
                audio_file['\xa9day'] = metadata['date']
            if metadata.get('track'):
                audio_file['trkn'] = [(int(metadata['track']), 0)]
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying MP4 metadata: {e}")
            return False
            
    def apply_generic_metadata(self, audio_file, metadata):
        """Apply metadata using generic approach"""
        try:
            # This is a fallback for other formats
            # Most formats support basic key-value pairs
            if metadata.get('title'):
                audio_file['TITLE'] = metadata['title']
            if metadata.get('artist'):
                audio_file['ARTIST'] = metadata['artist']
            if metadata.get('album'):
                audio_file['ALBUM'] = metadata['album']
            if metadata.get('date'):
                audio_file['DATE'] = metadata['date']
            if metadata.get('track'):
                audio_file['TRACKNUMBER'] = metadata['track']
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying generic metadata: {e}")
            return False
        
    def clean_album_name(self, album_name):
        """Clean album name by removing edition info like (Deluxe Edition), [Remastered], etc."""
        import re
        
        # Common patterns to remove
        patterns = [
            r'\s*\(.*?[Dd]eluxe.*?\)',           # (Deluxe Edition), (Deluxe)
            r'\s*\(.*?[Ee]dition.*?\)',         # (Special Edition), (Anniversary Edition)
            r'\s*\(.*?[Rr]emaster.*?\)',        # (Remastered), (2015 Remaster)
            r'\s*\(.*?[Aa]nniversary.*?\)',     # (10th Anniversary)
            r'\s*\(.*?[Ee]xpanded.*?\)',        # (Expanded)
            r'\s*\(.*?[Bb]onus.*?\)',           # (Bonus Tracks)
            r'\s*\[.*?[Dd]eluxe.*?\]',          # [Deluxe Edition]
            r'\s*\[.*?[Ee]dition.*?\]',         # [Special Edition]
            r'\s*\[.*?[Rr]emaster.*?\]',        # [Remastered]
            r'\s*\[.*?[Aa]nniversary.*?\]',     # [Anniversary]
            r'\s*\[.*?[Ee]xpanded.*?\]',        # [Expanded]
            r'\s*\[.*?[Bb]onus.*?\]',           # [Bonus Tracks]
        ]
        
        cleaned = album_name
        for pattern in patterns:
            cleaned = re.sub(pattern, '', cleaned)
        
        # Clean up extra whitespace
        cleaned = ' '.join(cleaned.split())
        
        return cleaned.strip()
        
    def group_files_by_album(self, music_files):
        """Group music files by album"""
        albums = defaultdict(list)
        
        for file_path in music_files:
            metadata = self.extract_metadata(file_path)
            if not metadata:
                self.logger.warning(f"Could not extract any metadata from: {file_path}")
                continue
                
            # Check for missing essential metadata and log what's missing
            missing_fields = []
            if not metadata['artist']:
                missing_fields.append('artist')
            if not metadata['album']:
                missing_fields.append('album')
                
            if missing_fields:
                self.logger.warning(f"Skipping file with incomplete metadata: {file_path}")
                self.logger.warning(f"Missing fields: {', '.join(missing_fields)}")
                self.logger.debug(f"Available metadata: artist='{metadata['artist']}', album='{metadata['album']}', title='{metadata['title']}', track='{metadata['track']}'")
                continue
                
            # Create album key from artist and album
            album_key = f"{metadata['artist']} - {metadata['album']}".lower()
            albums[album_key].append({
                'file_path': file_path,
                'metadata': metadata
            })
            
        return albums
        
    def search_lidarr_album(self, artist, album, track_title=None):
        """Search for album in Lidarr using robust fuzzy matching"""
        
        # Skip track-based search for Various Artists compilations
        if artist.lower() in ['various artists', 'various', 'va', 'compilation']:
            self.logger.debug(f"Skipping track-based search for compilation: {artist}")
            track_title = None  # Disable track search for compilations
        
        def fuzzy_match(str1, str2, threshold=0.8):
            """Simple fuzzy string matching"""
            str1_clean = ''.join(c.lower() for c in str1 if c.isalnum())
            str2_clean = ''.join(c.lower() for c in str2 if c.isalnum())
            
            if not str1_clean or not str2_clean:
                return False
                
            # Simple ratio comparison
            longer = max(len(str1_clean), len(str2_clean))
            shorter = min(len(str1_clean), len(str2_clean))
            
            # Check if one string is contained in the other
            if str1_clean in str2_clean or str2_clean in str1_clean:
                return True
                
            # Calculate simple similarity ratio
            matches = sum(1 for a, b in zip(str1_clean, str2_clean) if a == b)
            ratio = matches / longer if longer > 0 else 0
            
            return ratio >= threshold
        
        def calculate_similarity(str1, str2):
            """Calculate similarity ratio between two strings"""
            str1_clean = ''.join(c.lower() for c in str1 if c.isalnum())
            str2_clean = ''.join(c.lower() for c in str2 if c.isalnum())
            
            if not str1_clean or not str2_clean:
                return 0.0
                
            # Check if one string is contained in the other (higher score)
            if str1_clean in str2_clean or str2_clean in str1_clean:
                shorter = min(len(str1_clean), len(str2_clean))
                longer = max(len(str1_clean), len(str2_clean))
                return shorter / longer
                
            # Calculate character-by-character similarity
            longer = max(len(str1_clean), len(str2_clean))
            matches = sum(1 for a, b in zip(str1_clean, str2_clean) if a == b)
            return matches / longer if longer > 0 else 0.0
        
        def search_artist_in_lidarr(search_artist):
            """Find artist in Lidarr using fuzzy matching"""
            artists = self.get_all_artists()  # Use cached artists
            if not artists:
                return None
                
            # Try exact match first
            for artist_data in artists:
                artist_name = artist_data.get('artistName', '')
                if artist_name.lower() == search_artist.lower():
                    self.logger.debug(f"Exact artist match: {artist_name}")
                    return artist_data
            
            # Try fuzzy match
            for artist_data in artists:
                artist_name = artist_data.get('artistName', '')
                if fuzzy_match(artist_name, search_artist):
                    self.logger.debug(f"Fuzzy artist match: '{search_artist}' -> '{artist_name}'")
                    return artist_data
            
            self.logger.debug(f"No artist match found for: {search_artist}")
            return None
        
        def search_album_for_artist(artist_data, search_album, track_title=None):
            """Find album for specific artist using fuzzy matching and track matching"""
            artist_id = artist_data.get('id')
            artist_name = artist_data.get('artistName', 'Unknown')
            
            albums = self.get_artist_albums(artist_id)  # Use cached albums
            if not albums:
                return None
                
            self.logger.debug(f"Found {len(albums)} albums for artist '{artist_name}'")
            
            # Try exact match first
            for album_data in albums:
                album_title = album_data.get('title', '')
                if album_title.lower() == search_album.lower():
                    self.logger.debug(f"Exact album match: {album_title}")
                    return album_data
            
            # Try cleaned version exact match
            cleaned_search_album = self.clean_album_name(search_album)
            if cleaned_search_album != search_album:
                for album_data in albums:
                    album_title = album_data.get('title', '')
                    if album_title.lower() == cleaned_search_album.lower():
                        self.logger.info(f"Exact album match with cleaned name: '{search_album}' -> '{album_title}'")
                        return album_data
            
            # Try fuzzy match on original
            for album_data in albums:
                album_title = album_data.get('title', '')
                if fuzzy_match(album_title, search_album):
                    self.logger.info(f"Fuzzy album match: '{search_album}' -> '{album_title}'")
                    return album_data
            
            # Try fuzzy match on cleaned version
            if cleaned_search_album != search_album:
                for album_data in albums:
                    album_title = album_data.get('title', '')
                    if fuzzy_match(album_title, cleaned_search_album):
                        self.logger.info(f"Fuzzy album match with cleaned name: '{search_album}' -> '{album_title}'")
                        return album_data
            
            # If track title is available and no album name match, search by track
            if track_title and track_title.strip():
                self.logger.info(f"Album name matching failed, searching by track title: '{track_title}'")
                
                for album_data in albums:
                    album_title = album_data.get('title', '')
                    album_id = album_data.get('id')
                    
                    # Get tracks for this album
                    try:
                        self.logger.debug(f"Checking tracks for album '{album_title}' (ID: {album_id})")
                        tracks = self.get_album_tracks(album_id)
                        
                        if not tracks:
                            self.logger.debug(f"No tracks returned for album '{album_title}'")
                            continue
                            
                        self.logger.debug(f"Found {len(tracks)} tracks in album '{album_title}'")
                        
                        for track in tracks:
                            track_name = track.get('title', '').strip()
                            track_number = track.get('trackNumber', 'Unknown')
                            has_file = track.get('hasFile', False)
                            file_status = "✓" if has_file else "✗"
                            
                            self.logger.debug(f"  Track {track_number}: '{track_name}' {file_status}")
                            
                            if track_name and fuzzy_match(track_name, track_title):
                                match_score = calculate_similarity(track_name, track_title)
                                self.logger.info(f"Found album by track match ({match_score:.2f}): '{track_title}' -> '{track_name}' in album '{album_title}'")
                                return album_data
                                
                    except Exception as e:
                        self.logger.error(f"Error checking tracks for album '{album_title}': {e}")
                        continue
            
            # Log available albums for debugging
            album_titles = [album.get('title', 'Unknown') for album in albums[:5]]
            self.logger.debug(f"No album match found. Available albums: {album_titles}")
            
            return None
        
        # Main search logic
        self.logger.debug(f"Searching Lidarr for: {artist} - {album}")
        if track_title:
            self.logger.debug(f"Track title available for fallback search: {track_title}")
        
        # Step 1: Find the artist
        artist_data = search_artist_in_lidarr(artist)
        if not artist_data:
            self.logger.warning(f"Artist not found in Lidarr: {artist}")
            return None
        
        # Step 2: Find the album for that artist (with optional track-based search)
        album_data = search_album_for_artist(artist_data, album, track_title)
        if not album_data:
            self.logger.warning(f"Album not found for artist '{artist_data.get('artistName', artist)}': {album}")
            return None
        
        self.logger.info(f"Found album: {artist_data.get('artistName')} - {album_data.get('title')}")
        return album_data
            
    def get_album_tracks(self, album_id):
        """Get track list for album from Lidarr"""
        try:
            # First try the track endpoint directly
            url = f"{self.lidarr_url}/api/v1/track"
            params = {'albumId': album_id}
            response = requests.get(url, headers=self.lidarr_headers, params=params, timeout=30)
            response.raise_for_status()
            
            tracks = response.json()
            
            if tracks:
                self.logger.debug(f"Found {len(tracks)} tracks for album {album_id} via track endpoint")
                
                # Count tracks with files vs without files
                tracks_with_files = 0
                tracks_without_files = 0
                
                for track in tracks:
                    if track.get('hasFile', False):
                        tracks_with_files += 1
                    else:
                        tracks_without_files += 1
                
                self.logger.debug(f"Album {album_id} track status: {tracks_with_files} with files, {tracks_without_files} without files")
                
                return tracks
            
            # Fallback: try album endpoint with different approach
            self.logger.debug(f"No tracks from track endpoint, trying album endpoint for album {album_id}")
            url = f"{self.lidarr_url}/api/v1/album/{album_id}"
            response = requests.get(url, headers=self.lidarr_headers, timeout=30)
            response.raise_for_status()
            
            album_data = response.json()
            tracks = album_data.get('tracks', [])
            
            self.logger.debug(f"Album endpoint returned {len(tracks)} tracks for album {album_id}")
            if not tracks:
                # Check album statistics to see if tracks should exist
                stats = album_data.get('statistics', {})
                total_tracks = stats.get('totalTrackCount', 0)
                track_file_count = stats.get('trackFileCount', 0)
                if total_tracks > 0:
                    self.logger.warning(f"Album {album_id} should have {total_tracks} tracks ({track_file_count} with files) but none are populated in Lidarr")
                else:
                    self.logger.warning(f"Album {album_id} has no track data and no expected track count")
            
            return tracks
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error getting tracks for album {album_id}: {e}")
            return []
            
    def check_album_completeness(self, album_files, artist, album):
        """Check if downloaded album is complete against Lidarr"""
        
        # Get track title from first file for fallback search
        track_title = None
        if album_files and album_files[0].get('metadata', {}).get('title'):
            track_title = album_files[0]['metadata']['title']
        
        lidarr_album = self.search_lidarr_album(artist, album, track_title)
        if not lidarr_album:
            self.logger.warning(f"Album not found in Lidarr: {artist} - {album}")
            return False, f"Album not found in Lidarr", None
            
        expected_tracks = self.get_album_tracks(lidarr_album['id'])
        if not expected_tracks:
            self.logger.warning(f"No track data returned from Lidarr API for album: {artist} - {album} (ID: {lidarr_album['id']})")
            return False, f"No track data in Lidarr API response", lidarr_album
            
        # Extract track numbers from downloaded files
        downloaded_tracks = set()
        for file_info in album_files:
            track_num = file_info['metadata'].get('track', '')
            if track_num:
                # Handle track numbers like "1/12" or "01"
                track_num = track_num.split('/')[0].strip()
                try:
                    downloaded_tracks.add(int(track_num))
                except ValueError:
                    continue
                    
        # Get expected track numbers and track info
        expected_track_nums = set()
        tracks_with_files = 0
        tracks_without_files = 0
        
        for track in expected_tracks:
            if track.get('trackNumber'):
                expected_track_nums.add(track['trackNumber'])
            
            # Count file status
            if track.get('hasFile', False):
                tracks_with_files += 1
            else:
                tracks_without_files += 1
        
        self.logger.debug(f"Track comparison for {artist} - {album}:")
        self.logger.debug(f"  Expected tracks: {sorted(expected_track_nums)} (total: {len(expected_track_nums)})")
        self.logger.debug(f"  Downloaded tracks: {sorted(downloaded_tracks)} (total: {len(downloaded_tracks)})")
        self.logger.debug(f"  Lidarr file status: {tracks_with_files} with files, {tracks_without_files} without files")
                
        missing_tracks = expected_track_nums - downloaded_tracks
        extra_tracks = downloaded_tracks - expected_track_nums
        
        is_complete = len(missing_tracks) == 0
        
        # Enhanced status message with Lidarr file info
        status_msg = f"({len(downloaded_tracks)}/{len(expected_track_nums)})"
        if tracks_with_files > 0:
            status_msg += f" [Lidarr has {tracks_with_files}/{len(expected_track_nums)} files]"
        if missing_tracks:
            status_msg += f", Missing: {sorted(missing_tracks)}"
        if extra_tracks:
            status_msg += f", Extra: {sorted(extra_tracks)}"
            
        return is_complete, status_msg, lidarr_album
        
    def move_album(self, album_files, destination_dir, album_key, lidarr_album_data=None):
        """Move album files to destination directory"""
        if not album_files:
            return False
            
        # Get artist and album from first file
        first_file = album_files[0]
        original_artist = first_file['metadata']['artist']
        original_album = first_file['metadata']['album']
        
        # Use Lidarr data if available for proper naming
        if lidarr_album_data:
            # Get proper artist name from Lidarr
            artist_name = lidarr_album_data.get('artist', {}).get('artistName', original_artist)
            if not artist_name:
                # Fallback: get artist name from album data
                artist_name = original_artist
            
            # Get proper album name and year from Lidarr
            album_title = lidarr_album_data.get('title', original_album)
            release_year = None
            
            # Try to get release year from various date fields
            release_date = lidarr_album_data.get('releaseDate')
            if release_date:
                try:
                    release_year = release_date[:4]  # Extract year from date string
                except:
                    pass
            
            # Format album name with year if available
            if release_year:
                formatted_album = f"[{release_year}] {album_title}"
            else:
                formatted_album = f"[unknown] {album_title}"
                
            self.logger.info(f"Using Lidarr album info: {artist_name} - {formatted_album}")
            
        else:
            # Use original metadata
            artist_name = original_artist
            formatted_album = original_album
            self.logger.debug(f"Using file metadata: {artist_name} - {formatted_album}")
        
        # Create destination path
        safe_artist = self.sanitize_filename(artist_name)
        safe_album = self.sanitize_filename(formatted_album)
        dest_album_dir = destination_dir / safe_artist / safe_album
        
        try:
            if not self.dry_run:
                dest_album_dir.mkdir(parents=True, exist_ok=True)
                
            moved_files = []
            for file_info in album_files:
                src_path = file_info['file_path']
                dest_path = dest_album_dir / src_path.name
                
                if self.dry_run:
                    self.logger.info(f"[DRY RUN] Would move: {src_path} -> {dest_path}")
                else:
                    shutil.move(str(src_path), str(dest_path))
                    self.logger.info(f"Moved: {src_path} -> {dest_path}")
                    
                moved_files.append(str(dest_path))
                
            # Record action
            action = {
                'timestamp': datetime.now().isoformat(),
                'action': 'move_album',
                'album': album_key,
                'destination': str(destination_dir),
                'file_count': len(album_files),
                'files': moved_files,
                'dry_run': self.dry_run
            }
            self.action_history.append(action)
            
            # Clean up empty source directories if not dry run
            if not self.dry_run:
                self.cleanup_empty_dirs(album_files[0]['file_path'].parent)
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error moving album {album_key}: {e}")
            return False
            
    def sanitize_filename(self, filename):
        """Sanitize filename for filesystem"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename.strip()
        
    def cleanup_empty_dirs(self, directory):
        """Remove directories that contain no music files or subdirectories"""
        try:
            for root, dirs, files in os.walk(directory, topdown=False):
                current_dir = Path(root)
                
                # Skip the main downloads directory itself
                if current_dir == self.downloads_dir:
                    continue
                    
                # Check if directory contains any music files
                music_files = self.get_music_files(current_dir)
                
                # Check if directory has any subdirectories
                subdirs = [d for d in current_dir.iterdir() if d.is_dir()]
                
                # If no music files and no subdirectories, remove the directory
                if not music_files and not subdirs:
                    all_files = list(current_dir.iterdir())
                    if all_files:
                        self.logger.info(f"Removing directory with only non-music files: {current_dir}")
                        self.logger.debug(f"Files found: {[f.name for f in all_files]}")
                    else:
                        self.logger.info(f"Removing empty directory: {current_dir}")
                    
                    if not self.dry_run:
                        # Remove all files first
                        for file_path in current_dir.iterdir():
                            if file_path.is_file():
                                file_path.unlink()
                        # Then remove the directory
                        current_dir.rmdir()
                    else:
                        self.logger.info(f"[DRY RUN] Would remove directory: {current_dir}")
                        
        except Exception as e:
            self.logger.warning(f"Error cleaning up directories in {directory}: {e}")
            
    def save_action_history(self):
        """Save action history to file"""
        if self.action_history:
            history_file = self.work_dir / 'process_downloads_history.json'
            try:
                with open(history_file, 'w') as f:
                    json.dump(self.action_history, f, indent=2)
                self.logger.info(f"Saved action history to {history_file}")
            except Exception as e:
                self.logger.error(f"Error saving action history: {e}")
                
    def process_downloads(self):
        """Main processing function"""
        if not self.downloads_dir.exists():
            self.logger.error(f"Downloads directory does not exist: {self.downloads_dir}")
            return
            
        self.logger.info(f"Starting downloads processing (dry_run={self.dry_run})")
        self.logger.info(f"Scanning directory: {self.downloads_dir}")
        
        # Get all music files
        music_files = self.get_music_files(self.downloads_dir)
        if not music_files:
            self.logger.info("No music files found in downloads directory")
            return
            
        self.logger.info(f"Found {len(music_files)} music files")
        
        # Fix metadata using MusicBrainz before processing (if enabled)
        if not self.skip_metadata_fix:
            self.logger.info("Fixing metadata using MusicBrainz...")
            fixed_count = 0
            failed_count = 0
            
            for music_file in music_files:
                try:
                    if self.fix_metadata_with_musicbrainz(music_file):
                        fixed_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    self.logger.error(f"Error processing metadata for {music_file}: {e}")
                    failed_count += 1
                    
            self.logger.info(f"Metadata fixing complete: {fixed_count} files fixed, {failed_count} files failed/skipped")
        else:
            self.logger.info("Skipping metadata fixing (disabled)")
        
        # Group files by album
        albums = self.group_files_by_album(music_files)
        self.logger.info(f"Grouped into {len(albums)} albums")
        
        complete_count = 0
        incomplete_count = 0
        error_count = 0
        skipped_count = 0
        compilation_count = 0
        
        for album_key, album_files in albums.items():
            self.logger.info(f"Processing album: {album_key} ({len(album_files)} files)")
            
            # Extract artist and album from key
            try:
                artist, album = album_key.split(' - ', 1)
            except ValueError:
                self.logger.error(f"Invalid album key format: {album_key}")
                error_count += 1
                continue
            
            # Skip Various Artists compilations and common compilation patterns
            compilation_patterns = [
                'various artists', 'various', 'va', 'compilation',
                'verschiedene',  # German for "various"
                'bravo hits', 'now that\'s what i call music', 'hits',
                'sampler', 'mixtape', 'mix cd', 'promo cd'
            ]
            
            artist_lower = artist.lower()
            album_lower = album.lower()
            
            is_compilation = any(pattern in artist_lower for pattern in compilation_patterns)
            is_compilation_album = any(pattern in album_lower for pattern in ['hits', 'sampler', 'compilation', 'mixtape', 'promo', 'cd01', 'cd02', 'cd03'])
            
            if is_compilation or is_compilation_album:
                self.logger.info(f"Skipping compilation album: {album_key}")
                self.logger.debug(f"Compilation albums have unreliable metadata and require manual handling")
                compilation_count += 1
                continue
                
            # Check completeness
            is_complete, status_msg, lidarr_album_data = self.check_album_completeness(album_files, artist, album)
            self.logger.info(f"Album completeness check: {status_msg}")
            
            # Skip if not found in Lidarr - leave in downloads folder
            if lidarr_album_data is None:
                self.logger.info(f"Skipping album not found in Lidarr: {album_key}")
                self.logger.debug(f"Album will remain in downloads folder for manual review")
                skipped_count += 1
                continue
            
            # Move to appropriate directory
            if is_complete:
                if self.move_album(album_files, self.music_dir, album_key, lidarr_album_data):
                    complete_count += 1
                    self.logger.info(f"Moved complete album to music library: {album_key}")
                else:
                    error_count += 1
            else:
                if self.move_album(album_files, self.incomplete_dir, album_key, lidarr_album_data):
                    incomplete_count += 1
                    self.logger.info(f"Moved incomplete album to incomplete directory: {album_key}")
                else:
                    error_count += 1
                    
        # Save action history
        self.save_action_history()
        
        # Summary
        total_processed = complete_count + incomplete_count + error_count
        total_albums = total_processed + skipped_count + compilation_count
        self.logger.info(f"Processing complete!")
        self.logger.info(f"Total albums found: {total_albums}")
        self.logger.info(f"Albums processed: {total_processed}")
        self.logger.info(f"Complete albums moved to library: {complete_count}")
        self.logger.info(f"Incomplete albums moved to incomplete: {incomplete_count}")
        self.logger.info(f"Albums skipped (not found in Lidarr): {skipped_count}")
        self.logger.info(f"Compilation albums skipped: {compilation_count}")
        self.logger.info(f"Errors: {error_count}")
        
        # Clean up empty directories from downloads folder
        self.logger.info("Cleaning up empty directories from downloads folder...")
        self.cleanup_empty_dirs(self.downloads_dir)
        self.logger.info("Empty directory cleanup complete.")

def main():
    try:
        logger.info("=== Process Downloads Script Starting ===")
        logger.info("Parsing command line arguments...")
        
        parser = argparse.ArgumentParser(description='Process downloaded music files')
        parser.add_argument('--dry-run', action='store_true', 
                           help='Run in dry-run mode (no actual file operations)')
        parser.add_argument('--skip-metadata-fix', action='store_true',
                           help='Skip MusicBrainz metadata fixing step')
        
        args = parser.parse_args()
        logger.info(f"Command line arguments parsed: dry_run={args.dry_run}, skip_metadata_fix={args.skip_metadata_fix}")
        
        logger.info("Creating DownloadsProcessor instance...")
        processor = DownloadsProcessor(dry_run=args.dry_run, skip_metadata_fix=args.skip_metadata_fix)
        
        logger.info("Starting downloads processing...")
        processor.process_downloads()
        
        logger.info("=== Process Downloads Script Completed Successfully ===")
        
    except Exception as e:
        logger.error(f"CRITICAL ERROR in main(): {e}")
        logger.error(f"Error type: {type(e).__name__}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

if __name__ == "__main__":
    main()
