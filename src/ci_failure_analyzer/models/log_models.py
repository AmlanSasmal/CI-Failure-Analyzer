"""
Pydantic models for representing parsed CI log data.

These models provide typed, validated data structures for log entries
and failure contexts extracted from hardware CI logs.

Models:
- LogEntry: Single processed log line with metadata
- FailureContext: Complete test failure with context and T32 debugging info
- ParseStatistics: Summary statistics from log parsing
- AnalysisResult: AI-generated analysis of a failure
"""

from typing import List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    """Categories of test failures for AI classification."""
    CODE_BUG = "CODE_BUG"
    INFRA_FAILURE = "INFRA_FAILURE"
    FLAKY_TEST = "FLAKY_TEST"
    TOOL_ISSUE = "TOOL_ISSUE"
    UNKNOWN = "UNKNOWN"


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
    
    def to_ai_prompt(self) -> str:
        """
        Convert failure context to a structured prompt for AI analysis.
        
        Creates a narrative that includes:
        - Analysis target (test ID)
        - Hardware debugger (T32) state
        - Test log narrative (cleaned messages)
        
        The output is constrained to ~3000 tokens (12,000 chars) by keeping
        first 50 lines and last 100 lines, with truncation marker in between.
        
        Returns:
            Structured text prompt suitable for LLM analysis.
        """
        lines = []
        
        # Header
        lines.append(f"ANALYSIS TARGET: {self.test_id}")
        lines.append(f"Test Status: {self.status}")
        lines.append(f"Duration: {self.duration}s | Bytes Received: {self.received_bytes}")
        lines.append("")
        
        # T32 Debugger State Section
        lines.append("=" * 70)
        lines.append("HARDWARE DEBUGGER (T32) STATE:")
        lines.append("=" * 70)
        
        if self.t32_debug_lines:
            for entry in self.t32_debug_lines:
                lines.append(f"[L{entry.line_number}] {entry.message}")
        else:
            lines.append("(No T32 debugger output recorded)")
        
        lines.append("")
        
        # Test Log Narrative Section
        lines.append("=" * 70)
        lines.append("TEST LOG NARRATIVE:")
        lines.append("=" * 70)
        
        # Build the log narrative with truncation if needed
        MAX_CHARS = 12000  # ~3000 tokens
        KEEP_FIRST = 50
        KEEP_LAST = 100
        
        if len(self.context_lines) <= (KEEP_FIRST + KEEP_LAST):
            # No truncation needed
            for entry in self.context_lines:
                lines.append(f"[L{entry.line_number}] {entry.message}")
        else:
            # Need truncation - keep first and last sections
            first_section = self.context_lines[:KEEP_FIRST]
            last_section = self.context_lines[-KEEP_LAST:]
            
            # Add first section
            for entry in first_section:
                lines.append(f"[L{entry.line_number}] {entry.message}")
            
            # Add truncation marker
            truncated_lines = len(self.context_lines) - KEEP_FIRST - KEEP_LAST
            lines.append("")
            lines.append(f"[... {truncated_lines} lines truncated ...]")
            lines.append("")
            
            # Add last section
            for entry in last_section:
                lines.append(f"[L{entry.line_number}] {entry.message}")
        
        lines.append("")
        lines.append("=" * 70)
        
        # Join and check token count
        prompt = "\n".join(lines)
        
        # If still too large, apply stricter truncation
        if len(prompt) > MAX_CHARS:
            # Use more aggressive truncation: 30 first, 80 last
            lines = []
            lines.append(f"ANALYSIS TARGET: {self.test_id}")
            lines.append(f"Test Status: {self.status}")
            lines.append(f"Duration: {self.duration}s | Bytes Received: {self.received_bytes}")
            lines.append("")
            
            lines.append("=" * 70)
            lines.append("HARDWARE DEBUGGER (T32) STATE:")
            lines.append("=" * 70)
            
            if self.t32_debug_lines:
                for entry in self.t32_debug_lines[:20]:  # Keep only first 20 T32 lines
                    lines.append(f"[L{entry.line_number}] {entry.message}")
                if len(self.t32_debug_lines) > 20:
                    lines.append(f"[... {len(self.t32_debug_lines) - 20} T32 lines omitted ...]")
            
            lines.append("")
            lines.append("=" * 70)
            lines.append("TEST LOG NARRATIVE (CONDENSED):")
            lines.append("=" * 70)
            
            first_section = self.context_lines[:30]
            last_section = self.context_lines[-80:]
            
            for entry in first_section:
                lines.append(f"[L{entry.line_number}] {entry.message}")
            
            if len(self.context_lines) > 110:
                truncated_lines = len(self.context_lines) - 30 - 80
                lines.append("")
                lines.append(f"[... {truncated_lines} lines truncated ...]")
                lines.append("")
            
            for entry in last_section:
                lines.append(f"[L{entry.line_number}] {entry.message}")
            
            lines.append("")
            lines.append("=" * 70)
            
            prompt = "\n".join(lines)
        
        return prompt


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


class AnalysisResult(BaseModel):
    """
    AI-generated analysis result for a test failure.
    
    Produced by the AI agent after analyzing a FailureContext. Categorizes
    the failure, provides root cause analysis, and suggests remediation steps.
    
    Attributes:
        category: Failure category (CODE_BUG, INFRA_FAILURE, FLAKY_TEST, TOOL_ISSUE, UNKNOWN)
        summary: Brief summary of the analysis finding
        root_cause: Detailed explanation of the identified root cause
        suggested_fix: Recommended remediation steps or investigation direction
        confidence: Confidence score (0.0 to 1.0) in this analysis
        analysis_timestamp: When the analysis was generated
    """
    category: FailureCategory
    summary: str = Field(..., description="Brief summary of the analysis")
    root_cause: str = Field(..., description="Detailed root cause explanation")
    suggested_fix: str = Field(..., description="Recommended fix or investigation steps")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    analysis_timestamp: Optional[str] = None
    
    def to_json_report(self) -> dict:
        """Convert analysis result to a JSON-serializable dictionary."""
        return {
            "category": self.category.value,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "suggested_fix": self.suggested_fix,
            "confidence": round(self.confidence, 3),
            "timestamp": self.analysis_timestamp
        }
