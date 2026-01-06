#!/usr/bin/env python3
"""
Scan Library Age v1.0

Scans the Not_Owned directory to update the database with album age and expiry status.
This script is the source of truth for the Expiring Albums tab.

Name: Scan Library Age
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: scanning, library, age, cleanup
Supports dry run: true
"""

import os
import sys
import time
import signal
import logging
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
import requests
import random
import string

# Import dependencies with error handling
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import project modules
from settings import (
    get_not_owned_directory,
    get_setting,
    get_navidrome_config
)
from action_logger import log_script_start, log_script_complete, log_action

try:
    from database import get_db
    DATABASE_AVAILABLE = True
    db = get_db()
except ImportError:
    DATABASE_AVAILABLE = False
    db = None

# Setup logging
log_dir = Path('/logs') if Path('/logs').exists() else Path(__file__).parent.parent / 'logs'
log_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = log_dir / f'scan_library_age_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Graceful interruption
interrupted = False

def signal_handler(signum, frame):
    global interrupted
    interrupted = True
    logger.warning("\n⚠️ Script interrupted by user")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def get_subsonic_auth(username, password):
    """Generate Subsonic API authentication parameters."""
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    
    return {
        'u': username,
        't': token,
        's': salt,
        'v': '1.16.1',  # Subsonic API version
        'c': 'ScanLibraryAge',  # Client name
        'f': 'json'  # Response format
    }

