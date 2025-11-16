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

# Supported audio extensions for owned music detection
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aiff', '.ape', '.wv'}

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
        
        # Collect queued filenames
        queued_files = set()
        for user_group in downloads_data:
            directories = user_group.get('directories', [])
            for directory in directories:
                files = directory.get('files', [])
                for file_transfer in files:
                    filename = file_transfer.get('filename', '').lower()
                    state = file_transfer.get('state', '')
                    
                    # Include active download states
                    if state in ['Queued', 'Requested', 'Initializing', 'InProgress']:
                        file_basename = filename.split('/')[-1].split('\\')[-1]
                        queued_files.add(file_basename)
        
        if not queued_files:
            return tracks_to_check
        
        # Check each track against queued files
        tracks_not_queued = []
        already_queued = []
        
        for track in tracks_to_check:
            track_title = track['title'].lower().strip()
            track_number = track.get('trackNumber', 0)
            
            # Create possible filename patterns
            possible_patterns = [
                track_title,
                f"{track_number:02d} - {track_title}",
                f"{track_number:02d}. {track_title}",
                f"{track_number:02d} {track_title}",
            ]
            
            # Check if any pattern matches queued files
            track_queued = False
            for pattern in possible_patterns:
                pattern = ''.join(c for c in pattern if c.isalnum() or c in ' -.')
                for queued_file in queued_files:
                    queued_clean = ''.join(c for c in queued_file if c.isalnum() or c in ' -.')
                    if pattern in queued_clean or queued_clean in pattern:
                        track_queued = True
                        already_queued.append(track['title'])
                        break
                if track_queued:
                    break
            
            if not track_queued:
                tracks_not_queued.append(track)
        
        if already_queued:
            logging.info(f"   ⏭️  {len(already_queued)} tracks already in download queue:")
            for track_title in already_queued[:3]:
                logging.info(f"      🎵 {track_title}")
            if len(already_queued) > 3:
                logging.info(f"      ... and {len(already_queued) - 3} more")
            
            STATS['tracks_already_queued'] += len(already_queued)
        
        return tracks_not_queued
        
    except Exception as e:
        logging.error(f"   ❌ Error checking download queue: {e}")
        return tracks_to_check

def queue_tracks_for_download(tracks: List[Dict], artist_name: str, album_title: str, dry_run: bool = False) -> bool:
    """Queue tracks for download in slskd using intelligent album-first approach"""
    if not tracks:
        return True
    
    logging.info(f"   🎯 Queuing {len(tracks)} tracks for download")
    
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
    # Create more specific search query to prioritize original versions
    album_search_query = f'"{artist_name}" "{album_title}"'
    logging.info(f"   🔍 Searching for album: \"{album_search_query}\"")
    
    success = queue_album_with_specific_tracks(album_search_query, artist_name, album_title, tracks)
    
    if success:
        STATS['tracks_queued'] += len(tracks)
        logging.info("   ✅ Album search and track selection successful")
        return True
    else:
        logging.info("   ⚠️  Album search failed, trying individual track searches as fallback...")
        
        # Fallback: Try individual tracks (limit to avoid spam)
        success_count = 0
        for i, track in enumerate(tracks[:3]):  # Limit to 3 tracks as fallback
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
            
            if i < min(3, len(tracks)) - 1:
                time.sleep(1)
        
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
                
                # Debug: log the search progress
                if attempt == 0 or attempt % 5 == 0:  # Log every 5 attempts
                    logging.debug(f"      🔍 Search state: {state}, isComplete: {is_complete}, responses: {response_count}")
                
                if is_complete:
                    logging.debug(f"      ✅ Search completed after {attempt + 1}s with {response_count} responses")
                    return True
                elif attempt % 3 == 0:  # Log every 3 seconds
                    logging.debug(f"      ⏳ Search still running... state: {state} ({attempt + 1}s)")
            else:
                logging.debug(f"      ⚠️ HTTP {response.status_code}: {response.text[:100]}")
            
        except Exception as e:
            logging.debug(f"      ⚠️ Error checking search status: {e}")
        
        time.sleep(1)
    
    logging.debug(f"      ⏰ Search timed out after {max_wait_time}s, proceeding anyway")
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
            logging.debug(f"      No suitable album candidates found")
            return False
        
        # Try the best candidate, but queue only needed tracks
        username, filename = candidates[0]
        logging.debug(f"      Selected album from: {username}")
        
        return attempt_selective_download(username, filename, results, missing_tracks)
        
    except Exception as e:
        logging.debug(f"      Error in album search: {e}")
        return False

