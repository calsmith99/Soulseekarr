#!/usr/bin/env python3
"""
Queue Wanted Albums - Enhanced Python Version with Comprehensive Cleanup

Connects to Lidarr API to find wanted/monitored albums and queues them in slskd.
Features comprehensive cleanup, caching, better file selection, detailed logging, 
and progress tracking with duplicate detection.

Name: Queue Wanted Albums
Author: SoulSeekarr
Version: 2.0
Section: commands
Tags: lidarr, slskd, downloads, automation
Supports dry run: true

Cleanup features:
- Remove all completed downloads (remotely via API)
- Cancel ongoing downloads at startup  
- Clear stuck/queued downloads remotely
- Clear incomplete downloads folder
- Clear completed downloads folder
- Check for already completed downloads to avoid duplicates
- Handle stuck/errored downloads
"""

import os
import sys
import json
import time
import signal
import logging
import shutil
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path
import argparse

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
    from settings import (get_lidarr_config, get_slskd_config, is_dry_run, 
                         get_slskd_downloads_complete_path, get_slskd_downloads_incomplete_path, 
                         get_slskd_downloads_path)
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

# Only configure HTTP logging if requests is available
if REQUESTS_AVAILABLE:
    # Enable detailed HTTP logging
    import http.client as http_client
    http_client.HTTPConnection.debuglevel = 1

    # Configure requests logging
    logging.getLogger("requests.packages.urllib3").setLevel(logging.DEBUG)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.DEBUG)

# Global variables for graceful shutdown
ALBUMS_CHECKED = 0
ALBUMS_SKIPPED_EXISTING = 0
ALBUMS_SKIPPED_UNMONITORED = 0
ALBUMS_QUEUED = 0
ALBUMS_FAILED = 0
ALBUMS_SKIPPED_ALREADY_QUEUED = 0
interrupted = False

# Global configuration variables (populated in main)
LIDARR_CONFIG = {}
SLSKD_CONFIG = {}

def log_download_request(method, url, data=None, response=None):
    """Simple logging for download requests"""
    if response is None:
        return  # Skip request logging
    else:
        # Only log the result - accept both 200 OK and 201 Created
        if response.status_code in [200, 201]:
            logging.info(f"   ✅ Download queued successfully")
        else:
            logging.warning(f"   ⚠️  Download failed: HTTP {response.status_code}")

def log_raw_request_response(method, url, headers=None, data=None, response=None):
    """Minimal logging for debugging"""
    if response and response.status_code != 200:
        logging.debug(f"❌ {method} {url} → {response.status_code}")

