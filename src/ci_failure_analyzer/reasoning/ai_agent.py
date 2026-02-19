"""
AI Reasoning Agent for Root Cause Analysis.

This module implements the LLM-powered reasoning engine that analyzes
enriched failure contexts and generates structured RCA reports.

Architecture:
    - Multi-Mode Operation: Mock mode for testing, Production mode for real analysis
    - Provider-Agnostic: Supports OpenAI, Anthropic, Azure, Cody, and local LLMs
    - Hallucination Guardrails: Validates AI output, enforces evidence-based reasoning
    - Confidence Scoring: Dynamic scoring based on available context
    - Self-Correction: Single retry on invalid JSON responses

Prompt Engineering Philosophy:
    - Surgical System Prompt: Instructs AI as embedded systems forensic expert
    - Evidence Weighting: Correlates log clues with code snippets
    - Strict JSON Output: No conversational text, only structured reports
    - Truthfulness Rule: AI must acknowledge missing context
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

import requests
from pydantic import ValidationError

from ci_failure_analyzer.config import Config
from ci_failure_analyzer.models.analysis_models import (
    CodeSnippet,
    EnrichedFailureContext,
    RCAReport,
    RootCauseBucket,
)

logger = logging.getLogger(__name__)


class AIAgent:
    """
    AI-powered root cause analysis agent with multi-mode operation.
    
    This class implements the "Detective's Mindset" for failure analysis:
    1. Evidence Weighting: Correlates log clues with code snippets
    2. Categorization: Classifies failures into taxonomy buckets
    3. Confidence Scoring: Dynamic scoring based on context completeness
    4. Hallucination Prevention: Validates output, enforces evidence-based reasoning
    
    Modes:
        - Mock Mode: Returns deterministic reports for testing (no API costs)
        - Production Mode: Uses LLM for intelligent analysis
    """
    
    # Mock mode flag (set via environment or for testing)
    MOCK_MODE: bool = False
    
    # Token usage tracking
    total_tokens_used: int = 0
    total_api_calls: int = 0
    
    def __init__(self, mock_mode: bool = False):
        """
        Initialize the AI agent with configured LLM provider.
        
        Args:
            mock_mode: If True, use mock responses instead of real LLM calls
        """
        self.mock_mode = mock_mode or self.MOCK_MODE
        
        if self.mock_mode:
            logger.info("AIAgent initialized in MOCK MODE (no API calls)")
            return
        
        # Validate configuration before initializing
        try:
            Config.validate()
        except ValueError as e:
            logger.warning(f"Configuration validation failed: {e}")
            logger.warning("Falling back to MOCK MODE")
            self.mock_mode = True
            return
        
        self.provider = Config.LLM_PROVIDER
        self.model = Config.LLM_MODEL
        
        # Initialize LLM client based on provider
        self._init_llm_client()
        
        logger.info(
            f"AIAgent initialized: provider={self.provider}, model={self.model}"
        )
    
    def _init_llm_client(self):
        """Initialize the LLM client based on configured provider."""
        if self.provider == "cody":
            self._init_cody()
        elif self.provider == "openai":
            self._init_openai()
        elif self.provider == "anthropic":
            self._init_anthropic()
        elif self.provider == "azure":
            self._init_azure()
        elif self.provider == "local":
            self._init_local()
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")
    
    def _init_cody(self):
        """Initialize Cody API client."""
        self.api_url = Config.CODY_API_URL
        self.api_token = Config.LLM_API_KEY
        
        if not self.api_token:
            raise ValueError(
                "CODY_API_TOKEN not set. Get your token from: "
                "https://sourcegraph.com/user/settings/tokens"
            )
        
        self.headers = {
            "Authorization": f"token {self.api_token}",
            "Content-Type": "application/json",
        }
        
        logger.info(f"Cody API client initialized: {self.api_url}")
    
    def _init_openai(self):
        """Initialize OpenAI client."""
        try:
            from openai import OpenAI
            
            self.client = OpenAI(
                api_key=Config.LLM_API_KEY,
                base_url=Config.LLM_BASE_URL
            )
            logger.info("OpenAI client initialized")
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Run: pip install openai"
            )
    
    def _init_anthropic(self):
        """Initialize Anthropic client."""
        try:
            from anthropic import Anthropic
            
            self.client = Anthropic(
                api_key=Config.LLM_API_KEY
            )
            logger.info("Anthropic client initialized")
        except ImportError:
            raise ImportError(
                "Anthropic package not installed. Run: pip install anthropic"
            )
    
    def _init_azure(self):
        """Initialize Azure OpenAI client."""
        try:
            from openai import AzureOpenAI
            
            self.client = AzureOpenAI(
                api_key=Config.LLM_API_KEY,
                azure_endpoint=Config.LLM_BASE_URL,
                api_version=Config.AZURE_API_VERSION
            )
            logger.info("Azure OpenAI client initialized")
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Run: pip install openai"
            )
    
    def _init_local(self):
        """Initialize local LLM client (OpenAI-compatible API)."""
        try:
            from openai import OpenAI
            
            self.client = OpenAI(
                api_key="not-needed",
                base_url=Config.LLM_BASE_URL
            )
            logger.info(f"Local LLM client initialized: {Config.LLM_BASE_URL}")
        except ImportError:
            raise ImportError(
                "OpenAI package not installed. Run: pip install openai"
            )
    
    def analyze_failure(
        self,
        enriched_context: EnrichedFailureContext
    ) -> RCAReport:
        """
        Analyze enriched failure context and generate RCA report.
        
        This is the main entry point for AI-powered root cause analysis.
        
        Process:
        1. Build surgical prompt from enriched context
        2. Call LLM (or return mock response)
        3. Parse and validate JSON response
        4. Apply confidence scoring based on context completeness
        5. Return structured RCAReport
        
        Args:
            enriched_context: Failure context with code snippets from Phase 2
            
        Returns:
            Structured RCA report with root cause analysis
        """
        failure_id = enriched_context.original_failure.error_id
        
        logger.info(f"Starting RCA analysis for failure: {failure_id}")
        
        # Mock mode: Return deterministic response
        if self.mock_mode:
            return self._generate_mock_report(enriched_context)
        
        # Production mode: Use LLM
        try:
            # Build the surgical prompt
            prompt = self._build_analysis_prompt(enriched_context)
            
            # Call LLM
            raw_response = self._call_llm(prompt)
            
            # Parse and validate response
            report = self._parse_and_validate_response(
                raw_response,
                enriched_context
            )
            
            # Apply dynamic confidence scoring
            report = self._adjust_confidence_score(report, enriched_context)
            
            logger.info(
                f"RCA analysis complete: {failure_id} -> "
                f"{report.root_cause_bucket} (confidence: {report.confidence_score:.2f})"
            )
            
            return report
            
        except Exception as e:
            logger.error(f"RCA analysis failed for {failure_id}: {e}", exc_info=True)
            # Return graceful fallback report
            return self._generate_fallback_report(enriched_context, str(e))
    
    def _build_analysis_prompt(
        self,
        enriched_context: EnrichedFailureContext
    ) -> str:
        """
        Build the surgical system prompt for LLM analysis.
        
        This implements the "Detective's Mindset" prompt engineering:
        - Instructs AI as embedded systems forensic expert
        - Provides all available evidence (logs, code, test)
        - Enforces strict JSON output format
        - Implements hallucination guardrails
        
        Args:
            enriched_context: Enriched failure context
            
        Returns:
            Complete prompt string for LLM
        """
        failure = enriched_context.original_failure
        
        # System instruction
        system_prompt = """You are an expert embedded software debugger specializing in automotive radar systems and Jenkins CI/CD failure analysis.

