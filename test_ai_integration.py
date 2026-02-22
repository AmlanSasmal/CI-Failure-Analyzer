#!/usr/bin/env python3
"""
Integration test for AI-ready failure analysis models.

Demonstrates the complete workflow:
1. Parse logs to extract failures
2. Generate AI prompts with token-aware truncation
3. Create analysis results
4. Export reports
"""

from src.ci_failure_analyzer.models.log_models import (
    LogEntry, FailureContext, AnalysisResult, FailureCategory
)
import json


def test_full_ai_workflow():
    """Test complete AI integration workflow."""
    
    print("=" * 80)
    print("AI INTEGRATION WORKFLOW TEST")
    print("=" * 80)
    
    # Step 1: Create failure contexts (as would come from LogParser)
    print("\n[STEP 1] Creating failure contexts from parsed logs...")
    
    failures = []
    
    # Failure 1: CRC Bias error
    failure1 = FailureContext(
        test_id="rfe_tc_3120_sm69RfgCrcGbias",
        status="FAILED",
        duration=45,
        received_bytes=2048,
        start_line=100,
        verdict_line=200
    )
    
    # Add log entries simulating real parsed logs
    log_messages = [
        "Executing rfe_tc_3120_sm69RfgCrcGbias",
        "Test sequence: Frequency sweep 2.0GHz to 2.5GHz",
        "Setting frequency: 2.0GHz",
        "T32 breakpoint set at frequency config",
        "Register dump before CRC write:",
        "T32> r 0x1040",
        "0x1040: 0x00000000 (CRC_BIAS register)",
        "Calling sm69RfgCrc_set_bias(0x0A)",
        "T32 step into sm69RfgCrc module",
        "CRC initialization sequence started",
        "Frequency sweep: 2.1GHz",
        "Frequency sweep: 2.2GHz",
        "Frequency sweep: 2.3GHz",
        "Frequency sweep: 2.4GHz",
        "T32> r 0x1040",
        "0x1040: 0x0000000A (CRC_BIAS = 0x0A as expected)",
        "CRC check started for 2.4GHz configuration",
        "Transmitted CRC frame: 1024 bytes",
        "Assertion failed: CRC mismatch",
        "Expected CRC: 0xDEADBEEF",
        "Received CRC: 0xCAFEBABE",
        "Error analysis:",
        "High 4 bits of CRC register undefined (garbage from previous test)",
        "sm69RfgCrc_set_bias() uses |= operator, doesn't clear register first",
        "Test verdict: FAILED"
    ]
    
    for i, msg in enumerate(log_messages, start=101):
        is_t32 = "T32" in msg or "0x1040:" in msg
        failure1.add_line(LogEntry(
            line_number=i,
            message=msg,
            raw_message=msg,
            is_t32_script=is_t32
        ))
    
    failures.append(failure1)
    print(f"  Created: {failure1.test_id}")
    print(f"    Lines: {len(failure1.context_lines)}")
    print(f"    T32 references: {len(failure1.t32_debug_lines)}")
    
    # Failure 2: LOIF frequency issue
    failure2 = FailureContext(
        test_id="rfe_tc_3125_sm69RfgCrcLoif",
        status="FAILED",
        duration=38,
        received_bytes=1536,
        start_line=500,
        verdict_line=580
    )
    
    loif_messages = [
        "Executing rfe_tc_3125_sm69RfgCrcLoif",
        "Local oscillator test: IF frequency validation",
        "Setting LO frequency: 2.35GHz",
        "T32> freq 2.35GHz",
        "Local oscillator locked",
        "T32> r 0x1080",
        "0x1080: 0x00000023 (LOIF_CTRL register)",
        "IF frequency offset calculation: -45MHz",
        "CRC initialization for LOIF test",
        "Transmitting LOIF test pattern",
        "Received signal sample 1: phase=45.2deg",
        "Received signal sample 2: phase=45.1deg",
        "Received signal sample 3: phase=44.8deg",
        "Received signal sample 4: phase=319.2deg (PHASE JUMP!)",
        "Received signal sample 5: phase=320.1deg",
        "Phase coherence lost during transmission",
        "Assertion failed: Phase jump detected",
        "Phase continuity violation: 44.8 -> 319.2 degrees",
        "LO sync loss suspected in hardware PLL",
        "Test verdict: FAILED"
    ]
    
    for i, msg in enumerate(loif_messages, start=501):
        is_t32 = "T32" in msg or "0x1080:" in msg
        failure2.add_line(LogEntry(
            line_number=i,
            message=msg,
            raw_message=msg,
            is_t32_script=is_t32
        ))
    
    failures.append(failure2)
    print(f"  Created: {failure2.test_id}")
    print(f"    Lines: {len(failure2.context_lines)}")
    print(f"    T32 references: {len(failure2.t32_debug_lines)}")
    
    # Step 2: Generate AI prompts
    print("\n[STEP 2] Generating AI prompts with token-aware truncation...")
    
    prompts = {}
    for failure in failures:
        prompt = failure.to_ai_prompt()
        prompts[failure.test_id] = prompt
        
        chars = len(prompt)
        estimated_tokens = chars / 4  # Rough estimate
        truncated = "[... truncated ...]" in prompt
        
        print(f"  {failure.test_id}:")
        print(f"    Size: {chars:,} chars (~{estimated_tokens:.0f} tokens)")
        print(f"    Truncated: {'Yes' if truncated else 'No'}")
        print(f"    Token budget OK: {chars <= 12000} (limit: 12,000 chars)")
    
    # Step 3: Simulate AI analysis
    print("\n[STEP 3] Simulating AI analysis of failures...")
    
    analyses = {}
    
    # Analysis 1: CRC Bias issue
    analysis1 = AnalysisResult(
        category=FailureCategory.CODE_BUG,
        summary="CRC register bias initialization bug",
        root_cause=(
            "The sm69RfgCrc_set_bias() function uses bitwise OR (|=) to set the "
            "bias field without first clearing the register. High bits remain "
            "uninitialized from the previous test, corrupting the CRC calculation."
        ),
        suggested_fix=(
            "1. Change CRC_BIAS write from: reg |= (bias << 0)\n"
            "   To: reg = (reg & 0xFFFFFFF0) | (bias & 0x0F)\n"
            "2. OR add explicit register clear in test setup: CRC_BIAS = 0x00\n"
            "3. Add register state validation in test prologue"
        ),
        confidence=0.94
    )
    analyses[failure1.test_id] = analysis1
    print(f"  {failure1.test_id}:")
    print(f"    Category: {analysis1.category.value}")
    print(f"    Confidence: {analysis1.confidence * 100:.0f}%")
    print(f"    Root Cause: {analysis1.root_cause[:60]}...")
    
    # Analysis 2: LO phase jump issue
    analysis2 = AnalysisResult(
        category=FailureCategory.INFRA_FAILURE,
        summary="Local oscillator synchronization loss in test harness",
        root_cause=(
            "Phase jump from 44.8° to 319.2° indicates loss of PLL lock in the "
            "RF frontend. The signal generator's LO did not maintain frequency "
            "lock during the IF frequency offset test, likely due to:"
            "- Inadequate loop filter response time"
            "- RF cable impedance mismatch during frequency transition"
            "- Test harness ground integrity issue (return path inductance)"
        ),
        suggested_fix=(
            "1. Increase PLL lock time after LO frequency change\n"
            "2. Verify RF cable quality and impedance (50 ohm specification)\n"
            "3. Check test fixture ground straps for corrosion\n"
            "4. Review signal generator PLL tuning range for 2.35GHz\n"
            "5. Add oscilloscope probe to LO output for hardware validation"
        ),
        confidence=0.78
    )
    analyses[failure2.test_id] = analysis2
    print(f"  {failure2.test_id}:")
    print(f"    Category: {analysis2.category.value}")
    print(f"    Confidence: {analysis2.confidence * 100:.0f}%")
    print(f"    Root Cause: {analysis2.root_cause[:60]}...")
    
    # Step 4: Export reports
    print("\n[STEP 4] Exporting analysis reports...")
    
    report = {
        "metadata": {
            "analysis_version": "1.0",
            "total_failures": len(failures),
            "analysis_count": len(analyses)
        },
        "failures": []
    }
    
    for failure in failures:
        if failure.test_id in analyses:
            analysis = analyses[failure.test_id]
            
            failure_report = {
                "test_id": failure.test_id,
                "status": failure.status,
                "duration_seconds": failure.duration,
                "received_bytes": failure.received_bytes,
                "context_lines": len(failure.context_lines),
                "t32_debug_lines": len(failure.t32_debug_lines),
                "analysis": analysis.to_json_report()
            }
            
            report["failures"].append(failure_report)
    
    # Pretty print report
    print("  Report structure:")
    print(json.dumps(report, indent=2)[:500])
    print("  ... (truncated for display)")
    
    # Step 5: Validation
    print("\n[STEP 5] Validation checks...")
    
    all_passed = True
    
    # Check 1: All failures analyzed
    check1 = len(analyses) == len(failures)
    print(f"  All failures analyzed: {check1}")
    all_passed &= check1
    
    # Check 2: Prompts within token budget
    check2 = all(len(p) <= 12000 for p in prompts.values())
    print(f"  Prompts within token budget (12K chars): {check2}")
    all_passed &= check2
    
    # Check 3: Confidence scores valid
    check3 = all(0.0 <= a.confidence <= 1.0 for a in analyses.values())
    print(f"  Confidence scores in range [0.0-1.0]: {check3}")
    all_passed &= check3
    
    # Check 4: FailureCategory enum used
    check4 = all(isinstance(a.category, FailureCategory) for a in analyses.values())
    print(f"  FailureCategory enum properly used: {check4}")
    all_passed &= check4
    
    # Check 5: T32 debugger output captured
    check5 = all(len(f.t32_debug_lines) > 0 for f in failures)
    print(f"  T32 debugger output captured in all failures: {check5}")
    all_passed &= check5
    
    print("\n" + "=" * 80)
    if all_passed:
        print("SUCCESS: All AI integration tests passed!")
    else:
        print("FAILURE: Some validation checks failed!")
    print("=" * 80)
    
    return all_passed


if __name__ == "__main__":
    success = test_full_ai_workflow()
    exit(0 if success else 1)
