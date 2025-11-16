#!/usr/bin/env python3
"""
Lidarr Utilities - Reusable functions for Lidarr API operations

This module provides common functionality for interacting with Lidarr API:
- Adding artists with proper monitoring configuration
- Searching MusicBrainz for artist metadata
- Setting album monitoring status
- Getting Lidarr configuration (quality profiles, root folders)

Usage:
    from lidarr_utils import LidarrClient
    
    client = LidarrClient(lidarr_url, lidarr_api_key, logger, dry_run=False)
    success = client.add_artist_with_future_monitoring("Artist Name")

Requirements:
    - LIDARR_URL environment variable
    - LIDARR_API_KEY environment variable
    - requests library
    - logging configured
"""

import os
import re
import time
import logging
import requests
from typing import Optional, Dict, Any, List


class LidarrClient:
    """Client for interacting with Lidarr API with reusable functions"""
    
    def __init__(self, lidarr_url: str = None, lidarr_api_key: str = None, 
                 logger: logging.Logger = None, dry_run: bool = False):
        """Initialize Lidarr client
        
        Args:
            lidarr_url: Lidarr server URL (or from LIDARR_URL env var)
            lidarr_api_key: Lidarr API key (or from LIDARR_API_KEY env var) 
            logger: Logger instance (or create a basic one)
            dry_run: If True, don't make actual changes
        """
        self.lidarr_url = lidarr_url or os.getenv('LIDARR_URL')
        self.lidarr_api_key = lidarr_api_key or os.getenv('LIDARR_API_KEY')
        self.dry_run = dry_run
        
        # Setup logger
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger('lidarr_utils')
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO)
        
        # Validate configuration
        if not self.lidarr_url or not self.lidarr_api_key:
            missing = []
            if not self.lidarr_url:
                missing.append('LIDARR_URL')
            if not self.lidarr_api_key:
                missing.append('LIDARR_API_KEY')
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        # Cache for Lidarr configuration
        self._quality_profiles = None
        self._root_folders = None
        self._metadata_profiles = None
        
        self.logger.debug(f"LidarrClient initialized: {self.lidarr_url} (dry_run={self.dry_run})")

    def _get_headers(self) -> Dict[str, str]:
        """Get standard headers for Lidarr API requests"""
        return {
            'X-Api-Key': self.lidarr_api_key,
            'Content-Type': 'application/json'
        }

    def test_connection(self) -> bool:
        """Test connection to Lidarr server"""
        try:
            if self.dry_run:
                self.logger.info("DRY RUN: Skipping Lidarr connection test")
                return True
            
            headers = self._get_headers()
            test_url = f"{self.lidarr_url}/api/v1/system/status"
            response = requests.get(test_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                system_status = response.json()
                self.logger.info(f"Connected to Lidarr: {system_status.get('appName', 'Unknown')} v{system_status.get('version', 'Unknown')}")
                return True
            else:
                self.logger.error(f"Failed to connect to Lidarr: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error testing Lidarr connection: {e}")
            return False

    def get_quality_profiles(self) -> List[Dict[str, Any]]:
        """Get quality profiles from Lidarr (cached)"""
        try:
            if self._quality_profiles is not None:
                return self._quality_profiles
            
            if self.dry_run:
                self.logger.debug("DRY RUN: Using mock quality profiles")
                self._quality_profiles = [{'id': 1, 'name': 'Mock Profile'}]
                return self._quality_profiles
            
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/qualityprofile"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                self._quality_profiles = response.json()
                self.logger.debug(f"Retrieved {len(self._quality_profiles)} quality profiles")
                return self._quality_profiles
            else:
                self.logger.error(f"Failed to get quality profiles: HTTP {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting quality profiles: {e}")
            return []

    def get_root_folders(self) -> List[Dict[str, Any]]:
        """Get root folders from Lidarr (cached)"""
        try:
            if self._root_folders is not None:
                return self._root_folders
            
            if self.dry_run:
                self.logger.debug("DRY RUN: Using mock root folders")
                self._root_folders = [{'path': '/music', 'id': 1}]
                return self._root_folders
            
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/rootfolder"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                self._root_folders = response.json()
                self.logger.debug(f"Retrieved {len(self._root_folders)} root folders")
                return self._root_folders
            else:
                self.logger.error(f"Failed to get root folders: HTTP {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting root folders: {e}")
            return []

    def get_metadata_profiles(self) -> List[Dict[str, Any]]:
        """Get metadata profiles from Lidarr (cached)"""
        try:
            if hasattr(self, '_metadata_profiles') and self._metadata_profiles is not None:
                return self._metadata_profiles
            
            if self.dry_run:
                self.logger.debug("DRY RUN: Using mock metadata profiles")
                self._metadata_profiles = [{'id': 1, 'name': 'Mock Metadata Profile'}]
                return self._metadata_profiles
            
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/metadataprofile"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                self._metadata_profiles = response.json()
                self.logger.debug(f"Retrieved {len(self._metadata_profiles)} metadata profiles")
                return self._metadata_profiles
            else:
                self.logger.error(f"Failed to get metadata profiles: HTTP {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting metadata profiles: {e}")
            return []

    def search_musicbrainz_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """Search for artist in MusicBrainz
        
        Args:
            artist_name: Name of artist to search for
            
        Returns:
            Dict with MusicBrainz artist data or None if not found
        """
        try:
            # Clean up artist name for search
            search_name = re.sub(r'[^\w\s-]', '', artist_name).strip()
            
            self.logger.debug(f"Searching MusicBrainz for: {search_name}")
            
            url = "https://musicbrainz.org/ws/2/artist"
            params = {
                'query': f'artist:"{search_name}"',
                'fmt': 'json',
                'limit': 10
            }
            headers = {
                'User-Agent': 'LidarrUtils/1.0'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                artists = data.get('artists', [])
                
                # Look for exact match first
                for artist in artists:
                    if artist.get('name', '').lower() == artist_name.lower():
                        result = {
                            'musicbrainz_id': artist.get('id'),
                            'name': artist.get('name'),
                            'sort_name': artist.get('sort-name'),
                            'disambiguation': artist.get('disambiguation', ''),
                            'type': artist.get('type', ''),
                            'score': artist.get('score', 0)
                        }
                        self.logger.debug(f"Found exact MusicBrainz match for '{artist_name}': {result['name']}")
                        return result
                
                # Look for close matches (handle cases like "JAY Z" vs "Jay-Z")
                for artist in artists:
                    artist_mb_name = artist.get('name', '').lower()
                    search_lower = artist_name.lower()
                    
                    # Handle common variations
                    if (artist_mb_name.replace('-', ' ') == search_lower.replace('-', ' ') or
                        artist_mb_name.replace(' ', '') == search_lower.replace(' ', '') or
                        artist_mb_name == search_lower.replace(' ', '-')):
                        
                        result = {
                            'musicbrainz_id': artist.get('id'),
                            'name': artist.get('name'),
                            'sort_name': artist.get('sort-name'),
                            'disambiguation': artist.get('disambiguation', ''),
                            'type': artist.get('type', ''),
                            'score': artist.get('score', 0)
                        }
                        self.logger.info(f"Found close MusicBrainz match for '{artist_name}': '{result['name']}'")
                        return result
                
                # If no exact/close match, return the first result with high score
                if artists and artists[0].get('score', 0) >= 90:
                    artist = artists[0]
                    result = {
                        'musicbrainz_id': artist.get('id'),
                        'name': artist.get('name'),
                        'sort_name': artist.get('sort-name'),
                        'disambiguation': artist.get('disambiguation', ''),
                        'type': artist.get('type', ''),
                        'score': artist.get('score', 0)
                    }
                    self.logger.info(f"Using high-score MusicBrainz match for '{artist_name}': '{result['name']}' (score: {result['score']})")
                    return result
                
                self.logger.debug(f"No suitable MusicBrainz match found for '{artist_name}'")
                return None
            else:
                self.logger.warning(f"MusicBrainz search failed for {artist_name}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.warning(f"Error searching MusicBrainz for {artist_name}: {e}")
            return None

    def get_artists(self) -> List[Dict[str, Any]]:
        """Get all artists from Lidarr
        
        Note: Still queries Lidarr even in dry run mode to check for existing artists.
        Only the add/modify operations are skipped in dry run, not reads.
        """
        try:
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/artist"
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                artists = response.json()
                self.logger.debug(f"Retrieved {len(artists)} artists from Lidarr")
                return artists
            else:
                self.logger.error(f"Failed to get artists from Lidarr: HTTP {response.status_code}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting artists from Lidarr: {e}")
            return []

    def normalize_artist_name(self, artist_name: str) -> str:
        """Normalize artist name for comparison by handling different unicode characters"""
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
        
        return normalized.strip()

    def artist_exists(self, artist_name: str) -> bool:
        """Check if artist already exists in Lidarr
        
        Args:
            artist_name: Name of artist to check
            
        Returns:
            True if artist exists, False otherwise
        """
        try:
            artists = self.get_artists()
            normalized_search_name = self.normalize_artist_name(artist_name)
            
            for artist in artists:
                lidarr_artist_name = artist.get('artistName', '').strip()
                if self.normalize_artist_name(lidarr_artist_name) == normalized_search_name:
                    self.logger.debug(f"Artist '{artist_name}' already exists in Lidarr")
                    return True
            
            self.logger.debug(f"Artist '{artist_name}' not found in Lidarr")
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking if artist exists: {e}")
            return False

    def add_artist_with_future_monitoring(self, artist_name: str, 
                                          musicbrainz_data: Optional[Dict[str, Any]] = None,
                                          search_for_missing: bool = False) -> bool:
        """Add artist to Lidarr with future monitoring (recommended for new artists)
        
        This function:
        1. Adds the artist to Lidarr
        2. Sets artist monitoring to True
        3. Sets album monitoring to 'future' (only monitor future releases)
        4. Optionally searches for missing albums (default: False)
        
        Args:
            artist_name: Name of artist to add
            musicbrainz_data: Optional MusicBrainz metadata dict
            search_for_missing: Whether to search for existing albums (default: False)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dry_run:
                mb_info = f" (MusicBrainz: {musicbrainz_data['name']})" if musicbrainz_data else ""
                self.logger.debug(f"DRY RUN: Would add artist with future monitoring: {artist_name}{mb_info}")
                return True
            
            # Check if artist already exists
            if self.artist_exists(artist_name):
                self.logger.debug(f"Artist '{artist_name}' already exists in Lidarr")
                return True
            
            # Get Lidarr configuration
            quality_profiles = self.get_quality_profiles()
            root_folders = self.get_root_folders()
            metadata_profiles = self.get_metadata_profiles()
            
            if not quality_profiles or not root_folders or not metadata_profiles:
                self.logger.error("Failed to get Lidarr configuration (quality profiles, root folders, or metadata profiles)")
                return False
            
            quality_profile_id = quality_profiles[0]['id']
            root_folder_path = root_folders[0]['path']
            metadata_profile_id = metadata_profiles[0]['id']
            
            # Prepare artist data for future monitoring
            artist_data = {
                'artistName': musicbrainz_data['name'] if musicbrainz_data else artist_name,
                'foreignArtistId': musicbrainz_data['musicbrainz_id'] if musicbrainz_data else None,
                'qualityProfileId': quality_profile_id,
                'metadataProfileId': metadata_profile_id,
                'rootFolderPath': root_folder_path,
                'monitored': True,  # Monitor the artist
                'albumFolder': True,
                'addOptions': {
                    'monitor': 'none',  # Don't monitor any existing albums
                    'searchForMissingAlbums': search_for_missing  # Usually False for new artists
                }
            }
            
            # Remove None values
            artist_data = {k: v for k, v in artist_data.items() if v is not None}
            
            # Log the artist data for debugging
            self.logger.debug(f"Artist data to be sent to Lidarr: {artist_data}")
            
            # Add artist to Lidarr
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/artist"
            response = requests.post(url, headers=headers, json=artist_data, timeout=30)
            
            if response.status_code in [200, 201]:
                mb_info = f" (MusicBrainz: {musicbrainz_data['name']})" if musicbrainz_data else ""
                self.logger.info(f"Successfully added artist with future monitoring: {artist_name}{mb_info}")
                return True
            else:
                self.logger.warning(f"Failed to add artist {artist_name} to Lidarr: HTTP {response.status_code}")
                if response.text:
                    self.logger.warning(f"Error response: {response.text[:500]}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error adding artist {artist_name} to Lidarr: {e}")
            return False

    def add_artist_with_all_monitoring(self, artist_name: str, 
                                       musicbrainz_data: Optional[Dict[str, Any]] = None,
                                       search_for_missing: bool = True) -> bool:
        """Add artist to Lidarr with all albums monitoring (use with caution)
        
        This function:
        1. Adds the artist to Lidarr
        2. Sets artist monitoring to True  
        3. Sets album monitoring to 'all' (monitor all releases)
        4. Optionally searches for missing albums (default: True)
        
        WARNING: This will monitor ALL albums by the artist, which could trigger
        downloads of entire discographies. Use sparingly and only when intended.
        
        Args:
            artist_name: Name of artist to add
            musicbrainz_data: Optional MusicBrainz metadata dict
            search_for_missing: Whether to search for existing albums (default: True)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dry_run:
                mb_info = f" (MusicBrainz: {musicbrainz_data['name']})" if musicbrainz_data else ""
                self.logger.debug(f"DRY RUN: Would add artist with ALL albums monitoring: {artist_name}{mb_info}")
                return True
            
            # Check if artist already exists
            if self.artist_exists(artist_name):
                self.logger.debug(f"Artist '{artist_name}' already exists in Lidarr")
                return True
            
            # Get Lidarr configuration
            quality_profiles = self.get_quality_profiles()
            root_folders = self.get_root_folders()
            metadata_profiles = self.get_metadata_profiles()
            
            if not quality_profiles or not root_folders or not metadata_profiles:
                self.logger.error("Failed to get Lidarr configuration (quality profiles, root folders, or metadata profiles)")
                return False
            
            quality_profile_id = quality_profiles[0]['id']
            root_folder_path = root_folders[0]['path']
            metadata_profile_id = metadata_profiles[0]['id']
            
            # Prepare artist data for all albums monitoring
            artist_data = {
                'artistName': musicbrainz_data['name'] if musicbrainz_data else artist_name,
                'foreignArtistId': musicbrainz_data['musicbrainz_id'] if musicbrainz_data else None,
                'qualityProfileId': quality_profile_id,
                'metadataProfileId': metadata_profile_id,
                'rootFolderPath': root_folder_path,
                'monitored': True,  # Monitor the artist
                'albumFolder': True,
                'addOptions': {
                    'monitor': 'none',  # Don't monitor any existing albums automatically
                    'searchForMissingAlbums': search_for_missing
                }
            }
            
            # Remove None values
            artist_data = {k: v for k, v in artist_data.items() if v is not None}
            
            # Add artist to Lidarr
            headers = self._get_headers()
            url = f"{self.lidarr_url}/api/v1/artist"
            response = requests.post(url, headers=headers, json=artist_data, timeout=30)
            
            if response.status_code in [200, 201]:
                mb_info = f" (MusicBrainz: {musicbrainz_data['name']})" if musicbrainz_data else ""
                self.logger.warning(f"Added artist with ALL albums monitoring: {artist_name}{mb_info}")
                return True
            else:
                self.logger.warning(f"Failed to add artist {artist_name} to Lidarr: HTTP {response.status_code}")
                if response.text:
                    self.logger.debug(f"Error response: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error adding artist {artist_name} to Lidarr: {e}")
            return False

    def set_album_monitored(self, album_data: Dict[str, Any], monitored: bool = True) -> bool:
        """Set an album's monitoring status in Lidarr
        
        Args:
            album_data: Album data dict from Lidarr API
            monitored: Whether to monitor the album
            
        Returns:
            True if successful, False otherwise
        """
        try:
            album_id = album_data.get('id')
            album_title = album_data.get('title', 'Unknown')
            
            if not album_id:
                self.logger.error(f"Album '{album_title}' has no ID")
                return False
            
            if self.dry_run:
                action = "monitor" if monitored else "unmonitor"
                self.logger.info(f"DRY RUN: Would {action} album '{album_title}'")
                return True
            
            # Update the album data
            updated_album = album_data.copy()
            updated_album['monitored'] = monitored
            
            headers = self._get_headers()
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

    def add_artist_and_search_musicbrainz(self, artist_name: str, 
                                          future_monitoring: bool = True,
                                          search_for_missing: bool = False) -> bool:
        """Convenience function: Search MusicBrainz and add artist with proper monitoring
        
        This is the recommended function for most use cases. It:
        1. Searches MusicBrainz for artist metadata
        2. Adds artist to Lidarr with appropriate monitoring settings
        3. Uses future monitoring by default (safer)
        
        Args:
            artist_name: Name of artist to add
            future_monitoring: If True, only monitor future releases (default: True)
            search_for_missing: Whether to search for existing albums (default: False)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if artist already exists (even in dry run mode)
            if self.artist_exists(artist_name):
                if self.dry_run:
                    self.logger.debug(f"Artist '{artist_name}' already exists in Lidarr (would skip)")
                else:
                    self.logger.debug(f"Artist '{artist_name}' already exists in Lidarr")
                return True
            
            # Search MusicBrainz for artist metadata
            musicbrainz_data = None
            try:
                musicbrainz_data = self.search_musicbrainz_artist(artist_name)
                if musicbrainz_data:
                    self.logger.debug(f"Found MusicBrainz data for '{artist_name}': {musicbrainz_data['name']} (ID: {musicbrainz_data['musicbrainz_id']})")
                else:
                    self.logger.debug(f"No MusicBrainz data found for '{artist_name}', will add with original name")
            except Exception as mb_error:
                self.logger.warning(f"MusicBrainz search failed for '{artist_name}': {mb_error}")
            
            # Add artist with appropriate monitoring
            if future_monitoring:
                return self.add_artist_with_future_monitoring(
                    artist_name, 
                    musicbrainz_data, 
                    search_for_missing
                )
            else:
                return self.add_artist_with_all_monitoring(
                    artist_name, 
                    musicbrainz_data, 
                    search_for_missing
                )
                
        except Exception as e:
            self.logger.error(f"Error in add_artist_and_search_musicbrainz for '{artist_name}': {e}")
            return False


# Convenience functions for backward compatibility and easy usage
def add_artist_to_lidarr(artist_name: str, 
                        lidarr_url: str = None, 
                        lidarr_api_key: str = None,
                        logger: logging.Logger = None,
                        dry_run: bool = False,
                        future_monitoring: bool = True) -> bool:
    """Convenience function to add an artist to Lidarr with future monitoring
    
    Args:
        artist_name: Name of artist to add
        lidarr_url: Lidarr server URL (or from LIDARR_URL env var)
        lidarr_api_key: Lidarr API key (or from LIDARR_API_KEY env var)
        logger: Logger instance (optional)
        dry_run: If True, don't make actual changes
        future_monitoring: If True, only monitor future releases (default: True)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        client = LidarrClient(lidarr_url, lidarr_api_key, logger, dry_run)
        return client.add_artist_and_search_musicbrainz(
            artist_name, 
            future_monitoring=future_monitoring
        )
    except Exception as e:
        if logger:
            logger.error(f"Error in add_artist_to_lidarr convenience function: {e}")
        else:
            print(f"Error adding artist to Lidarr: {e}")
        return False


if __name__ == "__main__":
    # Example usage
    import sys
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    if len(sys.argv) > 1:
        artist_name = sys.argv[1]
        dry_run = '--dry-run' in sys.argv
        
        print(f"Testing Lidarr utilities with artist: {artist_name}")
        
        try:
            client = LidarrClient(logger=logger, dry_run=dry_run)
            
            # Test connection
            if client.test_connection():
                # Try adding artist
                success = client.add_artist_and_search_musicbrainz(artist_name)
                if success:
                    print(f"✅ Successfully processed artist: {artist_name}")
                else:
                    print(f"❌ Failed to process artist: {artist_name}")
            else:
                print("❌ Failed to connect to Lidarr")
                
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("Usage: python lidarr_utils.py 'Artist Name' [--dry-run]")