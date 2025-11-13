#!/usr/bin/env python3
"""
Deduplicate Tracks - Find and Remove Duplicate Music Files

Scans a music folder for duplicate tracks and keeps only the highest quality version.
Duplicates are identified by matching artist + title metadata, and quality is determined
by bitrate, sample rate, and file size.

Name: Deduplicate Tracks
Author: SoulSeekarr
Version: 1.0
Section: commands
Tags: cleanup, duplicates, quality, organization
Supports dry run: true

Quality ranking criteria (in order):
1. Lossless formats (FLAC, ALAC, WAV, AIFF) ranked highest
2. Higher bitrate
3. Higher sample rate
4. Larger file size (as tiebreaker)
"""

import os
import sys
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import argparse

# Try to import optional dependencies
try:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    MutagenFile = None

# Add parent directory to path to import settings
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from settings import get_target_uid, get_target_gid
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler()]
)

# Global statistics
STATS = {
    'files_scanned': 0,
    'duplicates_found': 0,
    'files_removed': 0,
    'space_saved': 0,
    'errors': 0
}

# Lossless formats (ranked higher in quality)
LOSSLESS_FORMATS = {'.flac', '.wav', '.aiff', '.aif', '.alac', '.ape', '.wv'}
LOSSY_FORMATS = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wma'}
SUPPORTED_FORMATS = LOSSLESS_FORMATS | LOSSY_FORMATS


class TrackInfo:
    """Store information about a music track"""
    
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.filename = self.filepath.name
        self.extension = self.filepath.suffix.lower()
        self.filesize = self.filepath.stat().st_size if self.filepath.exists() else 0
        
        # Metadata
        self.artist = None
        self.title = None
        self.album = None
        self.bitrate = 0
        self.sample_rate = 0
        self.duration = 0
        
        # Quality indicators
        self.is_lossless = self.extension in LOSSLESS_FORMATS
        self.quality_score = 0
        
    def extract_metadata(self):
        """Extract metadata from audio file using mutagen"""
        if not MUTAGEN_AVAILABLE:
            logging.warning(f"   ⚠️  Mutagen not available, using filename for: {self.filename}")
            self._parse_filename()
            return
        
        try:
            audio = MutagenFile(str(self.filepath))
            if audio is None:
                logging.warning(f"   ⚠️  Could not read audio file: {self.filename}")
                self._parse_filename()
                return
            
            # Extract basic metadata
            if hasattr(audio, 'tags') and audio.tags:
                self.artist = self._get_tag(audio.tags, ['artist', 'TPE1', '©ART', 'ARTIST'])
                self.title = self._get_tag(audio.tags, ['title', 'TIT2', '©nam', 'TITLE'])
                self.album = self._get_tag(audio.tags, ['album', 'TALB', '©alb', 'ALBUM'])
            
            # Extract audio quality info
            if hasattr(audio.info, 'bitrate'):
                self.bitrate = audio.info.bitrate
            if hasattr(audio.info, 'sample_rate'):
                self.sample_rate = audio.info.sample_rate
            if hasattr(audio.info, 'length'):
                self.duration = audio.info.length
            
            # If no metadata found, try filename
            if not self.artist or not self.title:
                self._parse_filename()
            
        except Exception as e:
            logging.warning(f"   ⚠️  Error reading metadata from {self.filename}: {e}")
            self._parse_filename()
    
    def _get_tag(self, tags, keys):
        """Get tag value from various possible keys"""
        for key in keys:
            if key in tags:
                value = tags[key]
                if isinstance(value, list):
                    return str(value[0]) if value else None
                return str(value)
        return None
    
    def _parse_filename(self):
        """Parse artist and title from filename as fallback"""
        # Remove extension
        name = self.filepath.stem
        
        # Common patterns: "Artist - Title", "Artist-Title", etc.
        for separator in [' - ', ' – ', ' — ', '-']:
            if separator in name:
                parts = name.split(separator, 1)
                if len(parts) == 2:
                    self.artist = parts[0].strip()
                    self.title = parts[1].strip()
                    return
        
        # If no separator found, use filename as title
        self.title = name
        self.artist = "Unknown Artist"
    
    def calculate_quality_score(self):
        """Calculate quality score for comparison"""
        score = 0
        
        # Lossless formats get a huge boost
        if self.is_lossless:
            score += 100000
        
        # Bitrate contributes significantly
        score += self.bitrate / 1000  # Convert to kbps
        
        # Sample rate contributes
        score += self.sample_rate / 100
        
        # File size as tiebreaker (larger usually means better quality)
        score += self.filesize / (1024 * 1024)  # Convert to MB
        
        self.quality_score = score
        return score
    
    def get_duplicate_key(self):
        """Get a key for identifying duplicates (artist + title)"""
        artist = (self.artist or "unknown").lower().strip()
        title = (self.title or "unknown").lower().strip()
        
        # Normalize some common variations
        artist = artist.replace('the ', '').replace('feat.', 'ft.')
        title = title.replace('feat.', 'ft.')
        
        return f"{artist}::{title}"
    
    def __repr__(self):
        quality = "Lossless" if self.is_lossless else f"{self.bitrate//1000}kbps"
        size_mb = self.filesize / (1024 * 1024)
        return f"{self.artist} - {self.title} [{quality}, {size_mb:.1f}MB]"


