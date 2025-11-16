#!/usr/bin/env python3
"""
Queue Lidarr Monitored - Intelligent music download manager

This script provides a comprehensive approach to downloading missing music:
1. Fetches wanted albums from Lidarr
2. Gets complete track listings for each album  
3. Checks what tracks are already owned (in owned music folder)
4. Checks what tracks are already in download queue
5. Downloads only the truly missing tracks

Name: Queue Lidarr Monitored
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: lidarr, slskd, downloads, automation, smart
Supports dry run: true

Features:
- Precise track-level downloading
- Owned music detection
- Download queue deduplication
- MusicBrainz metadata integration
- Comprehensive progress tracking
- Dry run support
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

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
    from settings import get_lidarr_config, get_slskd_config, is_dry_run, get_setting
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

# Global variables for graceful shutdown and statistics
interrupted = False
STATS = {
    'albums_checked': 0,
    'albums_complete': 0,
    'albums_queued': 0,
    'albums_failed': 0,
    'tracks_total': 0,
    'tracks_owned': 0,
    'tracks_already_queued': 0,
    'tracks_queued': 0,
    'tracks_failed': 0
}

# Global configuration
CONFIG = {}

# Global tracking for downloaded tracks to prevent duplicates across searches
DOWNLOADED_TRACKS = {}  # key: "artist|track_title", value: {"user": username, "filename": filepath}

# Supported audio extensions for owned music detection - focusing on FLAC and MP3 only
AUDIO_EXTENSIONS = {'.mp3', '.flac'}

# File extensions to explicitly exclude (cover art, lyrics, etc.)
EXCLUDED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',  # Images/cover art
                      '.lrc', '.txt', '.cue', '.log', '.m3u', '.pls',    # Lyrics/metadata
                      '.nfo', '.sfv', '.md5', '.par2', '.zip', '.rar'}   # Other metadata

def is_audio_file_wanted(filename: str) -> bool:
    """Check if a file should be downloaded (audio only, no cover art/lyrics)"""
    file_ext = Path(filename).suffix.lower()
    
    # Must be an audio file
    if file_ext not in AUDIO_EXTENSIONS:
        return False
    
    # Must not be explicitly excluded
    if file_ext in EXCLUDED_EXTENSIONS:
        return False
    
    return True

def setup_logging():
    """Set up logging with timestamps"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"queue_lidarr_monitored_{timestamp}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Suppress verbose HTTP connection logging from urllib3/requests
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("requests.packages.urllib3.connectionpool").setLevel(logging.WARNING)
    
    logging.info("🚀 STARTING QUEUE LIDARR MONITORED")
    logging.info(f"📝 Log file: {log_file}")
    logging.info("=" * 80)
    
    return log_file

def signal_handler(signum, frame):
    """Handle graceful shutdown on SIGINT/SIGTERM"""
    global interrupted
    interrupted = True
    logging.info("")
    logging.info("🛑 Script interrupted by user")
    print_summary(interrupted=True)
    sys.exit(130)

def check_dependencies():
    """Check required dependencies and configuration"""
    if not REQUESTS_AVAILABLE:
        logging.error("✗ Missing required dependency: requests")
        logging.error("   Install with: pip install requests")
        return False
    
    global CONFIG
    
    if SETTINGS_AVAILABLE:
        # Get configuration from settings module
        lidarr_config = get_lidarr_config()
        slskd_config = get_slskd_config()
        
        missing_configs = []
        if not lidarr_config.get('url'):
            missing_configs.append("Lidarr URL")
        if not lidarr_config.get('api_key'):
            missing_configs.append("Lidarr API key")
        if not slskd_config.get('url'):
            missing_configs.append("slskd URL")
        if not slskd_config.get('api_key'):
            missing_configs.append("slskd API key")
        
        if missing_configs:
            logging.error("✗ Missing required configuration:")
            for config in missing_configs:
                logging.error(f"   - {config}")
            return False
        
        # Get owned music path from settings, with fallback
        owned_music_path = get_setting('owned_music_path') or get_setting('music_library_path') or '/media/Owned'
        
        CONFIG = {
            'lidarr_url': lidarr_config['url'].rstrip('/'),
            'lidarr_api_key': lidarr_config['api_key'],
            'slskd_url': slskd_config['url'].rstrip('/'),
            'slskd_api_key': slskd_config['api_key'],
            'owned_music_path': owned_music_path
        }
        
        logging.info("🔧 Configuration loaded from settings")
        
    else:
        # Fall back to environment variables
        required_vars = {
            'LIDARR_URL': 'Lidarr server URL',
            'LIDARR_API_KEY': 'Lidarr API key',
            'SLSKD_URL': 'slskd server URL', 
            'SLSKD_API_KEY': 'slskd API key'
        }
        
        missing_vars = []
        for var, desc in required_vars.items():
            if not os.getenv(var):
                missing_vars.append(f"{var} ({desc})")
        
        if missing_vars:
            logging.error("✗ Missing required environment variables:")
            for var in missing_vars:
                logging.error(f"   - {var}")
            return False
        
        CONFIG = {
            'lidarr_url': os.getenv('LIDARR_URL').rstrip('/'),
            'lidarr_api_key': os.getenv('LIDARR_API_KEY'),
            'slskd_url': os.getenv('SLSKD_URL').rstrip('/'),
            'slskd_api_key': os.getenv('SLSKD_API_KEY'),
            'owned_music_path': os.getenv('OWNED_MUSIC_PATH', '/media/Owned')
        }
        
        logging.info("🔧 Configuration loaded from environment variables")
    
    logging.info(f"   📍 Lidarr: {CONFIG['lidarr_url']}")
    logging.info(f"   📍 slskd: {CONFIG['slskd_url']}")
    logging.info(f"   📁 Owned Music: {CONFIG['owned_music_path']}")
    
    return True

def get_wanted_albums() -> List[Dict]:
    """Get all wanted albums from Lidarr"""
    logging.info("\n🔍 Fetching wanted albums from Lidarr...")
    logging.info("PROGRESS_SUB: Connecting to Lidarr...")
    
    try:
        url = f"{CONFIG['lidarr_url']}/api/v1/wanted/missing"
        params = {
            'apikey': CONFIG['lidarr_api_key'],
            'page': 1,
            'pageSize': 1000,
            'sortKey': 'releaseDate',
            'sortDirection': 'descending'
        }
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            logging.error("✗ Failed to fetch wanted albums from Lidarr")
            return []
        
        wanted_data = response.json()
        total_records = wanted_data.get('totalRecords', 0)
        albums = wanted_data.get('records', [])
        
        logging.info(f"📋 Found {total_records} wanted albums")
        return albums
        
    except Exception as e:
        logging.error(f"✗ Error fetching wanted albums: {e}")
        return []

