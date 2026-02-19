"""
Log parsing models for Jenkins pipeline log ingestion and analysis.

This module defines the core data structures for representing hierarchical
log segments and failure contexts using Pydantic v2.
"""

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class SegmentType(str, Enum):
    """Enumeration of log segment types in the pipeline hierarchy."""

    PHASE = "PHASE"
    STAGE = "STAGE"
    SUB_STAGE = "SUB_STAGE"
    BRANCH = "BRANCH"


class LogSegment(BaseModel):
    """
    Represents a hierarchical piece of a Jenkins pipeline log.

    Attributes:
        segment_type: The type of segment (PHASE, STAGE, SUB_STAGE, or BRANCH)
        name: Identifier for the segment (e.g., 'vcs_lfs_pull', 'linux_test_suite')
        agent: Optional agent identifier where this segment executed
        content: The actual log lines contained in this segment
        metadata: Additional context such as parallel branch IDs, timestamps,
                  parent references, etc.
    """

    segment_type: SegmentType = Field(
        ...,
        description="Type of log segment in the pipeline hierarchy"
    )
    name: str = Field(
        ...,
        description="Name or identifier of the segment",
        min_length=1
    )
    agent: Optional[str] = Field(
        default=None,
        description="Agent identifier where this segment executed"
    )
    content: List[str] = Field(
        default_factory=list,
        description="Raw log lines contained in this segment"
    )
    metadata: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional metadata (timestamps, branch IDs, parent refs, etc.)"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "segment_type": "PHASE",
                    "name": "Phase_1_Setup",
                    "agent": "linux-build-01",
                    "content": ["[Pipeline] Start of Pipeline", "forcing lfs pull"],
                    "metadata": {"start_time": "2024-01-15T10:30:00", "phase_id": "1"}
                }
            ]
        }
    }


class FailureContext(BaseModel):
    """
    Represents a detected failure with contextual information.

    Attributes:
        error_id: Unique identifier from the Error Catalog (e.g., 'LINK_001')
        primary_error_line: The main log line that triggered the error detection
        line_number: Line number in the original log file
        hunted_clues: Dictionary of contextual clues found near the error
                      (keyword -> log line mapping)
        test_name: Optional name of the test that failed
    """

    error_id: str = Field(
        ...,
        description="Error identifier from the Error Catalog",
        min_length=1
    )
    primary_error_line: str = Field(
        ...,
        description="The primary log line containing the error",
        min_length=1
    )
    line_number: int = Field(
        ...,
        description="Line number in the original log file",
        ge=1
    )
    hunted_clues: Dict[str, str] = Field(
        default_factory=dict,
        description="Contextual clues: keyword to log line mapping"
    )
    test_name: Optional[str] = Field(
        default=None,
        description="Name of the failing test, if applicable"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error_id": "LINK_001",
                    "primary_error_line": "error: undefined reference to `foo_function`",
                    "line_number": 1523,
                    "hunted_clues": {
                        "file": "src/main.c:45: undefined reference",
                        "symbol": "foo_function"
                    },
                    "test_name": "test_link_validation"
                }
            ]
        }
    }
