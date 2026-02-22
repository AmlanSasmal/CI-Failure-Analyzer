"""
AI-powered failure analysis agent using Google Gemini API.

Analyzes CI log failures and provides root cause analysis using
a specialized AI model trained to understand OneChip A53 firmware
and T32 debugger output.
"""

import json
import logging
import os
from typing import Optional
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from src.ci_failure_analyzer.models.log_models import (
    FailureContext, AnalysisResult, FailureCategory
)

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FailureAnalyzerAgent:
    """
    AI agent for analyzing CI test failures using Google Gemini.
    
    This agent takes parsed failure contexts from logs and uses Gemini
    to identify root causes and categorize failures. It specializes in:
    - OneChip A53 firmware issues
    - Trace32 (T32) debugger interactions
    - Test infrastructure problems
    - Hardware integration issues
    """
    
    # System prompt defining the AI agent's expertise
    SYSTEM_PROMPT = (
        "You are a Senior Silicon Validation Engineer. "
        "You specialize in OneChip A53 firmware and Trace32 (T32) debugging. "
        "Your goal is to analyze CI logs and identify if a failure is a:\n"
        "- CODE_BUG (C code error in firmware)\n"
        "- INFRA_FAILURE (Hardware/T32 connection issue)\n"
        "- FLAKY_TEST (Non-deterministic test)\n"
        "- TOOL_ISSUE (CMM script error, test framework problem)\n"
        "- UNKNOWN (Cannot be classified)\n\n"
        "Analyze the provided test log context and respond with a JSON object "
        "containing: category, summary, root_cause, suggested_fix, and confidence (0.0-1.0). "
        "Be precise and reference specific line numbers from the log when possible."
    )
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Gemini AI agent.
        
        Args:
            api_key: Google Gemini API key (if None, uses GEMINI_API_KEY from .env)
            
        Raises:
            ValueError: If api_key is empty or None and not in .env
        """
        # Get API key from parameter or environment
        if api_key is None:
            api_key = os.getenv("GEMINI_API_KEY")
        
        if not api_key:
            raise ValueError(
                "API key required. Set GEMINI_API_KEY in .env file or pass as parameter"
            )
        
        self.api_key = api_key
        genai.configure(api_key=self.api_key)
        
        # Use Gemini 2.0 Flash for fast, reliable analysis
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self.model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=self.SYSTEM_PROMPT
        )
        
        logger.info(f"FailureAnalyzerAgent initialized with {model_name}")
    
    def analyze_failure(
        self,
        context: FailureContext,
        max_retries: int = 3
    ) -> AnalysisResult:
        """
        Analyze a test failure using Gemini AI.
        
        Generates an AI prompt from the failure context and sends it to Gemini
        for analysis. The response is parsed and validated as an AnalysisResult.
        
        Args:
            context: The failure context to analyze
            max_retries: Number of retry attempts on API failure (default: 3)
            
        Returns:
            AnalysisResult: Structured analysis from the AI
            
        Raises:
            RuntimeError: If analysis fails after all retries
        """
        logger.info(f"Analyzing failure: {context.test_id}")
        
        # Generate the prompt from failure context
        prompt = context.to_ai_prompt()
        
        # Try analysis with retry logic
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"Analysis attempt {attempt}/{max_retries}")
                
                # Call Gemini API
                response = self.model.generate_content(prompt)
                
                # Parse and validate response
                analysis = self._parse_response(response.text, context.test_id)
                
                logger.info(
                    f"Successfully analyzed {context.test_id}: "
                    f"{analysis.category.value} (confidence: {analysis.confidence})"
                )
                
                return analysis
                
            except (ResourceExhausted, ServiceUnavailable) as e:
                # Rate limiting or service unavailable
                if attempt == max_retries:
                    logger.error(
                        f"Rate limit reached after {max_retries} attempts. "
                        f"Using free tier? Consider:\n"
                        f"  1. Wait a few seconds before retrying\n"
                        f"  2. Upgrade to paid API tier\n"
                        f"  3. Check API quota at console.cloud.google.com"
                    )
                    raise RuntimeError(
                        f"Gemini API rate limit exceeded after {max_retries} attempts"
                    ) from e
                
                logger.warning(
                    f"Rate limit hit on attempt {attempt}/{max_retries}, retrying..."
                )
                # Continue to next attempt
                
            except json.JSONDecodeError as e:
                # Invalid JSON from API
                if attempt == max_retries:
                    logger.error(f"Failed to parse AI response as JSON after {max_retries} attempts")
                    raise RuntimeError(
                        f"Could not parse AI response as valid JSON: {str(e)}"
                    ) from e
                
                logger.warning(
                    f"Invalid JSON in response (attempt {attempt}/{max_retries}), retrying..."
                )
                
            except ValueError as e:
                # Invalid response structure
                if attempt == max_retries:
                    logger.error(f"AI response did not match AnalysisResult schema: {str(e)}")
                    raise RuntimeError(
                        f"AI response validation failed: {str(e)}"
                    ) from e
                
                logger.warning(
                    f"Response validation failed (attempt {attempt}/{max_retries}), retrying..."
                )
        
        # Should not reach here due to exceptions, but just in case
        raise RuntimeError(f"Failed to analyze {context.test_id} after {max_retries} attempts")
    
    def _parse_response(self, response_text: str, test_id: str) -> AnalysisResult:
        """
        Parse and validate the AI response.
        
        Attempts to extract JSON from the response and validate it against
        the AnalysisResult schema.
        
        Args:
            response_text: Raw text response from Gemini
            test_id: Test ID for error context
            
        Returns:
            AnalysisResult: Validated analysis result
            
        Raises:
            json.JSONDecodeError: If response contains invalid JSON
            ValueError: If response doesn't match AnalysisResult schema
        """
        # Try to extract JSON from response
        json_str = self._extract_json(response_text)
        
        # Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from response: {json_str[:200]}")
            raise json.JSONDecodeError(
                f"Invalid JSON in AI response for {test_id}",
                json_str,
                0
            ) from e
        
        # Validate and construct AnalysisResult
        try:
            # Ensure category is valid enum
            if "category" in data and isinstance(data["category"], str):
                data["category"] = FailureCategory(data["category"])
            
            # Create AnalysisResult model
            analysis = AnalysisResult(**data)
            
            logger.debug(f"Successfully parsed AnalysisResult for {test_id}")
            return analysis
            
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"AnalysisResult validation failed: {str(e)}")
            logger.error(f"Response data: {json.dumps(data, indent=2)[:500]}")
            
            raise ValueError(
                f"AI response does not match AnalysisResult schema: {str(e)}"
            ) from e
    
    def _extract_json(self, text: str) -> str:
        """
        Extract JSON object from text response.
        
        Handles cases where the AI wraps JSON in markdown code blocks
        or includes explanation text.
        
        Args:
            text: Raw response text from AI
            
        Returns:
            str: Extracted JSON string
            
        Raises:
            ValueError: If no valid JSON found in response
        """
        text = text.strip()
        
        # Try markdown code block format (```json ... ```)
        if "```json" in text:
            try:
                start = text.index("```json") + 7
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                pass
        
        # Try generic code block format (``` ... ```)
        if "```" in text:
            try:
                start = text.index("```") + 3
                end = text.index("```", start)
                return text[start:end].strip()
            except ValueError:
                pass
        
        # Try to find JSON object directly
        brace_count = 0
        start_idx = -1
        
        for i, char in enumerate(text):
            if char == "{":
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    # Found complete JSON object
                    return text[start_idx:i+1]
        
        # If we get here, no valid JSON found
        logger.error(f"Could not extract JSON from response: {text[:300]}")
        raise ValueError("No valid JSON found in AI response")
    
    def batch_analyze_failures(
        self,
        contexts: list[FailureContext],
        stop_on_error: bool = False
    ) -> list[tuple[FailureContext, Optional[AnalysisResult], Optional[str]]]:
        """
        Analyze multiple failures.
        
        Args:
            contexts: List of FailureContext objects
            stop_on_error: If True, stop on first error; if False, continue
            
        Returns:
            List of tuples: (context, analysis, error_message)
            Analysis is None if there was an error.
            Error message is None if successful.
        """
        results = []
        
        for i, context in enumerate(contexts, start=1):
            logger.info(f"Analyzing {i}/{len(contexts)}: {context.test_id}")
            
            try:
                analysis = self.analyze_failure(context)
                results.append((context, analysis, None))
                
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.error(f"Failed to analyze {context.test_id}: {error_msg}")
                results.append((context, None, error_msg))
                
                if stop_on_error:
                    break
        
        # Summary
        successful = sum(1 for _, analysis, _ in results if analysis is not None)
        logger.info(
            f"Batch analysis complete: {successful}/{len(contexts)} "
            f"failures analyzed successfully"
        )
        
        return results