def get_complete_track_listing(album_id: int, artist_name: str, album_title: str) -> Tuple[List[Dict], int]:
    """
    Get complete track listing for an album using the tracks endpoint.
    Returns (missing_tracks, total_tracks)
    """
    try:
        # Debug: Show album information
        url = f"{CONFIG['lidarr_url']}/api/v1/album/{album_id}"
        params = {'apikey': CONFIG['lidarr_api_key']}
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            logging.warning(f"   ⚠️  Could not get album details (HTTP {response.status_code})")
            return [], 0
        
        album_data = response.json()
        
        logging.debug(f"   📊 Album ID: {album_id}")
        logging.debug(f"   📊 Album monitored: {album_data.get('monitored', 'Unknown')}")
        logging.debug(f"   📊 Album releases: {len(album_data.get('releases', []))}")
        logging.debug(f"   📊 Album foreignAlbumId: {album_data.get('foreignAlbumId', 'None')}")
        
        # Try to get tracks directly from tracks endpoint - this is the most reliable method
        current_tracks = []
        
        try:
            tracks_url = f"{CONFIG['lidarr_url']}/api/v1/track"
            tracks_params = {
                'apikey': CONFIG['lidarr_api_key'],
                'albumId': album_id
            }
            
            tracks_response = requests.get(tracks_url, params=tracks_params, timeout=30)
            if tracks_response.status_code == 200:
                direct_tracks = tracks_response.json()
                if direct_tracks:
                    logging.info(f"   ✅ Found {len(direct_tracks)} tracks from tracks endpoint")
                    current_tracks = direct_tracks
                else:
                    logging.debug(f"   📊 Tracks endpoint returned empty list")
            else:
                logging.debug(f"   📊 Tracks endpoint failed (HTTP {tracks_response.status_code})")
        except Exception as tracks_error:
            logging.debug(f"   📊 Tracks endpoint error: {tracks_error}")
        
        # Fallback: check media in album data
        if not current_tracks:
            media = album_data.get('media', [])
            logging.debug(f"   � Media count: {len(media)}")
            
            for disc_num, disc in enumerate(media, 1):
                tracks = disc.get('tracks', [])
                logging.debug(f"   � Disc {disc_num}: {len(tracks)} tracks")
                current_tracks.extend(tracks)
            
            if current_tracks:
                logging.info(f"   ✅ Found {len(current_tracks)} tracks from album media")
        
        # If still no tracks, this album doesn't have track data available
        if not current_tracks:
            logging.warning(f"   ⚠️  No track information available for this album")
            foreign_album_id = album_data.get('foreignAlbumId')
            if foreign_album_id:
                logging.info(f"   💡 Foreign Album ID: {foreign_album_id}")
                logging.info(f"   💡 Consider manually refreshing this album in Lidarr UI")
            return [], 0
        
        # Process tracks
        missing_tracks = []
        total_tracks = len(current_tracks)
        
        logging.info(f"   📊 Processing {total_tracks} tracks...")
        
        for track in current_tracks:
            has_file = track.get('hasFile', False)
            track_title = track.get('title', 'Unknown Track')
            track_number = track.get('trackNumber', 0)
            
            logging.debug(f"   🎵 Track {track_number}: {track_title} (hasFile: {has_file})")
            
            if not has_file:
                track_info = {
                    'id': track.get('id'),
                    'title': track_title,
                    'trackNumber': track_number,
                    'discNumber': track.get('discNumber', 1),
                    'duration': track.get('duration', 0),
                    'artist': artist_name,
                    'album': album_title
                }
                missing_tracks.append(track_info)
        
        logging.info(f"   📊 Result: {len(missing_tracks)} missing tracks out of {total_tracks} total")
        return missing_tracks, total_tracks
        
    except Exception as e:
        logging.error(f"   ❌ Error getting track listing: {e}")
        import traceback
        logging.debug(f"   🐛 Full traceback: {traceback.format_exc()}")
        return [], 0

def check_owned_tracks(missing_tracks: List[Dict], artist_name: str, album_title: str) -> List[Dict]:
    """
    Check which missing tracks are already owned in the owned music folder.
    Returns tracks that are NOT owned.
    """
    owned_path = Path(CONFIG['owned_music_path'])
    
    if not owned_path.exists():
        logging.debug(f"   ℹ️  Owned music path doesn't exist: {owned_path}")
        return missing_tracks
    
    # Look for artist/album folder
    artist_folders = [d for d in owned_path.iterdir() if d.is_dir()]
    artist_folder = None
    
    # Find matching artist folder (case insensitive)
    for folder in artist_folders:
        if folder.name.lower().strip() == artist_name.lower().strip():
            artist_folder = folder
            break
    
    if not artist_folder:
        logging.debug(f"   ℹ️  Artist not found in owned music: {artist_name}")
        return missing_tracks
    
    # Find matching album folder
    album_folders = [d for d in artist_folder.iterdir() if d.is_dir()]
    album_folder = None
    
    for folder in album_folders:
        folder_name = folder.name.lower().strip()
        album_name = album_title.lower().strip()
        
        # Remove common prefixes like "[2023]"
        import re
        folder_name = re.sub(r'^\[\d{4}\]\s*', '', folder_name)
        
        if folder_name == album_name or album_name in folder_name:
            album_folder = folder
            break
    
    if not album_folder:
        logging.debug(f"   ℹ️  Album not found in owned music: {album_title}")
        return missing_tracks
    
    # Get all audio files in the album folder
    owned_files = []
    for file_path in album_folder.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in AUDIO_EXTENSIONS:
            owned_files.append(file_path.stem.lower())
    
    if not owned_files:
        logging.debug(f"   ℹ️  No audio files found in owned album folder")
        return missing_tracks
    
    # Check each missing track against owned files
    tracks_not_owned = []
    owned_tracks = []
    
    for track in missing_tracks:
        track_title = track['title'].lower().strip()
        track_number = track.get('trackNumber', 0)
        
        # Create possible filename patterns
        possible_patterns = [
            track_title,
            f"{track_number:02d} - {track_title}",
            f"{track_number:02d}. {track_title}",
            f"{track_number:02d} {track_title}",
            f"track {track_number:02d}",
            f"{track_number:02d}"
        ]
        
        # Check if any pattern matches owned files
        track_owned = False
        for pattern in possible_patterns:
            pattern = pattern.lower().strip()
            for owned_file in owned_files:
                if pattern in owned_file or owned_file in pattern:
                    track_owned = True
                    owned_tracks.append(track['title'])
                    break
            if track_owned:
                break
        
        if not track_owned:
            tracks_not_owned.append(track)
    
    if owned_tracks:
        logging.info(f"   ✅ Found {len(owned_tracks)} tracks already owned:")
        for track_title in owned_tracks[:3]:
            logging.info(f"      🎵 {track_title}")
        if len(owned_tracks) > 3:
            logging.info(f"      ... and {len(owned_tracks) - 3} more")
        
        STATS['tracks_owned'] += len(owned_tracks)
    
    return tracks_not_owned