def setup_logging():
    """Set up logging with timestamps"""
    # Create logs directory
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # Create log file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"queue_wanted_albums_{timestamp}.log")
    
    # Configure logging with detailed format for debugging
    logging.basicConfig(
        level=logging.DEBUG,  # Changed to DEBUG for maximum detail
        format='%(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Log the start
    logging.info("🚀 STARTING QUEUE WANTED ALBUMS WITH FULL DEBUG LOGGING")
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

def print_summary(interrupted=False):
    """Print processing summary"""
    logging.info("==================================================")
    if interrupted:
        logging.info("📊 Summary before interruption:")
    else:
        logging.info("📊 Want List Processing Summary:")
    logging.info(f"   📋 Albums checked: {ALBUMS_CHECKED}")
    logging.info(f"   ✅ Skipped (complete): {ALBUMS_SKIPPED_EXISTING}")
    logging.info(f"   🔄 Skipped (already queued): {ALBUMS_SKIPPED_ALREADY_QUEUED}")
    logging.info(f"   ⏭️  Skipped (not monitored): {ALBUMS_SKIPPED_UNMONITORED}")
    logging.info(f"   📥 Queued for download: {ALBUMS_QUEUED}")
    logging.info(f"   ❌ Failed to queue: {ALBUMS_FAILED}")
    logging.info("")

def check_environment():
    """Check required configuration from settings or environment variables"""
    
    if not REQUESTS_AVAILABLE:
        logging.error("✗ Missing required dependency: requests")
        logging.error("   Install with: pip install requests")
        return False
    
    if SETTINGS_AVAILABLE:
        # Get configuration from settings module
        lidarr_config = get_lidarr_config()
        slskd_config = get_slskd_config()
        
        missing_configs = []
        
        if not lidarr_config.get('url'):
            missing_configs.append("Lidarr URL (set in web interface Settings or LIDARR_URL env var)")
        if not lidarr_config.get('api_key'):
            missing_configs.append("Lidarr API key (set in web interface Settings or LIDARR_API_KEY env var)")
        if not slskd_config.get('url'):
            missing_configs.append("slskd URL (set in web interface Settings or SLSKD_URL env var)")
        if not slskd_config.get('api_key'):
            missing_configs.append("slskd API key (set in web interface Settings or SLSKD_API_KEY env var)")
        
        if missing_configs:
            logging.error("✗ Missing required configuration:")
            for config in missing_configs:
                logging.error(f"   - {config}")
            return False
        
        logging.info("🔧 Using configuration from settings")
        logging.info(f"   📍 Lidarr: {lidarr_config['url']}")
        logging.info(f"   📍 slskd: {slskd_config['url']}")
        
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
        
        logging.info("🔧 Using environment variables")
        # Note: These are fallback environment variables when settings module unavailable
        logging.info(f"   📍 Lidarr: {os.getenv('LIDARR_URL', 'Not set')}")
        logging.info(f"   📍 slskd: {os.getenv('SLSKD_URL', 'Not set')}")
    
    return True

def get_missing_tracks_for_album(album_id):
    """
    Get list of missing tracks for an album from Lidarr.
    Returns tuple: (missing_tracks_list, total_tracks_count)
    """
    try:
        # Get album details with tracks
        url = f"{LIDARR_CONFIG['url']}/api/v1/album/{album_id}"
        params = {'apikey': LIDARR_CONFIG['api_key']}
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            logging.warning(f"   ⚠️  Could not get album details for ID {album_id}")
            return [], 0
        
        album_data = response.json()
        media = album_data.get('media', [])
        missing_tracks = []
        total_tracks = 0
        
        for disc in media:
            tracks = disc.get('tracks', [])
            total_tracks += len(tracks)
            
            for track in tracks:
                # Check if track has a file
                has_file = track.get('hasFile', False)
                if not has_file:
                    track_info = {
                        'id': track.get('id'),
                        'title': track.get('title', 'Unknown Track'),
                        'trackNumber': track.get('trackNumber', 0),
                        'discNumber': disc.get('discNumber', 1),
                        'duration': track.get('duration', 0),
                        'artist': album_data.get('artist', {}).get('artistName', 'Unknown Artist'),
                        'album': album_data.get('title', 'Unknown Album')
                    }
                    missing_tracks.append(track_info)
        
        return missing_tracks, total_tracks
        
    except Exception as e:
        logging.error(f"   ❌ Error getting missing tracks for album {album_id}: {e}")
        return [], 0

def get_album_tracks_from_musicbrainz(album_id):
    """
    Get complete track listing for an album from MusicBrainz via Lidarr.
    Returns list of track information that can be added to Lidarr.
    """
    try:
        # First get the album info from Lidarr to get MusicBrainz ID
        url = f"{LIDARR_CONFIG['url']}/api/v1/album/{album_id}"
        params = {'apikey': LIDARR_CONFIG['api_key']}
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            logging.warning(f"   ⚠️  Could not get album details for track lookup")
            return []
        
        album_data = response.json()
        mb_id = album_data.get('foreignAlbumId')  # MusicBrainz ID
        
        if not mb_id:
            logging.warning(f"   ⚠️  Album has no MusicBrainz ID for track lookup")
            return []
        
        # Refresh album data from MusicBrainz to get complete track listing
        refresh_url = f"{LIDARR_CONFIG['url']}/api/v1/album/{album_id}"
        refresh_data = {
            'id': album_id,
            'foreignAlbumId': mb_id,
            'monitored': album_data.get('monitored', True)
        }
        headers = {'X-Api-Key': LIDARR_CONFIG['api_key'], 'Content-Type': 'application/json'}
        
        # Trigger a refresh to get updated track info from MusicBrainz
        logging.info(f"   🔄 Refreshing album metadata from MusicBrainz...")
        
        # Use the refresh command endpoint
        refresh_command_url = f"{LIDARR_CONFIG['url']}/api/v1/command"
        refresh_command_data = {
            'name': 'RefreshAlbum',
            'albumId': album_id
        }
        
        refresh_response = requests.post(refresh_command_url, 
                                       headers=headers, 
                                       json=refresh_command_data, 
                                       timeout=30)
        
        if refresh_response.status_code in [200, 201]:
            # Wait a bit for the refresh to complete
            import time
            logging.info(f"   ⏳ Waiting for metadata refresh to complete...")
            time.sleep(8)  # Increased wait time for MusicBrainz to process
            
            # Now get the updated album data
            updated_response = requests.get(url, params=params, timeout=30)
            if updated_response.status_code == 200:
                updated_album_data = updated_response.json()
                media = updated_album_data.get('media', [])
                
                track_list = []
                for disc in media:
                    tracks = disc.get('tracks', [])
                    for track in tracks:
                        track_info = {
                            'id': track.get('id'),
                            'title': track.get('title', 'Unknown Track'),
                            'trackNumber': track.get('trackNumber', 0),
                            'discNumber': disc.get('discNumber', 1),
                            'duration': track.get('duration', 0),
                            'hasFile': track.get('hasFile', False)
                        }
                        track_list.append(track_info)
                
                if track_list:
                    logging.info(f"   ✅ Retrieved {len(track_list)} tracks from MusicBrainz")
                    return track_list
                else:
                    logging.warning(f"   ⚠️  No tracks found after MusicBrainz refresh")
                    return []
            else:
                logging.warning(f"   ⚠️  Could not get updated album data after refresh")
                return []
        else:
            logging.warning(f"   ⚠️  Failed to trigger MusicBrainz refresh")
            return []
        
    except Exception as e:
        logging.error(f"   ❌ Error getting track listing from MusicBrainz: {e}")
        return []

def check_tracks_in_download_queue(missing_tracks, artist_name, album_title):
    """
    Check if any of the missing tracks are already in the download queue.
    Returns a list of tracks that are NOT already queued.
    """
    try:
        # Get current downloads from slskd
        url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.warning("   ⚠️  Could not fetch current download queue - proceeding with all tracks")
            return missing_tracks
        
        downloads_data = response.json()
        
        # Collect all filenames currently in queue (any state except completed)
        queued_files = set()
        for user_group in downloads_data:
            directories = user_group.get('directories', [])
            for directory in directories:
                files = directory.get('files', [])
                for file_transfer in files:
                    filename = file_transfer.get('filename', '').lower()
                    state = file_transfer.get('state', '')
                    
                    # Include queued, downloading, and requested states
                    if state in ['Queued', 'Requested', 'Initializing', 'InProgress']:
                        # Extract just the filename without path for comparison
                        file_basename = filename.split('/')[-1].split('\\')[-1]
                        queued_files.add(file_basename)
        
        if not queued_files:
            logging.debug("   ℹ️  No files currently in download queue")
            return missing_tracks
        
        # Check each missing track against the queue
        tracks_to_queue = []
        already_queued = []
        
        for track in missing_tracks:
            track_title = track['title']
            artist_name_clean = artist_name.replace('/', '_').replace('\\', '_')  # Safe filename
            
            # Create possible filename patterns to match
            possible_patterns = [
                f"{track['trackNumber']:02d}",  # Track number
                track_title.lower(),             # Track title
                f"{track['trackNumber']:02d} - {track_title}".lower(),  # Number - Title
                f"{track['trackNumber']:02d}. {track_title}".lower(),   # Number. Title
                f"{artist_name_clean} - {track_title}".lower(),         # Artist - Title
            ]
            
            # Check if any queued file matches this track
            track_already_queued = False
            for queued_file in queued_files:
                for pattern in possible_patterns:
                    # Remove special characters for comparison
                    pattern_clean = ''.join(c for c in pattern if c.isalnum() or c in ' -.')
                    queued_clean = ''.join(c for c in queued_file if c.isalnum() or c in ' -.')
                    
                    if pattern_clean in queued_clean or queued_clean in pattern_clean:
                        track_already_queued = True
                        already_queued.append(track_title)
                        break
                
                if track_already_queued:
                    break
            
            if not track_already_queued:
                tracks_to_queue.append(track)
        
        # Log results
        if already_queued:
            logging.info(f"   ⏭️  Skipping {len(already_queued)} tracks already in download queue:")
            for track_title in already_queued[:3]:  # Show first 3
                logging.info(f"      🎵 {track_title}")
            if len(already_queued) > 3:
                logging.info(f"      ... and {len(already_queued) - 3} more")
        
        if tracks_to_queue:
            logging.info(f"   📥 {len(tracks_to_queue)} tracks need to be queued")
        else:
            logging.info("   ℹ️  All tracks are already in download queue")
        
        return tracks_to_queue
        
    except Exception as e:
        logging.error(f"   ❌ Error checking download queue: {e}")
        logging.info("   ⚠️  Proceeding with all tracks due to queue check error")
        return missing_tracks

def get_wanted_albums():
    """Get wanted albums from Lidarr"""
    global ALBUMS_CHECKED, ALBUMS_SKIPPED_EXISTING, ALBUMS_SKIPPED_UNMONITORED, ALBUMS_QUEUED, ALBUMS_FAILED, ALBUMS_SKIPPED_ALREADY_QUEUED
    
    logging.info("🔍 Fetching wanted albums from Lidarr...")
    
    try:
        # Get all albums that are monitored and not downloaded
        url = f"{LIDARR_CONFIG['url']}/api/v1/wanted/missing"
        params = {
            'apikey': LIDARR_CONFIG['api_key'],
            'page': 1,
            'pageSize': 1000,
            'sortKey': 'releaseDate',
            'sortDirection': 'descending'
        }
        
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 200:
            logging.error("✗ Failed to fetch wanted albums from Lidarr API")
            return False
        
        wanted_data = response.json()
        total_records = wanted_data.get('totalRecords', 0)
        
        if total_records == 0:
            logging.info("ℹ️  No wanted albums found in Lidarr")
            return True
        
        logging.info(f"📋 Found {total_records} wanted albums in Lidarr")
        logging.info("")
        
        albums = wanted_data.get('records', [])
        
        for i, album in enumerate(albums, 1):
            if interrupted:
                break
                
            album_id = album.get('id')
            album_title = album.get('title', 'Unknown Album')
            artist_info = album.get('artist', {})
            artist_name = artist_info.get('artistName', 'Unknown Artist')
            release_date = album.get('releaseDate', 'Unknown')[:10]  # Just the date part
            monitored = album.get('monitored', False)
            
            if not album_id:
                continue
            
            ALBUMS_CHECKED += 1
            
            logging.info(f"\n📋 Processing album {i}/{total_records}")
            logging.info(f"🎵 Album: {album_title}")
            logging.info(f"   🎤 Artist: {artist_name}")
            logging.info(f"   📅 Release Date: {release_date}")
            logging.info(f"   👁️  Monitored: {monitored}")
            
            if monitored:
                # Get missing tracks for this album first
                missing_tracks, total_tracks = get_missing_tracks_for_album(album_id)
                
                # Check different cases
                if total_tracks == 0:
                    logging.info("   ⚠️  Album has no track information in Lidarr")
                    logging.info("   🔄 Attempting to get track listing from MusicBrainz...")
                    
                    # Try to get complete track listing from MusicBrainz
                    track_list = get_album_tracks_from_musicbrainz(album_id)
                    
                    if track_list:
                        # We now have track information, create missing tracks list
                        missing_tracks_from_mb = []
                        for track in track_list:
                            if not track.get('hasFile', False):
                                track_info = {
                                    'id': track.get('id'),
                                    'title': track.get('title', 'Unknown Track'),
                                    'trackNumber': track.get('trackNumber', 0),
                                    'discNumber': track.get('discNumber', 1),
                                    'duration': track.get('duration', 0),
                                    'artist': artist_name,
                                    'album': album_title
                                }
                                missing_tracks_from_mb.append(track_info)
                        
                        if missing_tracks_from_mb:
                            total_tracks_mb = len(track_list)
                            missing_count_mb = len(missing_tracks_from_mb)
                            logging.info(f"   📊 Found {missing_count_mb}/{total_tracks_mb} missing tracks from MusicBrainz")
                            
                            # Check which tracks are not already in download queue
                            tracks_to_queue = check_tracks_in_download_queue(missing_tracks_from_mb, artist_name, album_title)
                            
                            if not tracks_to_queue:
                                logging.info("   ✅ All missing tracks already in download queue - skipping")
                                ALBUMS_SKIPPED_ALREADY_QUEUED += 1
                            else:
                                tracks_needed = len(tracks_to_queue)
                                logging.info(f"   🎯 Queuing {tracks_needed} tracks for download")
                                
                                # Log some details about tracks to queue
                                if tracks_needed <= 5:
                                    for track in tracks_to_queue:
                                        disc_info = f" (Disc {track['discNumber']})" if track['discNumber'] > 1 else ""
                                        logging.info(f"      🎵 Track {track['trackNumber']}{disc_info}: {track['title']}")
                                else:
                                    # Just show first few tracks
                                    for track in tracks_to_queue[:3]:
                                        disc_info = f" (Disc {track['discNumber']})" if track['discNumber'] > 1 else ""
                                        logging.info(f"      🎵 Track {track['trackNumber']}{disc_info}: {track['title']}")
                                    logging.info(f"      ... and {tracks_needed - 3} more tracks")
                                
                                # Queue missing tracks for download via slskd
                                if queue_missing_tracks_in_slskd(album_id, artist_name, album_title, tracks_to_queue):
                                    ALBUMS_QUEUED += 1
                                else:
                                    ALBUMS_FAILED += 1
                        else:
                            logging.info(f"   ✅ All {len(track_list)} tracks from MusicBrainz are already downloaded")
                            ALBUMS_SKIPPED_EXISTING += 1
                    else:
                        # Fallback to full album search if MusicBrainz lookup fails
                        logging.info("   ⚠️  Could not get track listing from MusicBrainz - falling back to album search")
                        if queue_missing_tracks_in_slskd(album_id, artist_name, album_title, [], dry_run=False):
                            ALBUMS_QUEUED += 1
                        else:
                            ALBUMS_FAILED += 1
                elif not missing_tracks:
                    logging.info(f"   ✅ Album is complete - all {total_tracks} tracks downloaded")
                    ALBUMS_SKIPPED_EXISTING += 1
                elif check_completed_downloads(artist_name, album_title):
                    logging.info("   ✅ Album found in completed downloads - skipping")
                    ALBUMS_SKIPPED_EXISTING += 1
                else:
                    # Album has missing tracks, check if any are already queued
                    missing_count = len(missing_tracks)
                    logging.info(f"   📊 Found {missing_count}/{total_tracks} missing tracks")
                    
                    # Check which tracks are not already in download queue
                    tracks_to_queue = check_tracks_in_download_queue(missing_tracks, artist_name, album_title)
                    
                    if not tracks_to_queue:
                        logging.info("   ✅ All missing tracks already in download queue - skipping")
                        ALBUMS_SKIPPED_ALREADY_QUEUED += 1
                    else:
                        tracks_needed = len(tracks_to_queue)
                        logging.info(f"   🎯 Queuing {tracks_needed} tracks for download")
                        
                        # Log some details about tracks to queue
                        if tracks_needed <= 5:
                            for track in tracks_to_queue:
                                disc_info = f" (Disc {track['discNumber']})" if track['discNumber'] > 1 else ""
                                logging.info(f"      🎵 Track {track['trackNumber']}{disc_info}: {track['title']}")
                        else:
                            # Just show first few tracks
                            for track in tracks_to_queue[:3]:
                                disc_info = f" (Disc {track['discNumber']})" if track['discNumber'] > 1 else ""
                                logging.info(f"      🎵 Track {track['trackNumber']}{disc_info}: {track['title']}")
                            logging.info(f"      ... and {tracks_needed - 3} more tracks")
                        
                        # Queue missing tracks for download via slskd
                        if queue_missing_tracks_in_slskd(album_id, artist_name, album_title, tracks_to_queue):
                            ALBUMS_QUEUED += 1
                        else:
                            ALBUMS_FAILED += 1
            else:
                logging.info("   ⏭️  Skipping (not monitored)")
                ALBUMS_SKIPPED_UNMONITORED += 1
            
            # Add a small delay between albums to be nice to slskd
            if monitored and not interrupted:
                logging.info("   ⏳ Waiting 2 seconds before next album...")
                time.sleep(2)
        
        return True
        
    except requests.RequestException as e:
        logging.error(f"✗ Network error fetching wanted albums: {e}")
        return False
    except Exception as e:
        logging.error(f"✗ Unexpected error fetching wanted albums: {e}")
        return False

def queue_missing_tracks_in_slskd(album_id, artist_name, album_title, missing_tracks, dry_run=False):
    """Queue specific missing tracks for download in slskd"""
    
    # Handle case where album has no track information - search for full album
    if not missing_tracks:
        logging.info(f"   🎯 Searching for complete album: {album_title}")
        
        if dry_run:
            logging.info(f"   [DRY RUN] Would search slskd for complete album: \"{artist_name} {album_title}\"")
            return True
        
        # Search for the full album only
        album_search_query = f"{artist_name} {album_title}"
        logging.info(f"   🔍 Searching for album: \"{album_search_query}\"")
        
        return queue_single_search_in_slskd(album_search_query, "album")
    
    total_missing = len(missing_tracks)
    logging.info(f"   🎯 Queuing {total_missing} missing tracks from: {album_title}")
    
    if dry_run:
        logging.info(f"   [DRY RUN] Would search slskd for {total_missing} missing tracks")
        for track in missing_tracks[:3]:  # Show first 3 tracks
            logging.info(f"      [DRY RUN] Would search: \"{artist_name} {track['title']}\"")
        if total_missing > 3:
            logging.info(f"      [DRY RUN] ... and {total_missing - 3} more tracks")
        return True
    
    success_count = 0
    
    # Strategy 1: Try searching for the full album first (might get all tracks at once)
    album_search_query = f"{artist_name} {album_title}"
    logging.info(f"   🔍 First trying album search: \"{album_search_query}\"")
    
    if queue_single_search_in_slskd(album_search_query, "album"):
        success_count += 1
        logging.info("   ✓ Album search queued successfully")
    else:
        logging.info("   ⚠️  Album search failed, trying individual tracks...")
        
        # Strategy 2: Search for individual tracks if album search fails
        for i, track in enumerate(missing_tracks[:5], 1):  # Limit to first 5 tracks to avoid spam
            if interrupted:
                break
                
            track_search_query = f"{artist_name} {track['title']}"
            logging.info(f"   🔍 Searching for track {i}/{min(5, total_missing)}: \"{track['title']}\"")
            
            if queue_single_search_in_slskd(track_search_query, "track"):
                success_count += 1
                logging.info(f"      ✓ Track search queued: {track['title']}")
            else:
                logging.info(f"      ✗ Failed to queue: {track['title']}")
            
            # Small delay between track searches
            if i < min(5, total_missing):
                time.sleep(1)
        
        if total_missing > 5:
            logging.info(f"   ℹ️  Limited to first 5 tracks. {total_missing - 5} tracks not searched individually.")
    
    return success_count > 0

def queue_single_search_in_slskd(search_query, search_type="unknown"):
    """Queue a single search in slskd and attempt to download results"""
    try:
        # Search slskd
        url = f"{SLSKD_CONFIG['url']}/api/v0/searches"
        headers = {
            'Content-Type': 'application/json',
            'X-API-Key': SLSKD_CONFIG['api_key']
        }
        data = {
            'searchText': search_query,
            'timeout': 30000
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=35)
        if response.status_code != 200:
            logging.error(f"      ✗ Failed to search slskd for {search_type}")
            return False
        
        search_response = response.json()
        search_id = search_response.get('id')
        
        if not search_id:
            logging.error(f"      ✗ Failed to get search ID from slskd for {search_type}")
            return False
        
        # Wait a bit for search to populate
        time.sleep(3)
        
        # Get search results and try to queue the best match
        return queue_best_search_result(search_id, search_query, search_type)
    
    except Exception as e:
        logging.error(f"      ❌ Error searching for {search_type}: {e}")
        return False

def queue_best_search_result(search_id, search_query, search_type):
    """Get search results and queue the best match"""
    try:
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        # Get search responses
        url = f"{SLSKD_CONFIG['url']}/api/v0/searches/{search_id}/responses"
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logging.error(f"      ✗ Failed to get search responses for {search_type}")
            return False
        
        results = response.json()
        
        # Find best matches (use existing function but limit results)
        candidates = find_best_matches(results, max_candidates=1)  # Just get the best one
        
        if not candidates:
            logging.info(f"      ℹ️  No suitable matches found for {search_type}")
            return False
        
        # Try the best candidate
        username, filename, filesize = candidates[0]
        logging.info(f"      🎯 Found match from user: {username}")
        
        # Try to download
        success = attempt_download_from_user(username, filename, results)
        
        if success:
            logging.info(f"      ✅ Successfully queued {search_type}")
            return True
        else:
            logging.info(f"      ⚠️  Failed to queue {search_type}")
            return False
            
    except Exception as e:
        logging.error(f"      ❌ Error processing {search_type} results: {e}")
        return False

def queue_album_in_slskd(album_id, artist_name, album_title, dry_run=False):
    """Queue album for download in slskd (legacy function - now redirects to missing tracks)"""
    # Get missing tracks and use the new targeted approach
    missing_tracks, total_tracks = get_missing_tracks_for_album(album_id)
    return queue_missing_tracks_in_slskd(album_id, artist_name, album_title, missing_tracks, dry_run)

def attempt_download_from_user(username, filename, results):
    """Attempt to download an album from a specific user"""
    directory = get_directory_path(filename)
    
    # Get all files from this user for this album directory
    # Use the search results we already have instead of making another API call
    user_files = []
    audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aiff', '.ape', '.wv']
    
    for user_response in results:
        if user_response.get('username') == username:
            # Get all files from this user's response
            for file_info in user_response.get('files', []):
                user_filename = file_info.get('filename', '')
                user_filesize = file_info.get('size', 0)
                
                # Check if it's likely part of the same album directory
                user_dir = get_directory_path(user_filename)
                if user_dir == directory:
                    # Only include audio files - skip images, NFO, LRC, etc.
                    if any(user_filename.lower().endswith(ext) for ext in audio_extensions):
                        user_files.append({
                            "filename": user_filename,
                            "size": user_filesize
                        })
                    else:
                        # Log what we're skipping for transparency
                        file_ext = os.path.splitext(user_filename)[1].lower()
                        logging.debug(f"      ⏭️  Skipping non-audio file: {os.path.basename(user_filename)} ({file_ext})")
            
            break  # Found the user, no need to continue
    
    if not user_files:
        logging.warning("   ⚠️  No audio files found for this album")
        return False
    
    total_files_found = len([f for user_resp in results if user_resp.get('username') == username 
                            for f in user_resp.get('files', []) 
                            if get_directory_path(f.get('filename', '')) == directory])
    audio_files_count = len(user_files)
    
    if total_files_found > audio_files_count:
        skipped_count = total_files_found - audio_files_count
        logging.info(f"   📋 Found {audio_files_count} audio files for download ({skipped_count} non-audio files skipped)")
    else:
        logging.info(f"   📋 Found {audio_files_count} audio files for download")
    
    # Use the actual endpoint discovered from UI network tab
    enqueue_url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads/{username}"
    enqueue_headers = {
        'Content-Type': 'application/json',
        'X-API-Key': SLSKD_CONFIG['api_key']
    }
    
    # Payload is just the files array, not wrapped in an object
    enqueue_data = user_files
    
    # Debug: show what we're sending
    logging.info(f"   🔧 Sending {len(enqueue_data)} files to {enqueue_url}")
    logging.info(f"   🔧 Sample payload: {enqueue_data[0] if enqueue_data else 'No files'}")
    
    try:
        log_download_request("POST", enqueue_url, enqueue_data)
        enqueue_response = requests.post(enqueue_url, headers=enqueue_headers, json=enqueue_data, timeout=30)
        log_download_request("POST", enqueue_url, enqueue_data, enqueue_response)
        
        if enqueue_response.status_code in [200, 201]:  # Accept both 200 OK and 201 Created
            logging.info("   ✅ Album queued for download!")
            return True
        else:
            logging.error(f"   ⚠️  Failed to queue download (HTTP {enqueue_response.status_code})")
            # Show server response for debugging
            try:
                error_detail = enqueue_response.text[:200] if enqueue_response.text else "No response body"
                logging.error(f"   🔧 Server response: {error_detail}")
            except:
                logging.error("   🔧 Could not read server response")
            
            # Try alternative: individual file downloads
            logging.info("   🔄 Trying individual file downloads...")
            success_count = 0
            
            for file_info in user_files:
                single_file_data = [file_info]  # Just the file in an array
                
                single_url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads/{username}"
                log_download_request("POST", single_url, single_file_data)
                single_response = requests.post(single_url, headers=enqueue_headers, json=single_file_data, timeout=30)
                log_download_request("POST", single_url, single_file_data, single_response)
                
                if single_response.status_code in [200, 201]:  # Accept both 200 OK and 201 Created
                    success_count += 1
                    logging.info(f"   ✅ File queued: {file_info['filename']}")
                else:
                    logging.warning(f"   ⚠️  Failed to queue file: {file_info['filename']} (HTTP {single_response.status_code})")
            
            if success_count > 0:
                logging.info(f"   ✅ Successfully queued {success_count}/{len(user_files)} files")
                return True
            else:
                logging.error("   ❌ Failed to queue any files")
                return False
                
    except requests.RequestException as e:
        logging.error(f"   ❌ Network error queuing album: {e}")
        return False
    except Exception as e:
        logging.error(f"   ❌ Unexpected error queuing album: {e}")
        return False

def cleanup_downloads():
    """Clean up any ongoing downloads, incomplete files, and completed downloads"""
    logging.info("🧹 Starting comprehensive download cleanup...")
    
    # Step 1: Remove all completed downloads (remotely)
    remove_completed_downloads()
    
    # Step 2: Cancel all ongoing downloads and clear stuck queued downloads
    cancel_ongoing_downloads()
    
    # Step 3: Clear incomplete downloads folder
    clear_incomplete_downloads()
    
    # Step 4: Clear completed downloads folder
    clear_completed_downloads()
    
    logging.info("✅ Download cleanup completed")
    logging.info("")

def remove_completed_downloads():
    """Remove all completed downloads remotely via slskd API"""
    logging.info("   🗑️  Removing completed downloads...")
    
    try:
        # Get current downloads including completed ones
        url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.warning("   ⚠️  Could not fetch current downloads")
            return False
        
        downloads_data = response.json()
        total_removed = 0
        
        if isinstance(downloads_data, list):
            for user_group in downloads_data:
                username = user_group.get('username', '')
                directories = user_group.get('directories', [])
                
                for directory in directories:
                    files = directory.get('files', [])
                    for file_transfer in files:
                        transfer_id = file_transfer.get('id')
                        filename = file_transfer.get('filename', '')
                        state = file_transfer.get('state', '')
                        
                        # Remove completed downloads and stuck queued downloads
                        if state in ['Completed', 'Succeeded'] or (state == 'Queued' and should_clear_stuck_queued(file_transfer)):
                            if remove_download(transfer_id, filename, state):
                                total_removed += 1
        
        if total_removed > 0:
            logging.info(f"   ✅ Removed {total_removed} completed/stuck downloads")
        else:
            logging.info("   ℹ️  No completed downloads to remove")
        
        return True
        
    except Exception as e:
        logging.error(f"   ❌ Error removing completed downloads: {e}")
        return False

def should_clear_stuck_queued(file_transfer):
    """Check if a queued download should be considered stuck and removed"""
    # For now, consider any queued download as potentially stuck
    # You could add time-based logic here if needed
    return True

def remove_download(transfer_id, filename, state):
    """Remove a specific download by transfer ID"""
    try:
        url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads/{transfer_id}"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.delete(url, headers=headers, timeout=30)
        if response.status_code == 200:
            logging.info(f"      🗑️  Removed ({state}): {os.path.basename(filename)}")
            return True
        else:
            logging.warning(f"      ⚠️  Failed to remove ({state}): {os.path.basename(filename)}")
            return False
            
    except Exception as e:
        logging.error(f"      ❌ Error removing {filename}: {e}")
        return False

def clear_completed_downloads():
    """Clear the downloads/complete folder"""
    logging.info("   🗂️  Clearing completed downloads folder...")
    
    # Common download paths - check settings/environment or use defaults
    possible_paths = [
        get_slskd_downloads_complete_path(),
        get_slskd_downloads_path() + '/complete' if get_slskd_downloads_path() else '',
        '/downloads/complete',
        '/data/downloads/complete',
        './downloads/complete'
    ]
    
    completed_path = None
    for path in possible_paths:
        if path and os.path.exists(path):
            completed_path = path
            break
    
    if not completed_path:
        logging.info("   ℹ️  Completed downloads folder not found - skipping")
        return True
    
    try:
        # Count files before deletion
        total_files = 0
        total_dirs = 0
        total_size = 0
        
        for root, dirs, files in os.walk(completed_path):
            if root != completed_path:  # Don't count the root folder itself
                total_dirs += 1
            for file in files:
                file_path = os.path.join(root, file)
                if os.path.isfile(file_path):
                    total_files += 1
                    total_size += os.path.getsize(file_path)
        
        if total_files == 0:
            logging.info(f"   ✅ Completed folder is already empty: {completed_path}")
            return True
        
        # Format size
        size_mb = total_size / (1024 * 1024)
        size_gb = size_mb / 1024
        
        if size_gb > 1:
            size_str = f"{size_gb:.1f} GB"
        else:
            size_str = f"{size_mb:.1f} MB"
        
        logging.info(f"   🗑️  Removing {total_files} completed files in {total_dirs} folders ({size_str})")
        logging.info(f"      📁 Path: {completed_path}")
        
        # Remove all contents except the root folder
        for root, dirs, files in os.walk(completed_path, topdown=False):
            # Remove files
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"      ⚠️  Could not remove {file}: {e}")
            
            # Remove directories (but not the root completed path)
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    logging.warning(f"      ⚠️  Could not remove directory {dir}: {e}")
        
        logging.info("   ✅ Completed downloads folder cleared")
        return True
        
    except Exception as e:
        logging.error(f"   ❌ Error clearing completed folder: {e}")
        return False

