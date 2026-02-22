#!/usr/bin/env python3
"""
Test and demonstration of the FailureAnalyzerAgent with Gemini API.

Shows how to:
1. Initialize the agent with API key from .env
2. Analyze individual failures
3. Batch analyze multiple failures
4. Handle errors gracefully
"""

import os
import json
from dotenv import load_dotenv
from src.ci_failure_analyzer.models.log_models import (
    LogEntry, FailureContext, FailureCategory
)
from src.ci_failure_analyzer.reasoning.ai_agent import FailureAnalyzerAgent

# Load environment variables
load_dotenv()


def create_sample_failure_1() -> FailureContext:
    """Create a CRC bias test failure for analysis."""
    failure = FailureContext(
        test_id="rfe_tc_3120_sm69RfgCrcGbias",
        status="FAILED",
        duration=45,
        received_bytes=2048,
        start_line=100,
        verdict_line=150
    )
    
    messages = [
        "Executing rfe_tc_3120_sm69RfgCrcGbias",
        "Test sequence: CRC bias validation 2.0-2.5GHz",
        "Setting frequency: 2.4GHz",
        "T32> freq 2.4GHz",
        "Local oscillator locked to 2.4GHz",
        "T32> r 0x1040",
        "0x1040 (CRC_BIAS): 0x00000000 - register cleared",
        "Calling sm69RfgCrc_set_bias(0x0A)",
        "T32> r 0x1040 after set_bias",
        "0x1040: 0x0000000A - bias set correctly",
        "CRC initialization complete",
        "Transmitting test frame 1024 bytes",
        "Assertion failed: CRC mismatch",
        "Expected CRC: 0xDEADBEEF",
        "Received CRC: 0xCAFEBABE",
        "Register check after failure:",
        "T32> r 0x1040",
        "0x1040: 0x000000CA (CORRUPTED! High bits garbage)",
        "Analysis: sm69RfgCrc_set_bias uses |= without clearing register",
        "Test verdict: FAILED"
    ]
    
    for i, msg in enumerate(messages, start=101):
        is_t32 = "T32" in msg or ("0x1040:" in msg)
        failure.add_line(LogEntry(
            line_number=i,
            message=msg,
            raw_message=msg,
            is_t32_script=is_t32
        ))
    
    return failure


def create_sample_failure_2() -> FailureContext:
    """Create a LOIF frequency test failure for analysis."""
    failure = FailureContext(
        test_id="rfe_tc_3125_sm69RfgCrcLoif",
        status="FAILED",
        duration=38,
        received_bytes=1536,
        start_line=200,
        verdict_line=250
    )
    
    messages = [
        "Executing rfe_tc_3125_sm69RfgCrcLoif",
        "Test sequence: LOIF frequency offset validation",
        "Setting LO frequency: 2.35GHz",
        "T32> freq 2.35GHz",
        "Waiting for PLL lock... (timeout 100ms)",
        "PLL lock acquired after 85ms",
        "T32> r 0x1080",
        "0x1080 (LOIF_CTRL): 0x00000023 - config loaded",
        "IF frequency offset: -45MHz from LO",
        "Transmitting LOIF calibration frame",
        "Sample 1: phase=45.2deg",
        "Sample 2: phase=45.1deg",
        "Sample 3: phase=45.0deg",
        "Sample 4: phase=319.5deg *** PHASE JUMP DETECTED ***",
        "Sample 5: phase=320.1deg",
        "Phase coherence lost!",
        "Assertion failed: Phase continuity violation",
        "Phase jump magnitude: 274.3 degrees",
        "LO sync loss suspected in hardware PLL",
        "Test verdict: FAILED"
    ]
    
    for i, msg in enumerate(messages, start=201):
        is_t32 = "T32" in msg or ("0x1080:" in msg)
        failure.add_line(LogEntry(
            line_number=i,
            message=msg,
            raw_message=msg,
            is_t32_script=is_t32
        ))
    
    return failure


def main():
    """Main test function."""
    
    # Get API key from .env (loaded via load_dotenv above)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in .env file")
        print("\nAdd to .env:")
        print("  GEMINI_API_KEY=your-api-key-here")
        print("  GEMINI_MODEL=gemini-2.0-flash")
        return
    
    print("=" * 80)
    print("FAILURE ANALYZER AGENT - TEST WITH GEMINI API")
    print("=" * 80)
    
    # Initialize agent (will use .env variables)
    print("\n[INIT] Initializing FailureAnalyzerAgent...")
    try:
        agent = FailureAnalyzerAgent()  # Uses .env automatically
        print("  SUCCESS: Agent initialized with Gemini API")
    except ValueError as e:
        print(f"  ERROR: {e}")
        return
    
    # Create sample failures
    print("\n[SETUP] Creating sample failure contexts...")
    failure1 = create_sample_failure_1()
    failure2 = create_sample_failure_2()
    print(f"  Created: {failure1.test_id} ({len(failure1.context_lines)} lines)")
    print(f"  Created: {failure2.test_id} ({len(failure2.context_lines)} lines)")
    
    # Single analysis example
    print("\n[ANALYZE] Analyzing first failure...")
    print(f"  Test: {failure1.test_id}")
    print(f"  Context lines: {len(failure1.context_lines)}")
    print(f"  T32 debug lines: {len(failure1.t32_debug_lines)}")
    
    try:
        analysis1 = agent.analyze_failure(failure1)
        
        print(f"\n  ANALYSIS RESULT:")
        print(f"    Category: {analysis1.category.value}")
        print(f"    Confidence: {analysis1.confidence * 100:.0f}%")
        print(f"    Summary: {analysis1.summary}")
        print(f"    Root Cause: {analysis1.root_cause[:100]}...")
        print(f"    Suggested Fix: {analysis1.suggested_fix[:100]}...")
        
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)}")
        print("\nTroubleshooting:")
        print("  - Check API key is valid")
        print("  - Check rate limits (free tier: 15 requests/min)")
        print("  - Wait a moment and try again")
        return
    
    # Batch analysis example
    print("\n[BATCH] Analyzing multiple failures...")
    failures = [failure1, failure2]
    
    try:
        results = agent.batch_analyze_failures(failures, stop_on_error=False)
        
        print(f"\n  BATCH ANALYSIS RESULTS:")
        for context, analysis, error in results:
            if analysis:
                print(f"    {context.test_id}: {analysis.category.value} "
                      f"({analysis.confidence * 100:.0f}%)")
            else:
                print(f"    {context.test_id}: ERROR - {error}")
        
        # Generate summary report
        print("\n[REPORT] Generating analysis report...")
        report = {
            "metadata": {
                "total_failures": len(results),
                "successful_analyses": sum(1 for _, a, _ in results if a),
                "failed_analyses": sum(1 for _, a, _ in results if not a)
            },
            "analyses": []
        }
        
        for context, analysis, error in results:
            if analysis:
                report["analyses"].append({
                    "test_id": context.test_id,
                    "analysis": analysis.to_json_report()
                })
            else:
                report["analyses"].append({
                    "test_id": context.test_id,
                    "error": error
                })
        
        # Save report
        report_file = "failure_analysis_report.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)
        
        print(f"  Report saved to: {report_file}")
        
        # Display summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        for item in report["analyses"]:
            if "analysis" in item:
                analysis = item["analysis"]
                print(f"\n{item['test_id']}:")
                print(f"  Category: {analysis['category']}")
                print(f"  Confidence: {analysis['confidence']*100:.0f}%")
                print(f"  Summary: {analysis['summary']}")
        
    except Exception as e:
        print(f"  ERROR during batch analysis: {type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    main()
