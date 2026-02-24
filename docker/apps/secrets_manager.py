"""
Secrets Manager - API Key Configuration UI
A simple Streamlit app to configure and save API keys for LLM providers.
"""

import streamlit as st
import os
from pathlib import Path

# Find the .env file location (project root)
ENV_FILE = Path(__file__).parent.parent / ".env"


def load_env_file():
    """Load existing .env file contents."""
    env_vars = {}
    if ENV_FILE.exists():
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def save_env_file(env_vars: dict):
    """Save environment variables to .env file."""
    # Preserve comments and structure from .env.example if it exists
    example_file = ENV_FILE.parent / ".env.example"

    lines = []
    if example_file.exists():
        with open(example_file, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in env_vars:
                        lines.append(f"{key}={env_vars[key]}\n")
                    else:
                        lines.append(line)
                else:
                    lines.append(line)
    else:
        # No example file, just write the keys
        for key, value in env_vars.items():
            lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(lines)


def check_key_status(key: str, env_vars: dict) -> tuple[str, str]:
    """Check if a key is configured and return status emoji and text."""
    value = env_vars.get(key, "")
    if value and not value.startswith("your_") and not value.startswith("gsk_your_"):
        return "configured", value
    return "not_configured", ""


# Page config
st.set_page_config(
    page_title="Secrets Manager",
    page_icon="key",
    layout="centered"
)

st.title("Secrets Manager")
st.markdown("Configure your API keys for LLM providers. Keys are saved to `.env` file.")

# Load current env vars
env_vars = load_env_file()

# API Keys section
st.header("API Keys")

# Groq (primary)
groq_status, groq_current = check_key_status("GROQ_API_KEY", env_vars)
col1, col2 = st.columns([3, 1])
with col1:
    groq_key = st.text_input(
        "Groq API Key (primary)",
        type="password",
        placeholder="gsk_...",
        help="Get your API key from https://console.groq.com/keys"
    )
with col2:
    if groq_status == "configured":
        st.success("Configured")
    else:
        st.warning("Not set")

# OpenAI
openai_status, openai_current = check_key_status("OPENAI_API_KEY", env_vars)
col1, col2 = st.columns([3, 1])
with col1:
    openai_key = st.text_input(
        "OpenAI API Key",
        type="password",
        placeholder="sk-...",
        help="Get your API key from https://platform.openai.com/api-keys"
    )
with col2:
    if openai_status == "configured":
        st.success("Configured")
    else:
        st.warning("Not set")

# Anthropic
anthropic_status, anthropic_current = check_key_status("ANTHROPIC_API_KEY", env_vars)
col1, col2 = st.columns([3, 1])
with col1:
    anthropic_key = st.text_input(
        "Anthropic API Key",
        type="password",
        placeholder="sk-ant-...",
        help="Get your API key from https://console.anthropic.com/"
    )
with col2:
    if anthropic_status == "configured":
        st.success("Configured")
    else:
        st.warning("Not set")

# Model settings
st.header("Model Settings")

llm_provider = st.selectbox(
    "LLM Provider",
    options=["groq", "openai", "anthropic"],
    index=["groq", "openai", "anthropic"].index(
        env_vars.get("LLM_PROVIDER", "groq")
    ) if env_vars.get("LLM_PROVIDER", "groq") in ["groq", "openai", "anthropic"] else 0,
    help="Which LLM provider to use"
)

llm_model = st.selectbox(
    "LLM Model",
    options=[
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "llama-3.3-70b-versatile",
        "gpt-4",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
    ],
    index=0,
    help="Model to use for LLM calls (must match your provider)"
)

# Save button
st.divider()

if st.button("Save Configuration", type="primary", use_container_width=True):
    # Update env vars with new values (only if provided)
    if groq_key:
        env_vars["GROQ_API_KEY"] = groq_key
    if openai_key:
        env_vars["OPENAI_API_KEY"] = openai_key
    if anthropic_key:
        env_vars["ANTHROPIC_API_KEY"] = anthropic_key

    env_vars["LLM_PROVIDER"] = llm_provider
    env_vars["LLM_MODEL"] = llm_model

    # Ensure other required vars exist
    env_vars.setdefault("ENVIRONMENT", "development")
    env_vars.setdefault("API_HOST", "0.0.0.0")
    env_vars.setdefault("API_PORT", "8001")
    env_vars.setdefault("ALLOWED_ORIGINS", "*")
    try:
        save_env_file(env_vars)
        st.success("Configuration saved successfully!")
        st.rerun()
    except Exception as e:
        st.error(f"Failed to save configuration: {e}")

# Help section
with st.expander("How to get API keys"):
    st.markdown("""
    **Groq (recommended):**
    1. Go to [console.groq.com](https://console.groq.com/)
    2. Sign in or create an account
    3. Navigate to API Keys
    4. Create a new API key

    **OpenAI:**
    1. Go to [platform.openai.com](https://platform.openai.com/)
    2. Sign in or create an account
    3. Navigate to API Keys section
    4. Create a new secret key

    **Anthropic:**
    1. Go to [console.anthropic.com](https://console.anthropic.com/)
    2. Sign in or create an account
    3. Navigate to API Keys
    4. Generate a new key
    """)
