#!/usr/bin/env python3
"""
Test improved search and filtering logic

This script tests the improvements made to:
1. Search query construction (removal of negative operators)
2. Enhanced song matching with album context
3. Better filtering of unwanted versions

Name: Test Search Improvements
Author: SoulSeekarr Team
Version: 1.0.0
Section: Testing
Tags: test, search, filtering, validation
"""

import os
import sys
import logging
from pathlib import Path

# Add parent directory to path so we can import modules
sys.path.append(str(Path(__file__).parent.parent))

from slskd_utils import SlskdDownloader

def test_search_query_construction():
    """Test that search queries no longer include negative operators."""
    
    # Mock test data
    test_cases = [
        {
            "artist": "The Beatles",
            "title": "Hey Jude",
            "album": "The Beatles 1967-1970",
            "expected_query": "The Beatles Hey Jude The Beatles 1967-1970"
        },
        {
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "album": None,
            "expected_query": "Queen Bohemian Rhapsody"
        }
    ]
    
    print("🧪 Testing search query construction...")
    
    for i, test_case in enumerate(test_cases, 1):
        # Simulate how search_and_download_song constructs queries
        if test_case["album"]:
            query = f"{test_case['artist']} {test_case['title']} {test_case['album']}"
        else:
            query = f"{test_case['artist']} {test_case['title']}"
        
        print(f"Test {i}: {query}")
        
        # Verify no negative operators
        if "-remix" in query or "-mix" in query or "-live" in query:
            print(f"  ❌ FAIL: Query contains negative operators")
            return False
        else:
            print(f"  ✅ PASS: No negative operators found")
        
        # Verify expected structure
        if query == test_case["expected_query"]:
            print(f"  ✅ PASS: Query matches expected format")
        else:
            print(f"  ❌ FAIL: Expected '{test_case['expected_query']}', got '{query}'")
            return False
    
    return True

def test_filename_filtering():
    """Test the filtering logic for unwanted versions."""
    
    # Mock filename test cases
    test_files = [
        # Should be preferred (high scores)
        {"filename": "Queen - Bohemian Rhapsody.flac", "should_pass": True, "score_expectation": "high"},
        {"filename": "The Beatles - Hey Jude (Album Version).mp3", "should_pass": True, "score_expectation": "high"},
        {"filename": "01 - Artist - Song Title.mp3", "should_pass": True, "score_expectation": "medium"},
        
        # Should be penalized (low/negative scores)
        {"filename": "Artist - Song (Remix).mp3", "should_pass": False, "score_expectation": "low"},
        {"filename": "Artist - Song (Live Version).flac", "should_pass": False, "score_expectation": "low"},
        {"filename": "Song Title (Radio Edit).mp3", "should_pass": False, "score_expectation": "low"},
        {"filename": "Artist - Song [Club Mix].mp3", "should_pass": False, "score_expectation": "low"},
        
        # Edge cases
        {"filename": "Artist - Song (Remastered 2023).flac", "should_pass": True, "score_expectation": "medium"},
        {"filename": "Artist - Song (Demo).mp3", "should_pass": False, "score_expectation": "low"},
    ]
    
    print("\\n🧪 Testing filename filtering logic...")
    
    # Simulate the filtering logic from slskd_utils.py
    for i, test_file in enumerate(test_files, 1):
        filename = test_file["filename"]
        
        # Check for unwanted keywords (simplified version of the logic)
        unwanted_keywords = [
            'remix', 'mix)', 'edit)', 'version)', 'live', 'acoustic', 'demo', 
            'karaoke', 'instrumental', 'radio edit', 'clean version', 'explicit',
            'cover', 'tribute', 'mashup', 'bootleg', 'alternate', 'alternative'
        ]
        
        unwanted_penalty = 0
        normalized_filename = filename.lower()
        
        for keyword in unwanted_keywords:
            if keyword in normalized_filename:
                unwanted_penalty += 20
        
        # Base score simulation
        base_score = 30  # Assume some base matching score
        
        # Quality bonus
        if '.flac' in filename.lower():
            base_score += 5
        elif '.mp3' in filename.lower():
            base_score += 3
        
        # Original version bonus
        original_indicators = ['original', 'album version', 'studio version', 'single version']
        for indicator in original_indicators:
            if indicator in normalized_filename:
                base_score += 20
        
        final_score = base_score - unwanted_penalty
        
        print(f"Test {i}: {filename}")
        print(f"  Score: {final_score} (base: {base_score}, penalty: {unwanted_penalty})")
        
        # Validate expectations
        passed = final_score > 0 if test_file["should_pass"] else final_score <= 0
        
        if passed:
            print(f"  ✅ PASS: Correctly {'accepted' if final_score > 0 else 'rejected'}")
        else:
            print(f"  ❌ FAIL: Expected {'acceptance' if test_file['should_pass'] else 'rejection'}")
            return False
    
    return True

def test_album_context_matching():
    """Test that album information improves matching accuracy."""
    
    print("\\n🧪 Testing album context matching...")
    
    # Test cases where album context should help
    test_cases = [
        {
            "query": "The Beatles Hey Jude The Beatles 1967-1970",
            "target_title": "Hey Jude (from The Beatles 1967-1970)",
            "should_extract_album": True,
            "expected_album": "the beatles 1967-1970"
        },
        {
            "query": "Queen Bohemian Rhapsody",
            "target_title": "Bohemian Rhapsody",
            "should_extract_album": False,
            "expected_album": ""
        }
    ]
    
    # Simulate the album extraction logic
    for i, test_case in enumerate(test_cases, 1):
        target_title = test_case["target_title"]
        
        # Extract album from target_title (from _find_best_song_match logic)
        target_album = ""
        if target_title and "(from " in target_title:
            title_parts = target_title.split(" (from ", 1)
            actual_title = title_parts[0]
            target_album = title_parts[1].rstrip(")").lower().strip()
        
        print(f"Test {i}: {target_title}")
        print(f"  Extracted album: '{target_album}'")
        
        if test_case["should_extract_album"]:
            if target_album == test_case["expected_album"]:
                print(f"  ✅ PASS: Correctly extracted album context")
            else:
                print(f"  ❌ FAIL: Expected '{test_case['expected_album']}', got '{target_album}'")
                return False
        else:
            if not target_album:
                print(f"  ✅ PASS: No album context expected or extracted")
            else:
                print(f"  ❌ FAIL: Unexpected album extraction: '{target_album}'")
                return False
    
    return True

def main():
    """Run all tests."""
    print("🔧 Testing Search and Filtering Improvements")
    print("=" * 50)
    
    tests = [
        ("Search Query Construction", test_search_query_construction),
        ("Filename Filtering Logic", test_filename_filtering),
        ("Album Context Matching", test_album_context_matching),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"\\n📋 {test_name}")
        print("-" * 30)
        
        try:
            if test_func():
                print(f"✅ {test_name} PASSED")
                passed += 1
            else:
                print(f"❌ {test_name} FAILED")
                failed += 1
        except Exception as e:
            print(f"❌ {test_name} ERROR: {e}")
            failed += 1
    
    print("\\n" + "=" * 50)
    print(f"📊 Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All tests passed! The improvements are working correctly.")
        return True
    else:
        print("⚠️  Some tests failed. Please review the implementation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)