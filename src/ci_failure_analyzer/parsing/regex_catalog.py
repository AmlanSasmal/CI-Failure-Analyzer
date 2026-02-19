"""
Error pattern matcher for Jenkins log analysis.

This module implements the error detection engine based on the Error Catalog.
It uses pre-compiled regex patterns and enforces strict priority rules to
prevent false positives while identifying build failures, warnings, and
informational messages.

Architecture:
    - Pre-filter Rule: Ignore patterns (ok) are checked FIRST
    - Priority Order: ignore > error > warning > info
    - Pattern Compilation: All regex patterns pre-compiled at initialization
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """
    Severity levels for log line classifications.
    
    Order of precedence (highest to lowest):
    ERROR > WARNING > INFO > IGNORE
    """
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    IGNORE = "IGNORE"
    START = "START"


@dataclass
class MatchResult:
    """
    Structured result from error pattern matching.
    
    Attributes:
        pattern_id: Unique identifier for the matched pattern (e.g., 'ERROR_GMAKE_001')
        severity: Severity level of the match
        matched_line: The original log line that matched
        pattern_text: The regex pattern that matched
        description: Human-readable description of the pattern
        is_root_cause: Flag indicating if this is likely a root cause vs symptom
        groups: Captured regex groups for context extraction
    """
    pattern_id: str
    severity: Severity
    matched_line: str
    pattern_text: str
    description: str
    is_root_cause: bool = False
    groups: Tuple[str, ...] = ()
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"MatchResult(id={self.pattern_id}, "
            f"severity={self.severity}, "
            f"root_cause={self.is_root_cause})"
        )


class ErrorMatcher:
    """
    High-performance error pattern matcher with pre-compiled regex patterns.
    
    This class implements the core error detection logic using the Error Catalog.
    It enforces the Pre-filter Rule: all lines are first checked against ignore
    patterns before being evaluated for errors/warnings.
    
    Why Pre-compilation?
    --------------------
    Jenkins logs can contain millions of lines. Pre-compiling regex patterns
    during initialization (once) instead of on every match attempt provides
    significant performance improvements (10-100x faster).
    
    Why Pre-filter Rule?
    --------------------
    Many build systems generate output that looks like errors but are actually
    known false positives (e.g., "Error: ignored", "gmake[1]: Error (ignored)").
    The pre-filter ensures these are discarded immediately, preventing
    downstream false positive analysis.
    """
    
    def __init__(self, catalog_path: Optional[Path] = None):
        """
        Initialize the ErrorMatcher with pre-compiled patterns.
        
        Args:
            catalog_path: Optional path to custom error catalog file.
                         If None, uses the default ERROR_CATALOG.
        """
        self.ignore_patterns: List[Tuple[str, Pattern]] = []
        self.error_patterns: List[Tuple[str, Pattern]] = []
        self.warning_patterns: List[Tuple[str, Pattern]] = []
        self.info_patterns: List[Tuple[str, Pattern]] = []
        self.start_patterns: List[Tuple[str, Pattern]] = []
        
        # Pattern metadata for enrichment
        self.pattern_metadata: Dict[str, Dict[str, any]] = {}
        
        # Performance counters
        self.stats = {
            'total_checks': 0,
            'ignore_hits': 0,
            'error_hits': 0,
            'warning_hits': 0,
            'info_hits': 0,
        }
        
        # Load and compile patterns
        if catalog_path:
            self._load_catalog_from_file(catalog_path)
        else:
            self._load_default_catalog()
        
        logger.info(
            f"ErrorMatcher initialized: "
            f"{len(self.ignore_patterns)} ignore, "
            f"{len(self.error_patterns)} error, "
            f"{len(self.warning_patterns)} warning, "
            f"{len(self.info_patterns)} info patterns"
        )
    
    def _load_default_catalog(self) -> None:
        """
        Load and compile the default Error Catalog patterns.
        
        This method parses the Error Catalog format:
        - ok /pattern/       -> ignore patterns
        - error /pattern/    -> error patterns
        - warning /pattern/  -> warning patterns
        - info /pattern/     -> info patterns
        - start /pattern/    -> section markers
        
        Patterns are extracted from between forward slashes and compiled
        with appropriate flags (case-insensitive where (?i) is used).
        """
        # Based on the provided log_parser.txt catalog
        catalog_content = self._get_default_catalog_content()
        self._parse_and_compile_catalog(catalog_content)
    
    def _load_catalog_from_file(self, catalog_path: Path) -> None:
        """
        Load catalog from an external file.
        
        Args:
            catalog_path: Path to the error catalog file
        """
        logger.info(f"Loading error catalog from: {catalog_path}")
        with open(catalog_path, 'r', encoding='utf-8') as f:
            catalog_content = f.read()
        self._parse_and_compile_catalog(catalog_content)
    
    def _parse_and_compile_catalog(self, catalog_content: str) -> None:
        """
        Parse catalog content and compile all regex patterns.
        
        Args:
            catalog_content: Raw catalog file content
        """
        pattern_counter = {
            'ok': 0,
            'error': 0,
            'warning': 0,
            'info': 0,
            'start': 0,
        }
        
        for line in catalog_content.split('\n'):
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            
            # Parse pattern lines: <type> /<pattern>/
            if line.startswith('ok '):
                pattern = self._extract_pattern(line, 'ok')
                if pattern:
                    pattern_counter['ok'] += 1
                    pattern_id = f"IGNORE_{pattern_counter['ok']:03d}"
                    self._compile_and_store(pattern, pattern_id, Severity.IGNORE, self.ignore_patterns)
            
            elif line.startswith('error '):
                pattern = self._extract_pattern(line, 'error')
                if pattern:
                    pattern_counter['error'] += 1
                    pattern_id = f"ERROR_{pattern_counter['error']:03d}"
                    is_root_cause = self._determine_root_cause(pattern)
                    self._compile_and_store(
                        pattern, pattern_id, Severity.ERROR,
                        self.error_patterns, is_root_cause=is_root_cause
                    )
            
            elif line.startswith('warning '):
                pattern = self._extract_pattern(line, 'warning')
                if pattern:
                    pattern_counter['warning'] += 1
                    pattern_id = f"WARNING_{pattern_counter['warning']:03d}"
                    self._compile_and_store(pattern, pattern_id, Severity.WARNING, self.warning_patterns)
            
            elif line.startswith('info '):
                pattern = self._extract_pattern(line, 'info')
                if pattern:
                    pattern_counter['info'] += 1
                    pattern_id = f"INFO_{pattern_counter['info']:03d}"
                    self._compile_and_store(pattern, pattern_id, Severity.INFO, self.info_patterns)
            
            elif line.startswith('start '):
                pattern = self._extract_pattern(line, 'start')
                if pattern:
                    pattern_counter['start'] += 1
                    pattern_id = f"START_{pattern_counter['start']:03d}"
                    self._compile_and_store(pattern, pattern_id, Severity.START, self.start_patterns)
        
        logger.info(f"Compiled pattern statistics: {pattern_counter}")
    
    def _extract_pattern(self, line: str, prefix: str) -> Optional[str]:
        """
        Extract regex pattern from catalog line format.
        
        Args:
            line: Catalog line (e.g., "error /pattern/")
            prefix: Pattern type prefix (ok, error, warning, info)
            
        Returns:
            Extracted pattern string or None if invalid format
        """
        # Remove prefix
        pattern_part = line[len(prefix):].strip()
        
        # Extract pattern between forward slashes
        if pattern_part.startswith('/') and pattern_part.endswith('/'):
            return pattern_part[1:-1]
        
        logger.warning(f"Invalid pattern format: {line}")
        return None
    
    def _compile_and_store(
        self,
        pattern_str: str,
        pattern_id: str,
        severity: Severity,
        storage_list: List[Tuple[str, Pattern]],
        is_root_cause: bool = False
    ) -> None:
        """
        Compile a regex pattern and store with metadata.
        
        Args:
            pattern_str: Raw regex pattern string
            pattern_id: Unique identifier for this pattern
            severity: Severity level
            storage_list: List to store the compiled pattern
            is_root_cause: Whether this pattern indicates a root cause
        """
        try:
            # Compile with MULTILINE flag for ^ and $ anchors
            compiled_pattern = re.compile(pattern_str, re.MULTILINE)
            storage_list.append((pattern_id, compiled_pattern))
            
            # Store metadata
            self.pattern_metadata[pattern_id] = {
                'pattern_text': pattern_str,
                'severity': severity,
                'is_root_cause': is_root_cause,
                'description': self._generate_description(pattern_str, severity)
            }
            
        except re.error as e:
            logger.error(f"Failed to compile pattern {pattern_id}: {pattern_str} - Error: {e}")
    
    def _determine_root_cause(self, pattern: str) -> bool:
        """
        Heuristic to determine if an error pattern indicates a root cause.
        
        Root cause indicators:
        - Compiler errors (syntax, type errors)
        - Linker errors (undefined reference, multiple definition)
        - Missing files/dependencies
        - Test failures (as opposed to test infrastructure issues)
        
        Args:
            pattern: Regex pattern string
            
        Returns:
            True if likely a root cause pattern
        """
        root_cause_keywords = [
            'undefined reference',
            'multiple definition',
            'No such file',
            'syntax error',
            'Unresolved symbol',
            'TEST FAILED',
            'FAIL:',
            'missing.*Stop',
            'No rule to make target',
            'overflowed by',
        ]
        
        pattern_lower = pattern.lower()
        return any(keyword.lower() in pattern_lower for keyword in root_cause_keywords)
    
    def _generate_description(self, pattern: str, severity: Severity) -> str:
        """
        Generate human-readable description for a pattern.
        
        Args:
            pattern: Regex pattern string
            severity: Pattern severity
            
        Returns:
            Description string
        """
        # Extract readable parts from regex
        if 'gmake' in pattern.lower():
            return "GNU Make build error"
        elif 'undefined reference' in pattern.lower():
            return "Linker error - undefined symbol"
        elif 'multiple definition' in pattern.lower():
            return "Linker error - duplicate symbol"
        elif 'test' in pattern.lower() and 'fail' in pattern.lower():
            return "Unit test failure"
        elif 'no such file' in pattern.lower():
            return "Missing file or directory"
        else:
            return f"{severity.value} pattern match"
    
    def find_match(self, line: str) -> Optional[MatchResult]:
        """
        Find the first matching pattern for a log line.
        
        This implements the Pre-filter Rule:
        1. Check ignore patterns first (MUST discard if matched)
        2. Check error patterns (highest priority)
        3. Check warning patterns
        4. Check info patterns
        
        Args:
            line: Single log line to analyze
            
        Returns:
            MatchResult if a pattern matches, None otherwise
            
        Example:
            >>> matcher = ErrorMatcher()
            >>> result = matcher.find_match("gmake: *** [all] Error 1")
            >>> if result and result.severity == Severity.ERROR:
            ...     print(f"Error detected: {result.pattern_id}")
        """
        self.stats['total_checks'] += 1
        
        # PRE-FILTER RULE: Check ignore patterns FIRST
        # If matched, immediately return None (discard line)
        for pattern_id, pattern in self.ignore_patterns:
            if pattern.search(line):
                self.stats['ignore_hits'] += 1
                logger.debug(f"Line ignored by {pattern_id}: {line[:80]}")
                return None
        
        # Check error patterns
        for pattern_id, pattern in self.error_patterns:
            match = pattern.search(line)
            if match:
                self.stats['error_hits'] += 1
                return self._build_match_result(pattern_id, line, match)
        
        # Check warning patterns
        for pattern_id, pattern in self.warning_patterns:
            match = pattern.search(line)
            if match:
                self.stats['warning_hits'] += 1
                return self._build_match_result(pattern_id, line, match)
        
        # Check info patterns
        for pattern_id, pattern in self.info_patterns:
            match = pattern.search(line)
            if match:
                self.stats['info_hits'] += 1
                return self._build_match_result(pattern_id, line, match)
        
        return None
    
    def _build_match_result(
        self,
        pattern_id: str,
        line: str,
        match: re.Match
    ) -> MatchResult:
        """
        Build a MatchResult object from a regex match.
        
        Args:
            pattern_id: ID of the matched pattern
            line: Original log line
            match: Regex match object
            
        Returns:
            Populated MatchResult object
        """
        metadata = self.pattern_metadata.get(pattern_id, {})
        
        return MatchResult(
            pattern_id=pattern_id,
            severity=metadata.get('severity', Severity.INFO),
            matched_line=line,
            pattern_text=metadata.get('pattern_text', ''),
            description=metadata.get('description', ''),
            is_root_cause=metadata.get('is_root_cause', False),
            groups=match.groups()
        )
    
    def get_statistics(self) -> Dict[str, int]:
        """
        Get matching statistics for performance monitoring.
        
        Returns:
            Dictionary with hit counts and ratios
        """
        total = self.stats['total_checks']
        if total == 0:
            return self.stats.copy()
        
        return {
            **self.stats,
            'ignore_rate': self.stats['ignore_hits'] / total,
            'error_rate': self.stats['error_hits'] / total,
            'warning_rate': self.stats['warning_hits'] / total,
        }
    
    def _get_default_catalog_content(self) -> str:
        """
        Return the default error catalog content.
        
        This is the Error Catalog provided in log_parser.txt.
        In production, this would be loaded from a configuration file.
        
        Returns:
            Error catalog content as string
        """
        return """
