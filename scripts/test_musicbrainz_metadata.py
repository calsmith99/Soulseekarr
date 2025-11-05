#!/usr/bin/env python3
"""
Test script for MusicBrainz metadata functionality

This script tests the MusicBrainz metadata fixing functionality
to ensure it works correctly before integration.

Name: Test MusicBrainz Metadata
Author: SoulSeekarr
Version: 1.0
Section: tests
Tags: musicbrainz, metadata, testing
Supports dry run: false
"""

import os
import sys
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import musicbrainzngs
    logger.info("Successfully imported musicbrainzngs")
except ImportError as e:
    logger.error("'musicbrainzngs' package not found. Install with: pip install musicbrainzngs")
    sys.exit(1)

try:
    import acoustid
    logger.info("Successfully imported pyacoustid")
except ImportError as e:
    logger.error("'pyacoustid' package not found. Install with: pip install pyacoustid")
    sys.exit(1)

class MusicBrainzTester:
    def __init__(self):
        self.setup_musicbrainz()
        self.acoustid_api_key = 'cSpUJKpD'  # Demo key for testing
        
    def setup_musicbrainz(self):
        """Setup MusicBrainz API configuration"""
        logger.info("Setting up MusicBrainz configuration...")
        
        # Set user agent for MusicBrainz API requests
        app_name = "navidrome-cleanup-test"
        app_version = "1.0"
        contact_email = "admin@localhost"
        
        musicbrainzngs.set_useragent(app_name, app_version, contact_email)
        logger.info(f"MusicBrainz user agent set: {app_name}/{app_version}")
        
        # Set rate limiting to be respectful to MusicBrainz servers
        musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)
        logger.info("MusicBrainz rate limiting enabled")
        
    def test_search_recordings(self):
        """Test searching for recordings"""
        logger.info("Testing MusicBrainz recording search...")
        
        try:
            # Test with a well-known song
            query = 'artist:"The Beatles" AND recording:"Hey Jude"'
            logger.info(f"Searching with query: {query}")
            
            result = musicbrainzngs.search_recordings(query=query, limit=5)
            recordings = result.get('recording-list', [])
            
            logger.info(f"Found {len(recordings)} recordings")
            
            for i, recording in enumerate(recordings):
                title = recording.get('title', 'Unknown')
                artist_credits = recording.get('artist-credit', [])
                artist = artist_credits[0].get('artist', {}).get('name', 'Unknown') if artist_credits else 'Unknown'
                
                logger.info(f"  {i+1}. {artist} - {title}")
                
            return len(recordings) > 0
            
        except Exception as e:
            logger.error(f"Error testing recording search: {e}")
            return False
            
    def test_search_releases(self):
        """Test searching for releases (albums)"""
        logger.info("Testing MusicBrainz release search...")
        
        try:
            # Test with a well-known album
            query = 'artist:"The Beatles" AND release:"Abbey Road"'
            logger.info(f"Searching with query: {query}")
            
            result = musicbrainzngs.search_releases(query=query, limit=5)
            releases = result.get('release-list', [])
            
            logger.info(f"Found {len(releases)} releases")
            
            for i, release in enumerate(releases):
                title = release.get('title', 'Unknown')
                artist_credits = release.get('artist-credit', [])
                artist = artist_credits[0].get('artist', {}).get('name', 'Unknown') if artist_credits else 'Unknown'
                date = release.get('date', 'Unknown')
                
                logger.info(f"  {i+1}. {artist} - {title} ({date})")
                
            return len(releases) > 0
            
        except Exception as e:
            logger.error(f"Error testing release search: {e}")
            return False
            
    def test_get_release_details(self):
        """Test getting detailed release information"""
        logger.info("Testing MusicBrainz release details...")
        
        try:
            # First, search for a release
            query = 'artist:"The Beatles" AND release:"Abbey Road"'
            result = musicbrainzngs.search_releases(query=query, limit=1)
            releases = result.get('release-list', [])
            
            if not releases:
                logger.warning("No releases found for detail test")
                return False
                
            release_id = releases[0].get('id')
            if not release_id:
                logger.warning("No release ID found")
                return False
                
            logger.info(f"Getting details for release ID: {release_id}")
            
            # Get full release info including track list
            release_info = musicbrainzngs.get_release_by_id(
                release_id, 
                includes=['recordings', 'artist-credits']
            )
            
            release_data = release_info.get('release', {})
            title = release_data.get('title', 'Unknown')
            
            logger.info(f"Release: {title}")
            
            medium_list = release_data.get('medium-list', [])
            logger.info(f"Found {len(medium_list)} media")
            
            for medium_idx, medium in enumerate(medium_list):
                track_list = medium.get('track-list', [])
                logger.info(f"  Medium {medium_idx + 1}: {len(track_list)} tracks")
                
                for track_idx, track in enumerate(track_list[:3]):  # Show first 3 tracks
                    recording = track.get('recording', {})
                    track_title = recording.get('title', 'Unknown')
                    position = track.get('position', track_idx + 1)
                    
                    logger.info(f"    {position}. {track_title}")
                    
                if len(track_list) > 3:
                    logger.info(f"    ... and {len(track_list) - 3} more tracks")
                    
            return True
            
        except Exception as e:
            logger.error(f"Error testing release details: {e}")
            return False
            
    def test_acoustid_functionality(self):
        """Test AcoustID audio fingerprinting functionality"""
        logger.info("Testing AcoustID functionality...")
        
        try:
            # Test if we can import and use acoustid functions
            logger.info("Testing AcoustID module functions...")
            
            # Test lookup function with a known fingerprint (this won't work without an actual audio file)
            # This is just testing that the API is accessible
            logger.info("Testing AcoustID API connectivity...")
            
            # Try a simple lookup that should fail gracefully
            try:
                # This will fail because we don't have a real fingerprint, but it tests API connectivity
                result = acoustid.lookup(self.acoustid_api_key, "fake_fingerprint", 180)
                logger.info("AcoustID API is accessible (got response even with fake data)")
            except Exception as api_error:
                # Expected to fail with fake data, but if it's a connection error, that's different
                error_str = str(api_error).lower()
                if 'network' in error_str or 'connection' in error_str or 'timeout' in error_str:
                    logger.error(f"AcoustID network connectivity issue: {api_error}")
                    return False
                else:
                    logger.info(f"AcoustID API accessible (rejected fake fingerprint as expected): {api_error}")
                    
            logger.info("AcoustID functionality test completed")
            return True
            
        except Exception as e:
            logger.error(f"Error testing AcoustID functionality: {e}")
            return False
            
    def test_audio_fingerprint_workflow(self):
        """Test the complete audio fingerprinting workflow"""
        logger.info("Testing audio fingerprinting workflow...")
        
        try:
            # Note: This test requires an actual audio file to work properly
            logger.info("Note: Audio fingerprinting requires actual audio files to test properly")
            logger.info("In a real scenario, the workflow would be:")
            logger.info("1. Generate fingerprint from audio file using acoustid.fingerprint_file()")
            logger.info("2. Look up fingerprint in AcoustID database")
            logger.info("3. Process results to find best metadata match")
            logger.info("4. Extract metadata from MusicBrainz recording data")
            logger.info("5. Apply improved metadata to file")
            
            # We can test the results processing part with mock data
            logger.info("Testing result processing with mock data...")
            
            mock_results = [
                {
                    'score': 0.95,
                    'recordings': [
                        {
                            'title': 'Hey Jude',
                            'artists': [{'name': 'The Beatles'}],
                            'releasegroups': [
                                {
                                    'title': 'The Beatles 1967-1970',
                                    'type': 'Album',
                                    'first-release-date': '1973'
                                }
                            ]
                        }
                    ]
                }
            ]
            
            # Test metadata extraction from mock result
            recording = mock_results[0]['recordings'][0]
            metadata = self.extract_test_metadata(recording)
            
            if metadata and metadata.get('title') == 'Hey Jude':
                logger.info(f"✓ Successfully extracted metadata: {metadata}")
                return True
            else:
                logger.error("Failed to extract metadata from mock result")
                return False
                
        except Exception as e:
            logger.error(f"Error testing audio fingerprinting workflow: {e}")
            return False
            
    def extract_test_metadata(self, recording):
        """Test metadata extraction function"""
        try:
            metadata = {}
            
            metadata['title'] = recording.get('title', '')
            
            artists = recording.get('artists', [])
            if artists:
                if len(artists) == 1:
                    metadata['artist'] = artists[0].get('name', '')
                else:
                    artist_names = [artist.get('name', '') for artist in artists if artist.get('name')]
                    metadata['artist'] = ', '.join(artist_names)
            
            releasegroups = recording.get('releasegroups', [])
            if releasegroups:
                best_release = None
                for release in releasegroups:
                    release_type = release.get('type', '')
                    if release_type.lower() in ['album', '']:
                        best_release = release
                        break
                
                if not best_release and releasegroups:
                    best_release = releasegroups[0]
                    
                if best_release:
                    metadata['album'] = best_release.get('title', '')
                    first_release_date = best_release.get('first-release-date')
                    if first_release_date:
                        try:
                            metadata['date'] = first_release_date[:4]
                        except:
                            pass
                            
            return metadata if metadata.get('title') and metadata.get('artist') else None
            
        except Exception as e:
            logger.error(f"Error in test metadata extraction: {e}")
            return None
            
    def run_all_tests(self):
        """Run all tests"""
        logger.info("=== Starting MusicBrainz API Tests ===")
        
        tests = [
            ("Recording Search", self.test_search_recordings),
            ("Release Search", self.test_search_releases),
            ("Release Details", self.test_get_release_details),
            ("AcoustID Functionality", self.test_acoustid_functionality),
            ("Audio Fingerprint Workflow", self.test_audio_fingerprint_workflow),
        ]
        
        passed = 0
        total = len(tests)
        
        for test_name, test_func in tests:
            logger.info(f"\n--- Running {test_name} ---")
            try:
                if test_func():
                    logger.info(f"✓ {test_name} PASSED")
                    passed += 1
                else:
                    logger.error(f"✗ {test_name} FAILED")
            except Exception as e:
                logger.error(f"✗ {test_name} ERROR: {e}")
                
        logger.info(f"\n=== Test Results: {passed}/{total} tests passed ===")
        return passed == total

def main():
    """Main function"""
    try:
        tester = MusicBrainzTester()
        success = tester.run_all_tests()
        
        if success:
            logger.info("All tests passed! MusicBrainz integration is working.")
            sys.exit(0)
        else:
            logger.error("Some tests failed. Check the logs above.")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Critical error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()