def check_downloads_folder(tracks_to_check: List[Dict], artist_name: str) -> List[Dict]:
    """
    Check which tracks are not already in the downloads folder.
    Returns tracks that are NOT already downloaded.
    """
    try:
        logging.info(f"🔍 Checking downloads folder for existing tracks by '{artist_name}'...")
        
        # Deduplicate tracks by title to avoid processing the same track multiple times
        seen_titles = set()
        unique_tracks = []
        for track in tracks_to_check:
            track_title = track['title'].lower().strip()
            if track_title not in seen_titles:
                seen_titles.add(track_title)
                unique_tracks.append(track)
            else:
                logging.debug(f"   🔄 Skipping duplicate track: {track['title']}")
        
        if len(unique_tracks) < len(tracks_to_check):
            logging.info(f"   🔄 Removed {len(tracks_to_check) - len(unique_tracks)} duplicate tracks, checking {len(unique_tracks)} unique tracks")
        
        downloads_path = Path("/downloads/completed")
        if not downloads_path.exists():
            logging.info(f"   📁 Downloads folder not found: {downloads_path}, skipping local check")
            return unique_tracks
        
        # Get all audio files in downloads folder
        downloaded_files = []
        for audio_ext in ['.mp3', '.flac']:
            downloaded_files.extend(downloads_path.rglob(f"*{audio_ext}"))
        
        if not downloaded_files:
            logging.info("   📁 No audio files found in downloads folder")
            return unique_tracks
        
        logging.info(f"   📁 Checking {len(unique_tracks)} tracks against {len(downloaded_files)} downloaded files")
        
        tracks_not_downloaded = []
        already_downloaded = []
        
        for track in unique_tracks:
            track_title = track['title'].lower().strip()
            track_number = track.get('trackNumber', 0)
            
            # Ensure track_number is an integer
            if isinstance(track_number, str):
                try:
                    track_number = int(track_number)
                except (ValueError, TypeError):
                    track_number = 0
            
            logging.info(f"📁 Checking track: {track['title']} by {artist_name}")
            
            # Check if track is already downloaded
            track_downloaded = False
            matched_file = None
            
            for downloaded_file in downloaded_files:
                file_path_str = str(downloaded_file).lower()
                file_name_str = downloaded_file.name.lower()
                
                # Normalize names for comparison (remove special chars, extra spaces)
                def normalize_for_comparison(text):
                    # Keep only alphanumeric and spaces, then normalize spaces
                    normalized = ''.join(c if c.isalnum() else ' ' for c in text)
                    return ' '.join(normalized.split())
                
                normalized_track = normalize_for_comparison(track_title)
                normalized_artist = normalize_for_comparison(artist_name)
                normalized_file_path = normalize_for_comparison(file_path_str)
                normalized_file_name = normalize_for_comparison(file_name_str)
                
                # Create multiple search patterns to check
                patterns_to_check = [
                    # Track title in full path
                    normalized_track in normalized_file_path,
                    # Track title in filename
                    normalized_track in normalized_file_name,
                    # Artist and track in full path
                    normalized_artist in normalized_file_path and normalized_track in normalized_file_path,
                    # Track number patterns
                    f"{track_number:02d} {normalized_track}" in normalized_file_name,
                    f"{track_number} {normalized_track}" in normalized_file_name,
                    # Combined artist_track patterns
                    f"{normalized_artist} {normalized_track}" in normalized_file_path,
                ]
                
                # Check if any pattern matches
                if any(patterns_to_check):
                    track_downloaded = True
                    matched_file = str(downloaded_file.relative_to(downloads_path))
                    logging.info(f"      ✅ Found match: {matched_file}")
                    break
            
            if not track_downloaded:
                logging.info(f"      ❌ Not found: {track['title']}")
                tracks_not_downloaded.append(track)
            else:
                already_downloaded.append({
                    'track': track['title'],
                    'matched_file': matched_file
                })
        
        if already_downloaded:
            logging.info(f"   📁 {len(already_downloaded)} tracks already in downloads folder:")
            for item in already_downloaded[:3]:
                logging.info(f"      ✅ {item['track']} → {item['matched_file']}")
            if len(already_downloaded) > 3:
                logging.info(f"      ... and {len(already_downloaded) - 3} more")
        
        if tracks_not_downloaded:
            logging.info(f"   📁 {len(tracks_not_downloaded)} tracks still need downloading")
        
        return tracks_not_downloaded
        
    except Exception as e:
        logging.error(f"   ❌ Error checking downloads folder: {e}")
        return tracks_to_check