# RFE SW Error Catalog
# Author: Vinoth Ranee Kumar

# IGNORE PATTERNS (Pre-filter - checked FIRST)
ok /not really/
ok /Warning:\s.*?:\scouldn\'t read file.*No such file or directory/
ok /gmake\:\s\[all\] Error .* \(ignored\)/
ok /\:\s+cannot stat.*\.ORT\'\:\sNo such file or directory/
ok /mv\: cannot stat.*\: No such file or directory/
ok /cp\: cannot stat.*\: No such file or directory/
ok /.*\:\s+END OF BUILD SCRIPT on/
ok /svn: E200009: Could not display info for all targets/
ok /does not exist. Calculating /
ok /ERROR: No submodules found/

# INFORMATIONAL PATTERNS
info /TestCase: CFG_MNGR_rfe_sw_cfg_mngr_tc_001_configure_paramDistribution: Test Result: TEST FAILED/
info /testSwCfgMngr\.c:261:TEST\(CFG_MNGR, rfe_sw_cfg_mngr_tc_001_configure_paramDistribution\):FAIL:/
info /.*Scenario .+ is allowed to fail.*/
info /.*INFO:UART:.+:FAIL.*/
info /.*INFO:RCA\+QTA:UART:.+:FAIL.*/
info /INFO:Skipping scenario .*Error:.*/
info /The system cannot find the path specified\./
info /data-type-mismatch/
info /INFO:/
info /!!.*!!/
info /INFO: OK/
info / TIME FOR /
info /^Info: Start Time /
info /^Info: End Time /

