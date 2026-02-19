"""
Log Parser with Contextual Clue Hunter for Jenkins CI Failure Analysis.

This module implements intelligent error detection and root cause analysis by:
1. Identifying primary failures using the ErrorMatcher
2. Classifying errors into taxonomy categories
3. Hunting backwards through logs for contextual clues
4. Extracting relevant context for AI-powered diagnosis

Architecture:
    - Primary Error Detection: Uses pre-compiled regex patterns
    - Taxonomy Mapping: Maps error patterns to failure categories
    - Backward Scanning: Searches up to 500 lines for relevant indicators
    - Context Aggregation: Builds rich FailureContext objects
"""

import logging
from typing import Dict, List, Optional, Set

from ci_failure_analyzer.models.log_models import FailureContext, LogSegment
from ci_failure_analyzer.parsing.regex_catalog import ErrorMatcher, Severity

logger = logging.getLogger(__name__)


class LogParser:
    """
    Intelligent log parser with contextual clue hunting capabilities.
    
    This parser goes beyond simple pattern matching by understanding the
    failure taxonomy and hunting for relevant contextual information that
    helps explain WHY the failure occurred, not just WHAT failed.
    
    Key Features:
        - Root cause identification (not just symptoms)
        - Backward scanning for contextual clues (up to 500 lines)
        - Taxonomy-aware keyword hunting
        - Immediate context preservation (5 lines before error)
    """
    
    # Failure Mode Taxonomy
    # Maps error categories to their diagnostic indicators
    # Source: Pipeline Model context (Jenkinsfile failure modes)
    FAILURE_MODE_TAXONOMY: Dict[str, Dict[str, any]] = {
        "linker_errors": {
            "category": "build",
            "indicators": [
                "undefined reference",
                "multiple definition",
                "ld:",
                "relocation",
                "overflowed",
                ".text",
                ".data",
                ".bss",
                "memory region",
                "section",
            ],
            "description": "Linker-related build failures (symbols, memory layout)"
        },
        "compiler_errors": {
            "category": "build",
            "indicators": [
                "error:",
                "syntax error",
                "undeclared",
                "expected",
                "implicit declaration",
                "incompatible",
                "type",
                "#error",
                "fatal error",
            ],
            "description": "Compiler errors (syntax, types, declarations)"
        },
        "make_errors": {
            "category": "build",
            "indicators": [
                "gmake",
                "make",
                "Makefile",
                "target",
                "recipe",
                "No rule",
                "missing separator",
                "Stop.",
            ],
            "description": "Build system failures (Make/CMake)"
        },
        "test_failures": {
            "category": "test",
            "indicators": [
                "FAIL",
                "FAILED",
                "assertion",
                "expected",
                "actual",
                "TestCase",
                "test_",
                "AssertionError",
                "UART",
                "scenario",
            ],
            "description": "Unit/integration test failures"
        },
        "hardware_failures": {
            "category": "environment",
            "indicators": [
                "FTDI",
                "board",
                "allocation",
                "pool",
                "USB",
                "device",
                "serial",
                "timeout",
                "hardware",
                "connection",
            ],
            "description": "Hardware resource allocation or connection issues"
        },
        "dependency_errors": {
            "category": "environment",
            "indicators": [
                "No such file",
                "cannot find",
                "missing",
                "not found",
                "ImportError",
                "ModuleNotFoundError",
                "svn:",
                "git:",
                "package",
            ],
            "description": "Missing files, libraries, or dependencies"
        },
        "permission_errors": {
            "category": "environment",
            "indicators": [
                "Permission denied",
                "Access denied",
                "Access is denied",
                "cannot access",
                "locked",
                "Authentication failed",
                "license",
                "FLEXnet",
            ],
            "description": "File system or license permission issues"
        },
        "resource_errors": {
            "category": "environment",
            "indicators": [
                "Insufficient memory",
                "Insufficient disk space",
                "out of memory",
                "OOM",
                "disk full",
                "timeout",
                "semaphore",
            ],
            "description": "System resource exhaustion"
        },
    }
    # Pattern ID to Taxonomy Category Mapping
    # Maps ERROR_XXX pattern IDs to their failure categories
    PATTERN_TAXONOMY_MAP: Dict[str, str] = {
        # Linker errors
        "ERROR_031": "linker_errors",  # undefined reference
        "ERROR_041": "linker_errors",  # multiple definition
        "ERROR_042": "linker_errors",  # overlaps section
        "ERROR_043": "linker_errors",  # region overflowed
        "ERROR_025": "linker_errors",  # no memory region
        "ERROR_018": "compiler_errors",  # Error: File: (should be compiler)
        
        # Compiler errors
        "ERROR_019": "compiler_errors",  # \d+:\serror:
        "ERROR_020": "compiler_errors",  # Segmentation error
        "ERROR_021": "compiler_errors",  # fatal: Symbol referencing errors
        
        # Make/build errors
        "ERROR_001": "make_errors",  # gmake: ***
        "ERROR_002": "make_errors",  # gmake: *** Error
        "ERROR_003": "make_errors",  # gmake error code
        "ERROR_004": "make_errors",  # gmake: missing Stop
        "ERROR_005": "make_errors",  # gmake: No rule to make target
        "ERROR_006": "make_errors",  # makefile: *** Stop.
        "ERROR_007": "make_errors",  # gmake: Command not found
        "ERROR_008": "make_errors",  # File not found
        "ERROR_011": "make_errors",  # make: ***
        "ERROR_012": "make_errors",  # make: *** Error
        "ERROR_013": "make_errors",  # make error code
        "ERROR_014": "make_errors",  # make: missing Stop
        "ERROR_015": "make_errors",  # make: No rule to make target
        
        # Test failures - ADD THESE
        "ERROR_053": "test_failures",  # FAIL:
        "ERROR_054": "test_failures",  # :FAIL$
        "ERROR_055": "test_failures",  # TEST FAILED
        "ERROR_056": "test_failures",  # Error: Failed to execute test
        "ERROR_049": "test_failures",  # \d+ Failure
        
        # Hardware failures
        "ERROR_075": "test_failures",  # This is the one from your test!
        
        # Dependency/file errors
        "ERROR_027": "dependency_errors",  # No such file or directory
        "ERROR_029": "dependency_errors",  # CMake Error
        "ERROR_032": "dependency_errors",  # No such file or directory
        "ERROR_035": "dependency_errors",  # ImportError
        "ERROR_047": "dependency_errors",  # svn: E\d+
        
        # Permission errors
        "ERROR_026": "permission_errors",  # Working copy locked
        "ERROR_037": "permission_errors",  # Access denied
        "ERROR_039": "permission_errors",  # Access is denied
        "ERROR_048": "permission_errors",  # Authentication failed
        "ERROR_050": "permission_errors",  # FLEXnet Licensing error
        
        # Resource errors
        "ERROR_033": "resource_errors",  # Insufficient memory
        "ERROR_034": "resource_errors",  # Insufficient disk space
        "ERROR_040": "resource_errors",  # File creation error - semaphore
    }
    
    # Search window for backward scanning (lines)
    MAX_BACKWARD_SCAN_LINES = 500
    
    # Immediate context window (lines immediately before error)
    IMMEDIATE_CONTEXT_LINES = 5
    
    def __init__(self, error_matcher: Optional[ErrorMatcher] = None):
        """
        Initialize the LogParser with an ErrorMatcher instance.
        
        Args:
            error_matcher: Optional ErrorMatcher instance. If None, creates new one.
        """
        self.error_matcher = error_matcher or ErrorMatcher()
        
        # Statistics
        self.stats = {
            'segments_analyzed': 0,
            'failures_found': 0,
            'clues_hunted': 0,
            'total_lines_scanned': 0,
        }
        
        logger.info("LogParser initialized with ErrorMatcher")
    
    def analyze_segment(self, segment: LogSegment) -> Optional[FailureContext]:
        """
        Analyze a log segment to find the primary failure and hunt for clues.
        
        This is the main entry point for log analysis. It:
        1. Scans all lines in the segment for errors
        2. Identifies the first ROOT CAUSE error (not cascading symptoms)
        3. Maps the error to a taxonomy category
        4. Hunts backward for contextual clues
        5. Builds a rich FailureContext object
        
        Args:
            segment: LogSegment to analyze
            
        Returns:
            FailureContext if a root cause is found, None otherwise
            
        Example:
            >>> parser = LogParser()
            >>> segment = LogSegment(...)
            >>> failure = parser.analyze_segment(segment)
            >>> if failure:
            ...     print(f"Found {failure.error_id} at line {failure.line_number}")
        """
        self.stats['segments_analyzed'] += 1
        
        logger.info(
            f"Analyzing segment: {segment.segment_type.value} - {segment.name} "
            f"({len(segment.content)} lines)"
        )
        
        # Find primary error
        primary_failure = self._find_primary_error(segment.content)
        
        if not primary_failure:
            logger.debug(f"No root cause errors found in segment: {segment.name}")
            return None
        
        error_line, error_line_num, match_result = primary_failure
        self.stats['failures_found'] += 1
        
        logger.info(
            f"Primary error found: {match_result.pattern_id} at line {error_line_num} "
            f"in segment {segment.name}"
        )
        
        # Map error to taxonomy category
        taxonomy_category = self._get_taxonomy_category(match_result.pattern_id)
        
        # Hunt for contextual clues
        hunted_clues = self._hunt_clues(
            segment.content,
            error_line_num,
            taxonomy_category
        )
        
        # Extract test name if applicable
        test_name = self._extract_test_name(error_line, segment.content, error_line_num)
        
        # Build FailureContext
        failure_context = FailureContext(
            error_id=match_result.pattern_id,
            primary_error_line=error_line,
            line_number=error_line_num + 1,  # Convert to 1-indexed
            hunted_clues=hunted_clues,
            test_name=test_name
        )
        
        logger.info(
            f"FailureContext created: {match_result.pattern_id} with "
            f"{len(hunted_clues)} clues hunted"
        )
        
        return failure_context
    
    def _find_primary_error(
        self,
        lines: List[str]
    ) -> Optional[tuple[str, int, any]]:
        """
        Find the first ROOT CAUSE error in the log lines.
        
        This method prioritizes errors marked as root_cause=True and
        ignores cascading symptoms and warnings unless no root cause is found.
        
        Args:
            lines: List of log lines to scan
            
        Returns:
            Tuple of (error_line, line_number, match_result) or None
        """
        first_error = None
        first_root_cause = None
        
        for line_num, line in enumerate(lines):
            self.stats['total_lines_scanned'] += 1
            
            match_result = self.error_matcher.find_match(line)
            
            if not match_result:
                continue
            
            # Skip warnings and info unless nothing else found
            if match_result.severity in (Severity.WARNING, Severity.INFO):
                continue
            
            # Record first error (fallback)
            if first_error is None and match_result.severity == Severity.ERROR:
                first_error = (line, line_num, match_result)
            
            # Prioritize root cause errors
            if match_result.is_root_cause:
                first_root_cause = (line, line_num, match_result)
                logger.debug(
                    f"Root cause identified: {match_result.pattern_id} at line {line_num}"
                )
                break  # Stop at first root cause
        
        return first_root_cause or first_error
    
    def _get_taxonomy_category(self, pattern_id: str) -> Optional[str]:
        """
        Map an error pattern ID to its taxonomy category.
        
        Args:
            pattern_id: Pattern identifier (e.g., 'ERROR_031')
            
        Returns:
            Taxonomy category key or None
        """
        category = self.PATTERN_TAXONOMY_MAP.get(pattern_id)
        
        if category:
            logger.debug(f"Pattern {pattern_id} mapped to category: {category}")
        else:
            logger.warning(f"Pattern {pattern_id} not mapped to any taxonomy category")
        
        return category
    
    def _hunt_clues(
        self,
        lines: List[str],
        error_line_num: int,
        taxonomy_category: Optional[str]
    ) -> Dict[str, str]:
        """
        Hunt for contextual clues by scanning backwards from the error.
        
        This implements the "Backward-Scanning Clue Hunter" algorithm:
        1. Always capture 5 lines immediately before error (immediate context)
        2. If taxonomy category known, scan up to 500 lines backward
        3. Search for lines containing taxonomy indicators (keywords)
        4. Aggregate all matching clues with keyword -> line mapping
        
        Args:
            lines: All log lines from the segment
            error_line_num: Line number where error occurred (0-indexed)
            taxonomy_category: Taxonomy category key (e.g., 'linker_errors')
            
        Returns:
            Dictionary mapping keywords to log lines containing them
        """
        hunted_clues: Dict[str, str] = {}
        
        # PHASE 1: Immediate Context (always captured)
        immediate_start = max(0, error_line_num - self.IMMEDIATE_CONTEXT_LINES)
        immediate_context = lines[immediate_start:error_line_num]
        
        for i, line in enumerate(immediate_context):
            context_key = f"context_line_{i+1}"
            hunted_clues[context_key] = line.strip()
        
        logger.debug(
            f"Captured {len(immediate_context)} lines of immediate context"
        )
        
        # PHASE 2: Taxonomy-Aware Keyword Hunting
        if not taxonomy_category:
            logger.info("No taxonomy category - skipping keyword hunting")
            return hunted_clues
        
        taxonomy_data = self.FAILURE_MODE_TAXONOMY.get(taxonomy_category)
        
        if not taxonomy_data:
            logger.warning(
                f"Taxonomy category '{taxonomy_category}' not found in taxonomy"
            )
            return hunted_clues
        
        indicators: List[str] = taxonomy_data.get("indicators", [])
        
        if not indicators:
            logger.warning(
                f"No indicators defined for category '{taxonomy_category}'"
            )
            return hunted_clues
        
        logger.info(
            f"Hunting for clues in category '{taxonomy_category}' "
            f"with {len(indicators)} indicators"
        )
        
        # Determine scan window
        scan_start = max(0, error_line_num - self.MAX_BACKWARD_SCAN_LINES)
        scan_window = lines[scan_start:error_line_num]
        
        logger.debug(
            f"Scanning {len(scan_window)} lines backward from line {error_line_num}"
        )
        
        # Search for indicators
        indicators_found: Set[str] = set()
        
        for line in reversed(scan_window):  # Scan backward
            line_lower = line.lower()
            
            for indicator in indicators:
                indicator_lower = indicator.lower()
                
                # Check if indicator is in the line
                if indicator_lower in line_lower:
                    # Avoid duplicates
                    if indicator not in indicators_found:
                        clue_key = f"indicator_{indicator.replace(' ', '_')}"
                        hunted_clues[clue_key] = line.strip()
                        indicators_found.add(indicator)
                        self.stats['clues_hunted'] += 1
                        
                        logger.debug(
                            f"Clue found: '{indicator}' in line: {line[:60]}..."
                        )
        
        logger.info(
            f"Clue hunting complete: {len(indicators_found)} unique indicators found"
        )
        
        return hunted_clues
    
    def _extract_test_name(
        self,
        error_line: str,
        all_lines: List[str],
        error_line_num: int
    ) -> Optional[str]:
        """
        Extract test name from error line or nearby context.
        
        Looks for patterns like:
        - TestCase: test_name
        - TEST(suite, test_name)
        - test_function_name
        
        Args:
            error_line: The line containing the error
            all_lines: All log lines
            error_line_num: Line number of the error
            
        Returns:
            Test name if found, None otherwise
        """
        import re
        
        # Pattern 1: TestCase: test_name
        match = re.search(r'TestCase:\s+(\w+)', error_line)
        if match:
            return match.group(1)
        
        # Pattern 2: TEST(suite, test_name)
        match = re.search(r'TEST\((\w+),\s*(\w+)\)', error_line)
        if match:
            return f"{match.group(1)}.{match.group(2)}"
        
        # Pattern 3: Search backward for test context
        scan_start = max(0, error_line_num - 20)
        context_lines = all_lines[scan_start:error_line_num + 1]
        
        for line in reversed(context_lines):
            match = re.search(r'TestCase:\s+(\w+)', line)
            if match:
                return match.group(1)
            
            match = re.search(r'TEST\((\w+),\s*(\w+)\)', line)
            if match:
                return f"{match.group(1)}.{match.group(2)}"
        
        return None
    
    def get_statistics(self) -> Dict[str, int]:
        """
        Get parser statistics for monitoring and debugging.
        
        Returns:
            Dictionary with statistics
        """
        return self.stats.copy()


def create_log_parser(error_matcher: Optional[ErrorMatcher] = None) -> LogParser:
    """
    Factory function to create a LogParser instance.
    
    Args:
        error_matcher: Optional ErrorMatcher instance
        
    Returns:
        Initialized LogParser instance
        
    Example:
        >>> parser = create_log_parser()
        >>> segment = LogSegment(...)
        >>> failure = parser.analyze_segment(segment)
    """
    return LogParser(error_matcher=error_matcher)
