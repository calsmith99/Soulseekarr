#!/usr/bin/env python3
"""
ListenBrainz Recommendations - Queue recommended albums from your Weekly Exploration playlist

This script:
1. Fetches your ListenBrainz "Weekly Exploration" playlist (personalized recommendations)
2. Extracts unique albums from the playlist tracks
3. Checks which albums are missing from your Lidarr library
4. Queues missing albums for download in slskd

Name: ListenBrainz Recommendations
Author: SoulSeekarr
Version: 2.0
Section: commands
Tags: listenbrainz, recommendations, slskd, discovery, weekly-exploration
Supports dry run: true

Configuration:
All settings are configured via the web interface Settings page:
- ListenBrainz username (token not required for public playlists)
- Lidarr connection details
- slskd connection details (for queuing downloads)

Usage:
    python listenbrainz_recommendations.py                    # Get 7 recommendations
    python listenbrainz_recommendations.py --limit 20         # Get 20 recommendations
    python listenbrainz_recommendations.py --dry-run          # Show what would be queued
"""

import os
import sys
import json
import re
import argparse
import logging
import requests
import hashlib
import random
import string
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import enhanced downloading logic from queue_lidarr_monitored
sys.path.append(str(Path(__file__).parent))
from queue_lidarr_monitored import (
    queue_tracks_for_download, check_downloads_folder, check_download_queue,
    DOWNLOADED_TRACKS, is_audio_file_wanted
)

# Import settings for configuration (needed by queue_lidarr_monitored functions)
from settings import get_lidarr_config, get_slskd_config

from datetime import datetime
from collections import defaultdict

# Global configuration for enhanced download functions
CONFIG = {}

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Try to import settings
try:
    from settings import (
        get_listenbrainz_config,
        get_lidarr_config,
        get_slskd_config
    )
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

try:
    from action_logger import log_action
except ImportError:
    def log_action(*args, **kwargs):
        pass


