#!/usr/bin/env python3
"""
ListenBrainz Recommendations - Monitor recommended albums in Lidarr

This script:
1. Fetches your ListenBrainz "Weekly Exploration" playlist (personalized recommendations)
2. Extracts unique albums from the playlist tracks
3. Adds artists to Lidarr if missing
4. Monitors specifically recommended albums in Lidarr
5. Creates ListenBrainz log history

Name: ListenBrainz Recommendations
Author: SoulSeekarr
Version: 3.0
Section: commands
Tags: listenbrainz, recommendations, lidarr, discovery, weekly-exploration
Supports dry run: true
"""

import os
import sys
import json
import re
import argparse
import logging
import requests
import time
import urllib.parse
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

# Import settings and utilities
try:
    from settings import (
        get_listenbrainz_config,
        get_lidarr_config,
        get_setting
    )
    from lidarr_utils import LidarrClient
    from action_logger import log_script_start, log_script_complete, log_action
    
    SETTINGS_AVAILABLE = True
except ImportError:
    print("Warning: Could not import project modules. Ensure script is run from project context.")
    SETTINGS_AVAILABLE = False
    
# Setup Logging
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'listenbrainz_recommendations_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class ListenBrainzRecommendations:
    def __init__(self, dry_run=False, limit=7):
        self.dry_run = dry_run
        self.limit = limit
        self.logger = logger
        
        # Load configs
        self.lb_config = get_listenbrainz_config() if SETTINGS_AVAILABLE else {}
        self.lidarr_config = get_lidarr_config() if SETTINGS_AVAILABLE else {}
        
        # Validate Lidarr Config
        if not self.lidarr_config or not self.lidarr_config.get('url') or not self.lidarr_config.get('api_key'):
             self.logger.error("❌ Lidarr configuration missing. Please configure Lidarr in Settings.")
             raise ValueError("Lidarr configuration missing")

        # Initialize Lidarr Client
        self.lidarr = LidarrClient(
            self.lidarr_config['url'], 
            self.lidarr_config['api_key'], 
            self.logger, 
            dry_run=self.dry_run
        )
        
        # Validate ListenBrainz Config
        self.lb_username = self.lb_config.get('username')
        if not self.lb_username:
             # Try environment
             self.lb_username = os.environ.get('LISTENBRAINZ_USERNAME')
             
        if not self.lb_username:
             self.logger.error("❌ ListenBrainz username missing. Please configure ListenBrainz in Settings.")
             raise ValueError("ListenBrainz username missing")

    def get_weekly_exploration_playlist(self):
        """Get the latest Weekly Exploration playlist ID."""
        url = f'https://api.listenbrainz.org/1/user/{self.lb_username}/playlists/recommendations'
        
        self.logger.info(f"Fetching playlists for user: {self.lb_username}")
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            playlists = data.get('playlists', [])
            
            # Find Weekly Exploration
            for item in playlists:
                playlist = item.get('playlist', {})
                title = playlist.get('title', '')
                if 'Weekly Exploration' in title:
                    playlist_id = playlist.get('identifier', '').split('/')[-1]
                    self.logger.info(f"✅ Found playlist: {title}")
                    return playlist_id
            
            self.logger.warning("❌ No 'Weekly Exploration' playlist found")
            return None
            
        except Exception as e:
            self.logger.error(f"Error fetching playlists: {e}")
            return None

    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a playlist."""
        url = f'https://api.listenbrainz.org/1/playlist/{playlist_id}'
        
        self.logger.info(f"Fetching playlist tracks...")
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            tracks = data.get('playlist', {}).get('track', [])
            self.logger.info(f"✅ Found {len(tracks)} tracks in playlist")
            return tracks
            
        except Exception as e:
            self.logger.error(f"Error fetching playlist tracks: {e}")
            return []

    def extract_albums(self, tracks):
        """Extract unique albums from tracks."""
        albums = {}
        
        for track in tracks:
            artist = track.get('creator', '').strip()
            album_name = track.get('album', '').strip()
            
            if not artist or not album_name:
                continue
                
            # Normalize artist name
            normalized_artist = self.lidarr.normalize_artist_name(artist)
            
            # Key by artist+album to dedup
            key = f"{normalized_artist}|{album_name.lower()}"
            
            if key not in albums:
                albums[key] = {
                    'artist': artist, # Original name for display/search
                    'normalized_artist': normalized_artist,
                    'album': album_name
                }
        
        unique_albums = list(albums.values())
        self.logger.info(f"Extracted {len(unique_albums)} unique albums from playlist")
        return unique_albums

    def process_recommendation(self, album_info):
        """Process a single recommended album."""
        artist_name = album_info['artist']
        album_name = album_info['album']
        
        self.logger.info(f"Processing: {artist_name} - {album_name}")
        
        # 1. Ensure Artist Exists
        # Using add_artist_and_search_musicbrainz which handles "if exists" check gracefully
        # Use future_monitoring=False so we don't accidentally monitor everything if we just want this one album
        # But wait, if we add a NEW artist, we probably DO want future monitoring? 
        # User requirement: "monitor the album in lidarr". 
        # Usually adding artist with "Future" is safest. Then we explicitly monitor the specific album.
        
        # First, we need to know if we need to add the artist.
        artist_details = self.lidarr.get_artist_by_name(artist_name)
        
        if not artist_details:
             self.logger.info(f"  Artist '{artist_name}' not in Lidarr. Adding...")
             success = self.lidarr.add_artist_and_search_musicbrainz(
                 artist_name, 
                 future_monitoring=True # Monitor future releases for new artists is generally good
             )
             if not success:
                 self.logger.error(f"  Failed to add artist '{artist_name}'. Skipping album.")
                 return False
             
             # Fetch details again after adding
             # Wait a sec for Lidarr to process
             if not self.dry_run:
                 time.sleep(2)
             artist_details = self.lidarr.get_artist_by_name(artist_name)
        
        if not artist_details and not self.dry_run:
            self.logger.error(f"  Could not retrieve artist details for '{artist_name}' even after adding.")
            return False
            
        if self.dry_run and not artist_details:
             self.logger.info(f"  [DRY RUN] Would retrieve artist details for {artist_name}")
             return True

        # 2. Find the Album
        artist_id = artist_details['id']
        
        # Retry loop for metadata (Lidarr takes time to sync new artists)
        target_album = None
        retries = 6
        
        self.logger.info(f"  Searching for album '{album_name}' in Lidarr metadata...")
        
        for attempt in range(retries):
            # Fetch albums using client
            lidarr_albums = self.lidarr.get_artist_albums(artist_id)
            
            # Fuzzy match album name
            norm_target_name = self.lidarr.normalize_artist_name(album_name)
            
            for alb in lidarr_albums:
                norm_alb_name = self.lidarr.normalize_artist_name(alb.get('title', ''))
                if norm_alb_name == norm_target_name:
                    target_album = alb
                    break
            
            if target_album:
                break
                
            if attempt < retries - 1:
                wait_time = 5
                self.logger.info(f"  Album not found yet. Waiting {wait_time}s for metadata sync (Attempt {attempt+1}/{retries})...")
                if not self.dry_run:
                    time.sleep(wait_time)
        
        # If album not found in Lidarr's metadata
        if not target_album:
             self.logger.warning(f"  Album '{album_name}' not found in Lidarr metadata after {retries} attempts.")
             return False
             
        # 3. Monitor the Album
        if target_album.get('monitored'):
            self.logger.info(f"  Album '{album_name}' is already monitored.")
            return True # Already done
            
        self.logger.info(f"  Setting '{album_name}' to monitored...")
        success = self.lidarr.set_album_monitored(target_album, monitored=True)
        
        if success:
            log_action("album_monitored", "ListenBrainz Recommendations", f"{artist_name} - {album_name}")
            
            # Trigger search for this album
            if not self.dry_run:
                self.logger.info(f"  Triggering search for '{album_name}'...")
                self._command_album_search(target_album['id'])
                
        return success

    def _command_album_search(self, album_id):
        """Trigger AlbumSearch command."""
        try:
            url = f"{self.lidarr.lidarr_url}/api/v1/command"
            headers = self.lidarr._get_headers()
            payload = {'name': 'AlbumSearch', 'albumIds': [album_id]}
            requests.post(url, json=payload, headers=headers)
        except Exception as e:
            self.logger.error(f"Error triggering search: {e}")

    def run(self):
        """Main execution."""
        log_script_start("ListenBrainz Recommendations", f"Limit: {self.limit}, Dry Run: {self.dry_run}")
        
        try:
            # 1. Get Playlist
            playlist_id = self.get_weekly_exploration_playlist()
            if not playlist_id:
                return

            # 2. Get Tracks
            tracks = self.get_playlist_tracks(playlist_id)
            if not tracks:
                return

            # 3. Extract Albums
            albums = self.extract_albums(tracks)
            
            # 4. Limit
            # We want to process only N albums that are NOT already monitored.
            # But we can't know if they are monitored without querying.
            # So we iterate and stop when we have Actioned 'limit' albums.
            
            processed_count = 0
            monitored_count = 0
            
            total_items = len(albums)
            
            for i, album in enumerate(albums):
                if monitored_count >= self.limit:
                    break
                
                percentage = int(((i+1) / total_items) * 100)
                print(f"PROGRESS: [{i+1}/{total_items}] {percentage}% - Checking: {album['artist']} - {album['album']}")
                
                if self.process_recommendation(album):
                    monitored_count += 1
                
                processed_count += 1
                
                if not self.dry_run:
                    time.sleep(1) # Be nice to APIs

            # Force 100% progress at completion
            print(f"PROGRESS: [{total_items}/{total_items}] 100% - Completed")

            # Summary
            self.logger.info("=" * 60)
            self.logger.info("SUMMARY")
            self.logger.info(f"Processed: {processed_count}")
            self.logger.info(f"Monitored: {monitored_count}")
            self.logger.info("=" * 60)
            
            log_script_complete("ListenBrainz Recommendations", success=True)
            
        except Exception as e:
            self.logger.error(f"Fatal Error: {e}")
            log_script_complete("ListenBrainz Recommendations", success=False, error=str(e))
            raise

def main():
    parser = argparse.ArgumentParser(description='ListenBrainz Recommendations')
    parser.add_argument('--limit', type=int, default=7, help='Number of albums to monitor')
    parser.add_argument('--dry-run', action='store_true', help='Run without changes')
    args = parser.parse_args()
    
    script = ListenBrainzRecommendations(dry_run=args.dry_run, limit=args.limit)
    script.run()

if __name__ == '__main__':
    main()
