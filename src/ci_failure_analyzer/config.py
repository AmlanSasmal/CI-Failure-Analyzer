"""
Configuration management for CI Failure Analyzer.

Supports environment variables and .env files for flexible configuration.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()


class Config:
    """
    Central configuration class for the analyzer.
    
    Attributes:
        TARGET_REPO_ROOT: Path to the target source repository
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
        MAX_CONTEXT_LINES: Maximum lines to extract around errors
        
        # AI/LLM Configuration
        LLM_PROVIDER: Which LLM service to use (openai, anthropic, azure, cody, local)
        LLM_API_KEY: API key for the LLM service (CODY_API_TOKEN for Cody)
        LLM_MODEL: Model identifier (e.g., gpt-4, claude-3-opus-20240229)
        LLM_BASE_URL: Optional base URL for self-hosted or Azure endpoints
        LLM_MAX_TOKENS: Maximum tokens for AI responses
        LLM_TEMPERATURE: Temperature for response generation (0.0-1.0)
    """
    
    # Repository Configuration
    TARGET_REPO_ROOT: Optional[Path] = None
    LOG_LEVEL: str = "INFO"
    MAX_CONTEXT_LINES: int = 20
    
    # LLM Configuration
    LLM_PROVIDER: str = "cody"  # openai | anthropic | azure | cody | local
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: str = "anthropic/claude-3-5-sonnet-20241022"  # Cody's default model
    LLM_BASE_URL: Optional[str] = None
    LLM_MAX_TOKENS: int = 2000
    LLM_TEMPERATURE: float = 0.1  # Low temperature for factual analysis
    
    # Azure-specific (if using Azure OpenAI)
    AZURE_DEPLOYMENT_NAME: Optional[str] = None
    AZURE_API_VERSION: str = "2024-02-15-preview"
    
    # Cody-specific
    CODY_API_URL: str = "https://sourcegraph.com/.api/completions/stream"
    
    @classmethod
    def load(cls) -> None:
        """Load configuration from environment variables."""
        # Target repository root
        repo_root = os.getenv("TARGET_REPO_ROOT")
        if repo_root:
            cls.TARGET_REPO_ROOT = Path(repo_root).resolve()
        
        # Logging level
        cls.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        
        # Context lines
        max_context = os.getenv("MAX_CONTEXT_LINES")
        if max_context:
            try:
                cls.MAX_CONTEXT_LINES = int(max_context)
            except ValueError:
                pass
        
        # LLM Configuration
        cls.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "cody").lower()
        
        # Support both LLM_API_KEY and CODY_API_TOKEN
        cls.LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("CODY_API_TOKEN")
        
        cls.LLM_MODEL = os.getenv("LLM_MODEL", cls._get_default_model())
        cls.LLM_BASE_URL = os.getenv("LLM_BASE_URL")
        
        # Cody-specific
        cls.CODY_API_URL = os.getenv(
            "CODY_API_URL",
            "https://sourcegraph.com/.api/completions/stream"
        )
        
        # LLM Parameters
        max_tokens = os.getenv("LLM_MAX_TOKENS")
        if max_tokens:
            try:
                cls.LLM_MAX_TOKENS = int(max_tokens)
            except ValueError:
                pass
        
        temperature = os.getenv("LLM_TEMPERATURE")
        if temperature:
            try:
                cls.LLM_TEMPERATURE = float(temperature)
            except ValueError:
                pass
        
        # Azure-specific
        cls.AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
        cls.AZURE_API_VERSION = os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
    
    @classmethod
    def _get_default_model(cls) -> str:
        """Get default model based on provider."""
        defaults = {
            "openai": "gpt-4",
            "anthropic": "claude-3-opus-20240229",
            "azure": "gpt-4",
            "cody": "anthropic/claude-3-5-sonnet-20241022",
            "local": "llama-3.1-70b",
        }
        return defaults.get(cls.LLM_PROVIDER, "gpt-4")
    
    @classmethod
    def validate(cls) -> bool:
        """
        Validate configuration.
        
        Returns:
            True if configuration is valid
            
        Raises:
            ValueError: If required configuration is missing
        """
        # Validate repository root
        if cls.TARGET_REPO_ROOT is None:
            raise ValueError(
                "TARGET_REPO_ROOT not configured. "
                "Set the TARGET_REPO_ROOT environment variable."
            )
        
        if not cls.TARGET_REPO_ROOT.exists():
            raise ValueError(
                f"TARGET_REPO_ROOT does not exist: {cls.TARGET_REPO_ROOT}"
            )
        
        # Validate LLM configuration
        if cls.LLM_PROVIDER in ["openai", "anthropic", "azure", "cody"]:
            if not cls.LLM_API_KEY:
                key_name = "CODY_API_TOKEN" if cls.LLM_PROVIDER == "cody" else "LLM_API_KEY"
                raise ValueError(
                    f"{key_name} required for provider '{cls.LLM_PROVIDER}'. "
                    f"Set the {key_name} environment variable."
                )
        
        if cls.LLM_PROVIDER == "azure":
            if not cls.AZURE_DEPLOYMENT_NAME:
                raise ValueError(
                    "AZURE_DEPLOYMENT_NAME required for Azure OpenAI. "
                    "Set the AZURE_DEPLOYMENT_NAME environment variable."
                )
            if not cls.LLM_BASE_URL:
                raise ValueError(
                    "LLM_BASE_URL required for Azure OpenAI. "
                    "Set the LLM_BASE_URL environment variable to your Azure endpoint."
                )
        
        return True
    
    @classmethod
    def get_llm_config(cls) -> dict:
        """
        Get LLM configuration as a dictionary.
        
        Returns:
            Dictionary with LLM configuration
        """
        config = {
            "provider": cls.LLM_PROVIDER,
            "model": cls.LLM_MODEL,
            "max_tokens": cls.LLM_MAX_TOKENS,
            "temperature": cls.LLM_TEMPERATURE,
        }
        
        # Add provider-specific config
        if cls.LLM_PROVIDER == "azure":
            config["azure_deployment"] = cls.AZURE_DEPLOYMENT_NAME
            config["azure_api_version"] = cls.AZURE_API_VERSION
            config["base_url"] = cls.LLM_BASE_URL
        elif cls.LLM_PROVIDER == "cody":
            config["api_url"] = cls.CODY_API_URL
        elif cls.LLM_BASE_URL:
            config["base_url"] = cls.LLM_BASE_URL
        
        return config


# Auto-load configuration on import
Config.load()