def find_music_files(directory):
    """Recursively find all music files in directory"""
    music_files = []
    directory = Path(directory)
    
    if not directory.exists():
        logging.error(f"❌ Directory does not exist: {directory}")
        return music_files
    
    logging.info(f"🔍 Scanning directory: {directory}")
    
    for root, dirs, files in os.walk(directory):
        for filename in files:
            filepath = Path(root) / filename
            
            # Delete macOS metadata files immediately
            if filename.startswith('._'):
                logging.info(f"🗑️  Removing macOS metadata file: {filename}")
                try:
                    filepath.unlink()
                except Exception as e:
                    logging.warning(f"Could not delete metadata file {filename}: {e}")
                continue
                
            if filepath.suffix.lower() in SUPPORTED_FORMATS:
                music_files.append(filepath)
    
    logging.info(f"📊 Found {len(music_files)} music files")
    return music_files


def scan_files_for_duplicates(music_files):
    """Scan music files and group duplicates"""
    logging.info(f"\n📋 Analyzing {len(music_files)} files for duplicates...")
    
    tracks_by_key = defaultdict(list)
    
    for i, filepath in enumerate(music_files, 1):
        if i % 100 == 0:
            logging.info(f"   Processing file {i}/{len(music_files)}...")
        
        try:
            track = TrackInfo(filepath)
            track.extract_metadata()
            track.calculate_quality_score()
            
            duplicate_key = track.get_duplicate_key()
            tracks_by_key[duplicate_key].append(track)
            
            STATS['files_scanned'] += 1
            
        except Exception as e:
            logging.error(f"   ❌ Error processing {filepath}: {e}")
            STATS['errors'] += 1
    
    # Find actual duplicates (groups with more than 1 file)
    duplicates = {key: tracks for key, tracks in tracks_by_key.items() if len(tracks) > 1}
    
    return duplicates


def identify_files_to_keep_and_remove(duplicates):
    """Identify which files to keep and which to remove"""
    files_to_remove = []
    
    logging.info(f"\n🔍 Found {len(duplicates)} groups of duplicate tracks")
    STATS['duplicates_found'] = len(duplicates)
    
    for key, tracks in duplicates.items():
        # Sort by quality score (highest first)
        tracks.sort(key=lambda t: t.quality_score, reverse=True)
        
        best_track = tracks[0]
        duplicates = tracks[1:]
        
        logging.info(f"\n📀 Duplicate group: {best_track.artist} - {best_track.title}")
        logging.info(f"   ✅ KEEPING: {best_track.filename}")
        logging.info(f"      Quality: {'Lossless' if best_track.is_lossless else f'{best_track.bitrate//1000}kbps'}")
        logging.info(f"      Size: {best_track.filesize / (1024 * 1024):.1f} MB")
        logging.info(f"      Path: {best_track.filepath.parent}")
        
        for dup in duplicates:
            logging.info(f"   ❌ REMOVING: {dup.filename}")
            logging.info(f"      Quality: {'Lossless' if dup.is_lossless else f'{dup.bitrate//1000}kbps'}")
            logging.info(f"      Size: {dup.filesize / (1024 * 1024):.1f} MB")
            logging.info(f"      Path: {dup.filepath.parent}")
            files_to_remove.append(dup)
    
    return files_to_remove