def cancel_ongoing_downloads():
    """Cancel all ongoing downloads and clear stuck queued downloads in slskd"""
    logging.info("   🛑 Cancelling ongoing and stuck downloads...")
    
    try:
        # Get current downloads
        url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.warning("   ⚠️  Could not fetch current downloads")
            return False
        
        downloads_data = response.json()
        total_cancelled = 0
        
        if isinstance(downloads_data, list):
            for user_group in downloads_data:
                username = user_group.get('username', '')
                directories = user_group.get('directories', [])
                
                for directory in directories:
                    files = directory.get('files', [])
                    for file_transfer in files:
                        transfer_id = file_transfer.get('id')
                        filename = file_transfer.get('filename', '')
                        state = file_transfer.get('state', '')
                        
                        # Cancel active downloads and stuck queued downloads
                        if state in ['Requested', 'Queued', 'Initializing', 'InProgress', 'Cancelled']:
                            if cancel_download(transfer_id, filename, state):
                                total_cancelled += 1
        
        if total_cancelled > 0:
            logging.info(f"   ✅ Cancelled {total_cancelled} ongoing/stuck downloads")
        else:
            logging.info("   ℹ️  No ongoing downloads to cancel")
        
        return True
        
    except Exception as e:
        logging.error(f"   ❌ Error cancelling downloads: {e}")
        return False

