#!/usr/bin/env python3
"""
Settings module for SoulSeekarr
Provides configuration management with database storage and environment variable fallback.
"""

import os
import logging
from typing import Optional, Dict, Any
from database import get_db

logger = logging.getLogger(__name__)

class SettingsManager:
    """Manages application settings with database storage and environment fallback."""
    
    def __init__(self):
        self.db = get_db()
        self._cache = {}
        self._cache_valid = False
    
    def _refresh_cache(self):
        """Refresh the settings cache from database."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM app_settings")
                self._cache = {row['key']: row['value'] for row in cursor.fetchall()}
                self._cache_valid = True
                logger.debug(f"Refreshed settings cache with {len(self._cache)} entries")
        except Exception as e:
            logger.error(f"Error refreshing settings cache: {e}")
            self._cache = {}
            self._cache_valid = False
    
    def get_setting(self, key: str, default: Optional[str] = None, env_fallback: bool = True) -> Optional[str]:
        """
        Get a setting value by key.
        
        Args:
            key: Setting key to retrieve
            default: Default value if setting not found
            env_fallback: Whether to fallback to environment variables
        
        Returns:
            Setting value or default if not found
        """
        # Refresh cache if needed
        if not self._cache_valid:
            self._refresh_cache()
        
        # Try database first
        if key in self._cache:
            return self._cache[key]
        
        # Try environment variable fallback
        if env_fallback:
            env_value = os.environ.get(key)
            if env_value is not None:
                return env_value
        
        return default
    
    def set_setting(self, key: str, value: str, description: Optional[str] = None):
        """
        Set a setting value.
        
        Args:
            key: Setting key
            value: Setting value
            description: Optional description
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO app_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (key, value, description))
                conn.commit()
                
                # Update cache
                self._cache[key] = value
                logger.debug(f"Set setting {key} = {value}")
        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            raise
    
    def get_connection_settings(self, service: str) -> Dict[str, str]:
        """
        Get all connection settings for a service.
        
        Args:
            service: Service name (navidrome, lidarr, slskd)
        
        Returns:
            Dictionary of connection settings
        """
        settings = {}
        prefix = f"{service}_connection_"
        
        # Refresh cache if needed
        if not self._cache_valid:
            self._refresh_cache()
        
        # Get settings from cache
        for key, value in self._cache.items():
            if key.startswith(prefix):
                setting_name = key[len(prefix):]
                settings[setting_name] = value
        
        return settings
    
    def get_service_config(self, service: str) -> Dict[str, str]:
        """
        Get service configuration with environment fallback.
        
        Args:
            service: Service name (navidrome, lidarr, slskd)
        
        Returns:
            Dictionary with service configuration
        """
        config = {}
        
        if service == 'navidrome':
            config = {
                'url': self.get_setting('navidrome_connection_url', os.environ.get('NAVIDROME_URL', '')),
                'username': self.get_setting('navidrome_connection_username', os.environ.get('NAVIDROME_USERNAME', '')),
                'password': self.get_setting('navidrome_connection_password', os.environ.get('NAVIDROME_PASSWORD', ''))
            }
        elif service == 'lidarr':
            config = {
                'url': self.get_setting('lidarr_connection_url', os.environ.get('LIDARR_URL', '')),
                'api_key': self.get_setting('lidarr_connection_api_key', os.environ.get('LIDARR_API_KEY', ''))
            }
        elif service == 'slskd':
            config = {
                'url': self.get_setting('slskd_connection_url', os.environ.get('SLSKD_URL', '')),
                'api_key': self.get_setting('slskd_connection_api_key', os.environ.get('SLSKD_API_KEY', ''))
            }
        
        return config
    
    def get_all_service_configs(self) -> Dict[str, Dict[str, str]]:
        """Get all service configurations."""
        return {
            'navidrome': self.get_service_config('navidrome'),
            'lidarr': self.get_service_config('lidarr'),
            'slskd': self.get_service_config('slskd')
        }
    
    def clear_cache(self):
        """Clear the settings cache."""
        self._cache = {}
        self._cache_valid = False
        logger.debug("Settings cache cleared")

# Global settings manager instance
settings_manager = SettingsManager()

# Convenience functions for easy access
def get_setting(key: str, default: Optional[str] = None, env_fallback: bool = True) -> Optional[str]:
    """Get a setting value by key."""
    return settings_manager.get_setting(key, default, env_fallback)

def set_setting(key: str, value: str, description: Optional[str] = None):
    """Set a setting value."""
    settings_manager.set_setting(key, value, description)

def get_service_config(service: str) -> Dict[str, str]:
    """Get service configuration."""
    return settings_manager.get_service_config(service)

def get_navidrome_config() -> Dict[str, str]:
    """Get Navidrome configuration."""
    return get_service_config('navidrome')