def remove_duplicate_files(files_to_remove, dry_run=False):
    """Remove duplicate files"""
    if not files_to_remove:
        logging.info("\n✨ No duplicate files to remove!")
        return
    
    total_size = sum(f.filesize for f in files_to_remove)
    
    if dry_run:
        logging.info(f"\n🧪 DRY RUN MODE - Would remove {len(files_to_remove)} files")
        logging.info(f"   Would save {total_size / (1024 * 1024):.1f} MB of space")
        return
    
    logging.info(f"\n🗑️  Removing {len(files_to_remove)} duplicate files...")
    
    for track in files_to_remove:
        try:
            track.filepath.unlink()
            STATS['files_removed'] += 1
            STATS['space_saved'] += track.filesize
            logging.info(f"   ✓ Removed: {track.filename}")
        except Exception as e:
            logging.error(f"   ❌ Failed to remove {track.filename}: {e}")
            STATS['errors'] += 1


def print_summary(dry_run=False):
    """Print summary of operations"""
    logging.info("\n" + "="*60)
    logging.info("📊 DEDUPLICATION SUMMARY")
    logging.info("="*60)
    logging.info(f"Files scanned: {STATS['files_scanned']}")
    logging.info(f"Duplicate groups found: {STATS['duplicates_found']}")
    
    if dry_run:
        logging.info(f"Files that would be removed: {STATS['files_removed']}")
        logging.info(f"Space that would be saved: {STATS['space_saved'] / (1024 * 1024):.1f} MB")
    else:
        logging.info(f"Files removed: {STATS['files_removed']}")
        logging.info(f"Space saved: {STATS['space_saved'] / (1024 * 1024):.1f} MB")
    
    logging.info(f"Errors encountered: {STATS['errors']}")
    logging.info("="*60)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Find and remove duplicate music files, keeping highest quality')
    parser.add_argument('directory', nargs='?', help='Directory to scan for duplicates')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without actually removing files')
    parser.add_argument('--folder', type=str, help='Alternative way to specify directory')
    
    args = parser.parse_args()
    
    # Check for mutagen
    if not MUTAGEN_AVAILABLE:
        logging.error("❌ Mutagen library is required for this script")
        logging.error("   Install with: pip install mutagen")
        sys.exit(1)
    
    # Get directory from args
    directory = args.directory or args.folder
    
    # If no directory specified, use settings module or environment variable or default
    if not directory:
        if SETTINGS_AVAILABLE:
            try:
                from settings import get_music_directory
                directory = get_music_directory()
            except Exception as e:
                logging.warning(f"Could not load directory from settings: {e}")
                directory = os.environ.get('MUSIC_DIRECTORY', '/media/Not_Owned')
        else:
            directory = os.environ.get('MUSIC_DIRECTORY', '/media/Not_Owned')
    
    directory = Path(directory)
    
    if not directory.exists():
        logging.error(f"❌ Directory does not exist: {directory}")
        logging.error("   Please specify a valid directory as an argument")
        sys.exit(1)
    
    dry_run = args.dry_run or os.environ.get('DRY_RUN', 'false').lower() == 'true'
    
    if dry_run:
        logging.info("🧪 DRY RUN MODE - No files will be removed")
    
    logging.info("🎵 Music Deduplication Tool")
    logging.info(f"📁 Target directory: {directory}")
    logging.info("")
    
    # Find all music files
    music_files = find_music_files(directory)
    
    if not music_files:
        logging.info("ℹ️  No music files found in directory")
        return
    
    # Scan for duplicates
    duplicates = scan_files_for_duplicates(music_files)
    
    if not duplicates:
        logging.info("\n✨ No duplicate files found!")
        print_summary(dry_run)
        return
    
    # Identify files to remove
    files_to_remove = identify_files_to_keep_and_remove(duplicates)
    
    # Remove duplicates (or show what would be removed in dry run)
    remove_duplicate_files(files_to_remove, dry_run)
    
    # Print summary
    print_summary(dry_run)
    
    if dry_run:
        logging.info("\n💡 Run without --dry-run to actually remove duplicate files")


if __name__ == '__main__':
    main()