def cancel_download(transfer_id, filename, state='Unknown'):
    """Cancel a specific download by transfer ID"""
    try:
        url = f"{SLSKD_CONFIG['url']}/api/v0/transfers/downloads/{transfer_id}"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.delete(url, headers=headers, timeout=30)
        if response.status_code == 200:
            logging.info(f"      🗑️  Cancelled ({state}): {os.path.basename(filename)}")
            return True
        else:
            logging.warning(f"      ⚠️  Failed to cancel ({state}): {os.path.basename(filename)}")
            return False
            
    except Exception as e:
        logging.error(f"      ❌ Error cancelling {filename}: {e}")
        return False

def clear_incomplete_downloads():
    """Clear the downloads/incomplete folder"""
    logging.info("   🗂️  Clearing incomplete downloads folder...")
    
    # Common download paths - check settings/environment or use defaults
    possible_paths = [
        get_slskd_downloads_incomplete_path(),
        get_slskd_downloads_path() + '/incomplete' if get_slskd_downloads_path() else '',
        '/downloads/incomplete',
        '/data/downloads/incomplete',
        './downloads/incomplete'
    ]
    
    incomplete_path = None
    for path in possible_paths:
        if path and os.path.exists(path):
            incomplete_path = path
            break
    
    if not incomplete_path:
        logging.warning("   ⚠️  Could not find incomplete downloads folder")
        logging.info("   💡 Set SLSKD_DOWNLOADS_INCOMPLETE environment variable if needed")
        return False
    
    try:
        # Count files before deletion
        total_files = 0
        total_size = 0
        
        for root, dirs, files in os.walk(incomplete_path):
            for file in files:
                file_path = os.path.join(root, file)
                if os.path.isfile(file_path):
                    total_files += 1
                    total_size += os.path.getsize(file_path)
        
        if total_files == 0:
            logging.info(f"   ✅ Incomplete folder is already empty: {incomplete_path}")
            return True
        
        # Format size
        size_mb = total_size / (1024 * 1024)
        logging.info(f"   🗑️  Removing {total_files} incomplete files ({size_mb:.1f} MB)")
        logging.info(f"      📁 Path: {incomplete_path}")
        
        # Remove all contents
        for root, dirs, files in os.walk(incomplete_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"      ⚠️  Could not remove {file}: {e}")
            
            # Remove empty directories
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    if not os.listdir(dir_path):  # Only remove if empty
                        os.rmdir(dir_path)
                except Exception:
                    pass  # Ignore errors removing directories
        
        logging.info("   ✅ Incomplete downloads folder cleared")
        return True
        
    except Exception as e:
        logging.error(f"   ❌ Error clearing incomplete folder: {e}")
        return False

