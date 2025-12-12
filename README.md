# PromptPotter Optimizer

## Chain it your way: workflows • algorithms • LLMs • web researchers • custom evaluators

- Compose and optimize chains of workflows, algorithms, LLMs, researchers, and custom evaluators
- Custom workflow chains for reranking. Continuous optimization campaigns. Custom evaluators.
- API-first prompt optimization service that iteratively improves prompts based on dataset performance.

## Overview

PromptPotter Optimizer is a microservice that helps you optimize prompts for LLM applications. Upload a dataset with examples, specify your target metric, and get back an optimized prompt that performs better on your specific use case.

### Key Features

- **API-First Design**: Clean REST API consumable by any client (Python, JavaScript, etc.)
- **Iterative Optimization**: Uses LLM feedback to iteratively improve prompts
- **Flexible Deployment**: Run locally, self-host, or use hosted service
- **Dataset-Driven**: Optimization based on your actual use cases
- **Multiple LLM Support**: Works with OpenAI, Anthropic, and other providers

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/prompt-potter-optimizer.git
cd prompt-potter-optimizer

# Copy environment file
cp .env.example .env

# Edit .env and add your API keys
# OPENAI_API_KEY=your_key_here

# Run with Docker Compose
docker-compose -f docker/docker-compose.yml up -d

# API will be available at http://localhost:8000
```

### Option 2: Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env and add your API keys

# Run the server
uvicorn api.main:app --reload

# API will be available at http://localhost:8000
```

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Example API Request

```python
import requests

# Optimization request
response = requests.post(
    "http://localhost:8000/api/v1/optimize",
    json={
        "initial_prompt": "Classify the sentiment:",
        "dataset": [
            {"text": "I love this!", "expected": "positive"},
            {"text": "This is terrible", "expected": "negative"},
            {"text": "Amazing product!", "expected": "positive"}
        ],
        "target_metric": "accuracy",
        "model": "gpt-4",
        "max_iterations": 5
    }
)

result = response.json()
print(f"Optimized prompt: {result['optimized_prompt']}")
print(f"Improvement: {result['improvement']:.2f}%")
```

## Google Colab Integration

See the `examples/` directory for Jupyter/Colab notebooks demonstrating usage:

- `examples/quickstart.ipynb`: Basic optimization workflow
- `examples/advanced_optimization.ipynb`: Advanced techniques and parameters

To use in Colab:

```python
# If using hosted service
API_URL = "https://your-hosted-api.com/api/v1"

# If running locally with ngrok
# API_URL = "https://your-ngrok-url.ngrok.io/api/v1"
```

## Configuration

Edit `.env` file or set environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Environment (development/production) | development |
| `DEBUG` | Enable debug mode | true |
| `API_HOST` | API host | 0.0.0.0 |
| `API_PORT` | API port | 8000 |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `MAX_DATASET_SIZE` | Maximum examples in dataset | 1000 |
| `MAX_ITERATIONS` | Maximum optimization iterations | 5 |
| `DEFAULT_MODEL` | Default LLM model | gpt-4 |

## Deployment

### Self-Hosted (Docker)

```bash
# Build production image
docker build -f docker/Dockerfile -t promptpotter-optimizer .

# Run container
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_key \
  --name promptpotter \
  promptpotter-optimizer
```

### Cloud Deployment

Deploy to your preferred cloud provider:

- **AWS**: ECS/Fargate or EC2
- **GCP**: Cloud Run or GKE
- **Azure**: Container Instances or AKS

See deployment guides in `docs/` (coming soon).

## Development

### Project Structure

```
prompt-potter-optimizer/
├── api/
│   ├── core/           # Optimization algorithms
│   ├── routers/        # API endpoints
│   ├── models/         # Request/response models
│   ├── services/       # LLM integrations
│   ├── config/         # Configuration
│   └── main.py         # FastAPI app
├── examples/           # Colab notebooks
├── docker/             # Docker configs
└── tests/              # Tests
```

### Running Tests

```bash
pytest tests/
```

### Adding New LLM Providers

1. Add provider SDK to `requirements.txt`
2. Create provider client in `api/services/`
3. Update `api/core/optimizer.py` to use new provider

## Roadmap

- [ ] Implement actual LLM-based optimization logic
- [ ] Add support for more metrics (F1, precision, recall)
- [ ] Custom metric functions
- [ ] Prompt versioning and A/B testing
- [ ] Rate limiting and authentication
- [ ] Web UI dashboard
- [ ] Batch optimization endpoints

## License

MIT License - see LICENSE file for details

## Contributing

Contributions welcome! Please open an issue or PR.

## Support

For issues or questions:
- GitHub Issues: [yourusername/prompt-potter-optimizer/issues]
- Documentation: [Link to docs]
