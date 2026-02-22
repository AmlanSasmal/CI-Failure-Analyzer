"""
Pydantic models for representing parsed CI log data.

These models provide typed, validated data structures for log entries
and failure contexts extracted from hardware CI logs.
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class LogEntry(BaseModel):
    """
    Represents a single log line after processing.
    
    Attributes:
        line_number: Original line number in the source log file
        timestamp: Extracted ISO8601 timestamp (if present)
        message: Log message content (timestamp prefix removed)
        raw_message: Original unprocessed message
        is_repeated: Whether this entry represents multiple consecutive identical lines
        repeat_count: Number of times this message was repeated consecutively
        is_t32_script: Whether this line contains T32 debugger output
        is_noise: Whether this line was marked as noise (SHELL_NOISE or JIRA_DEBUG)
    """
    line_number: int
    timestamp: Optional[str] = None
    message: str
    raw_message: str
    is_repeated: bool = False
    repeat_count: int = 1
    is_t32_script: bool = False
    is_noise: bool = False
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class FailureContext(BaseModel):
    """
    Represents a complete test failure with full context.
    
    Attributes:
        test_id: The test identifier (e.g., 'rfe_tc_001_validChirpTimingConfiguration')
        status: Test result (PASSED or FAILED)
        duration: Test execution duration in seconds
        received_bytes: Number of bytes received during test
        start_line: Line number where test execution started
        verdict_line: Line number where verdict was found
        context_lines: List of all log entries for this test
        t32_debug_lines: Lines containing T32 debugger output (prioritized)
        cleaned_message_count: Number of unique log messages (after consolidation)
        total_raw_lines: Total number of raw log lines for this test
        contains_failures: Whether this context contains failed assertions/errors
    """
    test_id: str
    status: str  # PASSED or FAILED
    duration: int  # in seconds
    received_bytes: int
    start_line: int
    verdict_line: int
    context_lines: List[LogEntry] = Field(default_factory=list)
    t32_debug_lines: List[LogEntry] = Field(default_factory=list)
    cleaned_message_count: int = 0
    total_raw_lines: int = 0
    contains_failures: bool = False
    
    def add_line(self, entry: LogEntry) -> None:
        """Add a log entry to this failure context."""
        self.context_lines.append(entry)
        self.total_raw_lines += 1
        if entry.is_t32_script:
            self.t32_debug_lines.append(entry)
    
    def get_summary(self) -> str:
        """Get a human-readable summary of this failure."""
        return (
            f"Test: {self.test_id} | "
            f"Status: {self.status} | "
            f"Duration: {self.duration}s | "
            f"Lines: {self.total_raw_lines} | "
            f"T32 Debug: {len(self.t32_debug_lines)}"
        )


class ParseStatistics(BaseModel):
    """
    Statistics about the parsing process.
    
    Attributes:
        total_lines_processed: Total number of input lines
        unique_failures: Number of unique test failures found
        passed_tests: Number of tests with PASSED verdict
        failed_tests: Number of tests with FAILED verdict
        lines_filtered_as_noise: Number of lines removed (SHELL_NOISE, JIRA_DEBUG)
        lines_consolidated: Number of duplicate messages consolidated
        avg_context_size: Average number of lines per failure
        incomplete_tests: Number of tests without verdict (log ended abruptly)
        parsing_duration_ms: Time taken to parse the log file (milliseconds)
    """
    total_lines_processed: int = 0
    unique_failures: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    lines_filtered_as_noise: int = 0
    lines_consolidated: int = 0
    avg_context_size: float = 0.0
    incomplete_tests: int = 0
    parsing_duration_ms: float = 0.0
    
    def calculate_averages(self, failures: List[FailureContext]) -> None:
        """Calculate average metrics based on failures found."""
        if not failures:
            self.avg_context_size = 0.0
            return
        
        total_context_lines = sum(len(f.context_lines) for f in failures)
        self.avg_context_size = total_context_lines / len(failures) if failures else 0.0
        
        self.unique_failures = len(failures)
        self.failed_tests = len([f for f in failures if f.status == "FAILED"])
        self.passed_tests = len([f for f in failures if f.status == "PASSED"])