def check_completed_downloads(artist_name, album_title):
    """Check if album is already in completed downloads folder"""
    completed_paths = [
        get_slskd_downloads_complete_path(),
        get_slskd_downloads_path() + '/complete' if get_slskd_downloads_path() else '',
        '/downloads/complete',
        '/data/downloads/complete',
        './downloads/complete'
    ]
    
    completed_path = None
    for path in completed_paths:
        if path and os.path.exists(path):
            completed_path = path
            break
    
    if not completed_path:
        return False
    
    try:
        # Create search patterns
        search_patterns = [
            f"{artist_name.lower()} - {album_title.lower()}",
            f"{artist_name.lower()}-{album_title.lower()}",
            f"{artist_name.lower()} {album_title.lower()}",
            album_title.lower()
        ]
        
        # Search for matching folders/files
        for root, dirs, files in os.walk(completed_path):
            folder_name = os.path.basename(root).lower()
            
            # Check if any pattern matches the folder name
            for pattern in search_patterns:
                if pattern in folder_name or folder_name in pattern:
                    # Count audio files in this folder (consistent with download filtering)
                    audio_files = [f for f in files if f.lower().endswith(('.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aiff', '.ape', '.wv'))]
                    if len(audio_files) >= 3:  # Likely an album
                        logging.info(f"   📁 Found in completed downloads: {os.path.basename(root)}")
                        logging.info(f"      🎵 Contains {len(audio_files)} audio files")
                        return True
        
        return False
        
    except Exception as e:
        logging.error(f"   ❌ Error checking completed downloads: {e}")
        return False