class ListenBrainzRecommendations:
    def __init__(self, dry_run=False, limit=7):
        self.dry_run = dry_run
        self.limit = limit
        
        # Set up logging first
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize config values
        self.listenbrainz_username = None
        self.lidarr_url = None
        self.lidarr_api_key = None
        self.slskd_url = None
        self.slskd_api_key = None
        
        # Load configuration
        self.load_config()
        
        # Set up global CONFIG for enhanced download functions
        self.setup_global_config()

    def load_config(self):
        """Load configuration from settings or environment variables."""
        # Try settings module first
        if SETTINGS_AVAILABLE:
            try:
                lb_config = get_listenbrainz_config()
                self.listenbrainz_username = lb_config.get('username')
                
                lidarr_config = get_lidarr_config()
                self.lidarr_url = lidarr_config.get('url')
                self.lidarr_api_key = lidarr_config.get('api_key')
                
                slskd_config = get_slskd_config()
                self.slskd_url = slskd_config.get('url')
                self.slskd_api_key = slskd_config.get('api_key')
                
                self.logger.info("Loaded configuration from settings module")
                return
            except Exception as e:
                self.logger.warning(f"Could not load from settings module: {e}")
        
        # Fall back to environment variables
        self.listenbrainz_username = os.getenv('LISTENBRAINZ_USERNAME')
        self.lidarr_url = os.getenv('LIDARR_URL')
        self.lidarr_api_key = os.getenv('LIDARR_API_KEY')
        self.slskd_url = os.getenv('SLSKD_URL')
        self.slskd_api_key = os.getenv('SLSKD_API_KEY')
        
        self.logger.info("Loaded configuration from environment variables")

    def setup_global_config(self):
        """Set up the global CONFIG variable needed by enhanced download functions."""
        global CONFIG
        CONFIG.update({
            'lidarr_url': self.lidarr_url,
            'lidarr_api_key': self.lidarr_api_key,
            'slskd_url': self.slskd_url,
            'slskd_api_key': self.slskd_api_key
        })
        self.logger.debug("Set up global CONFIG for enhanced download functions")

    def get_weekly_exploration_playlist(self):
        """Get the latest Weekly Exploration playlist ID for the user with retry logic."""
        if not self.listenbrainz_username:
            raise ValueError("ListenBrainz username must be configured")
        
        url = f'https://api.listenbrainz.org/1/user/{self.listenbrainz_username}/playlists/recommendations'
        
        self.logger.info(f"Fetching recommendation playlists for user: {self.listenbrainz_username}")
        
        # Retry up to 3 times with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                playlists = data.get('playlists', [])
                
                # Find the most recent Weekly Exploration playlist
                for item in playlists:
                    playlist = item.get('playlist', {})
                    title = playlist.get('title', '')
                    if 'Weekly Exploration' in title:
                        playlist_id = playlist.get('identifier', '').split('/')[-1]
                        self.logger.info(f"Found playlist: {title}")
                        return playlist_id
                
                raise ValueError("No Weekly Exploration playlist found")
                
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    self.logger.warning(f"SSL/Connection error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"Error fetching playlists after {max_retries} attempts: {e}")
                    raise
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Error fetching playlists: {e}")
                raise

    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a playlist with retry logic."""
        url = f'https://api.listenbrainz.org/1/playlist/{playlist_id}'
        
        self.logger.info(f"Fetching playlist tracks...")
        
        # Retry up to 3 times with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                tracks = data.get('playlist', {}).get('track', [])
                self.logger.info(f"Found {len(tracks)} tracks in playlist")
                
                return tracks
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    self.logger.warning(f"SSL/Connection error, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"Error fetching playlist tracks after {max_retries} attempts: {e}")
                    raise
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Error fetching playlist tracks: {e}")
                raise

    def normalize_artist_name(self, artist):
        """Normalize artist name by removing featuring artists and other common patterns."""
        if not artist:
            return ''
        
        # Remove everything after common delimiters for featured artists
        patterns = [
            r'\s+feat\..*$',
            r'\s+feat\s+.*$',
            r'\s+ft\..*$',
            r'\s+ft\s+.*$',
            r'\s+featuring.*$',
            r'\s+\+.*$',
            r'\s+x\s+.*$',
        ]
        
        normalized = artist
        for pattern in patterns:
            normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
        
        return normalized.strip()

    def extract_albums_from_tracks(self, tracks):
        """Extract unique albums from playlist tracks."""
        albums = []
        seen_albums = set()
        
        for track in tracks:
            artist = track.get('creator', '').strip()
            album = track.get('album', '').strip()
            
            if not artist or not album:
                continue
            
            # Normalize artist name (remove featuring artists, etc.)
            normalized_artist = self.normalize_artist_name(artist)
            
            album_key = (normalized_artist.lower(), album.lower())
            
            if album_key not in seen_albums:
                seen_albums.add(album_key)
                albums.append({
                    'artist': normalized_artist,  # Use normalized artist
                    'album': album,
                    'original_artist': artist  # Keep original for reference
                })
        
        self.logger.info(f"Extracted {len(albums)} unique albums from tracks")
        return albums

    def get_lidarr_albums(self):
        """Get all albums from Lidarr."""
        if not all([self.lidarr_url, self.lidarr_api_key]):
            raise ValueError("Lidarr configuration is incomplete")
        
        url = f"{self.lidarr_url}/api/v1/album"
        headers = {'X-Api-Key': self.lidarr_api_key}
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            albums_data = response.json()
            
            self.logger.info(f"Found {len(albums_data)} albums in Lidarr")
            
            # Create a set of (artist, album) tuples for quick lookup
            library_albums = set()
            for album in albums_data:
                artist = album.get('artist', {}).get('artistName', '').lower().strip()
                title = album.get('title', '').lower().strip()
                library_albums.add((artist, title))
            
            return library_albums
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching Lidarr albums: {e}")
            raise

    def filter_missing_albums(self, recommended_albums, library_albums):
        """Filter out albums that are already in Lidarr."""
        missing = []
        
        for album in recommended_albums:
            artist = album['artist'].lower().strip()
            title = album['album'].lower().strip()
            
            if (artist, title) not in library_albums:
                missing.append(album)
        
        self.logger.info(f"Found {len(missing)} missing albums out of {len(recommended_albums)} recommendations")
        return missing

    def queue_album_in_slskd(self, artist, album):
        """Queue an album for download using enhanced logic with deduplication."""
        if not all([self.slskd_url, self.slskd_api_key]):
            raise ValueError("slskd configuration is incomplete")
        
        try:
            # Create a fake track list for the album (since we don't have Lidarr track data)
            # We'll let the enhanced logic search for the full album
            fake_tracks = [{
                'title': f"Track from {album}",  # Placeholder - album search will find actual tracks
                'trackNumber': 1,
                'discNumber': 1
            }]
            
            self.logger.info(f"🎵 Queuing album: {artist} - {album}")
            
            # Use the enhanced downloading logic
            success = queue_tracks_for_download(
                tracks=fake_tracks,
                artist_name=artist, 
                album_title=album,
                dry_run=self.dry_run
            )
            
            if success and not self.dry_run:
                log_action(
                    'album_download_queued',
                    'listenbrainz_recommendations',
                    {'artist': artist, 'album': album}
                )
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error queuing album in slskd: {e}")
            return False

    def run(self):
        """Main execution flow."""
        try:
            self.logger.info("=" * 80)
            self.logger.info("ListenBrainz Weekly Exploration Recommendations")
            self.logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            self.logger.info(f"Target: {self.limit} missing albums to queue")
            self.logger.info("=" * 80)
            
            # Step 1: Get Lidarr library first
            self.logger.info("\n[1/5] Fetching Lidarr library...")
            library_albums = self.get_lidarr_albums()
            
            # Step 2: Get Weekly Exploration playlist
            self.logger.info("\n[2/5] Finding Weekly Exploration playlist...")
            playlist_id = self.get_weekly_exploration_playlist()
            
            # Step 3: Get tracks from playlist
            self.logger.info("\n[3/5] Fetching playlist tracks...")
            tracks = self.get_playlist_tracks(playlist_id)
            
            if not tracks:
                self.logger.warning("No tracks found in playlist")
                return
            
            # Step 4: Extract and filter albums
            self.logger.info(f"\n[4/5] Finding {self.limit} missing albums...")
            all_albums = self.extract_albums_from_tracks(tracks)
            
            # Filter for missing albums
            missing_albums = []
            for album in all_albums:
                album_key = (album['artist'].lower(), album['album'].lower())
                
                if album_key not in library_albums:
                    missing_albums.append(album)
                    self.logger.info(f"  [{len(missing_albums)}] {album['artist']} - {album['album']}")
                    
                    if len(missing_albums) >= self.limit:
                        break
            
            self.logger.info(f"\nFound {len(missing_albums)} missing albums")
            
            if not missing_albums:
                self.logger.info("All albums from playlist are already in your library!")
                return
            
            # Step 5: Queue missing albums
            self.logger.info(f"\n[5/5] Queuing {len(missing_albums)} missing albums...")
            queued_count = 0
            
            for i, album in enumerate(missing_albums, 1):
                print(f"PROGRESS: {i}/{len(missing_albums)} - Queuing: {album['artist']} - {album['album']}")
                self.logger.info(f"Queuing: {album['artist']} - {album['album']}")
                if self.queue_album_in_slskd(album['artist'], album['album']):
                    queued_count += 1
                time.sleep(0.5)  # Rate limiting
            
            # Summary
            self.logger.info("\n" + "=" * 80)
            self.logger.info("SUMMARY")
            self.logger.info("=" * 80)
            self.logger.info(f"Playlist tracks: {len(tracks)}")
            self.logger.info(f"Unique albums: {len(all_albums)}")
            self.logger.info(f"Missing from library: {len(missing_albums)}")
            self.logger.info(f"Successfully queued: {queued_count}")
            
            if self.dry_run:
                self.logger.info("\nThis was a DRY RUN - no actual downloads were queued")
            
            self.logger.info("=" * 80)
            
        except Exception as e:
            self.logger.error(f"Error running recommendations: {e}", exc_info=True)
            raise


def main():
    parser = argparse.ArgumentParser(
        description='Queue albums from your ListenBrainz Weekly Exploration playlist that are missing from Lidarr'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=7,
        help='Number of missing albums to queue (default: 7)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be queued without actually queuing'
    )
    
    args = parser.parse_args()
    
    recommender = ListenBrainzRecommendations(
        dry_run=args.dry_run,
        limit=args.limit
    )
    
    recommender.run()


if __name__ == '__main__':
    main()
