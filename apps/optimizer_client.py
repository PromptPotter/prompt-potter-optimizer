"""
Prompt Optimizer Client - Interactive UI
A Streamlit app to optimize prompts using the PromptPotter API.
"""

import streamlit as st
import requests
import json
import os
from datetime import datetime

# API endpoint (configurable)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# Page config
st.set_page_config(
    page_title="Prompt Optimizer Client",
    page_icon="lightning",
    layout="wide"
)

st.title("Prompt Optimizer Client")
st.markdown("Optimize your prompts iteratively using LLM feedback.")

# Sidebar for configuration
with st.sidebar:
    st.header("Configuration")

    api_url = st.text_input(
        "API URL",
        value=API_BASE_URL,
        help="URL of the PromptPotter API"
    )

    model = st.selectbox(
        "Model",
        options=["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet"],
        index=0,
        help="LLM model to use for optimization"
    )

    max_iterations = st.slider(
        "Max Iterations",
        min_value=1,
        max_value=10,
        value=5,
        help="Maximum number of optimization iterations"
    )

    target_metric = st.selectbox(
        "Target Metric",
        options=["accuracy", "f1", "precision", "recall"],
        index=0,
        help="Metric to optimize for"
    )

    # Health check button
    if st.button("Check API Status"):
        try:
            response = requests.get(f"{api_url}/api/v1/health", timeout=5)
            if response.status_code == 200:
                st.success("API is healthy!")
            else:
                st.error(f"API returned status {response.status_code}")
        except requests.exceptions.RequestException as e:
            st.error(f"Cannot connect to API: {e}")

# Main content
col1, col2 = st.columns([1, 1])

with col1:
    st.header("Input")

    # Initial prompt
    initial_prompt = st.text_area(
        "Initial Prompt",
        height=150,
        placeholder="Enter your initial prompt here...\n\nExample: Classify the sentiment of the following text as positive, negative, or neutral.",
        help="The prompt you want to optimize"
    )

    # Dataset input method
    dataset_method = st.radio(
        "Dataset Input Method",
        options=["Paste JSON", "Upload File", "Use Example"],
        horizontal=True
    )

    dataset = []

    if dataset_method == "Paste JSON":
        dataset_json = st.text_area(
            "Dataset (JSON)",
            height=200,
            placeholder='[\n  {"input": "I love this product!", "expected": "positive"},\n  {"input": "This is terrible.", "expected": "negative"}\n]',
            help="JSON array of examples with 'input' and 'expected' fields"
        )
        if dataset_json:
            try:
                dataset = json.loads(dataset_json)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

    elif dataset_method == "Upload File":
        uploaded_file = st.file_uploader("Upload JSON file", type=["json"])
        if uploaded_file:
            try:
                dataset = json.load(uploaded_file)
                st.success(f"Loaded {len(dataset)} examples")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON file: {e}")

    else:  # Use Example
        dataset = [
            {"input": "I absolutely love this product! Best purchase ever.", "expected": "positive"},
            {"input": "This is the worst experience I've ever had.", "expected": "negative"},
            {"input": "The package arrived on time.", "expected": "neutral"},
            {"input": "Amazing quality, highly recommend!", "expected": "positive"},
            {"input": "Disappointed with the service.", "expected": "negative"},
        ]
        st.info(f"Using example dataset with {len(dataset)} sentiment classification examples")
        with st.expander("View example dataset"):
            st.json(dataset)

    # Custom instructions (optional)
    custom_instructions = st.text_area(
        "Custom Instructions (Optional)",
        height=100,
        placeholder="Any additional instructions for the optimizer...",
        help="Extra guidance for the optimization process"
    )

    # Optimize button
    optimize_button = st.button(
        "Optimize Prompt",
        type="primary",
        use_container_width=True,
        disabled=not initial_prompt or not dataset
    )

with col2:
    st.header("Results")

    if optimize_button and initial_prompt and dataset:
        # Prepare request
        request_data = {
            "initial_prompt": initial_prompt,
            "dataset": dataset,
            "target_metric": target_metric,
            "model": model,
            "max_iterations": max_iterations,
        }

        if custom_instructions:
            request_data["custom_instructions"] = custom_instructions

        # Show progress
        with st.spinner("Optimizing prompt..."):
            try:
                response = requests.post(
                    f"{api_url}/api/v1/optimize",
                    json=request_data,
                    timeout=300  # 5 minute timeout for long optimizations
                )

                if response.status_code == 200:
                    result = response.json()

                    # Success metrics
                    st.success("Optimization complete!")

                    # Key metrics
                    metric_cols = st.columns(3)
                    with metric_cols[0]:
                        st.metric(
                            "Final Score",
                            f"{result.get('final_score', 0):.2%}"
                        )
                    with metric_cols[1]:
                        st.metric(
                            "Improvement",
                            f"{result.get('improvement_percentage', 0):.1f}%"
                        )
                    with metric_cols[2]:
                        st.metric(
                            "Iterations",
                            len(result.get('iterations', []))
                        )

                    # Final optimized prompt
                    st.subheader("Optimized Prompt")
                    st.code(result.get("optimized_prompt", ""), language=None)

                    # Copy button
                    st.button(
                        "Copy to Clipboard",
                        on_click=lambda: st.write(result.get("optimized_prompt", "")),
                        use_container_width=True
                    )

                    # Iteration history
                    st.subheader("Iteration History")
                    iterations = result.get("iterations", [])

                    if iterations:
                        # Score chart
                        scores = [it.get("score", 0) for it in iterations]
                        st.line_chart(scores, use_container_width=True)

                        # Detailed iterations
                        with st.expander("View iteration details"):
                            for i, iteration in enumerate(iterations):
                                st.markdown(f"**Iteration {i + 1}**")
                                st.write(f"Score: {iteration.get('score', 0):.2%}")
                                st.code(iteration.get("prompt", ""), language=None)
                                st.divider()

                    # Execution time
                    st.caption(f"Execution time: {result.get('execution_time', 0):.2f}s")

                else:
                    error = response.json() if response.headers.get("content-type") == "application/json" else response.text
                    st.error(f"API error: {error}")

            except requests.exceptions.Timeout:
                st.error("Request timed out. The optimization may take longer than expected.")
            except requests.exceptions.ConnectionError:
                st.error(f"Cannot connect to API at {api_url}. Is the server running?")
            except Exception as e:
                st.error(f"Error: {e}")

    elif not optimize_button:
        st.info("Configure your prompt and dataset, then click 'Optimize Prompt' to begin.")

# Footer
st.divider()
st.caption(f"PromptPotter Optimizer Client | Connected to: {api_url}")