def find_best_matches(results, max_candidates=3):
    """Find the best album matches from search results, returning multiple candidates"""
    candidates = []
    audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aiff', '.ape', '.wv']
    
    for user_response in results:
        username = user_response.get('username', '')
        files = user_response.get('files', [])
        file_count = user_response.get('fileCount', 0)
        
        for file_info in files:
            filename = file_info.get('filename', '')
            filesize = file_info.get('size', 0)
            
            # Check if it's an audio file
            if not any(filename.lower().endswith(ext) for ext in audio_extensions):
                continue
            
            # Skip very small files (likely not full tracks)
            if filesize < 1000000:  # 1MB minimum
                continue
            
            # Prefer responses with multiple files (album folders)
            priority = file_count if file_count > 3 else 1
            
            candidates.append((priority, filesize, username, filename, filesize))
    
    if not candidates:
        return []
    
    # Sort by priority (file count), then by file size
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    
    # Return unique users (no duplicates)
    seen_users = set()
    unique_candidates = []
    
    for priority, size, username, filename, filesize in candidates:
        if username not in seen_users:
            seen_users.add(username)
            unique_candidates.append((username, filename, filesize))
            
            if len(unique_candidates) >= max_candidates:
                break
    
    return unique_candidates
    """Find the best album match from search results"""
    best_matches = []
    
    for user_response in results:
        username = user_response.get('username', '')
        files = user_response.get('files', [])
        file_count = user_response.get('fileCount', 0)
        
        for file_info in files:
            filename = file_info.get('filename', '')
            filesize = file_info.get('size', 0)
            
            # Check if it's an audio file
            if not any(filename.lower().endswith(ext) for ext in ['.mp3', '.flac', '.m4a', '.ogg', '.wav']):
                continue
            
            # Skip very small files (likely not full tracks)
            if filesize < 1000000:  # 1MB minimum
                continue
            
            # Prefer responses with multiple files (album folders)
            priority = 1 if file_count > 3 else 0
            
            best_matches.append((priority, username, filename, filesize))
    
    if not best_matches:
        return None
    
    # Sort by priority (multi-file responses first), then by file size
    best_matches.sort(key=lambda x: (x[0], x[3]), reverse=True)
    
    # Return the best match (without priority)
    return best_matches[0][1:]