# WARNING PATTERNS
warning /(?i)^gmake:\swarning:.*/
warning /(?i)warning::\s+.*/
warning /warning\s+#/
warning /warning:/
warning /WARNING:\s+/
warning /.*BLSF: Magic identifier mismatch, not a valid bootloader script in flash.*/
warning /Use of uninitialized value/
warning /[1-9][0-9]*\s+warning/

# ERROR PATTERNS
error /(?i)gmake:\s\*\*\*\s.*\s/
error /(?i)gmake:\s\*\*\*\s.*\sError/
error /(?i)gmake error code.*/
error /(?i)gmake:\smissing.*Stop/
error /(?i)gmake:\sNo rule to make target/
error /(?i)makefile\:.*\:\s+\*\*\*\s+.*\.\s+Stop\./
error /(?i)gmake:\s+.*:\s+Command not found/
error /File not found\s+/
error /Caused by:\s+/
error /Option inxml requires an argument:\s+/
error /(?i)make:\s\*\*\*\s.*\s/
error /(?i)make:\s\*\*\*\s.*\sError/
error /(?i)make error code.*/
error /(?i)make:\smissing.*Stop/
error /(?i)make:\sNo rule to make target/
error /(?i)make:\s+.*:\s+Command not found/
error /Error: File:/
error /\d{1,}:\serror:\s.*/
error /Segmentation error\./
error /fatal: Symbol referencing errors. No output written to/
error /^\s*ERROR:\s+.*/
error /ERROR::\s+.*/
error /Can't call method\s.*/
error /is not recognized as an internal or external command/
error /The system cannot find the file specified\./
error /The file name is too long\./
error /svn: Working copy.*locked/
error /svn:.*Working copy.*locked/
error /No such file or directory/
error /Insufficient memory/
error /Insufficient disk space/
error /CMake Error.*/
error /ninja: build stopped: subcommand failed.*/
error /Option inxml requires an argument/
error /uses more than 100% of his size/
error /error \: Unresolved symbol/
error /Build FAILED\./
error /Application:.*: build_failed/
error /Library:.*: build_failed/
error /Error\:\s+.*\:\s+.*/
error /ImportError\: No module named xlrd/
error /xt-xcc ERROR:\s+/
error /\: error\: no memory region specified for loadable section/
error /\*+\s+\[ERROR\s\#\d+\]/
error /Error code is \d+/
error /error\: vdsp1 build failed/
error /error\: vdsp2 build failed/
error /error\: arm build failed/
error /The following error broke the build\:/
error /Access denied/
error /MISRA_c2012_rules.config does not exist/
error /Access is denied/
error /File creation error \- The semaphore timeout period has expired/
error /\smultiple definition of\s/
error /:\s+undefined reference to\s+/
error /overlaps section/s+.*/s+loaded at/
error /region\s+.*\s+overflowed by/
error /could not allocate \d+ blocks:/
error /ABORTING .*sat_genbin/
error /Unknown command line option/
error /Build step\s.*\smarked build as failure/
error /java\.lang\.InternalError:/
error /ERROR\: TOOL\: The product licence for QA C could not be obtained\./
error /Option appdir requires an argument/
error /buildapp\.pl \[options\]/
error /Error::Artifact does not/
error /svn: E\d+:/
error /Authentication failed/
error /FLEXnet Licensing error:/
error /The licence request was refused:/
error /capture.py error on exit\s+\=\s+1/
error /\d+\sFailure/
error /FAIL:\s/
error /:FAIL$/
error /:\s+TEST FAILED/
error /Error: Failed to execute test/
error /[1-9][0-9]*\s+error/
error /mismatch/
error /Verification failed/
error /Error Details:/

# WARNING for S32 CLI
error /WARNING: No Config matched .* Skipping.*/

# START PATTERNS (Section markers)
start /(?i)^-+\sStart Build\s/
start /(?i)^Building:\s.*\sfor\s.*with\s.*\sand\s.*/
start /(?i)^-+\sStart Package\s/
"""


def create_error_matcher(catalog_path: Optional[Path] = None) -> ErrorMatcher:
    """
    Factory function to create an ErrorMatcher instance.
    
    Args:
        catalog_path: Optional path to custom error catalog
        
    Returns:
        Initialized ErrorMatcher instance
        
    Example:
        >>> matcher = create_error_matcher()
        >>> result = matcher.find_match("gmake: *** [all] Error 1")
    """
    return ErrorMatcher(catalog_path=catalog_path)