def get_starred_items(config):
    """Fetch all starred albums and tracks from Navidrome."""
    if not config or not config.get('url'):
        logger.warning("Navidrome not configured, skipping starred check")
        return set(), set()

    try:
        auth_params = get_subsonic_auth(config['username'], config['password'])
        url = f"{config['url']}/rest/getStarred2"
        
        logger.info(f"Fetching starred items from Navidrome...")
        response = requests.get(url, params=auth_params, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            starred = data.get('subsonic-response', {}).get('starred2', {})
            
            starred_album_ids = {a['id'] for a in starred.get('album', [])}
            # Also store starred albums by name/artist hash for matching files
            starred_album_keys = set()
            for album in starred.get('album', []):
                 if album.get('musicBrainzId'):
                     starred_album_keys.add(album['musicBrainzId'])
                 
                 # Add fallback hash for files without MBID
                 # Must match the generation logic in scan_directory: f"{artist}-{album_name}".lower()
                 if album.get('artist') and album.get('name'): # Subsonic uses 'name' for album title
                     try:
                         key_str = f"{album['artist']}-{album['name']}".lower().encode('utf-8')
                         local_key = f"local-{hashlib.md5(key_str).hexdigest()}"
                         starred_album_keys.add(local_key)
                     except Exception:
                         pass
            
            starred_track_paths = set()
            for song in starred.get('song', []):
                if song.get('path'):
                    starred_track_paths.add(song.get('path'))
            
            logger.info(f"Found {len(starred_album_ids)} starred albums and {len(starred_track_paths)} starred tracks")
            return starred_album_keys, starred_track_paths
            
        else:
            logger.error(f"Failed to fetch starred items: {response.status_code}")
            return set(), set()
            
    except Exception as e:
        logger.error(f"Error fetching starred items: {e}")
        return set(), set()

def scan_directory(directory, starred_album_keys, starred_track_paths, dry_run=False):
    """Scan directory for albums and track age."""
    if not directory.exists():
        return

    # 1. Group files by album directory
    albums = {}
    
    # Walk directory
    all_files = list(directory.rglob('*'))
    audio_files = [f for f in all_files if f.is_file() and f.suffix.lower() in ['.mp3', '.flac', '.m4a', '.ogg', '.opus', '.wav']]
    
    logger.info(f"Found {len(audio_files)} audio files in {directory}")

    if TQDM_AVAILABLE:
        pbar = tqdm(audio_files, desc="Grouping files", unit="file")
    else:
        pbar = audio_files

    for file_path in pbar:
        if interrupted: break
        
        # Album directory is the parent folder
        album_dir = file_path.parent
        if album_dir not in albums:
            albums[album_dir] = []
        albums[album_dir].append(file_path)

    # 2. Process each album
    processed_count = 0
    total_albums = len(albums)
    
    logger.info(f"Processing {total_albums} albums...")
    
    if TQDM_AVAILABLE:
        iterator = tqdm(albums.items(), desc="Processing albums")
    else:
        iterator = albums.items()
        
    for album_dir, tracks in iterator:
        if interrupted: break
        
        try:
            # metadata from first file
            first_file = tracks[0]
            metadata = get_audio_metadata(first_file)
            
            if not metadata:
                logger.warning(f"Could not extract metadata for {album_dir.name}")
                continue
                
            artist = metadata.get('artist') or "Unknown Artist"
            album_name = metadata.get('album') or "Unknown Album"
            mbid = metadata.get('musicbrainz_albumid')
            
            # Generate key
            if mbid:
                album_key = mbid
            else:
                import hashlib
                key_str = f"{artist}-{album_name}".lower().encode('utf-8')
                album_key = f"local-{hashlib.md5(key_str).hexdigest()}"
            
            # Check starred status
            is_starred = album_key in starred_album_keys
            
            # Calculate stats
            total_size_mb = sum(f.stat().st_size for f in tracks) / (1024 * 1024)
            
            # Determine "first detected"
            # We use datetime.now() instead of file mtime to ensure that the expiry countdown 
            # starts from when SoulSeekarr FIRST SEES the album.
            # If we used mtime, downloads with preserved timestamps (e.g. from 2020) 
            # would expire immediately.
            first_detected_ts = datetime.now()
            
            # Determine Album Art URL
            album_art_url = None
            if mbid:
                # Use Cover Art Archive
                album_art_url = f"https://coverartarchive.org/release/{mbid}/front-250"
            
            # Prepare track data
            track_data_list = []
            album_has_starred_tracks = False
            
            for file_path in tracks:
                # specific track starred?
                # Navidrome paths might be relative or absolute, simple check for suffix match or full match
                # Ideally we match exactly what Navidrome returns as 'path' 
                path_str = str(file_path)
                track_is_starred = False
                for s_path in starred_track_paths:
                    if s_path in path_str or str(Path(s_path)) == path_str:
                        track_is_starred = True
                        break
                
                if track_is_starred:
                    album_has_starred_tracks = True
                    
                track_meta = get_audio_metadata(file_path) or {}
                
                track_data_list.append({
                    'file_path': str(file_path),
                    'file_name': file_path.name,
                    'track_title': track_meta.get('title'),
                    'track_number': track_meta.get('tracknumber'),
                    'track_artist': track_meta.get('artist'),
                    'file_size_mb': file_path.stat().st_size / (1024 * 1024),
                    'days_old': 0, # Unused
                    'last_modified': datetime.fromtimestamp(file_path.stat().st_mtime),
                    'is_starred': track_is_starred,
                    'year': track_meta.get('year'), # Can be None
                    'navidrome_id': None 
                })

            # If any track is starred, treat album as starred for protection? 
            # Usually only album-level star protects the whole album, but maybe tracking it helps.
            # We stick to is_starred being album level.
            
            # Database Update
            if not dry_run and db:
                # Upsert Album
                album_data = {
                    'album_key': album_key,
                    'artist': artist,
                    'album': album_name,
                    'directory': str(album_dir),
                    'file_count': len(tracks),
                    'total_size_mb': total_size_mb,
                    'is_starred': is_starred,
                    'first_detected': first_detected_ts, # Will be ignored by upsert if exists
                    'status': 'pending',
                    'album_art_url': album_art_url
                }
                
                # Use custom transaction or call specific method
                # We'll mimic organise_files.py logic here manually since upsert_expiring_album 
                # doesn't handle track list replacement
                update_database_album(db, album_data, track_data_list)
                
            processed_count += 1
            
            # Update progress
            percentage = int((processed_count / total_albums) * 100)
            print(f"PROGRESS: [{processed_count}/{total_albums}] {percentage}% - Processing: {artist} - {album_name}")

        except Exception as e:
            logger.error(f"Error processing {album_dir}: {e}")

def get_audio_metadata(file_path):
    """Extract metadata from audio file."""
    if not MutagenFile: return None
    try:
        audio = MutagenFile(file_path)
        if not audio or not audio.tags: return None
        tags = audio.tags
        metadata = {}
        
        # Simple extraction mapping
        mapping = {
            'artist': ['artist', 'ARTIST', 'TPE1'],
            'album': ['album', 'ALBUM', 'TALB'],
            'title': ['title', 'TITLE', 'TIT2'],
            'tracknumber': ['tracknumber', 'TRACKNUMBER', 'TRCK'],
            'musicbrainz_albumid': ['musicbrainz_albumid', 'MUSICBRAINZ_ALBUMID', 'TXXX:MusicBrainz Album Id'],
            'year': ['date', 'DATE', 'TDRC', 'year', 'YEAR', 'tyer', 'TYER']
        }
        
        for key, variants in mapping.items():
            for v in variants:
                if v in tags:
                    val = tags[v]
                    metadata[key] = str(val[0]) if isinstance(val, list) else str(val)
                    break
        
        # Clean year (often comes as 2023-01-01)
        if 'year' in metadata:
             try:
                 metadata['year'] = metadata['year'][:4]
             except:
                 pass

        # Album Artist override
        for v in ['albumartist', 'ALBUMARTIST', 'TPE2']:
             if v in tags:
                 val = tags[v]
                 metadata['artist'] = str(val[0]) if isinstance(val, list) else str(val)
                 break
                 
        return metadata
    except:
        return None

def update_database_album(db, album_data, track_data_list):
    """Update album and tracks in database transaction."""
    with db.get_connection() as conn:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        cursor = conn.cursor()
        
        # Check existing
        cursor.execute("SELECT id, first_detected FROM expiring_albums WHERE album_key = ?", (album_data['album_key'],))
        existing = cursor.fetchone()
        
        now = datetime.now()
        
        if existing:
            # Preserve first_detected, update other fields
            sql = """
                UPDATE expiring_albums 
                SET file_count = ?, total_size_mb = ?, is_starred = ?, 
                    last_seen = ?, updated_at = ?
            """
            params = [
                album_data['file_count'], album_data['total_size_mb'], 
                album_data['is_starred'], now, now
            ]
            
            # Only update art if found
            if album_data.get('album_art_url'):
                sql += ", album_art_url = ?"
                params.append(album_data['album_art_url'])
            
            sql += " WHERE id = ?"
            params.append(existing['id'])
            
            cursor.execute(sql, tuple(params))
            album_id = existing['id']
        else:
            # Insert new
            cursor.execute("""
                INSERT INTO expiring_albums 
                (album_key, artist, album, directory, file_count, total_size_mb, 
                 is_starred, first_detected, last_seen, status, album_art_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                album_data['album_key'], album_data['artist'], album_data['album'],
                album_data['directory'], album_data['file_count'], album_data['total_size_mb'],
                album_data['is_starred'], album_data['first_detected'], now, album_data['status'],
                album_data.get('album_art_url')
            ))
            album_id = cursor.lastrowid
            
        # Replace tracks
        cursor.execute("DELETE FROM album_tracks WHERE album_id = ?", (album_id,))
        
        if track_data_list:
            values = []
            for t in track_data_list:
                values.append((
                    album_id, t['file_path'], t['file_name'], t['track_title'],
                    t['track_number'], t['track_artist'], t['file_size_mb'],
                    t['days_old'], t['last_modified'], t['is_starred'], t['navidrome_id'],
                    t['year']
                ))
            
            cursor.executemany("""
                INSERT INTO album_tracks 
                (album_id, file_path, file_name, track_title, track_number, 
                 track_artist, file_size_mb, days_old, last_modified, is_starred, navidrome_id, year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
                
        conn.commit()

def main():
    """Main script execution."""
    # Parse arguments
    parser = argparse.ArgumentParser(description='Scan library age')
    parser.add_argument('--dry-run', action='store_true', help='Run without making changes')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    # Header
    logger.info("=" * 60)
    logger.info("SCAN LIBRARY AGE v1.0")
    logger.info("=" * 60)
    logger.info("")
    
    if dry_run:
        logger.info("🧪 DRY RUN MODE - Database will NOT be updated")
        logger.info("")
    
    # Log script start
    start_time = time.time()
    log_script_start("Scan Library Age", f"Parameters: {'--dry-run' if dry_run else 'normal'}")
    
    try:
        # Get Not_Owned directory
        not_owned_dir = Path(get_not_owned_directory())
        if not not_owned_dir.exists():
            logger.error(f"❌ Not_Owned directory not found: {not_owned_dir}")
            sys.exit(1)
            
        logger.info(f"Scanning directory: {not_owned_dir}")
        
        # Navidrome integration
        navidrome_config = get_navidrome_config()
        starred_albums, starred_tracks = get_starred_items(navidrome_config)
        
        # Scan and update
        scan_directory(not_owned_dir, starred_albums, starred_tracks, dry_run=dry_run)
        
        # Success
        duration = time.time() - start_time
        log_script_complete("Scan Library Age", duration, success=True)
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        
    except Exception as e:
        duration = time.time() - start_time
        log_script_complete("Scan Library Age", duration, success=False, error=str(e))
        logger.error(f"❌ Error: {e}")
        # Only raise if not a keyboard interrupt which is handled gracefully
        if not isinstance(e, KeyboardInterrupt):
             raise

if __name__ == '__main__':
    main()