def get_directory_path(filename):
    """Extract directory path from filename"""
    # Handle both Windows and Unix path separators
    normalized = filename.replace('\\', '/')
    
    # Get directory by removing the last component
    if '/' in normalized:
        directory = '/'.join(normalized.split('/')[:-1])
    else:
        # If no separator found, return the original (might be just a directory name)
        directory = filename
    
    return directory

def check_slskd_connection():
    """Check slskd connection and login status"""
    logging.info("🔍 Checking slskd connection...")
    
    try:
        # Try the application endpoint for slskd v0.23+
        url = f"{SLSKD_CONFIG['url']}/api/v0/application"
        headers = {'X-API-Key': SLSKD_CONFIG['api_key']}
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logging.error("✗ Could not connect to slskd API")
            logging.error("   Check SLSKD_URL and SLSKD_API_KEY")
            return False
        
        app_data = response.json()
        
        # Check login status
        user_info = app_data.get('user', {})
        server_info = app_data.get('server', {})
        
        username = user_info.get('username', '')
        server_state = server_info.get('state', '')
        is_logged_in = server_info.get('isLoggedIn', False)
        
        if username and is_logged_in:
            logging.info(f"✓ Connected to slskd as: {username}")
            logging.info(f"   Server state: {server_state}")
            

        else:
            logging.error("⚠️  slskd is not logged in to Soulseek")
            logging.error(f"   Server state: {server_state}")
            logging.error("   Login to Soulseek in slskd web interface")
            return False
        
        logging.info("")
        return True
        
    except requests.RequestException as e:
        logging.error(f"✗ Could not connect to slskd: {e}")
        return False
    except Exception as e:
        logging.error(f"✗ Unexpected error checking slskd: {e}")
        return False

