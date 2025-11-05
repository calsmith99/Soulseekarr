#!/usr/bin/env python3
"""
Example: Using LidarrUtils for Artist Monitoring

This example shows how other scripts can use the reusable LidarrClient
to add artists with proper monitoring configuration.
"""

import os
import sys
import logging
from pathlib import Path

# Add parent directory to path to import lidarr_utils
sys.path.append(str(Path(__file__).parent))

try:
    from lidarr_utils import LidarrClient, add_artist_to_lidarr
except ImportError as e:
    print(f"Error: Could not import lidarr_utils: {e}")
    print("Make sure lidarr_utils.py is in the same directory or parent directory")
    sys.exit(1)


def example_usage():
    """Example of how to use LidarrClient in other scripts"""
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    try:
        # Method 1: Using the convenience function (simplest)
        print("🎵 Method 1: Using convenience function")
        success = add_artist_to_lidarr(
            artist_name="Daft Punk",
            dry_run=True,  # Set to False to actually add
            future_monitoring=True,  # Only monitor future releases (recommended)
            logger=logger
        )
        print(f"✅ Convenience function result: {success}")
        print()
        
        # Method 2: Using LidarrClient directly (more control)
        print("🎵 Method 2: Using LidarrClient directly")
        client = LidarrClient(logger=logger, dry_run=True)
        
        # Test connection
        if client.test_connection():
            print("✅ Connected to Lidarr")
            
            # Check if artist already exists
            if client.artist_exists("Justice"):
                print("✅ Artist 'Justice' already exists in Lidarr")
            else:
                print("❌ Artist 'Justice' not found in Lidarr")
                
                # Add artist with future monitoring (recommended)
                success = client.add_artist_and_search_musicbrainz(
                    artist_name="Justice",
                    future_monitoring=True,  # Only monitor future releases
                    search_for_missing=False  # Don't search for existing albums
                )
                print(f"✅ Added artist result: {success}")
            
            # Example: Add artist with ALL monitoring (use with caution!)
            print("\n⚠️  Example: Adding artist with ALL album monitoring")
            success = client.add_artist_with_all_monitoring(
                artist_name="Moderat",
                search_for_missing=True  # This will download entire discography!
            )
            print(f"⚠️  Added with ALL monitoring: {success}")
            
        else:
            print("❌ Failed to connect to Lidarr")
        
        print()
        
        # Method 3: Batch processing multiple artists
        print("🎵 Method 3: Batch processing multiple artists")
        artists_to_add = [
            "Boards of Canada",
            "Aphex Twin", 
            "Autechre",
            "Squarepusher"
        ]
        
        results = []
        for artist in artists_to_add:
            print(f"🔍 Processing: {artist}")
            success = client.add_artist_and_search_musicbrainz(
                artist_name=artist,
                future_monitoring=True
            )
            results.append((artist, success))
            print(f"   {'✅' if success else '❌'} Result: {success}")
        
        # Summary
        print()
        print("📊 Batch Processing Summary:")
        successful = sum(1 for _, success in results if success)
        print(f"   ✅ Successful: {successful}/{len(results)}")
        print(f"   ❌ Failed: {len(results) - successful}/{len(results)}")
        
        for artist, success in results:
            status = "✅" if success else "❌"
            print(f"   {status} {artist}")
            
    except Exception as e:
        logger.error(f"Error in example usage: {e}")
        return False
    
    return True


def show_best_practices():
    """Show best practices for using LidarrUtils"""
    print()
    print("💡 Best Practices for LidarrUtils:")
    print()
    print("1. 🎯 Use Future Monitoring by Default")
    print("   - future_monitoring=True (only monitor future releases)")
    print("   - Prevents downloading entire discographies")
    print("   - Safer for new artists")
    print()
    print("2. 🔍 Let MusicBrainz Search Handle Metadata")
    print("   - Use add_artist_and_search_musicbrainz()")
    print("   - Automatically finds proper artist names and IDs")
    print("   - Handles variations like 'JAY Z' vs 'Jay-Z'")
    print()
    print("3. ⚠️  Use ALL Monitoring Sparingly")
    print("   - Only use add_artist_with_all_monitoring() when intentional")
    print("   - Will monitor and potentially download ALL albums")
    print("   - Better for specific artists you want complete collections")
    print()
    print("4. 🧪 Test with Dry Run First")
    print("   - Always test with dry_run=True first")
    print("   - Verify the behavior before making real changes")
    print("   - Check logs for any issues")
    print()
    print("5. 📝 Use Proper Logging")
    print("   - Pass a logger instance for detailed output")
    print("   - Check logs for MusicBrainz search results")
    print("   - Monitor for API errors or rate limits")


if __name__ == "__main__":
    print("🎵 LidarrUtils Example Script")
    print("=" * 40)
    
    # Check environment variables
    if not os.getenv('LIDARR_URL') or not os.getenv('LIDARR_API_KEY'):
        print("❌ Missing required environment variables:")
        print("   - LIDARR_URL")
        print("   - LIDARR_API_KEY")
        print()
        print("Please set these environment variables before running.")
        sys.exit(1)
    
    # Run examples
    try:
        example_usage()
        show_best_practices()
        
        print()
        print("✅ Example completed successfully!")
        print("💡 Check the logs above for detailed information about each operation.")
        
    except Exception as e:
        print(f"❌ Example failed: {e}")
        sys.exit(1)