def attempt_selective_download(username: str, filename: str, results, missing_tracks: List[Dict]) -> bool:
    """Attempt to download from a specific user, queuing only the needed tracks from the album"""
    try:
        # Get the directory path from the filename
        directory = '/'.join(filename.split('/')[:-1]) if '/' in filename else ''
        
        # Get all audio files from this user's album directory
        user_files = []
        audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aiff', '.ape', '.wv', '.aac', '.opus']
        
        for user_response in results:
            if user_response.get('username') == username:
                for file_info in user_response.get('files', []):
                    user_filename = file_info.get('filename', '')
                    user_filesize = file_info.get('size', 0)
                    
                    # Check if it's in the same album directory
                    user_directory = '/'.join(user_filename.split('/')[:-1]) if '/' in user_filename else ''
                    if user_directory == directory:
                        # Only include audio files
                        if any(user_filename.lower().endswith(ext) for ext in audio_extensions):
                            user_files.append({
                                "filename": user_filename,
                                "size": user_filesize
                            })
                break
        
        if not user_files:
            logging.debug(f"      No audio files found in album directory")
            return False
        
        # Filter files to only include tracks we actually need
        # For each missing track, find the BEST matching file (not all matching files)
        needed_files = []
        
        for track in missing_tracks:
            track_title = track['title'].lower()
            track_number = track.get('trackNumber', 0)
            
            # Find all files that match this track
            matching_files = []
            
            for file_info in user_files:
                file_path = file_info['filename']
                file_basename = file_path.split('/')[-1].lower()
                
                track_clean = track_title.replace(' ', '').replace('-', '')
                file_clean = file_basename.replace(' ', '').replace('-', '').replace('_', '')
                
                # Check if this file matches the track
                is_match = False
                
                # Try exact match first
                if track_clean in file_clean:
                    is_match = True
                # Try word-based match for longer titles
                elif len(track_title.split()) >= 3:
                    track_words = [w for w in track_title.split() if len(w) > 3]
                    if all(w in file_clean for w in track_words):
                        is_match = True
                
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
    audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac', '.opus']
    
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
            filename = file_info.get('filename', '').lower()
            filesize = file_info.get('size', 0)
            
            # Check if it's an audio file
            if not any(filename.endswith(ext) for ext in audio_extensions):
                continue
            
            # Skip very small files (likely samples or low quality)
            if filesize < 1000000:  # Less than 1MB
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
            
            # Check for artist/album match in path
            artist_match = False
            album_match = False
            
            artist_clean = artist_name.lower().replace(' ', '')
            album_clean = album_title.lower().replace(' ', '')
            
            for part in path_parts:
                part_clean = part.lower().replace(' ', '').replace('_', '').replace('-', '')
                if artist_clean in part_clean or part_clean in artist_clean:
                    artist_match = True
                    quality_score += 15
                if album_clean in part_clean or part_clean in album_clean:
                    album_match = True
                    quality_score += 15
            
            # If searching for specific track, check track match
            if track_title:
                track_clean = track_title.lower().replace(' ', '')
                track_match = False
                file_clean = file_basename.replace(' ', '').replace('_', '').replace('-', '')
                
                if track_clean in file_clean or file_clean in track_clean:
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
    
    # Log top candidates for debugging
    if candidates:
        logging.debug(f"      🎯 Top candidates:")
        for i, candidate in enumerate(candidates[:3], 1):
            score = candidate['quality_score']
            size_mb = candidate['size_mb']
            filename = candidate['filename'].split('/')[-1]  # Just filename
            logging.debug(f"         {i}. Score: {score}, Size: {size_mb}MB, File: {filename}")
    
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
    debug_mode = True  # Enable debug mode by default
    
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