def main():
    """Main function"""
    global interrupted, LIDARR_CONFIG, SLSKD_CONFIG
    
    # Initialize configurations if settings are available
    if SETTINGS_AVAILABLE:
        LIDARR_CONFIG = get_lidarr_config()
        SLSKD_CONFIG = get_slskd_config()
    else:
        # Use environment variables as fallback
        LIDARR_CONFIG = {
            'url': os.getenv('LIDARR_URL', ''),
            'api_key': os.getenv('LIDARR_API_KEY', '')
        }
        SLSKD_CONFIG = {
            'url': os.getenv('SLSKD_URL', ''),
            'api_key': os.getenv('SLSKD_API_KEY', '')
        }
    
    # Set up argument parsing
    parser = argparse.ArgumentParser(description='Queue Wanted Albums from Lidarr to slskd')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without actually queuing downloads')
    parser.add_argument('--skip-cleanup', action='store_true', help='Skip cleanup of downloads at startup')
    
    # Check for environment variable DRY_RUN as well
    if SETTINGS_AVAILABLE:
        dry_run_env = is_dry_run()
    else:
        dry_run_env = os.getenv('DRY_RUN', 'false').lower() == 'true'
    
    args = parser.parse_args()
    dry_run = args.dry_run or dry_run_env
    skip_cleanup = args.skip_cleanup
    
    # Set up logging
    log_file = setup_logging()
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check environment variables
    if not check_environment():
        sys.exit(1)
    
    if dry_run:
        logging.info("   🧪 DRY RUN MODE - No files will be queued")
    
    if skip_cleanup:
        logging.info("   ⏭️  SKIPPING CLEANUP - Downloads will not be cleaned")
    
    logging.info("")
    logging.info("🚀 Starting Queue Wanted Albums processor")
    
    # Pre-flight checks
    logging.info("🔧 Pre-flight checks...")
    if not check_slskd_connection():
        logging.error("❌ Pre-flight check failed - aborting execution")
        sys.exit(1)
    
    # Cleanup downloads unless skipped or dry run
    if not dry_run and not skip_cleanup:
        cleanup_downloads()
    elif dry_run:
        logging.info("🧹 Would perform download cleanup (skipped in dry run)")
        logging.info("")
    else:
        logging.info("⏭️  Skipping download cleanup")
        logging.info("")
    
    # Process wanted albums
    success = get_wanted_albums()
    
    if not interrupted:
        print_summary()
        
        if dry_run:
            logging.info("🔍 This was a dry run - no actual downloads were queued")
            logging.info("   Run without --dry-run to queue albums in slskd")
        else:
            logging.info("✅ Completed processing Lidarr want list")
            logging.info("   📥 Albums have been queued for download in slskd")
            logging.info("   🌐 Check slskd web interface for download progress")
        
        logging.info("")
        logging.info("💡 Tips:")
        logging.info("   • Check slskd web interface to monitor download progress")
        logging.info("   • Ensure you're logged in to Soulseek in slskd")
        logging.info("   • Downloads will appear in your configured slskd downloads folder")
        logging.info("   • Use the slskd auto-mover script to organize completed downloads")
        logging.info("   • Run this script as a cron job to regularly clean up failed downloads")
        
        # Simple cache info (for compatibility with previous version)
        cache_file = "queue_cache.json"
        cache_entries = 0
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                    cache_entries = len(cache_data) if isinstance(cache_data, dict) else 0
            except:
                pass
        
        logging.info(f"📊 Cache contains {cache_entries} entries")
        logging.info("✅ Want list processing complete!")
        
        if not success:
            sys.exit(1)

if __name__ == "__main__":
    main()