def get_lidarr_config() -> Dict[str, str]:
    """Get Lidarr configuration."""
    return get_service_config('lidarr')

def get_slskd_config() -> Dict[str, str]:
    """Get slskd configuration."""
    return get_service_config('slskd')

# Legacy environment variable functions for backward compatibility
def get_navidrome_url() -> str:
    """Get Navidrome URL."""
    return get_setting('navidrome_connection_url', os.environ.get('NAVIDROME_URL', ''))

def get_navidrome_username() -> str:
    """Get Navidrome username."""
    return get_setting('navidrome_connection_username', os.environ.get('NAVIDROME_USERNAME', ''))

def get_navidrome_password() -> str:
    """Get Navidrome password."""
    return get_setting('navidrome_connection_password', os.environ.get('NAVIDROME_PASSWORD', ''))

def get_lidarr_url() -> str:
    """Get Lidarr URL."""
    return get_setting('lidarr_connection_url', os.environ.get('LIDARR_URL', ''))

def get_lidarr_api_key() -> str:
    """Get Lidarr API key."""
    return get_setting('lidarr_connection_api_key', os.environ.get('LIDARR_API_KEY', ''))

def get_slskd_url() -> str:
    """Get slskd URL."""
    return get_setting('slskd_connection_url', os.environ.get('SLSKD_URL', ''))

def get_slskd_api_key() -> str:
    """Get slskd API key."""
    return get_setting('slskd_connection_api_key', os.environ.get('SLSKD_API_KEY', ''))

# Spotify and Tidal settings
def get_spotify_client_id() -> str:
    """Get Spotify client ID."""
    return get_setting('spotify_oauth_client_id', os.environ.get('SPOTIFY_CLIENT_ID', ''))

def get_spotify_client_secret() -> str:
    """Get Spotify client secret."""
    return get_setting('spotify_oauth_client_secret', os.environ.get('SPOTIFY_CLIENT_SECRET', ''))

def get_spotify_access_token() -> str:
    """Get Spotify access token."""
    return get_setting('spotify_oauth_access_token', os.environ.get('SPOTIFY_ACCESS_TOKEN', ''))

def get_tidal_access_token() -> str:
    """Get Tidal access token."""
    return get_setting('tidal_oauth_access_token', os.environ.get('TIDAL_ACCESS_TOKEN', ''))

def get_tidal_country_code() -> str:
    """Get Tidal country code."""
    return get_setting('tidal_connection_country_code', os.environ.get('TIDAL_COUNTRY_CODE', 'US'))

# Other common settings
def get_target_uid() -> int:
    """Get target UID for file operations."""
    return int(get_setting('file_target_uid', os.environ.get('TARGET_UID', '1000')))

def get_target_gid() -> int:
    """Get target GID for file operations."""
    return int(get_setting('file_target_gid', os.environ.get('TARGET_GID', '1000')))

def is_dry_run() -> bool:
    """Check if dry run mode is enabled."""
    return get_setting('dry_run_mode', os.environ.get('DRY_RUN', 'false')).lower() == 'true'

def get_slskd_downloads_complete_path() -> str:
    """Get slskd completed downloads path."""
    return get_setting('slskd_downloads_complete_path', os.environ.get('SLSKD_DOWNLOADS_COMPLETE', ''))

def get_slskd_downloads_incomplete_path() -> str:
    """Get slskd incomplete downloads path."""
    return get_setting('slskd_downloads_incomplete_path', os.environ.get('SLSKD_DOWNLOADS_INCOMPLETE', ''))

def get_slskd_downloads_path() -> str:
    """Get slskd downloads base path."""
    return get_setting('slskd_downloads_path', os.environ.get('SLSKD_DOWNLOADS_PATH', ''))

# Music directory settings
def get_music_directory() -> str:
    """Get main music directory (Not_Owned)."""
    return get_setting('music_directory', os.environ.get('MUSIC_DIRECTORY', '/media/Not_Owned'))

def get_not_owned_directory() -> str:
    """Get Not_Owned music directory."""
    return get_setting('not_owned_directory', os.environ.get('NOT_OWNED_DIRECTORY', '/media/Not_Owned'))

def get_owned_directory() -> str:
    """Get Owned music directory."""
    return get_setting('owned_directory', os.environ.get('OWNED_DIRECTORY', '/media/Owned'))

def get_incomplete_directory() -> str:
    """Get Incomplete music directory."""
    return get_setting('incomplete_directory', os.environ.get('INCOMPLETE_DIRECTORY', '/media/Incomplete'))

def get_downloads_completed_directory() -> str:
    """Get completed downloads directory."""
    return get_setting('downloads_completed_directory', os.environ.get('DOWNLOADS_COMPLETED', '/downloads/completed'))