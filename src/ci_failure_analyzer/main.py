"""
CI Failure Analyzer - Grand Conductor Entry Point.

This module orchestrates the complete "Detective's Workflow" for analyzing
Jenkins CI failures using AI-powered root cause analysis.

Workflow:
    1. Preparation: Load configuration, validate environment
    2. Ingestion: Segment Jenkins logs into hierarchical structure
    3. Detection: Identify failures using pattern matching and taxonomy
    4. Investigation: Enrich failures with source code context
    5. Reasoning: Apply AI analysis to determine root causes
    6. Reporting: Generate comprehensive analysis reports

Architecture:
    - Graceful Degradation: Never crashes on individual failures
    - Filtering Support: Analyze specific segments (e.g., Linux lab only)
    - Mock Mode: Test without consuming API credits
    - Statistics Tracking: Comprehensive metrics for monitoring
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

from ci_failure_analyzer.config import Config
from ci_failure_analyzer.ingestion.log_loader import LogLoader, load_jenkins_log
from ci_failure_analyzer.models.analysis_models import (
    EnrichedFailureContext,
    RCAReport,
    RootCauseBucket,
)
from ci_failure_analyzer.models.log_models import FailureContext, LogSegment, SegmentType
from ci_failure_analyzer.parsing.log_parser import LogParser, create_log_parser
from ci_failure_analyzer.parsing.regex_catalog import ErrorMatcher
from ci_failure_analyzer.reasoning.ai_agent import AIAgent, create_ai_agent
from ci_failure_analyzer.reasoning.repo_reader import RepoReader, create_repo_reader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('ci_failure_analyzer.log')
    ]
)

logger = logging.getLogger(__name__)


class CIFailureAnalyzer:
    """
    Grand Conductor for the CI Failure Analysis workflow.
    
    This class orchestrates the complete analysis pipeline from raw Jenkins
    logs to AI-powered root cause analysis reports.
    
    Attributes:
        mock_mode: If True, uses mock AI responses (no API costs)
        log_loader: Hierarchical log segmentation engine
        log_parser: Error detection and clue hunting engine
        repo_reader: Source code context extraction engine
        ai_agent: AI-powered root cause analysis engine
        statistics: Comprehensive workflow statistics
    """
    
    def __init__(
        self,
        mock_mode: bool = False,
        target_repo_root: Optional[Path] = None,
        pipeline_config: Optional[Dict] = None
    ):
        """
        Initialize the CI Failure Analyzer.
        
        Args:
            mock_mode: If True, use mock AI responses instead of real API calls
            target_repo_root: Optional path to source repository (overrides config)
            pipeline_config: Optional pipeline model configuration for log segmentation
        """
        logger.info("=" * 80)
        logger.info("CI FAILURE ANALYZER - INITIALIZATION")
        logger.info("=" * 80)
        
        self.mock_mode = mock_mode
        
        # Step 1: Load and validate configuration
        logger.info("Step 1: Loading configuration...")
        try:
            Config.load()
            
            # Override repo root if provided
            if target_repo_root:
                Config.TARGET_REPO_ROOT = Path(target_repo_root).resolve()
            
            # Validate configuration (may fall back to mock mode)
            try:
                Config.validate()
            except ValueError as e:
                logger.warning(f"Configuration validation failed: {e}")
                if not self.mock_mode:
                    logger.warning("Falling back to MOCK MODE")
                    self.mock_mode = True
            
            logger.info(f"Configuration loaded: LLM Provider = {Config.LLM_PROVIDER}")
            logger.info(f"Target Repository: {Config.TARGET_REPO_ROOT}")
            logger.info(f"Mock Mode: {self.mock_mode}")
            
        except Exception as e:
            logger.error(f"Configuration loading failed: {e}")
            raise
        
        # Step 2: Initialize workflow engines
        logger.info("Step 2: Initializing workflow engines...")
        
        try:
            # Ingestion Engine
            self.log_loader = LogLoader(pipeline_config=pipeline_config)
            logger.info("✓ Log Loader initialized")
            
            # Detection Engine
            error_matcher = ErrorMatcher()
            self.log_parser = create_log_parser(error_matcher=error_matcher)
            logger.info("✓ Log Parser initialized")
            
            # Investigation Engine
            self.repo_reader = create_repo_reader(target_repo_root=Config.TARGET_REPO_ROOT)
            logger.info("✓ Repo Reader initialized")
            
            # Reasoning Engine
            self.ai_agent = create_ai_agent(mock_mode=self.mock_mode)
            logger.info("✓ AI Agent initialized")
            
        except Exception as e:
            logger.error(f"Engine initialization failed: {e}")
            raise
        
        # Statistics tracking
        self.statistics = {
            'total_segments': 0,
            'segments_analyzed': 0,
            'failures_detected': 0,
            'failures_enriched': 0,
            'failures_analyzed': 0,
            'failures_skipped': 0,
            'total_clues_hunted': 0,
            'total_code_snippets': 0,
            'total_test_snippets': 0,
        }
        
        logger.info("Initialization complete!")
        logger.info("=" * 80)
    
    def analyze_log_file(
        self,
        log_file_path: Path,
        segment_filter: Optional[str] = None,
        output_file: Optional[Path] = None
    ) -> List[RCAReport]:
        """
        Execute the complete Detective's Workflow on a Jenkins log file.
        
        This is the main entry point for end-to-end analysis.
        
        Workflow:
            1. Ingestion: Segment the log into hierarchical structure
            2. Detection: Find failures in each segment
            3. Investigation: Enrich failures with code context
            4. Reasoning: Apply AI analysis to determine root causes
            5. Reporting: Generate and save comprehensive reports
        
        Args:
            log_file_path: Path to Jenkins console log file
            segment_filter: Optional filter (e.g., "linux" to analyze only Linux segments)
            output_file: Optional path to save JSON report
            
        Returns:
            List of RCAReport objects for all analyzed failures
            
        Example:
            >>> analyzer = CIFailureAnalyzer(mock_mode=True)
            >>> reports = analyzer.analyze_log_file(
            ...     Path("console.log"),
            ...     segment_filter="linux",
            ...     output_file=Path("analysis_report.json")
            ... )
            >>> print(f"Found {len(reports)} failures")
        """
        logger.info("=" * 80)
        logger.info("STARTING CI FAILURE ANALYSIS")
        logger.info("=" * 80)
        logger.info(f"Log File: {log_file_path}")
        logger.info(f"Segment Filter: {segment_filter or 'None (analyze all)'}")
        logger.info(f"Output File: {output_file or 'None (console only)'}")
        logger.info("=" * 80)
        
        # Validate log file exists
        if not log_file_path.exists():
            raise FileNotFoundError(f"Log file not found: {log_file_path}")
        
        # PHASE 1: INGESTION - Segment the log
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 1: INGESTION - Sorting the Log")
        logger.info("=" * 80)
        
        segments = self._ingest_log(log_file_path)
        
        # Apply segment filter if provided
        if segment_filter:
            segments = self._filter_segments(segments, segment_filter)
            logger.info(f"Filter applied: {len(segments)} segments match '{segment_filter}'")
        
        # PHASE 2: DETECTION - Find failures
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 2: DETECTION - Finding the Problems")
        logger.info("=" * 80)
        
        failure_contexts = self._detect_failures(segments)
        
        # PHASE 3: INVESTIGATION - Enrich with code context
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 3: INVESTIGATION - Gathering the Evidence")
        logger.info("=" * 80)
        
        enriched_contexts = self._enrich_failures(failure_contexts)
        
        # PHASE 4: REASONING - AI analysis
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 4: REASONING - Consulting the Brain")
        logger.info("=" * 80)
        
        rca_reports = self._analyze_failures(enriched_contexts)
        
        # PHASE 5: REPORTING - Generate summary
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 5: REPORTING - The Final Summary")
        logger.info("=" * 80)
        
        self._print_summary_report(rca_reports)
        
        # Save to file if requested
        if output_file:
            self._save_report(rca_reports, output_file)
        
        # Print final statistics
        self._print_final_statistics()
        
        logger.info("\n" + "=" * 80)
        logger.info("ANALYSIS COMPLETE")
        logger.info("=" * 80)
        
        return rca_reports
    
    def _ingest_log(self, log_file_path: Path) -> List[LogSegment]:
        """
        Phase 1: Ingest and segment the Jenkins log file.
        
        Args:
            log_file_path: Path to log file
            
        Returns:
            List of LogSegment objects
        """
        logger.info(f"Loading log file: {log_file_path}")
        
        try:
            segments = self.log_loader.load_and_segment(log_file_path)
            self.statistics['total_segments'] = len(segments)
            
            logger.info(f"✓ Log segmented into {len(segments)} segments")
            
            # Log segment breakdown
            segment_types = {}
            for seg in segments:
                seg_type = seg.segment_type.value
                segment_types[seg_type] = segment_types.get(seg_type, 0) + 1
            
            for seg_type, count in segment_types.items():
                logger.info(f"  - {seg_type}: {count} segments")
            
            return segments
            
        except Exception as e:
            logger.error(f"Log ingestion failed: {e}", exc_info=True)
            raise
    
    def _filter_segments(
        self,
        segments: List[LogSegment],
        filter_keyword: str
    ) -> List[LogSegment]:
        """
        Filter segments based on keyword (e.g., "linux", "windows").
        
        Args:
            segments: All log segments
            filter_keyword: Keyword to filter by (case-insensitive)
            
        Returns:
            Filtered list of segments
        """
        filter_lower = filter_keyword.lower()
        
        filtered = [
            seg for seg in segments
            if filter_lower in seg.name.lower() or
               filter_lower in seg.metadata.get('branch_id', '').lower()
        ]
        
        logger.info(
            f"Segment filter '{filter_keyword}': {len(filtered)}/{len(segments)} segments match"
        )
        
        return filtered
    
    def _detect_failures(
        self,
        segments: List[LogSegment]
    ) -> List[FailureContext]:
        """
        Phase 2: Detect failures in log segments.
        
        Args:
            segments: Log segments to analyze
            
        Returns:
            List of FailureContext objects for detected failures
        """
        logger.info(f"Analyzing {len(segments)} segments for failures...")
        
        failure_contexts: List[FailureContext] = []
        
        for i, segment in enumerate(segments, 1):
            self.statistics['segments_analyzed'] += 1
            
            logger.info(
                f"[{i}/{len(segments)}] Analyzing {segment.segment_type.value}: {segment.name}"
            )
            
            try:
                failure = self.log_parser.analyze_segment(segment)
                
                if failure:
                    failure_contexts.append(failure)
                    self.statistics['failures_detected'] += 1
                    
                    logger.info(
                        f"  ✗ FAILURE DETECTED: {failure.error_id} at line {failure.line_number}"
                    )
                    logger.info(f"    Error: {failure.primary_error_line[:80]}...")
                    logger.info(f"    Clues hunted: {len(failure.hunted_clues)}")
                else:
                    logger.debug(f"  ✓ No failures detected")
                    
            except Exception as e:
                logger.error(
                    f"  ⚠ Error analyzing segment '{segment.name}': {e}",
                    exc_info=True
                )
                self.statistics['failures_skipped'] += 1
                continue
        
        # Update statistics
        parser_stats = self.log_parser.get_statistics()
        self.statistics['total_clues_hunted'] = parser_stats.get('clues_hunted', 0)
        
        logger.info(f"\n✓ Detection complete: {len(failure_contexts)} failures found")
        
        return failure_contexts
    
    def _enrich_failures(
        self,
        failure_contexts: List[FailureContext]
    ) -> List[EnrichedFailureContext]:
        """
        Phase 3: Enrich failures with source code context.
        
        Args:
            failure_contexts: Detected failures from Phase 2
            
        Returns:
            List of EnrichedFailureContext objects with code snippets
        """
        logger.info(f"Enriching {len(failure_contexts)} failures with code context...")
        
        enriched_contexts: List[EnrichedFailureContext] = []
        
        for i, failure in enumerate(failure_contexts, 1):
            logger.info(
                f"[{i}/{len(failure_contexts)}] Enriching {failure.error_id}..."
            )
            
            try:
                enriched = self.repo_reader.enrich_failure(
                    failure,
                    context_lines=Config.MAX_CONTEXT_LINES
                )
                
                enriched_contexts.append(enriched)
                self.statistics['failures_enriched'] += 1
                
                # Log enrichment results
                if enriched.has_code_context:
                    logger.info(f"  ✓ Code context: {enriched.code_snippet.file_path}")
                    self.statistics['total_code_snippets'] += 1
                else:
                    logger.warning(f"  ⚠ Code context unavailable")
                
                if enriched.has_test_context:
                    logger.info(f"  ✓ Test context: {enriched.test_snippet.file_path}")
                    self.statistics['total_test_snippets'] += 1
                
                if enriched.missing_files:
                    logger.warning(
                        f"  ⚠ Missing files: {', '.join(enriched.missing_files)}"
                    )
                
                logger.info(
                    f"  Context completeness: {enriched.context_completeness_score:.0%}"
                )
                
            except Exception as e:
                logger.error(
                    f"  ⚠ Error enriching failure {failure.error_id}: {e}",
                    exc_info=True
                )
                
                # Create minimal enriched context (graceful degradation)
                enriched = EnrichedFailureContext(
                    original_failure=failure,
                    missing_files=[],
                    resolution_notes=[f"Enrichment failed: {str(e)}"]
                )
                enriched_contexts.append(enriched)
                continue
        
        # Update statistics
        reader_stats = self.repo_reader.get_statistics()
        logger.info(f"\n✓ Enrichment complete:")
        logger.info(f"  - Files resolved: {reader_stats.get('files_resolved', 0)}")
        logger.info(f"  - Files not found: {reader_stats.get('files_not_found', 0)}")
        logger.info(f"  - Code snippets: {self.statistics['total_code_snippets']}")
        logger.info(f"  - Test snippets: {self.statistics['total_test_snippets']}")
        
        return enriched_contexts
    
    def _analyze_failures(
        self,
        enriched_contexts: List[EnrichedFailureContext]
    ) -> List[RCAReport]:
        """
        Phase 4: Apply AI-powered root cause analysis.
        
        Args:
            enriched_contexts: Enriched failures from Phase 3
            
        Returns:
            List of RCAReport objects with AI analysis
        """
        logger.info(
            f"Analyzing {len(enriched_contexts)} failures with AI "
            f"(Mock Mode: {self.mock_mode})..."
        )
        
        rca_reports: List[RCAReport] = []
        
        for i, enriched in enumerate(enriched_contexts, 1):
            failure_id = enriched.original_failure.error_id
            
            logger.info(
                f"[{i}/{len(enriched_contexts)}] Analyzing {failure_id} "
                f"(context: {enriched.context_completeness_score:.0%})..."
            )
            
            try:
                report = self.ai_agent.analyze_failure(enriched)
                
                rca_reports.append(report)
                self.statistics['failures_analyzed'] += 1
                
                logger.info(f"  ✓ Analysis complete:")
                logger.info(f"    Root Cause: {report.root_cause_bucket.value}")
                logger.info(f"    Confidence: {report.confidence_score:.0%}")
                logger.info(f"    Summary: {report.root_cause_summary[:80]}...")
                
            except Exception as e:
                logger.error(
                    f"  ⚠ Error analyzing failure {failure_id}: {e}",
                    exc_info=True
                )
                self.statistics['failures_skipped'] += 1
                continue
        
        # Update statistics
        agent_stats = self.ai_agent.get_statistics()
        logger.info(f"\n✓ AI Analysis complete:")
        logger.info(f"  - API calls: {agent_stats.get('total_api_calls', 0)}")
        logger.info(f"  - Tokens used: {agent_stats.get('total_tokens_used', 0)}")
        logger.info(f"  - Reports generated: {len(rca_reports)}")
        
        return rca_reports
    
    def _print_summary_report(self, rca_reports: List[RCAReport]) -> None:
        """
        Print a beautiful summary report to console.
        
        Args:
            rca_reports: List of RCA reports to summarize
        """
        if not rca_reports:
            logger.info("\n📊 No failures to report!")
            return
        
        logger.info("\n" + "=" * 80)
        logger.info("📊 QUICK LIST - FAILURE SUMMARY")
        logger.info("=" * 80)
        
        # Group by root cause bucket
        bucket_groups: Dict[RootCauseBucket, List[RCAReport]] = {}
        for report in rca_reports:
            bucket = report.root_cause_bucket
            if bucket not in bucket_groups:
                bucket_groups[bucket] = []
            bucket_groups[bucket].append(report)
        
        # Print summary table
        logger.info(f"\n{'Failure ID':<15} {'Category':<25} {'Confidence':<12} {'Summary'}")
        logger.info("-" * 80)
        
        for report in rca_reports:
            confidence_bar = "█" * int(report.confidence_score * 10)
            logger.info(
                f"{report.failure_id:<15} "
                f"{report.root_cause_bucket.value:<25} "
                f"{confidence_bar:<10} {report.confidence_score:.0%}  "
                f"{report.root_cause_summary[:40]}..."
            )
        
        # Print category breakdown
        logger.info("\n" + "-" * 80)
        logger.info("CATEGORY BREAKDOWN:")
        for bucket, reports in bucket_groups.items():
            avg_confidence = sum(r.confidence_score for r in reports) / len(reports)
            logger.info(
                f"  {bucket.value}: {len(reports)} failures "
                f"(avg confidence: {avg_confidence:.0%})"
            )
        
        # Print detailed reports
        logger.info("\n" + "=" * 80)
        logger.info("🔍 DEEP DIVE - DETAILED ANALYSIS")
        logger.info("=" * 80)
        
        for i, report in enumerate(rca_reports, 1):
            logger.info(f"\n{'─' * 80}")
            logger.info(f"FAILURE #{i}: {report.failure_id}")
            logger.info(f"{'─' * 80}")
            logger.info(f"Category: {report.root_cause_bucket.value}")
            logger.info(f"Confidence: {report.confidence_score:.0%}")
            logger.info(f"\n📝 SUMMARY:")
            logger.info(f"  {report.root_cause_summary}")
            logger.info(f"\n🔬 TECHNICAL ANALYSIS:")
            for line in report.technical_analysis.split('\n'):
                logger.info(f"  {line}")
            logger.info(f"\n💡 SUGGESTED FIX:")
            for line in report.suggested_fix.split('\n'):
                logger.info(f"  {line}")
            logger.info(f"\n📋 EVIDENCE:")
            for evidence_type, items in report.evidence.items():
                if items:
                    logger.info(f"  {evidence_type}:")
                    for item in items[:3]:  # Limit to 3 items per type
                        logger.info(f"    - {item[:70]}...")
    
    def _save_report(
        self,
        rca_reports: List[RCAReport],
        output_file: Path
    ) -> None:
        """
        Save RCA reports to JSON file.
        
        Args:
            rca_reports: List of RCA reports
            output_file: Path to output JSON file
        """
        logger.info(f"\nSaving report to: {output_file}")
        
        try:
            # Convert reports to dictionaries
            report_dicts = [report.model_dump() for report in rca_reports]
            
            # Add metadata
            output_data = {
                'metadata': {
                    'total_failures': len(rca_reports),
                    'mock_mode': self.mock_mode,
                    'llm_provider': Config.LLM_PROVIDER,
                    'llm_model': Config.LLM_MODEL,
                    'statistics': self.statistics
                },
                'reports': report_dicts
            }
            
            # Write to file
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, default=str)
            
            logger.info(f"✓ Report saved successfully: {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to save report: {e}", exc_info=True)
    
    def _print_final_statistics(self) -> None:
        """Print final workflow statistics."""
        logger.info("\n" + "=" * 80)
        logger.info("📈 FINAL STATISTICS")
        logger.info("=" * 80)
        
        logger.info(f"Total Segments: {self.statistics['total_segments']}")
        logger.info(f"Segments Analyzed: {self.statistics['segments_analyzed']}")
        logger.info(f"Failures Detected: {self.statistics['failures_detected']}")
        logger.info(f"Failures Enriched: {self.statistics['failures_enriched']}")
        logger.info(f"Failures Analyzed: {self.statistics['failures_analyzed']}")
        logger.info(f"Failures Skipped: {self.statistics['failures_skipped']}")
        logger.info(f"Total Clues Hunted: {self.statistics['total_clues_hunted']}")
        logger.info(f"Code Snippets Extracted: {self.statistics['total_code_snippets']}")
        logger.info(f"Test Snippets Extracted: {self.statistics['total_test_snippets']}")
        
        # Success rate
        if self.statistics['failures_detected'] > 0:
            success_rate = (
                self.statistics['failures_analyzed'] /
                self.statistics['failures_detected'] * 100
            )
            logger.info(f"\nSuccess Rate: {success_rate:.1f}%")


def main():
    """
    Command-line interface for CI Failure Analyzer.
    
    Example usage:
        # Analyze with real AI (requires API key)
        python -m ci_failure_analyzer.main console.log
        
        # Analyze in mock mode (no API costs)
        python -m ci_failure_analyzer.main console.log --mock
        
        # Filter to Linux segments only
        python -m ci_failure_analyzer.main console.log --filter linux
        
        # Save report to file
        python -m ci_failure_analyzer.main console.log --output report.json
        
        # Specify custom repo root
        python -m ci_failure_analyzer.main console.log --repo /path/to/repo
    """
    parser = argparse.ArgumentParser(
        description='CI Failure Analyzer - AI-powered root cause analysis for Jenkins logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze with real AI (requires API key)
  %(prog)s console.log
  
  # Analyze in mock mode (no API costs)
  %(prog)s console.log --mock
  
  # Filter to Linux segments only
  %(prog)s console.log --filter linux
  
  # Save report to file
  %(prog)s console.log --output report.json
  
  # Specify custom repo root
  %(prog)s console.log --repo /path/to/repo
        """
    )
    
    parser.add_argument(
        'log_file',
        type=Path,
        help='Path to Jenkins console log file'
    )
    
    parser.add_argument(
        '--mock',
        action='store_true',
        help='Use mock mode (no API calls, deterministic responses)'
    )
    
    parser.add_argument(
        '--filter',
        type=str,
        help='Filter segments by keyword (e.g., "linux", "windows")'
    )
    
    parser.add_argument(
        '--output',
        type=Path,
        help='Save JSON report to file'
    )
    
    parser.add_argument(
        '--repo',
        type=Path,
        help='Path to target source repository (overrides TARGET_REPO_ROOT env var)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    try:
        # Initialize analyzer
        analyzer = CIFailureAnalyzer(
            mock_mode=args.mock,
            target_repo_root=args.repo
        )
        
        # Run analysis
        reports = analyzer.analyze_log_file(
            log_file_path=args.log_file,
            segment_filter=args.filter,
            output_file=args.output
        )
        
        # Exit with appropriate code
        if reports:
            logger.info(f"\n✓ Analysis complete: {len(reports)} failures analyzed")
            sys.exit(0)
        else:
            logger.info("\n✓ Analysis complete: No failures found")
            sys.exit(0)
            
    except KeyboardInterrupt:
        logger.warning("\n⚠ Analysis interrupted by user")
        sys.exit(130)
        
    except Exception as e:
        logger.error(f"\n✗ Analysis failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
