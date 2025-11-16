"""
slskd Utilities - Shared download logic for slskd integration

This module provides reusable functions for interacting with slskd,
including sophisticated search and download workflows.

Author: SoulSeekarr
Version: 1.0
"""

import os
import re
import time
import logging
import requests
from typing import Dict, List, Optional, Tuple, Any


class SlskdDownloader:
    """Handles slskd search and download operations with smart matching."""
    
    def __init__(self, slskd_url: str, slskd_api_key: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the slskd downloader.
        
        Args:
            slskd_url: Base URL for slskd API (e.g., "http://localhost:5030")
            slskd_api_key: API key for authentication
            logger: Optional logger instance (creates one if not provided)
        """
        self.slskd_url = slskd_url.rstrip('/')
        self.slskd_api_key = slskd_api_key
        self.logger = logger or logging.getLogger(__name__)
        self.headers = {
            'Content-Type': 'application/json',
            'X-API-Key': self.slskd_api_key
        }
    
    def search_and_download(self, search_query: str, search_type: str = 'album', 
                           target_name: Optional[str] = None, dry_run: bool = False) -> bool:
        """
        Search for content and download the best match.
        
        Args:
            search_query: Search string (e.g., "Artist Album" or "Artist - Song")
            search_type: Type of content ('album', 'song', 'any')
            target_name: Optional specific name to match against (for scoring)
            dry_run: If True, only simulate the download
            
        Returns:
            True if successful, False otherwise
        """
        if dry_run:
            self.logger.info(f"[DRY RUN] Would search slskd for: {search_query}")
            return True
        
        try:
            # Step 1: Initiate search
            search_id = self._initiate_search(search_query)
            if not search_id:
                return False
            
            # Step 2: Wait for search completion
            file_count = self._wait_for_search_completion(search_id)
            if file_count == 0:
                self.logger.info(f"No files found for: {search_query}")
                return True  # Not an error, just no results
            
            # Step 3: Get search results
            results = self._get_search_results(search_id)
            if not results:
                self.logger.warning(f"Failed to get search results")
                return False
            
            # Step 4: Find best match based on type
            if search_type == 'album':
                best_match = self._find_best_album_match(results, target_name)
            elif search_type == 'song':
                best_match = self._find_best_song_match(results, search_query, target_name)
            else:
                best_match = self._find_best_any_match(results)
            
            if not best_match:
                self.logger.warning(f"No suitable match found")
                return False
            
            # Step 5: Download the files
            return self._download_files(best_match)
            
        except Exception as e:
            self.logger.error(f"Error in search_and_download: {e}")
            return False
    
    def _initiate_search(self, search_query: str) -> Optional[str]:
        """Initiate a search and return the search ID."""
        try:
            url = f"{self.slskd_url}/api/v0/searches"
            data = {
                'searchText': search_query,
                'timeout': 45000
            }
            
            self.logger.info(f"Searching slskd for: {search_query}")
            response = requests.post(url, headers=self.headers, json=data, timeout=50)
            
            if response.status_code != 200:
                self.logger.warning(f"Failed to initiate search: HTTP {response.status_code}")
                return None
            
            search_response = response.json()
            search_id = search_response.get('id')
            
            if not search_id:
                self.logger.warning(f"No search ID in response")
                return None
            
            return search_id
            
        except Exception as e:
            self.logger.error(f"Error initiating search: {e}")
            return None
    
    def _wait_for_search_completion(self, search_id: str, max_wait: int = 120, 
                                    check_interval: int = 4) -> int:
        """
        Wait for search to complete and return file count.
        
        Args:
            search_id: ID of the search to monitor
            max_wait: Maximum time to wait in seconds
            check_interval: How often to check status in seconds
            
        Returns:
            Number of files found
        """
        self.logger.info("Waiting for search to complete...")
        max_attempts = max_wait // check_interval
        
        for attempt in range(1, max_attempts + 1):
            time.sleep(check_interval)
            
            try:
                status_url = f"{self.slskd_url}/api/v0/searches/{search_id}"
                response = requests.get(status_url, headers=self.headers, timeout=30)
                
                if response.status_code != 200:
                    continue
                
                status_data = response.json()
                file_count = status_data.get('fileCount', 0)
                response_count = status_data.get('responseCount', 0)
                is_complete = status_data.get('isComplete', False)
                
                elapsed = attempt * check_interval
                self.logger.info(f"  Search progress: {response_count} users, {file_count} files, "
                               f"complete: {is_complete} ({elapsed}s)")
                
                if is_complete:
                    self.logger.info(f"Search completed after {elapsed}s with {file_count} files")
                    return file_count
                
                # Early exit if we have results and waited long enough
                if elapsed >= 60 and file_count > 0:
                    self.logger.info(f"Proceeding with {file_count} files after {elapsed}s")
                    return file_count
                    
            except Exception as e:
                self.logger.debug(f"Error checking search status: {e}")
                continue
        
        # Timeout - return whatever we have
        return 0
    
    def _get_search_results(self, search_id: str) -> Optional[List[Dict]]:
        """Get the search results/responses."""
        try:
            url = f"{self.slskd_url}/api/v0/searches/{search_id}/responses"
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code != 200:
                self.logger.warning(f"Failed to get results: HTTP {response.status_code}")
                return None
            
            results = response.json()
            return results if results else None
            
        except Exception as e:
            self.logger.error(f"Error getting search results: {e}")
            return None
    
    def _normalize_string(self, text: str) -> str:
        """Normalize a string for comparison."""
        if not text:
            return ""
        # Convert to lowercase, remove special chars, normalize whitespace
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def _clean_song_title(self, title: str) -> str:
        """Clean song title by removing featuring artists and other metadata."""
        if not title:
            return ""
        
        cleaned = title
        
        # Remove common patterns that appear in titles but not in file names
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
    
    def _find_best_album_match(self, results: List[Dict], album_name: Optional[str] = None) -> Optional[Dict]:
        """
        Find the best album match from search results.
        
        Looks for results with multiple files (full albums), preferring:
        1. Albums with matching names
        2. FLAC quality
        3. Higher file counts
        4. Better bitrates
        
        Returns only the BEST single version of the album (not all versions).
        """
        album_candidates = []
        
        for result in results:
            if not isinstance(result, dict):
                continue
            
            username = result.get('username', '')
            files = result.get('files', [])
            
            # Group files by their parent directory (each directory = one album version)
            album_versions = {}
            
            for f in files:
                if not self._is_audio_file(f.get('filename', '')):
                    continue
                
                filename = f.get('filename', '')
                # Extract the directory path (everything before the last separator)
                if '\\' in filename:
                    directory = filename.rsplit('\\', 1)[0]
                elif '/' in filename:
                    directory = filename.rsplit('/', 1)[0]
                else:
                    directory = 'root'
                
                if directory not in album_versions:
                    album_versions[directory] = []
                album_versions[directory].append(f)
            
            # Evaluate each album version separately
            for directory, audio_files in album_versions.items():
                # Albums should have multiple files
                if len(audio_files) < 3:
                    continue
                
                # Calculate quality score
                has_flac = any('.flac' in f.get('filename', '').lower() for f in audio_files)
                avg_bitrate = self._calculate_avg_bitrate(audio_files)
                
                # Calculate name match score if album name provided
                name_score = 0
                if album_name:
                    normalized_album = self._normalize_string(album_name)
                    normalized_dir = self._normalize_string(directory)
                    if normalized_album in normalized_dir:
                        name_score += 10
                
                # Overall score
                score = 0
                score += name_score
                score += 20 if has_flac else 0
                score += len(audio_files)  # More files = better (likely complete album)
                score += min(avg_bitrate // 32, 10)  # Bitrate bonus (max 10)
                
                album_candidates.append({
                    'username': username,
                    'files': audio_files,
                    'directory': directory,
                    'file_count': len(audio_files),
                    'has_flac': has_flac,
                    'avg_bitrate': avg_bitrate,
                    'score': score
                })
        
        if not album_candidates:
            self.logger.info("No album candidates found")
            return None
        
        # Sort by score (highest first)
        album_candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # Log top candidates
        self.logger.info(f"Found {len(album_candidates)} album versions:")
        for i, candidate in enumerate(album_candidates[:5]):
            dir_name = os.path.basename(candidate['directory'])
            self.logger.info(f"  {i+1}. {dir_name}: {candidate['file_count']} files, "
                           f"FLAC: {candidate['has_flac']}, "
                           f"Bitrate: {candidate['avg_bitrate']}kbps, "
                           f"Score: {candidate['score']}")
        
        # Return ONLY the best version
        best = album_candidates[0]
        self.logger.info(f"Selected: {os.path.basename(best['directory'])} "
                        f"({best['file_count']} files, FLAC: {best['has_flac']})")
        
        return best
    
    def _find_best_song_match(self, results: List[Dict], search_query: str, 
                             target_title: Optional[str] = None) -> Optional[Dict]:
        """
        Find the best song match from search results.
        
        Uses sophisticated word-based matching and quality scoring.
        """
        # Parse search query to extract artist, title, and possibly album
        parts = search_query.split('-', 1)
        if len(parts) == 2:
            target_artist = self._normalize_string(parts[0])
            query_title = parts[1]
        else:
            target_artist = ""
            query_title = search_query
        
        # Check if target_title includes album info (from "title (from album)" format)
        target_album = ""
        if target_title and "(from " in target_title:
            title_parts = target_title.split(" (from ", 1)
            actual_title = title_parts[0]
            target_album = self._normalize_string(title_parts[1].rstrip(")"))
        else:
            actual_title = target_title or query_title
        
        # Clean and normalize title
        cleaned_title = self._clean_song_title(actual_title)
        normalized_title = self._normalize_string(cleaned_title)
        
        self.logger.info(f"Looking for matches - Artist: '{target_artist}', Title: '{normalized_title}'")
        if target_album:
            self.logger.info(f"  Album context: '{target_album}'")
        if cleaned_title != actual_title:
            self.logger.debug(f"  (Cleaned title: {actual_title} -> {cleaned_title})")
        
        # Extract words for matching
        artist_words = [w for w in target_artist.split() if len(w) > 2]
        title_words = [w for w in normalized_title.split() if len(w) > 2]
        album_words = [w for w in target_album.split() if len(w) > 2] if target_album else []
        all_words = [w for w in (target_artist + ' ' + normalized_title).split() if len(w) > 3]
        
        candidates = []
        total_files = 0
        
        for result in results:
            if not isinstance(result, dict):
                continue
            
            username = result.get('username', '')
            files = result.get('files', [])
            total_files += len(files)
            
            for file_info in files:
                if not isinstance(file_info, dict):
                    continue
                
                filename = file_info.get('filename', '')
                filesize = file_info.get('size', 0)
                bitrate = file_info.get('bitRate', 0)
                
                # Must be audio file
                if not self._is_audio_file(filename):
                    continue
                
                # Skip very small files
                if filesize < 1000000:  # 1MB minimum
                    continue
                
                # Skip huge files (probably not single songs)
                size_mb = filesize / (1024 * 1024)
                if size_mb > 100:
                    continue
                
                # Normalize filename
                normalized_filename = self._normalize_string(filename)
                
                # Remove track numbers for better matching (e.g., "08 Song.mp3" -> "Song.mp3")
                cleaned_filename = re.sub(r'^\d+\s*[-.]?\s*', '', normalized_filename)
                
                # Check for version indicators - but only penalize if they don't match target
                unwanted_keywords = [
                    'remix', 'mix)', 'edit)', 'version)', 'live', 'acoustic', 'demo', 
                    'karaoke', 'instrumental', 'radio edit', 'clean version', 'explicit',
                    'cover', 'tribute', 'mashup', 'bootleg', 'alternate', 'alternative'
                ]
                
                # Check if the target title itself contains version indicators
                target_has_remix = 'remix' in normalized_title.lower()
                target_has_live = 'live' in normalized_title.lower() 
                target_has_acoustic = 'acoustic' in normalized_title.lower()
                target_has_edit = 'edit' in normalized_title.lower()
                target_has_version = 'version' in normalized_title.lower()
                
                unwanted_penalty = 0
                for keyword in unwanted_keywords:
                    if keyword in normalized_filename.lower() or keyword in cleaned_filename.lower():
                        # Only penalize if target doesn't expect this type of version
                        should_penalize = True
                        
                        if keyword == 'remix' and target_has_remix:
                            should_penalize = False  # Target expects remix, so don't penalize
                        elif keyword == 'live' and target_has_live:
                            should_penalize = False  # Target expects live version
                        elif keyword == 'acoustic' and target_has_acoustic:
                            should_penalize = False  # Target expects acoustic version
                        elif keyword in ['edit)', 'version)'] and (target_has_edit or target_has_version):
                            should_penalize = False  # Target expects edit/version
                        
                        if should_penalize:
                            unwanted_penalty += 20  # Heavy penalty for unwanted versions
                
                # Special penalty for obvious remix patterns - but only if target doesn't expect them
                remix_patterns = [
                    r'\(.*remix.*\)', r'\[.*remix.*\]',  # (any remix) or [any remix]
                    r'\(.*mix\)', r'\[.*mix\]',          # (any mix) or [any mix] 
                    r'\(.*edit\)', r'\[.*edit\]',        # (any edit) or [any edit]
                ]
                
                for pattern in remix_patterns:
                    if re.search(pattern, normalized_filename, re.IGNORECASE):
                        # Only penalize remix patterns if target doesn't expect remix
                        if 'remix' in pattern and target_has_remix:
                            continue  # Target expects remix, don't penalize
                        elif ('mix' in pattern and not 'remix' in pattern) and target_has_remix:
                            continue  # Target expects some kind of mix
                        elif 'edit' in pattern and target_has_edit:
                            continue  # Target expects edit
                        else:
                            unwanted_penalty += 30  # Very heavy penalty for unexpected patterns
                
                # Calculate match score
                match_score = 0
                
                # Artist word matching (be lenient)
                for word in artist_words:
                    if word in normalized_filename or word in cleaned_filename:
                        match_score += 10
                
                # Title word matching (more important)
                for word in title_words:
                    if word in normalized_filename or word in cleaned_filename:
                        match_score += 15
                
                # Album word matching bonus (when album context available)
                album_bonus = 0
                if album_words:
                    for word in album_words:
                        if word in normalized_filename or word in cleaned_filename:
                            album_bonus += 10
                    # If we have album context but no album words match, slight penalty
                    if album_bonus == 0:
                        match_score -= 5
                    else:
                        match_score += album_bonus
                
                # Last resort: any meaningful word matches
                if match_score == 0:
                    for word in all_words:
                        if word in normalized_filename or word in cleaned_filename:
                            match_score += 5
                            break
                
                # Quality bonuses
                if '.flac' in filename.lower():
                    match_score += 5
                elif '.mp3' in filename.lower():
                    match_score += 3
                
                # Bitrate bonus
                if bitrate >= 320:
                    match_score += 5
                elif bitrate >= 256:
                    match_score += 3
                
                # Size bonus (larger = better quality usually)
                if size_mb > 3:
                    match_score += min(int(size_mb / 2), 5)
                
                # Apply unwanted version penalty
                match_score -= unwanted_penalty
                
                # Bonus for matching expected version types
                version_match_bonus = 0
                if target_has_remix and 'remix' in normalized_filename.lower():
                    version_match_bonus += 25  # Strong bonus for finding expected remix
                elif target_has_live and 'live' in normalized_filename.lower():
                    version_match_bonus += 25  # Strong bonus for finding expected live version
                elif target_has_acoustic and 'acoustic' in normalized_filename.lower():
                    version_match_bonus += 25  # Strong bonus for finding expected acoustic
                
                match_score += version_match_bonus
                
                # Prefer files with "original" or "album version" indicators (only if not looking for special version)
                if not (target_has_remix or target_has_live or target_has_acoustic or target_has_edit):
                    original_indicators = ['original', 'album version', 'studio version', 'single version']
                    for indicator in original_indicators:
                        if indicator in normalized_filename.lower():
                            match_score += 20
                
                # Only add candidates with positive scores (after penalties)
                if match_score > 0:
                    candidates.append({
                        'username': username,
                        'files': [file_info],
                        'filename': filename,
                        'filesize': filesize,
                        'bitrate': bitrate,
                        'score': match_score
                    })
                    self.logger.debug(f"  Candidate: {os.path.basename(filename)} "
                                    f"(score: {match_score}, {size_mb:.1f}MB)")
        
        self.logger.info(f"Found {len(candidates)} potential matches out of {total_files} total files")
        
        if not candidates:
            self.logger.info("No candidates found - trying fallback matching...")
            return self._find_any_audio_file(results)
        
        # Sort by score
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # Log top candidates
        self.logger.info(f"Top 5 candidates:")
        for i, candidate in enumerate(candidates[:5]):
            size_mb = candidate['filesize'] / (1024 * 1024)
            self.logger.info(f"  {i+1}. {os.path.basename(candidate['filename'])}: "
                           f"{size_mb:.1f}MB, {candidate['bitrate']}kbps, "
                           f"Score: {candidate['score']}")
        
        return candidates[0]
    
    def _find_best_any_match(self, results: List[Dict]) -> Optional[Dict]:
        """Find best match of any type (fallback)."""
        # Try album first
        album_match = self._find_best_album_match(results)
        if album_match:
            return album_match
        
        # Fall back to any audio file
        return self._find_any_audio_file(results)
    
    def _find_any_audio_file(self, results: List[Dict]) -> Optional[Dict]:
        """Find any reasonable audio file as last resort fallback."""
        self.logger.info("Trying to find ANY suitable audio file...")
        
        for result in results:
            if not isinstance(result, dict):
                continue
            
            username = result.get('username', '')
            files = result.get('files', [])
            
            for file_info in files:
                if not isinstance(file_info, dict):
                    continue
                
                filename = file_info.get('filename', '')
                filesize = file_info.get('size', 0)
                
                # Must be audio file
                if not self._is_audio_file(filename):
                    continue
                
                # Must be reasonable size
                if filesize < 1000000:  # 1MB minimum
                    continue
                
                size_mb = filesize / (1024 * 1024)
                if size_mb > 100:  # Skip huge files (probably not single songs)
                    continue
                
                self.logger.info(f"Found audio file: {os.path.basename(filename)} "
                               f"({size_mb:.1f}MB) from {username}")
                
                return {
                    'username': username,
                    'files': [file_info],
                    'filename': filename,
                    'filesize': filesize
                }
        
        self.logger.info("No suitable audio files found")
        return None
    
    def _download_files(self, match: Dict) -> bool:
        """Download the selected files."""
        username = match.get('username')
        files = match.get('files', [])
        
        if not username or not files:
            self.logger.warning("Invalid match data")
            return False
        
        # Use the correct endpoint format with username in the path
        url = f"{self.slskd_url}/api/v0/transfers/downloads/{username}"
        
        self.logger.info(f"Downloading {len(files)} file(s) from {username}:")
        
        # Build array of file objects with filename and size
        file_data = []
        for file_info in files:
            filename = file_info.get('filename')
            filesize = file_info.get('size', 0)
            
            if filename:
                file_data.append({
                    'filename': filename,
                    'size': filesize
                })
        
        if not file_data:
            self.logger.warning("No valid files to download")
            return False
        
        try:
            # Send all files in one request
            response = requests.post(url, headers=self.headers, json=file_data, timeout=30)
            
            if response.status_code in [200, 201]:
                for file_obj in file_data:
                    size_mb = file_obj['size'] / (1024 * 1024) if file_obj['size'] > 0 else 0
                    self.logger.info(f"  ✓ Queued: {os.path.basename(file_obj['filename'])} ({size_mb:.1f}MB)")
                return True
            else:
                self.logger.warning(f"Failed to queue files: HTTP {response.status_code}")
                if response.text:
                    self.logger.debug(f"Response: {response.text[:200]}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error queuing files for download: {e}")
            return False
    
    def _is_audio_file(self, filename: str) -> bool:
        """Check if filename is an audio file."""
        if not filename:
            return False
        audio_extensions = ['.mp3', '.flac', '.m4a', '.ogg', '.wav', '.aac', '.opus']
        return any(filename.lower().endswith(ext) for ext in audio_extensions)
    
    def _calculate_avg_bitrate(self, files: List[Dict]) -> int:
        """Calculate average bitrate from file list."""
        bitrates = [f.get('bitRate', 0) for f in files if f.get('bitRate', 0) > 0]
        return sum(bitrates) // len(bitrates) if bitrates else 0


# Convenience functions for quick usage

def search_and_download_album(slskd_url: str, slskd_api_key: str, 
                               artist: str, album: str, 
                               logger: Optional[logging.Logger] = None,
                               dry_run: bool = False) -> bool:
    """
    Search and download an album.
    
    Args:
        slskd_url: Base URL for slskd API
        slskd_api_key: API key for authentication
        artist: Artist name
        album: Album name
        logger: Optional logger instance
        dry_run: If True, only simulate the download
        
    Returns:
        True if successful, False otherwise
    """
    downloader = SlskdDownloader(slskd_url, slskd_api_key, logger)
    search_query = f"{artist} {album}"
    return downloader.search_and_download(search_query, search_type='album', 
                                         target_name=album, dry_run=dry_run)


def search_and_download_song(slskd_url: str, slskd_api_key: str,
                             artist: str, title: str,
                             album: Optional[str] = None,
                             logger: Optional[logging.Logger] = None,
                             dry_run: bool = False) -> bool:
    """
    Search and download a song.
    
    Args:
        slskd_url: Base URL for slskd API
        slskd_api_key: API key for authentication
        artist: Artist name
        title: Song title
        album: Optional album name for better matching
        logger: Optional logger instance
        dry_run: If True, only simulate the download
        
    Returns:
        True if successful, False otherwise
    """
    downloader = SlskdDownloader(slskd_url, slskd_api_key, logger)
    
    # Create search query - include album in search if available for better results
    if album:
        search_query = f"{artist} {title} {album}"
        target_name = f"{title} (from {album})"
    else:
        search_query = f"{artist} {title}"
        target_name = title
        
    return downloader.search_and_download(search_query, search_type='song',
                                         target_name=target_name, dry_run=dry_run)
