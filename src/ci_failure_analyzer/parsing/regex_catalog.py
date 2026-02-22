"""
Regex pattern catalog for parsing hardware CI logs.

This module defines and compiles regex patterns used to extract structured
information from CI/CD logs. Pre-compiled patterns are cached for performance.
"""

import re
from typing import Dict

# Raw pattern definitions
LOG_PATTERNS = {
    # ISO8601 timestamps in brackets at the start of a line
    # Matches: [2026-02-03T22:22:46.440Z]
    "TIMESTAMP": r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)\]",
    
    # Shell execution and environment exports
    # Matches lines with shell trace output (+ or ++ or +++) after timestamp
    # Example: [2026-02-03T22:19:34.268Z] + echo 'test'
    "SHELL_NOISE": r"\]\s+(\+{1,3})\s",
    
    # Test execution pattern
    # Matches: Executing rfe_tc_001_validChirpTimingConfiguration
    "TEST_EXECUTION": r"Executing\s+(?P<test_id>rfe_tc_\d+_\w+)",
    
    # Test verdict with status, duration and bytes received
    # Matches: Found verdict PASSED after 8s and 55036 received bytes.
    "VERDICT": r"Found verdict\s+(?P<status>PASSED|FAILED)\s+after\s+(?P<duration>\d+)s\s+and\s+(?P<bytes>\d+)\s+received bytes",
    
    # T32 debugger script lines
    # Matches lines with INFO-root:T32: or DEBUG-root:T32: patterns
    # Example: INFO-root:T32: ON ERROR GOTO errorexit
    "T32_SCRIPT": r"(?:INFO|DEBUG)-\w+:T32:\s",
    
    # JIRA/urllib3 debug lines
    # Matches DEBUG-urllib3 or DEBUG-root:Issue patterns
    # Example: DEBUG-urllib3.connectionpool:Starting new HTTPS connection
    "JIRA_DEBUG": r"(?:DEBUG-urllib3|DEBUG-root:Issue)",
}


def compile_patterns() -> Dict[str, re.Pattern]:
    """
    Compile all raw regex patterns into compiled regex objects for performance.
    
    Pre-compiling patterns improves performance when matching against large
    numbers of log lines, as regex compilation is the most expensive operation.
    
    Returns:
        Dict[str, re.Pattern]: Dictionary mapping pattern names to compiled
            regex Pattern objects with MULTILINE flag enabled.
    
    Example:
        >>> patterns = compile_patterns()
        >>> timestamp_pattern = patterns['TIMESTAMP']
        >>> match = timestamp_pattern.search("[2026-02-03T22:22:46.440Z] Some log line")
        >>> match.group('timestamp')
        '2026-02-03T22:22:46.440Z'
    """
    compiled = {}
    for name, pattern in LOG_PATTERNS.items():
        try:
            compiled[name] = re.compile(pattern, re.MULTILINE)
        except re.error as e:
            raise ValueError(
                f"Invalid regex pattern for '{name}': {pattern}\n"
                f"Error: {e}"
            )
    return compiled


# Cache compiled patterns at module level for reuse
_COMPILED_PATTERNS = None


def get_compiled_patterns() -> Dict[str, re.Pattern]:
    """
    Get cached compiled patterns, compiling them on first access.
    
    This function implements lazy compilation with caching to avoid
    recompiling patterns on every call.
    
    Returns:
        Dict[str, re.Pattern]: Cached dictionary of compiled patterns.
    
    Example:
        >>> patterns = get_compiled_patterns()
        >>> verdict_match = patterns['VERDICT'].search(log_line)
    """
    global _COMPILED_PATTERNS
    if _COMPILED_PATTERNS is None:
        _COMPILED_PATTERNS = compile_patterns()
    return _COMPILED_PATTERNS


# Pattern descriptions for documentation
PATTERN_DESCRIPTIONS = {
    "TIMESTAMP": "ISO8601 timestamp in brackets at line start: [YYYY-MM-DDTHH:MM:SS.fffZ]",
    "SHELL_NOISE": "Shell execution trace (lines starting with +, ++, or +++)",
    "TEST_EXECUTION": "Test execution start with test_id like rfe_tc_001_validChirpTimingConfiguration",
    "VERDICT": "Test verdict with status (PASSED/FAILED), duration in seconds, and received bytes",
    "T32_SCRIPT": "Trace32 debugger script output (T32: prefix in log lines)",
    "JIRA_DEBUG": "Debug output related to JIRA or urllib3 library",
}
