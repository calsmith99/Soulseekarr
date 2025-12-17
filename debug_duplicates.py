
import os
import sys
from collections import defaultdict

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import get_db

def check_duplicates():
    db = get_db()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, album_key, artist, album, first_detected, status FROM expiring_albums")
        rows = cursor.fetchall()

    print(f"Total rows: {len(rows)}")

    # Group by (artist, album)
    grouped = defaultdict(list)
    for row in rows:
        # Normalize for comparison
        key = (row['artist'].lower().strip(), row['album'].lower().strip())
        grouped[key].append(dict(row))

    duplicates_count = 0
    for (artist, album), entries in grouped.items():
        if len(entries) > 1:
            duplicates_count += 1
            print(f"\nDuplicate found for: {artist} - {album}")
            for entry in entries:
                print(f"  ID: {entry['id']}, Key: {entry['album_key']}, First Detected: {entry['first_detected']}, Status: {entry['status']}")

    print(f"\nTotal duplicate groups found: {duplicates_count}")

if __name__ == "__main__":
    check_duplicates()
