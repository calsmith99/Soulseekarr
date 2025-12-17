
import os

file_path = r'z:\AppData\navidrome-cleanup\scripts\spotify_playlist_monitor.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Check the last few lines to be sure
last_line = lines[-1]
print(f'Last line content: {repr(last_line)}')

if 'print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title' in last_line:
    print('Found truncated line. Fixing...')
    # Remove the truncated line
    lines.pop()
    
    # Add the correct code
    new_code = [
        '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
        '                self._handle_pending_track(track)\n',
        '                processed_count += 1\n',
        '            elif status == \'downloading\':\n',
        '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
        '                self._handle_downloading_track(track)\n',
        '                processed_count += 1\n',
        '            elif status == \'downloaded\':\n',
        '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
        '                self._handle_downloaded_track(track)\n',
        '                processed_count += 1\n',
        '            elif status == \'playlist_added\':\n',
        '                # Already done\n',
        '                pass\n'
    ]
    lines.extend(new_code)
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('File updated successfully.')
else:
    print('Truncated line not found at the end. Checking if it is the second to last line...')
    if len(lines) > 1 and 'print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title' in lines[-2]:
         print('Found truncated line at -2. Fixing...')
         lines.pop() # remove last empty line if any
         lines.pop() # remove truncated line
         new_code = [
            '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
            '                self._handle_pending_track(track)\n',
            '                processed_count += 1\n',
            '            elif status == \'downloading\':\n',
            '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
            '                self._handle_downloading_track(track)\n',
            '                processed_count += 1\n',
            '            elif status == \'downloaded\':\n',
            '                print(f"   🎵 Processing: {track[\'artist\']} - {track[\'title\']} [{status}]")\n',
            '                self._handle_downloaded_track(track)\n',
            '                processed_count += 1\n',
            '            elif status == \'playlist_added\':\n',
            '                # Already done\n',
            '                pass\n'
        ]
         lines.extend(new_code)
         with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
         print('File updated successfully.')
    else:
        print('Could not find the truncated line to fix.')
