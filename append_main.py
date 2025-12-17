
import os

file_path = r'z:\AppData\navidrome-cleanup\scripts\spotify_playlist_monitor.py'

main_block = """

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spotify Playlist Monitor")
    parser.add_argument("--playlist-url", help="Spotify playlist URL to process")
    parser.add_argument("--dry-run", action="store_true", help="Run without making changes")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tracks to process")
    
    args = parser.parse_args()
    
    monitor = SpotifyPlaylistMonitor(dry_run=args.dry_run, limit=args.limit)
    monitor.run(playlist_url=args.playlist_url)
"""

with open(file_path, 'a', encoding='utf-8') as f:
    f.write(main_block)

print("Main block appended successfully.")