Your task is to perform forensic root cause analysis on build/test failures by analyzing:
1. Log error patterns and contextual clues
2. Source code snippets around the failure point
3. Test case code and intent

CRITICAL RULES:
- Base your analysis ONLY on the evidence provided
- If source code is unavailable, explicitly state this limitation
- Identify the SPECIFIC line of code responsible if possible
- Determine if the failure is regressive (code change) or environmental (infrastructure)
- Output ONLY valid JSON matching the exact schema provided
- Do NOT include conversational text outside the JSON block

ROOT CAUSE CATEGORIES:
- LOGIC_ERROR: Bug in source code (syntax, logic, algorithm)
- ENVIRONMENT_FAILURE: Infrastructure issue (missing files, permissions, hardware)
- TEST_FLAKINESS: Non-deterministic test behavior
- INFRA_ISSUE: CI/CD pipeline or build system problem
- UNKNOWN: Insufficient evidence to determine root cause

OUTPUT FORMAT (strict JSON):
{
  "failure_id": "ERROR_XXX",
  "root_cause_bucket": "LOGIC_ERROR|ENVIRONMENT_FAILURE|TEST_FLAKINESS|INFRA_ISSUE|UNKNOWN",
  "root_cause_summary": "One-sentence summary for stakeholders",
  "technical_analysis": "Detailed forensic analysis with code references",
  "suggested_fix": "Concrete remediation recommendation",
  "confidence_score": 0.0-1.0,
  "evidence": {
    "log_clues": ["clue1", "clue2"],
    "code_files": ["file1.c:45"],
    "missing_context": ["unavailable_file.c"]
  }
}"""
        
        # Build evidence section
        evidence_section = self._format_evidence(enriched_context)
        
        # Construct full prompt
        full_prompt = f"""{system_prompt}