def check_download_queue(tracks_to_check: List[Dict]) -> List[Dict]:
    """
    Check which tracks are not already in the download queue.
    Returns tracks that are NOT already queued.
    """
    try:
        url = f"{CONFIG['slskd_url']}/api/v0/transfers/downloads"
        headers = {'X-API-Key': CONFIG['slskd_api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.warning("   ⚠️  Could not check download queue - proceeding with all tracks")
            return tracks_to_check
        
        downloads_data = response.json()
        
        # Collect all files in download queue
        queued_files = []
        
        # States that should prevent re-downloading (active or successfully completed)
        skip_download_states = [
            'Queued', 'Requested', 'Initializing', 'InProgress', 'Remotely Queued',
            'Completed', 'Succeeded'  # Successfully completed downloads
        ]
        
        # States that represent failed downloads that can be retried
        failed_states = [
            'TimedOut', 'Cancelled', 'Errored', 'Rejected', 'Failed'
        ]
        
        for user_group in downloads_data:
            username = user_group.get('username', 'unknown')
            directories = user_group.get('directories', [])
            
            for directory in directories:
                directory_name = directory.get('directory', '').lower()
                files = directory.get('files', [])
                
                for file_transfer in files:
                    filename = file_transfer.get('filename', '')
                    state = file_transfer.get('state', '')
                    size = file_transfer.get('size', 0)
                    
                    # Only skip downloads that are active or successfully completed
                    # Allow retries for failed downloads (TimedOut, Cancelled, etc.)
                    if state in skip_download_states:
                        # Extract just the filename without path
                        file_basename = filename.split('/')[-1].split('\\')[-1].lower()
                        
                        queued_files.append({
                            'filename': file_basename,
                            'full_path': filename.lower(),
                            'directory': directory_name,
                            'username': username,
                            'state': state,
                            'size': size
                        })
                    elif state in failed_states:
                        # Log failed downloads that we'll allow to retry
                        file_basename = filename.split('/')[-1].split('\\')[-1].lower()
                        logging.debug(f"   🔄 Allowing retry for failed download: {file_basename} ({state})")
        
        if not queued_files:
            logging.debug("   📋 No downloads found in queue (active or completed)")
            return tracks_to_check
        
        logging.debug(f"   📋 Found {len(queued_files)} files in download queue (active and completed)")
        
        # Check each track against queued files with improved matching
        tracks_not_queued = []
        already_queued = []
        
        for track in tracks_to_check:
            track_title = track['title'].lower().strip()
            track_number = track.get('trackNumber', 0)
            artist_name = track.get('artist', '').lower().strip()
            
            # Ensure track_number is an integer
            try:
                track_number = int(track_number) if track_number else 0
            except (ValueError, TypeError):
                track_number = 0
            
            # Normalize track title for better matching
            normalized_track = ''.join(c for c in track_title if c.isalnum() or c == ' ').strip()
            normalized_track = ' '.join(normalized_track.split())  # Remove extra spaces
            
            # Create various patterns to match against
            search_patterns = [
                normalized_track,
                f"{track_number:02d} {normalized_track}",
                f"{track_number:02d} - {normalized_track}",
                f"{track_number:02d}. {normalized_track}",
                f"track {track_number:02d}",
                f"{track_number:02d}"
            ]
            
            # Also try patterns without common words
            words_to_remove = ['feat', 'featuring', 'ft', 'remix', 'remaster', 'remastered', 'edit']
            clean_title = normalized_track
            for word in words_to_remove:
                clean_title = clean_title.replace(f" {word} ", " ").replace(f" {word}.", "")
            clean_title = ' '.join(clean_title.split())  # Clean up spaces
            if clean_title != normalized_track:
                search_patterns.append(clean_title)
            
            # Check if track is already being downloaded
            track_queued = False
            matched_file = None
            
            for queued_file in queued_files:
                filename = queued_file['filename']
                full_path = queued_file['full_path']
                
                # Normalize queued filename
                normalized_filename = ''.join(c for c in filename if c.isalnum() or c == ' ').strip()
                normalized_filename = ' '.join(normalized_filename.split())
                
                # Try various matching strategies
                for pattern in search_patterns:
                    if not pattern:  # Skip empty patterns
                        continue
                    
                    # Direct substring match
                    if pattern in normalized_filename or normalized_filename in pattern:
                        track_queued = True
                        matched_file = queued_file
                        break
                    
                    # Word-based matching (all words from pattern must be in filename)
                    pattern_words = set(pattern.split())
                    filename_words = set(normalized_filename.split())
                    
                    if pattern_words and pattern_words.issubset(filename_words):
                        # Additional check: ensure it's likely the same track
                        if len(pattern_words) >= 2:  # At least 2 words must match
                            track_queued = True
                            matched_file = queued_file
                            break
                
                if track_queued:
                    break
            
            if track_queued and matched_file:
                already_queued.append({
                    'track': track['title'],
                    'matched_file': matched_file['filename'],
                    'username': matched_file['username'],
                    'state': matched_file['state']
                })
            else:
                tracks_not_queued.append(track)
        
        if already_queued:
            # Group by status for better logging
            status_counts = {}
            for item in already_queued:
                status = item['state']
                if status not in status_counts:
                    status_counts[status] = []
                status_counts[status].append(item)
            
            logging.info(f"   ⏭️  {len(already_queued)} tracks already in download queue:")
            
            # Show breakdown by status
            for status, items in status_counts.items():
                if status == 'Queued':
                    logging.info(f"      � {len(items)} queued (waiting to start)")
                elif status in ['Requested', 'Initializing', 'InProgress', 'Remotely Queued']:
                    logging.info(f"      📥 {len(items)} actively downloading ({status})")
                elif status in ['Completed', 'Succeeded']:
                    logging.info(f"      ✅ {len(items)} already completed")
                else:
                    logging.info(f"      🔄 {len(items)} in status: {status}")
                
                # Show a few examples
                for item in items[:2]:
                    logging.debug(f"         • {item['track']} → {item['username']}")
            
            STATS['tracks_already_queued'] += len(already_queued)
        
        return tracks_not_queued
        
    except Exception as e:
        logging.error(f"   ❌ Error checking download queue: {e}")
        import traceback
        logging.debug(f"   🐛 Full traceback: {traceback.format_exc()}")
        return tracks_to_check

def queue_tracks_for_download(tracks: List[Dict], artist_name: str, album_title: str, dry_run: bool = False) -> bool:
    """Queue tracks for download in slskd using intelligent album-first approach"""
    if not tracks:
        return True

    logging.info(f"   🎯 Queuing {len(tracks)} tracks for download")
    
    # First, check if tracks are already downloaded before searching at all
    tracks_not_downloaded = check_downloads_folder(tracks, artist_name)
    
    if not tracks_not_downloaded:
        logging.info(f"   ✅ All tracks already in downloads folder, skipping search entirely")
        return True
        
    if len(tracks_not_downloaded) < len(tracks):
        already_downloaded = len(tracks) - len(tracks_not_downloaded)
        logging.info(f"   📁 {already_downloaded} tracks already downloaded, processing {len(tracks_not_downloaded)} remaining")
    
    # Update tracks to only those we need - this simplifies all downstream logic
    tracks = tracks_not_downloaded
    
    # Second, check download queue for the remaining tracks
    tracks_not_queued = check_download_queue(tracks)
    
    if not tracks_not_queued:
        logging.info(f"   ✅ All remaining tracks already in download queue, skipping search entirely")
        return True
        
    if len(tracks_not_queued) < len(tracks):
        already_queued = len(tracks) - len(tracks_not_queued)
        logging.info(f"   📋 {already_queued} tracks already in queue, processing {len(tracks_not_queued)} remaining")
    
    # Update tracks again to only those we actually need to search for
    tracks = tracks_not_queued

    # Show tracks being queued
    for i, track in enumerate(tracks[:5]):
        disc_info = f" (Disc {track['discNumber']})" if track['discNumber'] > 1 else ""
        logging.info(f"      🎵 Track {track['trackNumber']}{disc_info}: {track['title']}")

    if len(tracks) > 5:
        logging.info(f"      ... and {len(tracks) - 5} more tracks")
    
    if dry_run:
        logging.info(f"   [DRY RUN] Would search for album and queue {len(tracks)} specific tracks")
        STATS['tracks_queued'] += len(tracks)
        return True
    
    # Strategy: Search for the album, then queue only the specific tracks we need
    # Use basic search query (negative operators like -remix don't work in slskd)
    album_search_query = f'"{artist_name}" "{album_title}"'
    logging.info(f"   🔍 Searching for album: \"{album_search_query}\"")
    
    success = queue_album_with_specific_tracks(album_search_query, artist_name, album_title, tracks)
    
    if success:
        STATS['tracks_queued'] += len(tracks)
        logging.info("   ✅ Album search and track selection successful")
        return True
    else:
        logging.info("   ⚠️  Album search failed, trying individual track searches as fallback...")
        
        # tracks array already contains only the tracks we need (after downloads/queue filtering)
        max_fallback_tracks = min(len(tracks), 5)  # Allow up to 5 tracks in fallback
        success_count = 0
        for i, track in enumerate(tracks[:max_fallback_tracks]):
            if interrupted:
                break
            
            # Create basic track search query (post-filtering will handle quality)
            track_search_query = f'"{artist_name}" "{track["title"]}"'
            logging.info(f"      🔍 Searching: \"{track_search_query}\"")
            
            if queue_single_search(track_search_query, "track", artist_name, album_title, track['title']):
                success_count += 1
                logging.info(f"         ✅ Queued")
            else:
                logging.info(f"         ❌ Failed")
            
            if i < min(max_fallback_tracks, len(tracks)) - 1:
                time.sleep(1)
        
        # Log fallback results
        if len(tracks) > max_fallback_tracks:
            logging.info(f"   ⚠️  Note: Only tried {max_fallback_tracks} of {len(tracks)} tracks in fallback mode")
        
        STATS['tracks_queued'] += success_count
        STATS['tracks_failed'] += (len(tracks) - success_count)
        
        return success_count > 0

def wait_for_search_to_complete(search_id: str, search_query: str, max_wait_time: int = 30) -> bool:
    """Wait for search to complete by checking search status"""
    logging.debug(f"      ⏳ Waiting for search {search_id} to complete...")
    
    headers = {'X-API-Key': CONFIG['slskd_api_key']}
    search_url = f"{CONFIG['slskd_url']}/api/v0/searches/{search_id}"
    
    for attempt in range(max_wait_time):
        try:
            response = requests.get(search_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                search_data = response.json()
                is_complete = search_data.get('isComplete', False)
                state = search_data.get('state', 'Unknown')
                response_count = search_data.get('responseCount', 0)
                
                if is_complete:
                    logging.info(f"      ✅ Search completed after {attempt + 1}s with {response_count} responses")
                    return True
                # Only log progress every 10 seconds to reduce spam
                elif attempt > 0 and attempt % 10 == 0:
                    logging.info(f"      ⏳ Search still running... state: {state} ({attempt + 1}s)")
            else:
                logging.debug(f"      ⚠️ HTTP {response.status_code}: {response.text[:100]}")
            
        except Exception as e:
            logging.debug(f"      ⚠️ Error checking search status: {e}")
        
        time.sleep(1)
    
    logging.info(f"      ⏰ Search timed out after {max_wait_time}s, proceeding anyway")
    return True  # Proceed even if we didn't get completion

def queue_album_with_specific_tracks(search_query: str, artist_name: str, album_title: str, missing_tracks: List[Dict]) -> bool:
    """Search for album and queue only the specific tracks we need"""
    try:
        # Search for the album
        url = f"{CONFIG['slskd_url']}/api/v0/searches"
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': CONFIG['slskd_api_key']
        }
        data = {
            'searchText': search_query,
            'timeout': 30000
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=35)
        if response.status_code != 200:
            return False
        
        search_response = response.json()
        search_id = search_response.get('id')
        
        if not search_id:
            return False
        
        # Wait for search to complete
        wait_for_search_to_complete(search_id, search_query)
        
        # Get search results
        url = f"{CONFIG['slskd_url']}/api/v0/searches/{search_id}/responses"
        headers = {'X-API-Key': CONFIG['slskd_api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return False
        
        results = response.json()
        logging.debug(f"      🔍 Final response check: {len(results) if results else 0} responses")
        
        # Find best album match using our intelligent filtering
        candidates = find_best_candidates(results, artist_name, album_title)
        
        if not candidates:
            logging.info(f"   ❌ No suitable album candidates found from {len(results)} responses")
            logging.info(f"      📊 Check find_best_candidates output above for filtering details")
            logging.info(f"      💡 Possible issues:")
            logging.info(f"         - Unwanted keyword filtering (remix, live, etc.)")
            logging.info(f"         - Artist/album name matching too strict") 
            logging.info(f"         - File size requirements")
            return False
        
        # Try the best candidate, but queue only needed tracks
        username, filename = candidates[0]
        logging.info(f"      Selected album from: {username}")
        logging.info(f"      Album file: {filename}")
        
        return attempt_selective_download(username, filename, results, missing_tracks, artist_name)
        
    except Exception as e:
        logging.debug(f"      Error in album search: {e}")
        return False

def attempt_selective_download(username: str, filename: str, results, missing_tracks: List[Dict], artist_name: str) -> bool:
    """Attempt to download from a specific user, queuing only the needed tracks from the album"""
    try:
        logging.debug(f"      🔍 Checking {len(missing_tracks)} tracks before downloading...")
        
        # First, check if tracks are already in downloads folder
        tracks_not_downloaded = check_downloads_folder(missing_tracks, artist_name)
        
        if not tracks_not_downloaded:
            logging.info(f"      ✅ All tracks already in downloads folder, skipping")
            return True
        
        if len(tracks_not_downloaded) < len(missing_tracks):
            already_downloaded = len(missing_tracks) - len(tracks_not_downloaded)
            logging.info(f"      📁 {already_downloaded} tracks already downloaded, processing {len(tracks_not_downloaded)} remaining")
        
        # Second, filter out tracks that are already in the download queue
        tracks_to_download = check_download_queue(tracks_not_downloaded)
        
        if not tracks_to_download:
            logging.info(f"      ✅ All tracks already in download queue, skipping")
            return True
        
        if len(tracks_to_download) < len(missing_tracks):
            already_queued = len(missing_tracks) - len(tracks_to_download)
            logging.info(f"      📋 {already_queued} tracks already in queue, downloading {len(tracks_to_download)} remaining")
        
        # Get the directory path from the filename
        directory = '/'.join(filename.split('/')[:-1]) if '/' in filename else ''
        logging.info(f"      📁 Checking album directory: {directory}")
        
        # Get all audio files from this user's album directory
        user_files = []
        
        for user_response in results:
            if user_response.get('username') == username:
                for file_info in user_response.get('files', []):
                    user_filename = file_info.get('filename', '')
                    user_filesize = file_info.get('size', 0)
                    
                    # Check if it's in the same album directory
                    user_directory = '/'.join(user_filename.split('/')[:-1]) if '/' in user_filename else ''
                    if user_directory == directory:
                        # Only include wanted audio files (excludes cover art, lyrics, etc.)
                        if is_audio_file_wanted(user_filename):
                            user_files.append({
                                "filename": user_filename,
                                "size": user_filesize
                            })
                        else:
                            # Log what we're excluding for debugging
                            file_ext = Path(user_filename).suffix.lower()
                            if file_ext in EXCLUDED_EXTENSIONS:
                                logging.debug(f"      🚫 Excluding non-audio file: {Path(user_filename).name}")
                break
        
        if not user_files:
            logging.info(f"      ❌ No audio files found in album directory for {username}")
            return False
        
        logging.info(f"      📁 Found {len(user_files)} audio files in album directory:")
        for i, file_info in enumerate(user_files[:5], 1):
            file_basename = file_info['filename'].split('/')[-1]
            size_mb = file_info['size'] / (1024 * 1024)
            logging.info(f"         {i}. {file_basename} ({size_mb:.1f}MB)")
        if len(user_files) > 5:
            logging.info(f"         ... and {len(user_files) - 5} more files")
        
        # Filter files to only include tracks we actually need
        # For each missing track, find the BEST matching file (not all matching files)
        needed_files = []

        for track in tracks_to_download:  # Use filtered tracks instead of all missing tracks
            track_title = track['title'].lower()
            track_number = track.get('trackNumber', 0)
            
            logging.info(f"      🎵 Looking for track #{track_number}: '{track['title']}'")
            
            # Create a unique key for this track to check against global downloads
            track_key = f"{artist_name.lower()}|{track_title}"
            
            # Skip if we've already downloaded this track from another album in THIS session
            # Only skip if we're confident it's the same track from the same user
            if track_key in DOWNLOADED_TRACKS:
                previous_download = DOWNLOADED_TRACKS[track_key]
                # Only skip if it's from the same user (to avoid blocking different versions)
                if previous_download['user'] == username:
                    logging.info(f"      ⏭️  Skipping '{track['title']}': already downloading from {previous_download['user']} in this session")
                    logging.debug(f"         Previous file: {previous_download['filename']}")
                    continue
                else:
                    logging.debug(f"      🔍 Found '{track['title']}' from different user ({username} vs {previous_download['user']}), allowing download")
            
            # Find all files that match this track
            matching_files = []
            
            logging.debug(f"         Checking {len(user_files)} files in album directory...")
            
            for file_info in user_files:
                file_path = file_info['filename']
                file_basename = file_path.split('/')[-1].lower()
                
                track_clean = track_title.replace(' ', '').replace('-', '')
                file_clean = file_basename.replace(' ', '').replace('-', '').replace('_', '')
                
                # Check if this file matches the track
                is_match = False
                
                # Extract track number from filename if present
                import re
                track_num_match = re.search(r'[\s_-](\d{1,2})[\s_-]', file_basename)
                file_track_number = int(track_num_match.group(1)) if track_num_match else 0
                
                # Priority 1: Track number match (most reliable for album downloads)
                if track_number and file_track_number and track_number == file_track_number:
                    is_match = True
                    quality_score += 50  # High bonus for track number match
                    logging.debug(f"         🎯 Track #{track_number} match: {file_basename}")
                
                # Priority 2: Title match (for files that include track names)
                def normalize_for_matching(text):
                    """Same normalization as find_best_candidates"""
                    import re
                    text = text.lower()
                    # Replace various dash types and punctuation with spaces  
                    text = re.sub(r'[‐–—-]+', ' ', text)  # Various dash types
                    text = re.sub(r'[^\w\s]', ' ', text)  # Remove all non-alphanumeric except spaces
                    # Normalize spaces
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text
                
                track_normalized = normalize_for_matching(track_title)
                file_normalized = normalize_for_matching(file_basename)
                
                # Try exact match first
                if track_normalized in file_normalized:
                    is_match = True
                    quality_score += 30  # Good bonus for title match
                    logging.debug(f"         🎯 Title match: '{track_title}' in {file_basename}")
                # Try word-based match for longer titles
                elif len(track_normalized.split()) >= 2:
                    track_words = [w for w in track_normalized.split() if len(w) > 2]
                    if track_words and all(w in file_normalized for w in track_words):
                        is_match = True
                        quality_score += 25  # Moderate bonus for word match
                        logging.debug(f"         🎯 Word match: {track_words} in {file_basename}")
                
                if is_match:
                    # Score this file to help select the best version
                    quality_score = 0
                    
                    # Unwanted version patterns - these should be avoided
                    unwanted_patterns = [
                        'remix', 'mix)', 'live', 'acoustic', 'instrumental', 
                        'karaoke', 'edit)', 'demo', 'cover', 'tribute'
                    ]
                    
                    has_unwanted = any(pattern in file_basename for pattern in unwanted_patterns)
                    if has_unwanted:
                        quality_score -= 50  # Heavy penalty for unwanted versions
                    
                    # Prefer FLAC over MP3
                    if file_path.lower().endswith('.flac'):
                        quality_score += 30
                    elif file_path.lower().endswith('.mp3'):
                        if '320' in file_basename:
                            quality_score += 20
                        elif '192' in file_basename:
                            quality_score += 10
                    
                    # Bonus for having track number in filename
                    if f"{track_number:02d}" in file_basename or f"{track_number:01d}" in file_basename:
                        quality_score += 15
                    
                    # Bonus for "original" or "album version" indicators
                    if 'original' in file_basename or 'album version' in file_basename:
                        quality_score += 25
                    
                    # Size-based scoring (prefer reasonable sizes)
                    size_mb = file_info['size'] / (1024 * 1024)
                    if 3 <= size_mb <= 50:
                        quality_score += 10
                    
                    matching_files.append({
                        'file_info': file_info,
                        'quality_score': quality_score,
                        'basename': file_basename
                    })
            
            # Select the best matching file for this track
            if matching_files:
                # Sort by quality score (descending)
                matching_files.sort(key=lambda x: x['quality_score'], reverse=True)
                
                # Log if we're filtering out multiple versions
                if len(matching_files) > 1:
                    best = matching_files[0]
                    logging.debug(f"      🎯 Track '{track['title']}': Found {len(matching_files)} versions")
                    logging.debug(f"         ✅ Selected: {best['basename']} (score: {best['quality_score']})")
                    for alt in matching_files[1:3]:  # Show top alternatives
                        logging.debug(f"         ⏭️  Skipped: {alt['basename']} (score: {alt['quality_score']})")
                else:
                    logging.debug(f"      📥 Queuing: {matching_files[0]['basename']}")
                
                # Add only the best matching file
                needed_files.append(matching_files[0]['file_info'])
                
                # Record this track in global tracking to prevent future duplicates
                track_key = f"{artist_name.lower()}|{track_title}"
                DOWNLOADED_TRACKS[track_key] = {
                    'user': username,
                    'filename': matching_files[0]['file_info']['filename']
                }
            else:
                logging.debug(f"      ⚠️  No match found for track: {track['title']}")
        
        # If we couldn't match specific tracks, queue all audio files (safer approach)
        if not needed_files:
            logging.info(f"      📥 Could not match specific tracks, queuing all {len(user_files)} audio files from album")
            needed_files = user_files
        else:
            logging.info(f"      📥 Queuing {len(needed_files)} specific tracks out of {len(user_files)} available")
        
        # Queue the selected files
        url = f"{CONFIG['slskd_url']}/api/v0/transfers/downloads/{username}"
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': CONFIG['slskd_api_key']
        }
        
        response = requests.post(url, headers=headers, json=needed_files, timeout=30)
        return response.status_code in [200, 201]
        
    except Exception as e:
        logging.debug(f"      Error downloading: {e}")
        return False

def queue_single_search(search_query: str, search_type: str, artist_name: str, album_title: str, track_title: str = None) -> bool:
    """Queue a single search in slskd with intelligent filtering"""
    try:
        # Search
        url = f"{CONFIG['slskd_url']}/api/v0/searches"
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': CONFIG['slskd_api_key']
        }
        data = {
            'searchText': search_query,
            'timeout': 30000
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=35)
        if response.status_code != 200:
            return False
        
        search_response = response.json()
        search_id = search_response.get('id')
        
        if not search_id:
            return False
        
        # Wait for search to complete
        wait_for_search_to_complete(search_id, search_query)
        
        # Get results and queue best match
        return queue_best_result(search_id, artist_name, album_title, track_title)
        
    except Exception as e:
        logging.debug(f"      Error in search: {e}")
        return False

def queue_best_result(search_id: str, artist_name: str, album_title: str, track_title: str = None) -> bool:
    """Get search results and queue the best match using intelligent filtering"""
    try:
        url = f"{CONFIG['slskd_url']}/api/v0/searches/{search_id}/responses"
        headers = {'X-API-Key': CONFIG['slskd_api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return False
        
        results = response.json()
        
        # Find best match with intelligent filtering
        candidates = find_best_candidates(results, artist_name, album_title, track_title)
        
        if not candidates:
            logging.debug(f"      No suitable candidates found after filtering")
            return False
        
        # Try the best candidate
        username, filename = candidates[0]
        logging.debug(f"      Selected best candidate: {filename.split('/')[-1]}")
        return attempt_download(username, filename, results)
        
    except Exception as e:
        logging.debug(f"      Error processing results: {e}")
        return False

def find_best_candidates(results, artist_name: str, album_title: str, track_title: str = None):
    """Find best download candidates from search results, filtering out unwanted versions"""
    candidates = []
    audio_extensions = ['.mp3', '.flac']  # Only FLAC and MP3
    
    # Debug logging
    logging.info(f"   🔍 find_best_candidates: Processing {len(results)} responses for artist='{artist_name}', album='{album_title}'")
    if track_title:
        logging.info(f"      Looking for specific track: '{track_title}'")
    
    total_files = 0
    audio_files = 0
    filtered_out = 0
    
    def normalize_text_for_matching(text):
        """Normalize text by removing special characters, keeping only alphanumeric and spaces"""
        import re
        # Convert to lowercase
        text = text.lower()
        # Replace various dash types and punctuation with spaces
        text = re.sub(r'[‐–—-]+', ' ', text)  # Various dash types
        text = re.sub(r'[^\w\s]', ' ', text)  # Remove all non-alphanumeric except spaces
        # Normalize spaces
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    # Define unwanted keywords (case-insensitive)
    unwanted_keywords = [
        # Basic unwanted versions
        'instrumental', 'karaoke', 'acapella', 'a cappella',
        
        # Remix variations - comprehensive list
        'remix', 'remixed', 'remixes', 'rmx', 'mix)', 'mixed', 
        'rework', 'reworked', 'edit)', 'edited', 'version)', 
        'club mix', 'dance mix', 'radio mix', 'extended mix',
        'dub mix', 'house mix', 'techno mix', 'trance mix',
        
        # Live and acoustic versions  
        'live', 'concert', 'unplugged', 'acoustic', 'stripped',
        
        # Demo and alternate versions
        'demo', 'rehearsal', 'alternate', 'alternative', 'alt)',
        'rough', 'unfinished', 'work in progress', 'wip)',
        
        # Radio and commercial edits
        'radio edit', 'radio version', 'clean version', 'clean)', 
        'explicit', 'censored', 'uncensored', 'radio friendly',
        
        # Covers and tributes
        'cover', 'tribute', 'covers', 'mashup', 'bootleg',
        'performed by', 'sung by', 'version by',
        
        # Technical/production versions
        'stems', 'multitrack', 'isolated', 'backing track', 
        'minus one', '-1)', 'without vocals', 'vocal removed',
        
        # Special editions (less strict - only obvious ones)
        'bonus track', 'b-side', 'single edit'
    ]
    
    # Preferred quality indicators
    quality_indicators = ['flac', 'lossless', 'cd', '320', 'high quality']
    
    for user_response in results:
        username = user_response.get('username', '')
        files = user_response.get('files', [])
        
        for file_info in files:
            total_files += 1
            filename = file_info.get('filename', '').lower()
            filesize = file_info.get('size', 0)
            
            # Check if it's a wanted audio file (excludes cover art, lyrics, etc.)
            if not is_audio_file_wanted(filename):
                # Log what we're excluding if it's a common non-audio file
                file_ext = Path(filename).suffix.lower()
                if file_ext in EXCLUDED_EXTENSIONS:
                    filtered_out += 1
                    if file_ext in {'.jpg', '.jpeg', '.lrc'}:  # Log common excluded types
                        logging.debug(f"      🚫 Excluding {file_ext} file: {Path(filename).name}")
                continue
                
            audio_files += 1
            
            # Skip very small files (likely samples or low quality)
            if filesize < 1000000:  # Less than 1MB
                filtered_out += 1
                logging.debug(f"      ❌ Skipping small file: {filename.split('/')[-1]} ({filesize/1024:.0f}KB)")
                continue
            
            # Extract path components for analysis
            path_parts = filename.replace('\\', '/').split('/')
            file_basename = path_parts[-1] if path_parts else filename
            folder_path = '/'.join(path_parts[:-1]) if len(path_parts) > 1 else ''
            
            # Calculate quality score
            quality_score = 0
            
            # Check for unwanted keywords in filename and path - more comprehensive check
            unwanted_found = False
            filename_clean = filename.replace('_', ' ').replace('-', ' ')
            folder_path_clean = folder_path.replace('_', ' ').replace('-', ' ')
            
            # Check for explicit unwanted patterns
            for unwanted in unwanted_keywords:
                # Check both filename and folder path
                if (unwanted in filename_clean or unwanted in folder_path_clean):
                    # Special handling - allow "remaster" for older albums but penalize it
                    if unwanted in ['remaster', 'remastered']:
                        quality_score -= 10
                    else:
                        unwanted_found = True
                        break
            
            # Additional pattern checks for remix variations that might be missed
            remix_patterns = [
                r'\(.*remix.*\)', r'\[.*remix.*\]',  # (any remix) or [any remix]
                r'\(.*mix\)', r'\[.*mix\]',          # (any mix) or [any mix] 
                r'\(.*edit\)', r'\[.*edit\]',        # (any edit) or [any edit]
                r'\brmx\b', r'\bvs\.?\b',            # rmx or vs. (versus mixes)
                r'\bfeat\.\s+.*remix', r'\bft\.\s+.*remix'  # feat. remix or ft. remix
            ]
            
            import re
            for pattern in remix_patterns:
                if re.search(pattern, filename_clean, re.IGNORECASE):
                    unwanted_found = True
                    break
            
            if unwanted_found:
                filtered_out += 1
                logging.debug(f"      ❌ Skipping unwanted version: {file_basename}")
                continue
            
            # Check for quality indicators
            for quality in quality_indicators:
                if quality in filename_clean or quality in folder_path:
                    quality_score += 20
            
            # Heavily favor explicit "original" indicators
            original_indicators = [
                'original', 'studio version', 'album version', 'single version',
                'official', 'master', 'main version', 'standard'
            ]
            
            for indicator in original_indicators:
                if indicator in filename_clean or indicator in folder_path_clean:
                    quality_score += 30  # Strong bonus for original indicators
                    
            # Prefer files/folders that explicitly avoid unwanted terms
            if not any(term in filename_clean for term in ['remix', 'mix', 'live', 'acoustic', 'edit']):
                quality_score += 15  # Bonus for clean titles
            
            # Prefer FLAC over MP3
            if filename.endswith('.flac'):
                quality_score += 30
            elif filename.endswith('.mp3'):
                if '320' in filename_clean:
                    quality_score += 20
                elif '192' in filename_clean:
                    quality_score += 10
                elif '128' in filename_clean:
                    quality_score -= 10
            
            # Check for artist/album match in path - improved normalization
            artist_match = False
            album_match = False
            
            artist_normalized = normalize_text_for_matching(artist_name)
            album_normalized = normalize_text_for_matching(album_title)
            
            for part in path_parts:
                part_normalized = normalize_text_for_matching(part)
                
                # Check for artist match (both directions to handle partial matches)
                if artist_normalized in part_normalized or part_normalized in artist_normalized:
                    artist_match = True
                    quality_score += 15
                    
                # Check for album match (both directions to handle partial matches)
                if album_normalized in part_normalized or part_normalized in album_normalized:
                    album_match = True
                    quality_score += 15
            
            # If searching for specific track, check track match
            if track_title:
                track_normalized = normalize_text_for_matching(track_title)
                track_match = False
                file_normalized = normalize_text_for_matching(file_basename)
                
                if track_normalized in file_normalized or file_normalized in track_normalized:
                    track_match = True
                    quality_score += 25
                else:
                    # If track doesn't match, it's probably not what we want
                    quality_score -= 20
            
            # Prefer reasonable file sizes (not too small, not extremely large)
            size_mb = filesize / (1024 * 1024)
            if 3 <= size_mb <= 50:  # 3-50MB for typical songs
                quality_score += 10
            elif size_mb > 100:  # Very large files might be collections
                quality_score -= 5
            
            # Bonus for complete album structures
            if any('full album' in part.lower() or 'complete' in part.lower() for part in path_parts):
                quality_score += 5
            
            candidates.append({
                'username': username,
                'filename': file_info.get('filename'),
                'filesize': filesize,
                'quality_score': quality_score,
                'size_mb': round(size_mb, 1)
            })
    
    # Sort by quality score (descending) and return top candidates
    candidates.sort(key=lambda x: x['quality_score'], reverse=True)
    
    # Deduplicate by username - only keep the highest scored version per user
    seen_users = set()
    deduplicated_candidates = []
    
    for candidate in candidates:
        username = candidate['username']
        if username not in seen_users:
            seen_users.add(username)
            deduplicated_candidates.append(candidate)
        else:
            # Log when we skip a duplicate from the same user
            filename = candidate['filename'].split('/')[-1]
            score = candidate['quality_score']
            logging.debug(f"      ⏭️  Skipping duplicate from user '{username}': {filename} (score: {score})")
    
    # Use deduplicated candidates
    candidates = deduplicated_candidates
    
    # Summary logging
    logging.info(f"   📊 Search summary: {total_files} total files, {audio_files} audio files, {filtered_out} filtered out, {len(candidates)} final candidates")
    
    # Log top candidates for debugging
    if candidates:
        logging.info(f"   🎯 Found {len(candidates)} suitable candidates:")
        for i, candidate in enumerate(candidates[:3], 1):
            score = candidate['quality_score']
            size_mb = candidate['size_mb']
            filename = candidate['filename'].split('/')[-1]  # Just filename
            username = candidate['username']
            logging.info(f"      {i}. User: {username}, Score: {score}, Size: {size_mb}MB")
            logging.info(f"         File: {filename}")
    else:
        logging.info(f"   ❌ No suitable candidates found after filtering")
    
    # Return username and filename pairs for top candidates
    return [(c['username'], c['filename']) for c in candidates[:3]]

def attempt_download(username: str, filename: str, results) -> bool:
    """Attempt to download from a specific user"""
    try:
        # Get all files from this user for the same directory
        directory = '/'.join(filename.split('/')[:-1])
        user_files = []
        
        for user_response in results:
            if user_response.get('username') == username:
                for file_info in user_response.get('files', []):
                    user_filename = file_info.get('filename', '')
                    if directory in user_filename:
                        user_files.append({
                            "filename": user_filename,
                            "size": file_info.get('size', 0)
                        })
                break
        
        if not user_files:
            return False
        
        # Queue download
        url = f"{CONFIG['slskd_url']}/api/v0/transfers/downloads/{username}"
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': CONFIG['slskd_api_key']
        }
        
        response = requests.post(url, headers=headers, json=user_files, timeout=30)
        return response.status_code in [200, 201]
        
    except Exception as e:
        logging.debug(f"      Error downloading: {e}")
        return False

def process_albums(albums: List[Dict], dry_run: bool = False):
    """Process all wanted albums"""
    if not albums:
        logging.info("ℹ️  No albums to process")
        return
    
    logging.info(f"\n📋 Processing {len(albums)} wanted albums...")
    
    for i, album in enumerate(albums, 1):
        if interrupted:
            break
        
        try:
            album_id = album.get('id')
            album_title = album.get('title', 'Unknown Album')
            artist_info = album.get('artist', {})
            artist_name = artist_info.get('artistName', 'Unknown Artist')
            release_date = album.get('releaseDate', 'Unknown')[:10]
            monitored = album.get('monitored', False)
            
            STATS['albums_checked'] += 1
            
            # Output progress information for UI
            progress_percentage = int((i / len(albums)) * 100)
            logging.info(f"PROGRESS: [{i}/{len(albums)}] {progress_percentage}% - Processing: {artist_name} - {album_title}")
            
            logging.info(f"\n📀 Album {i}/{len(albums)}: {album_title}")
            logging.info(f"   🎤 Artist: {artist_name}")
            logging.info(f"   📅 Release: {release_date}")
            logging.info(f"   👁️  Monitored: {monitored}")
            
            if not monitored:
                logging.info("   ⏭️  Skipping (not monitored)")
                continue
            
            # Get complete track listing
            logging.info(f"PROGRESS_SUB: Getting track listing for {album_title}...")
            missing_tracks, total_tracks = get_complete_track_listing(album_id, artist_name, album_title)
            
            if total_tracks == 0:
                logging.info("   ⚠️  No track information available")
                continue
            
            if not missing_tracks:
                logging.info(f"   ✅ Complete - all {total_tracks} tracks available")
                STATS['albums_complete'] += 1
                continue
            
            logging.info(f"   📊 Missing {len(missing_tracks)}/{total_tracks} tracks")
            STATS['tracks_total'] += len(missing_tracks)
            
            # Check owned tracks
            logging.info(f"PROGRESS_SUB: Checking owned tracks for {album_title}...")
            tracks_not_owned = check_owned_tracks(missing_tracks, artist_name, album_title)
            
            if not tracks_not_owned:
                logging.info("   ✅ All missing tracks are already owned")
                STATS['albums_complete'] += 1
                continue
            
            # Check download queue
            logging.info(f"PROGRESS_SUB: Checking download queue for {album_title}...")
            tracks_to_queue = check_download_queue(tracks_not_owned)
            
            if not tracks_to_queue:
                logging.info("   ✅ All unowned tracks already in download queue")
                STATS['albums_complete'] += 1
                continue
            
            # Queue for download
            logging.info(f"PROGRESS_SUB: Queuing {len(tracks_to_queue)} tracks for {album_title}...")
            if queue_tracks_for_download(tracks_to_queue, artist_name, album_title, dry_run):
                STATS['albums_queued'] += 1
            else:
                STATS['albums_failed'] += 1
            
            # Small delay between albums
            if not dry_run and i < len(albums):
                time.sleep(2)
                
        except Exception as e:
            logging.error(f"   ❌ Error processing album {album_title}: {e}")
            logging.debug(f"   🐛 Full traceback:", exc_info=True)
            STATS['albums_failed'] += 1
            continue

def print_summary(interrupted: bool = False):
    """Print processing summary"""
    logging.info("\n" + "="*60)
    if interrupted:
        logging.info("📊 SUMMARY (Interrupted)")
    else:
        logging.info("📊 PROCESSING COMPLETE - SUMMARY")
    logging.info("="*60)
    
    logging.info(f"Albums:")
    logging.info(f"   📋 Checked: {STATS['albums_checked']}")
    logging.info(f"   ✅ Complete: {STATS['albums_complete']}")
    logging.info(f"   📥 Queued: {STATS['albums_queued']}")
    logging.info(f"   ❌ Failed: {STATS['albums_failed']}")
    
    logging.info(f"\nTracks:")
    logging.info(f"   📊 Total missing: {STATS['tracks_total']}")
    logging.info(f"   🏠 Already owned: {STATS['tracks_owned']}")
    logging.info(f"   ⏭️  Already queued: {STATS['tracks_already_queued']}")
    logging.info(f"   📥 Newly queued: {STATS['tracks_queued']}")
    logging.info(f"   ❌ Failed to queue: {STATS['tracks_failed']}")
    
    if STATS['tracks_total'] > 0:
        owned_rate = (STATS['tracks_owned'] / STATS['tracks_total']) * 100
        queued_rate = (STATS['tracks_queued'] / STATS['tracks_total']) * 100
        logging.info(f"\nEfficiency:")
        logging.info(f"   🏠 Already owned: {owned_rate:.1f}%")
        logging.info(f"   📥 Newly queued: {queued_rate:.1f}%")
    
    logging.info("="*60)

def main():
    """Main function"""
    global interrupted
    
    # Set up argument parsing
    parser = argparse.ArgumentParser(description='Queue Lidarr Monitored - Intelligent music download manager')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without queuing downloads')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging for detailed output')
    
    # Check for environment variable DRY_RUN
    if SETTINGS_AVAILABLE:
        dry_run_env = is_dry_run()
    else:
        dry_run_env = os.getenv('DRY_RUN', 'false').lower() == 'true'
    
    args = parser.parse_args()
    dry_run = args.dry_run or dry_run_env
    debug_mode = False  # Set to INFO level by default
    
    # Set up logging
    log_file = setup_logging()
    
    # Set debug level if requested
    if debug_mode:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.info("🐛 Debug mode enabled - detailed logging active")
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    if dry_run:
        logging.info("🧪 DRY RUN MODE - No downloads will be queued")
    
    logging.info("")
    
    # Get wanted albums
    logging.info("PROGRESS: [1/3] 33% - Fetching wanted albums from Lidarr")
    albums = get_wanted_albums()
    
    if not albums:
        logging.info("ℹ️  No wanted albums found")
        return
    
    # Process albums
    logging.info("PROGRESS: [2/3] 67% - Processing albums")
    process_albums(albums, dry_run)
    
    # Print summary
    logging.info("PROGRESS: [3/3] 100% - Generating final summary")
    if not interrupted:
        print_summary()
        
        if dry_run:
            logging.info("\n💡 This was a dry run - no downloads were queued")
            logging.info("   Run without --dry-run to actually queue downloads")
        else:
            logging.info("\n✅ Queue Lidarr Monitored processing complete!")
            logging.info("   🌐 Check slskd web interface for download progress")

if __name__ == "__main__":
    main()