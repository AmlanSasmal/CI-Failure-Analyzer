#!/usr/bin/env python3
"""
Quick analyzer to find first error and produce detailed analysis.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ci_failure_analyzer.ingestion.log_loader import LogLoader
from ci_failure_analyzer.parsing.log_parser import LogParser
from ci_failure_analyzer.parsing.regex_catalog import ErrorMatcher

def main():
    log_path = Path(__file__).parent / "raw_log.txt"
    
    print("=" * 100)
    print("QUICK ANALYZER - FINDING FIRST ERROR")
    print("=" * 100)
    
    # Load and segment log
    print("\n[1/3] Loading and segmenting log...")
    loader = LogLoader()
    segments = loader.load(log_path)
    print(f"✓ Loaded {len(segments)} segments")
    
    # Create parser
    print("\n[2/3] Scanning segments for errors...")
    error_matcher = ErrorMatcher()
    parser = LogParser(error_matcher=error_matcher)
    
    # Find first failure
    first_failure = None
    failure_segment = None
    
    for seg_idx, segment in enumerate(segments):
        print(f"  Scanning segment {seg_idx + 1}/{len(segments)}: {segment.name}...", end=" ")
        
        failure = parser.analyze_segment(segment)
        
        if failure:
            print(f"✓ FOUND ERROR!")
            first_failure = failure
            failure_segment = segment
            break
        else:
            print("OK")
    
    if not first_failure:
        print("\n⚠ No errors found in log!")
        return
    
    # Print detailed analysis
    print("\n" + "=" * 100)
    print("DETAILED ERROR ANALYSIS")
    print("=" * 100)
    
    print(f"\n📍 ERROR LOCATION:")
    print(f"   Segment: {failure_segment.name}")
    print(f"   Line Number: {first_failure.line_number}")
    print(f"   Error ID: {first_failure.error_id}")
    
    print(f"\n❌ PRIMARY ERROR LINE:")
    print(f"   {first_failure.primary_error_line}")
    
    print(f"\n🔍 HUNTED CONTEXTUAL CLUES ({len(first_failure.hunted_clues)} items):")
    for clue_key, clue_value in first_failure.hunted_clues.items():
        print(f"   [{clue_key}]")
        print(f"      {clue_value}")
    
    # Extract surrounding context from segment
    error_line_in_segment = first_failure.line_number - int(failure_segment.metadata.get('start_line', 1)) + 1
    
    if 0 <= error_line_in_segment < len(failure_segment.content):
        print(f"\n📋 IMMEDIATE CONTEXT (5 lines before error):")
        start = max(0, error_line_in_segment - 5)
        end = error_line_in_segment + 1
        
        for i, line in enumerate(failure_segment.content[start:end], start=start + 1):
            marker = "❌ " if i == error_line_in_segment else "   "
            print(f"   {marker}{line[:100]}")
    
    # Extract broader context for analysis
    print(f"\n📂 BROADER CONTEXT (20 lines before error):")
    start = max(0, error_line_in_segment - 20)
    
    print(f"   [Lines {start + 1} to {error_line_in_segment}]")
    for i, line in enumerate(failure_segment.content[start:error_line_in_segment], start=start + 1):
        # Print every 5th line or first/last
        if i % 5 == 0 or i == start + 1 or i == error_line_in_segment - 1:
            print(f"   {i:5d}: {line[:90]}")
    
    print("\n" + "=" * 100)
    print("PARSER STATISTICS:")
    print("=" * 100)
    stats = parser.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 100)
    print("✓ ANALYSIS COMPLETE")
    print("=" * 100)

if __name__ == "__main__":
    main()