{evidence_section}

ANALYZE THE ABOVE EVIDENCE AND PROVIDE YOUR ROOT CAUSE ANALYSIS IN STRICT JSON FORMAT:"""
        
        return full_prompt
    
    def _format_evidence(self, enriched_context: EnrichedFailureContext) -> str:
        """
        Format all available evidence for the LLM prompt.
        
        Args:
            enriched_context: Enriched failure context
            
        Returns:
            Formatted evidence string
        """
        failure = enriched_context.original_failure
        
        sections = []
        
        # Section 1: Failure Metadata
        sections.append("=" * 80)
        sections.append("FAILURE METADATA")
        sections.append("=" * 80)
        sections.append(f"Error ID: {failure.error_id}")
        sections.append(f"Primary Error Line: {failure.primary_error_line}")
        sections.append(f"Line Number: {failure.line_number}")
        if failure.test_name:
            sections.append(f"Test Name: {failure.test_name}")
        sections.append("")
        
        # Section 2: Log Clues (Hunted Context)
        sections.append("=" * 80)
        sections.append("LOG CLUES (Contextual Evidence from Logs)")
        sections.append("=" * 80)
        if failure.hunted_clues:
            for clue_key, clue_value in failure.hunted_clues.items():
                sections.append(f"[{clue_key}] {clue_value}")
        else:
            sections.append("No contextual clues available")
        sections.append("")
        
        # Section 3: Source Code Context
        sections.append("=" * 80)
        sections.append("SOURCE CODE CONTEXT")
        sections.append("=" * 80)
        if enriched_context.code_snippet:
            snippet = enriched_context.code_snippet
            sections.append(f"File: {snippet.file_path}")
            sections.append(f"Lines: {snippet.start_line}-{snippet.end_line}")
            if snippet.error_line:
                sections.append(f"Error at line: {snippet.error_line}")
            sections.append("")
            sections.append(snippet.content)
        else:
            sections.append("⚠️  SOURCE CODE UNAVAILABLE")
            if enriched_context.missing_files:
                sections.append(f"Missing files: {', '.join(enriched_context.missing_files)}")
            sections.append("Analysis must be based solely on log patterns.")
        sections.append("")
        
        # Section 4: Test Code Context
        sections.append("=" * 80)
        sections.append("TEST CODE CONTEXT")
        sections.append("=" * 80)
        if enriched_context.test_snippet:
            snippet = enriched_context.test_snippet
            sections.append(f"Test File: {snippet.file_path}")
            sections.append(f"Lines: {snippet.start_line}-{snippet.end_line}")
            sections.append("")
            sections.append(snippet.content)
        else:
            sections.append("Test code not available")
        sections.append("")
        
        # Section 5: Resolution Notes
        if enriched_context.resolution_notes:
            sections.append("=" * 80)
            sections.append("RESOLUTION NOTES")
            sections.append("=" * 80)
            for note in enriched_context.resolution_notes:
                sections.append(f"• {note}")
            sections.append("")
        
        return "\n".join(sections)
    
    def _call_llm(self, prompt: str) -> str:
        """
        Call the configured LLM provider.
        
        Args:
            prompt: The analysis prompt
            
        Returns:
            Raw LLM response text
        """
        self.total_api_calls += 1
        
        logger.debug(f"Calling LLM: provider={self.provider}, model={self.model}")
        
        if self.provider == "cody":
            return self._call_cody_api(prompt)
        elif self.provider in ["openai", "azure", "local"]:
            return self._call_openai_compatible(prompt)
        elif self.provider == "anthropic":
            return self._call_anthropic_api(prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    def _call_cody_api(self, prompt: str) -> str:
        """
        Call Cody API with streaming support.
        
        Args:
            prompt: The analysis prompt
            
        Returns:
            Complete response text
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "maxTokensToSample": Config.LLM_MAX_TOKENS,
            "temperature": Config.LLM_TEMPERATURE,
            "stream": False
        }
        
        try:
            logger.debug(f"Cody API request: {self.api_url}")
            
            response = requests.post(
                self.api_url,
                headers=self.headers,
                json=payload,
                timeout=120  # 2 minute timeout for complex analysis
            )
            response.raise_for_status()
            
            result = response.json()
            
            # Track token usage if available
            if "usage" in result:
                tokens = result["usage"].get("total_tokens", 0)
                self.total_tokens_used += tokens
                logger.info(f"Tokens used: {tokens} (total: {self.total_tokens_used})")
            
            # Extract completion from response
            if "completion" in result:
                return result["completion"]
            elif "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"Unexpected Cody API response format: {result}")
                raise ValueError("Invalid Cody API response format")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Cody API request failed: {e}")
            raise
    
    def _call_openai_compatible(self, prompt: str) -> str:
        """
        Call OpenAI-compatible API (OpenAI, Azure, Local).
        
        Args:
            prompt: The analysis prompt
            
        Returns:
            Complete response text
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model if self.provider != "azure" else Config.AZURE_DEPLOYMENT_NAME,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=Config.LLM_MAX_TOKENS,
                temperature=Config.LLM_TEMPERATURE
            )
            
            # Track token usage
            if hasattr(response, 'usage') and response.usage:
                tokens = response.usage.total_tokens
                self.total_tokens_used += tokens
                logger.info(f"Tokens used: {tokens} (total: {self.total_tokens_used})")
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"OpenAI-compatible API call failed: {e}")
            raise
    
    def _call_anthropic_api(self, prompt: str) -> str:
        """
        Call Anthropic API directly.
        
        Args:
            prompt: The analysis prompt
            
        Returns:
            Complete response text
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=Config.LLM_MAX_TOKENS,
                temperature=Config.LLM_TEMPERATURE,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Track token usage
            if hasattr(response, 'usage') and response.usage:
                tokens = response.usage.input_tokens + response.usage.output_tokens
                self.total_tokens_used += tokens
                logger.info(f"Tokens used: {tokens} (total: {self.total_tokens_used})")
            
            return response.content[0].text
            
        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}")
            raise
    
    def _parse_and_validate_response(
        self,
        raw_response: str,
        enriched_context: EnrichedFailureContext
    ) -> RCAReport:
        """
        Parse LLM response and validate against RCAReport schema.
        
        Implements self-correction: If first parse fails, tries to extract
        JSON from markdown code blocks or retry with clarification.
        
        Args:
            raw_response: Raw LLM response text
            enriched_context: Original context (for fallback)
            
        Returns:
            Validated RCAReport
            
        Raises:
            ValueError: If response cannot be parsed after retry
        """
        logger.debug("Parsing LLM response")
        
        # Try direct JSON parsing first
        try:
            json_data = json.loads(raw_response)
            report = RCAReport.model_validate(json_data)
            logger.info("Successfully parsed and validated LLM response")
            return report
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Direct JSON parsing failed: {e}")
        
        # Try extracting JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
        if json_match:
            try:
                json_data = json.loads(json_match.group(1))
                report = RCAReport.model_validate(json_data)
                logger.info("Successfully extracted JSON from markdown block")
                return report
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(f"Markdown JSON extraction failed: {e}")
        
        # Try finding any JSON object in the response
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw_response, re.DOTALL)
        if json_match:
            try:
                json_data = json.loads(json_match.group(0))
                report = RCAReport.model_validate(json_data)
                logger.info("Successfully extracted JSON from response text")
                return report
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(f"JSON extraction from text failed: {e}")
        
        # Self-correction: Single retry with clarification
        logger.warning("Attempting self-correction retry")
        try:
            retry_prompt = f"""The previous response was not valid JSON. Please provide ONLY a valid JSON object matching this exact schema:

{{
  "failure_id": "string",
  "root_cause_bucket": "LOGIC_ERROR|ENVIRONMENT_FAILURE|TEST_FLAKINESS|INFRA_ISSUE|UNKNOWN",
  "root_cause_summary": "string (min 10 chars)",
  "technical_analysis": "string (min 50 chars)",
  "suggested_fix": "string (min 10 chars)",
  "confidence_score": number (0.0-1.0),
  "evidence": {{
    "log_clues": ["string"],
    "code_files": ["string"],
    "missing_context": ["string"]
  }}
}}

Previous response:
{raw_response[:500]}

Provide corrected JSON:"""
            
            retry_response = self._call_llm(retry_prompt)
            json_data = json.loads(retry_response)
            report = RCAReport.model_validate(json_data)
            logger.info("Self-correction successful")
            return report
            
        except Exception as retry_error:
            logger.error(f"Self-correction failed: {retry_error}")
            raise ValueError(
                f"Failed to parse LLM response after retry. "
                f"Original error: {e}, Retry error: {retry_error}"
            )
    
    def _adjust_confidence_score(
        self,
        report: RCAReport,
        enriched_context: EnrichedFailureContext
    ) -> RCAReport:
        """
        Apply dynamic confidence scoring based on context completeness.
        
        Confidence Rules:
        - No code context: Max 0.6 (guessing from logs only)
        - Code context only: Max 0.8
        - Code + test context: Max 0.95
        - Missing files: Reduce by 0.1
        
        Args:
            report: Initial RCA report from LLM
            enriched_context: Enriched context with completeness info
            
        Returns:
            Report with adjusted confidence score
        """
        original_confidence = report.confidence_score
        
        # Calculate context-based ceiling
        if not enriched_context.has_code_context:
            max_confidence = 0.6
            reason = "no source code context"
        elif not enriched_context.has_test_context:
            max_confidence = 0.8
            reason = "no test code context"
        else:
            max_confidence = 0.95
            reason = "full context available"
        
        # Reduce for missing files
        if enriched_context.missing_files:
            max_confidence -= 0.1
            reason += f", {len(enriched_context.missing_files)} missing files"
        
        # Apply ceiling
        adjusted_confidence = min(original_confidence, max_confidence)
        
        if adjusted_confidence != original_confidence:
            logger.info(
                f"Confidence adjusted: {original_confidence:.2f} -> "
                f"{adjusted_confidence:.2f} ({reason})"
            )
            report.confidence_score = adjusted_confidence
        
        return report
    
    def _generate_mock_report(
        self,
        enriched_context: EnrichedFailureContext
    ) -> RCAReport:
        """
        Generate deterministic mock report for testing.
        
        Args:
            enriched_context: Enriched failure context
            
        Returns:
            Mock RCA report
        """
        failure = enriched_context.original_failure
        
        # Deterministic categorization based on error ID
        if "031" in failure.error_id or "041" in failure.error_id:
            bucket = RootCauseBucket.LOGIC_ERROR
            summary = "Linker error: undefined or multiply defined symbol"
            analysis = f"Mock analysis: {failure.error_id} indicates a linker error. Check symbol definitions."
            fix = "Verify that all referenced symbols are defined exactly once."
        elif "053" in failure.error_id or "054" in failure.error_id or "055" in failure.error_id:
            bucket = RootCauseBucket.TEST_FLAKINESS
            summary = "Test failure detected in validation suite"
            analysis = f"Mock analysis: {failure.error_id} indicates test failure. Review test assertions."
            fix = "Investigate test expectations and actual behavior."
        elif "027" in failure.error_id or "032" in failure.error_id:
            bucket = RootCauseBucket.ENVIRONMENT_FAILURE
            summary = "Missing file or dependency"
            analysis = f"Mock analysis: {failure.error_id} indicates missing file. Check build environment."
            fix = "Ensure all required files are present in the build workspace."
        else:
            bucket = RootCauseBucket.UNKNOWN
            summary = "Insufficient information for root cause determination"
            analysis = f"Mock analysis: {failure.error_id} requires manual investigation."
            fix = "Review logs and code manually."
        
        # Build evidence
        evidence = {
            "log_clues": list(failure.hunted_clues.values())[:3],
            "code_files": [enriched_context.code_snippet.file_path] if enriched_context.code_snippet else [],
            "missing_context": enriched_context.missing_files
        }
        
        # Calculate mock confidence
        confidence = 0.5 if enriched_context.has_code_context else 0.3
        
        report = RCAReport(
            failure_id=failure.error_id,
            root_cause_bucket=bucket,
            root_cause_summary=summary,
            technical_analysis=analysis,
            suggested_fix=fix,
            confidence_score=confidence,
            evidence=evidence
        )
        
        logger.info(f"Generated mock report for {failure.error_id}")
        return report
    
    def _generate_fallback_report(
        self,
        enriched_context: EnrichedFailureContext,
        error_message: str
    ) -> RCAReport:
        """
        Generate graceful fallback report when AI analysis fails.
        
        Args:
            enriched_context: Enriched failure context
            error_message: Error that caused the failure
            
        Returns:
            Fallback RCA report
        """
        failure = enriched_context.original_failure
        
        report = RCAReport(
            failure_id=failure.error_id,
            root_cause_bucket=RootCauseBucket.UNKNOWN,
            root_cause_summary="AI analysis failed - manual investigation required",
            technical_analysis=(
                f"Automated root cause analysis encountered an error: {error_message}\n\n"
                f"Primary error: {failure.primary_error_line}\n"
                f"Available clues: {len(failure.hunted_clues)}\n"
                f"Code context: {'Available' if enriched_context.has_code_context else 'Unavailable'}"
            ),
            suggested_fix="Manual investigation required. Review logs and code context.",
            confidence_score=0.0,
            evidence={
                "log_clues": list(failure.hunted_clues.values())[:5],
                "code_files": [enriched_context.code_snippet.file_path] if enriched_context.code_snippet else [],
                "missing_context": enriched_context.missing_files + [f"AI analysis error: {error_message}"]
            }
        )
        
        logger.warning(f"Generated fallback report for {failure.error_id}")
        return report
    
    def get_statistics(self) -> Dict[str, int]:
        """
        Get agent statistics for monitoring.
        
        Returns:
            Dictionary with usage statistics
        """
        return {
            "total_api_calls": self.total_api_calls,
            "total_tokens_used": self.total_tokens_used,
            "mock_mode": self.mock_mode
        }


def create_ai_agent(mock_mode: bool = False) -> AIAgent:
    """
    Factory function to create an AIAgent instance.
    
    Args:
        mock_mode: If True, use mock responses instead of real LLM calls
        
    Returns:
        Initialized AIAgent instance
        
    Example:
        >>> agent = create_ai_agent(mock_mode=False)
        >>> report = agent.analyze_failure(enriched_context)
    """
    return AIAgent(mock_mode=mock_mode)
