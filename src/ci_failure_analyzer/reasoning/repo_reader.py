"""
Repository Reader with Surgical Code Extraction.

This module implements intelligent path resolution and token-efficient code
snippet extraction to enrich failure contexts with actual source code evidence.

Architecture:
    - Fuzzy Path Matching: Resolves Jenkins absolute paths to local relative paths
    - Surgical Extraction: Extracts ±20 line windows around errors
    - Test Discovery: Locates test files based on test names
    - Graceful Degradation: Never crashes on missing files

Configuration:
    TARGET_REPO_ROOT: Environment variable pointing to the source repository
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from ci_failure_analyzer.models.analysis_models import (
    CodeSnippet,
    EnrichedFailureContext,
)
from ci_failure_analyzer.models.log_models import FailureContext

logger = logging.getLogger(__name__)


class RepoReader:
    """
    Intelligent repository reader with fuzzy path matching and surgical extraction.
    
    This class bridges the gap between Jenkins build server paths and local
    file system paths, enabling evidence-based code analysis.
    
    Key Features:
        - Fuzzy path resolution (strips prefixes until match found)
        - Token-efficient snippet extraction (±20 lines)
        - Test file discovery across common test directory structures
        - Comprehensive error handling (never crashes on missing files)
    """
    
    # Default context window for code snippets
    DEFAULT_CONTEXT_LINES = 20
    
    # Common test directory patterns
    TEST_DIR_PATTERNS = [
        "tests",
        "test",
        "Tests",
        "Test",
        "unit_tests",
        "integration_tests",
    ]
    
    # Common test file naming patterns
    TEST_FILE_PATTERNS = [
        "test_*.c",
        "test_*.cpp",
        "test_*.py",
        "*_test.c",
        "*_test.cpp",
        "*_test.py",
        "Test*.c",
        "Test*.cpp",
        "Test*.py",
    ]
    
    def __init__(self, target_repo_root: Optional[Path] = None):
        """
        Initialize the RepoReader.
        
        Args:
            target_repo_root: Path to the target source repository.
                            If None, reads from TARGET_REPO_ROOT environment variable.
        
        Raises:
            ValueError: If TARGET_REPO_ROOT is not set and target_repo_root is None
        """
        if target_repo_root:
            self.repo_root = Path(target_repo_root).resolve()
        else:
            repo_root_env = os.getenv("TARGET_REPO_ROOT")
            if not repo_root_env:
                raise ValueError(
                    "TARGET_REPO_ROOT environment variable not set. "
                    "Please set it to point to your source repository."
                )
            self.repo_root = Path(repo_root_env).resolve()
        
        if not self.repo_root.exists():
            raise ValueError(
                f"Target repository root does not exist: {self.repo_root}"
            )
        
        logger.info(f"RepoReader initialized with repo_root: {self.repo_root}")
        
        # Statistics
        self.stats = {
            'files_resolved': 0,
            'files_not_found': 0,
            'snippets_extracted': 0,
            'tests_found': 0,
            'tests_not_found': 0,
        }
    
    def enrich_failure(
        self,
        failure_context: FailureContext,
        context_lines: int = DEFAULT_CONTEXT_LINES
    ) -> EnrichedFailureContext:
        """
        Enrich a failure context with source code snippets.
        
        This is the main entry point for Phase 2 enrichment. It:
        1. Extracts file paths from the error line
        2. Resolves them to local paths using fuzzy matching
        3. Extracts surgical code snippets around the error
        4. Locates and extracts test code if test_name is available
        
        Args:
            failure_context: Phase 1 failure detection result
            context_lines: Number of lines to extract before/after error
            
        Returns:
            EnrichedFailureContext with code snippets and resolution notes
        """
        logger.info(
            f"Enriching failure: {failure_context.error_id} at line "
            f"{failure_context.line_number}"
        )
        
        enriched = EnrichedFailureContext(
            original_failure=failure_context,
            missing_files=[],
            resolution_notes=[]
        )
        
        # Extract file path and line number from error line or clues
        file_path, error_line = self._extract_file_reference(failure_context)
        
        if file_path:
            # Attempt to resolve and extract code snippet
            code_snippet = self._resolve_and_extract(
                file_path,
                error_line,
                context_lines
            )
            
            if code_snippet:
                enriched.code_snippet = code_snippet
                enriched.resolution_notes.append(
                    f"Resolved {file_path} to {code_snippet.file_path}"
                )
            else:
                enriched.missing_files.append(file_path)
                enriched.resolution_notes.append(
                    f"Could not locate file: {file_path}"
                )
        else:
            enriched.resolution_notes.append(
                "No file path found in error line or clues"
            )
        
        # Locate and extract test code if test name is available
        if failure_context.test_name:
            test_snippet = self._locate_and_extract_test(
                failure_context.test_name
            )
            
            if test_snippet:
                enriched.test_snippet = test_snippet
                enriched.resolution_notes.append(
                    f"Found test: {test_snippet.file_path}"
                )
            else:
                enriched.resolution_notes.append(
                    f"Could not locate test: {failure_context.test_name}"
                )
        
        logger.info(
            f"Enrichment complete: code_context={enriched.has_code_context}, "
            f"test_context={enriched.has_test_context}"
        )
        
        return enriched
    
    def _extract_file_reference(
        self,
        failure_context: FailureContext
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Extract file path and line number from failure context.
        
        Searches in order:
        1. Primary error line for pattern: filename.ext:line_number
        2. Hunted clues for similar patterns
        
        Args:
            failure_context: Failure context from Phase 1
            
        Returns:
            Tuple of (file_path, line_number) or (None, None)
        """
        # Pattern to match: filename.ext:line_number or /path/to/file.ext:line
        file_pattern = re.compile(
            r'([a-zA-Z0-9_/.\-]+\.[a-zA-Z]{1,4}):(\d+)'
        )
        
        # Check primary error line first
        match = file_pattern.search(failure_context.primary_error_line)
        if match:
            file_path = match.group(1)
            line_number = int(match.group(2))
            logger.debug(
                f"Extracted from error line: {file_path}:{line_number}"
            )
            return file_path, line_number
        
        # Check hunted clues
        for clue_key, clue_value in failure_context.hunted_clues.items():
            match = file_pattern.search(clue_value)
            if match:
                file_path = match.group(1)
                line_number = int(match.group(2))
                logger.debug(
                    f"Extracted from clue '{clue_key}': {file_path}:{line_number}"
                )
                return file_path, line_number
        
        logger.warning("No file reference found in error line or clues")
        return None, None
    
    def _resolve_path(self, log_path: str) -> Optional[Path]:
        """
        Resolve a log file path to a local repository path using fuzzy matching.
        
        Strategy:
        1. Try the path as-is (relative to repo_root)
        2. Strip leading directories one by one until a match is found
        3. Search common source directories (src/, lib/, drivers/, etc.)
        
        Args:
            log_path: File path from Jenkins log (may be absolute or relative)
            
        Returns:
            Resolved Path object or None if not found
            
        Example:
            >>> # Log says: /home/jenkins/workspace/RFE_CI/src/main.c
            >>> # Resolves to: /Users/dev/rfe_sw/src/main.c
        """
        # Normalize the path
        log_path_clean = log_path.strip()
        
        # Strategy 1: Try as-is (relative to repo_root)
        candidate = self.repo_root / log_path_clean
        if candidate.exists() and candidate.is_file():
            logger.debug(f"Resolved (as-is): {log_path_clean} -> {candidate}")
            self.stats['files_resolved'] += 1
            return candidate
        
        # Strategy 2: Strip leading directories (fuzzy matching)
        path_parts = Path(log_path_clean).parts
        
        for i in range(len(path_parts)):
            # Try progressively shorter paths
            truncated_path = Path(*path_parts[i:])
            candidate = self.repo_root / truncated_path
            
            if candidate.exists() and candidate.is_file():
                logger.debug(
                    f"Resolved (truncated): {log_path_clean} -> {candidate}"
                )
                self.stats['files_resolved'] += 1
                return candidate
        
        # Strategy 3: Search common source directories
        common_dirs = ["src", "lib", "drivers", "application", "modules"]
        filename = Path(log_path_clean).name
        
        for common_dir in common_dirs:
            search_root = self.repo_root / common_dir
            if search_root.exists():
                # Search recursively for the filename
                matches = list(search_root.rglob(filename))
                if matches:
                    logger.debug(
                        f"Resolved (search): {log_path_clean} -> {matches[0]}"
                    )
                    self.stats['files_resolved'] += 1
                    return matches[0]
        
        logger.warning(f"Could not resolve path: {log_path_clean}")
        self.stats['files_not_found'] += 1
        return None
    
    def _extract_code_snippet(
        self,
        file_path: Path,
        error_line: int,
        context_lines: int = DEFAULT_CONTEXT_LINES
    ) -> Optional[CodeSnippet]:
        """
        Extract a surgical code snippet around an error line.
        
        This implements token-efficient extraction:
        - Extracts ±context_lines around the error
        - Handles edge cases (error near start/end of file)
        - Formats with line numbers for AI context
        
        Args:
            file_path: Resolved local file path
            error_line: Line number where error occurred (1-indexed)
            context_lines: Number of lines to extract before/after
            
        Returns:
            CodeSnippet object or None if extraction fails
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            # Calculate extraction window (handle edge cases)
            start_line = max(1, error_line - context_lines)
            end_line = min(total_lines, error_line + context_lines)
            
            # Extract lines (convert to 0-indexed for array access)
            snippet_lines = lines[start_line - 1:end_line]
            
            # Format with line numbers
            formatted_lines = []
            for i, line in enumerate(snippet_lines, start=start_line):
                marker = " ❌ " if i == error_line else "    "
                formatted_lines.append(f"{i:4d}{marker}{line.rstrip()}")
            
            content = "\n".join(formatted_lines)
            
            # Get relative path for reporting
            relative_path = file_path.relative_to(self.repo_root)
            
            snippet = CodeSnippet(
                file_path=str(relative_path),
                start_line=start_line,
                end_line=end_line,
                content=content,
                error_line=error_line
            )
            
            self.stats['snippets_extracted'] += 1
            logger.debug(
                f"Extracted snippet: {relative_path} lines {start_line}-{end_line}"
            )
            
            return snippet
            
        except Exception as e:
            logger.error(f"Failed to extract snippet from {file_path}: {e}")
            return None
    
    def _resolve_and_extract(
        self,
        log_path: str,
        error_line: Optional[int],
        context_lines: int
    ) -> Optional[CodeSnippet]:
        """
        Resolve a path and extract code snippet in one operation.
        
        Args:
            log_path: File path from log
            error_line: Line number of error (1-indexed)
            context_lines: Context window size
            
        Returns:
            CodeSnippet or None
        """
        resolved_path = self._resolve_path(log_path)
        
        if not resolved_path:
            return None
        
        # If no specific error line, extract from start of file
        if error_line is None:
            error_line = min(context_lines, 1)
        
        return self._extract_code_snippet(resolved_path, error_line, context_lines)
    
    def _locate_test_file(self, test_name: str) -> Optional[Path]:
        """
        Locate a test file based on test name.
        
        Search strategy:
        1. Look in common test directories
        2. Search for files matching test name patterns
        3. Grep file contents for test function/class names
        
        Args:
            test_name: Test identifier from FailureContext
            
        Returns:
            Path to test file or None
        """
        logger.debug(f"Searching for test: {test_name}")
        
        # Normalize test name for searching
        test_name_lower = test_name.lower()
        
        # Strategy 1: Search test directories for filename matches
        for test_dir_pattern in self.TEST_DIR_PATTERNS:
            test_dirs = list(self.repo_root.glob(f"**/{test_dir_pattern}"))
            
            for test_dir in test_dirs:
                if not test_dir.is_dir():
                    continue
                
                # Try each test file pattern
                for pattern in self.TEST_FILE_PATTERNS:
                    for test_file in test_dir.rglob(pattern):
                        # Check if filename contains test name
                        if test_name_lower in test_file.name.lower():
                            logger.debug(f"Found test file (name match): {test_file}")
                            self.stats['tests_found'] += 1
                            return test_file
        
        # Strategy 2: Grep for test name in test files
        for test_dir_pattern in self.TEST_DIR_PATTERNS:
            test_dirs = list(self.repo_root.glob(f"**/{test_dir_pattern}"))
            
            for test_dir in test_dirs:
                if not test_dir.is_dir():
                    continue
                
                for pattern in self.TEST_FILE_PATTERNS:
                    for test_file in test_dir.rglob(pattern):
                        try:
                            with open(test_file, 'r', encoding='utf-8', errors='replace') as f:
                                content = f.read()
                                if test_name in content:
                                    logger.debug(
                                        f"Found test file (content match): {test_file}"
                                    )
                                    self.stats['tests_found'] += 1
                                    return test_file
                        except Exception as e:
                            logger.warning(f"Error reading {test_file}: {e}")
                            continue
        
        logger.warning(f"Could not locate test file for: {test_name}")
        self.stats['tests_not_found'] += 1
        return None
    
    def _extract_test_code(
        self,
        test_file: Path,
        test_name: str
    ) -> Optional[CodeSnippet]:
        """
        Extract the test function/method code from a test file.
        
        Args:
            test_file: Path to the test file
            test_name: Name of the test to extract
            
        Returns:
            CodeSnippet containing the test code or None
        """
        try:
            with open(test_file, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            # Find the line containing the test definition
            test_start_line = None
            for i, line in enumerate(lines, start=1):
                if test_name in line:
                    test_start_line = i
                    break
            
            if test_start_line is None:
                logger.warning(
                    f"Test name '{test_name}' not found in {test_file}"
                )
                return None
            
            # Extract context around the test (±20 lines by default)
            start_line = max(1, test_start_line - self.DEFAULT_CONTEXT_LINES)
            end_line = min(
                len(lines),
                test_start_line + self.DEFAULT_CONTEXT_LINES
            )
            
            snippet_lines = lines[start_line - 1:end_line]
            
            # Format with line numbers
            formatted_lines = []
            for i, line in enumerate(snippet_lines, start=start_line):
                marker = " 🧪 " if i == test_start_line else "    "
                formatted_lines.append(f"{i:4d}{marker}{line.rstrip()}")
            
            content = "\n".join(formatted_lines)
            
            relative_path = test_file.relative_to(self.repo_root)
            
            snippet = CodeSnippet(
                file_path=str(relative_path),
                start_line=start_line,
                end_line=end_line,
                content=content,
                error_line=test_start_line
            )
            
            logger.debug(
                f"Extracted test code: {relative_path} lines {start_line}-{end_line}"
            )
            
            return snippet
            
        except Exception as e:
            logger.error(f"Failed to extract test code from {test_file}: {e}")
            return None
    
    def _locate_and_extract_test(self, test_name: str) -> Optional[CodeSnippet]:
        """
        Locate and extract test code in one operation.
        
        Args:
            test_name: Test identifier
            
        Returns:
            CodeSnippet or None
        """
        test_file = self._locate_test_file(test_name)
        
        if not test_file:
            return None
        
        return self._extract_test_code(test_file, test_name)
    
    def get_statistics(self) -> dict:
        """
        Get reader statistics for monitoring.
        
        Returns:
            Dictionary with statistics
        """
        return self.stats.copy()


def create_repo_reader(target_repo_root: Optional[Path] = None) -> RepoReader:
    """
    Factory function to create a RepoReader instance.
    
    Args:
        target_repo_root: Optional path to target repository
        
    Returns:
        Initialized RepoReader instance
        
    Example:
        >>> reader = create_repo_reader(Path("/path/to/rfe_sw"))
        >>> enriched = reader.enrich_failure(failure_context)
    """
    return RepoReader(target_repo_root=target_repo_root)
