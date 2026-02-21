# Product Requirements Document: PromptPotter Optimizer

**Version:** 0.4.0
**Date:** 2026-02-20
**Status:** Draft
**Depends on:** [Project Charter v0.4.0](project-charter.md)

---

## Requirements Summary

| ID | Name | Priority | Description |
|----|------|----------|-------------|
| P0.1 | Evaluation on Dataset | P0 | Score a configuration against a labeled dataset to establish a quantitative baseline |
| P0.2 | Failure Analysis | P0 | Analyze evaluation results to identify and categorize failure patterns |
| P0.3 | Candidate Generation | P0 | Generate improved parameter configurations informed by failure analysis |
| P0.4 | Optimization Loop | P0 | Orchestrate the full evaluate-analyze-generate-select cycle without manual intervention |
| P0.5 | PROMPT_STATE Tracking | P0 | Version every configuration snapshot with structured metadata and a parameters dictionary |
| P1.1 | File-Based Registry | P1 | Persist campaigns and trials to disk in a standard format for comparison and audit |
| P1.2 | Workflow-Based Optimization | P1 | Optimize a single step within a multi-step pipeline while running the full workflow for scoring |
| P1.3 | Human-in-the-Loop Gates | P1 | Pause optimization for developer review and approval of candidates before promotion |
| P1.4 | Real Web Search Provider | P1 | Replace the mock web search node with a real search API provider |
| P1.5 | Candidate Population and Selection | P1 | Support multiple strategies for evaluating and selecting the best candidate |
| P1.6 | Ablation Comparison | P1 | Remove a pipeline component, replay, and compare with statistical significance tests (p-values) |
| P1.7 | Pipeline Parameter Passthrough | P1 | Forward controllable pipeline knobs (search depth, LLM temperatures, candidate limits, score weights) to backend via execution requests |
| P2.1 | Reflection-Based Learning | P2 | Generate natural language reflections after each iteration to inform the next |
| P2.2 | Evolutionary Operators | P2 | Apply genetic algorithm operators (crossover, mutation) to evolve a population of configurations |
| P2.3 | MCP Server Mode | P2 | Expose optimization as an MCP server for use by Claude Code and other MCP clients |
| P2.4 | Streamlit Dashboard | P2 | Provide a visual interface for browsing campaigns, comparing trials, and exploring datasets |
| P2.5 | Non-Prompt Optimization Targets | P2 | Generalize the optimization loop to non-prompt parameter types (schemas, scoring functions, fuzzy matchers, GA settings) |
| P2.6 | Public Deployment Readiness | P2 | Stateless API design with API key authentication and rate limiting readiness for public deployment |

---

## User Personas

**Solo Developer ("Prompt Engineer Pat")**
Builds LLM-powered features and needs to iterate on parameters systematically. Defines a dataset, runs an optimization campaign, and gets back a better configuration with evidence. Uses JupyterLab or the API directly. Values reproducibility and the ability to compare runs.

**Pipeline Operator ("CI/CD Casey")**
Integrates parameter optimization into automated workflows. Calls the REST API from scripts or CI pipelines. Needs structured responses and clear status reporting. Cares about idempotency and error handling.

**Benchmarking Researcher ("Dataset Dana")**
Runs systematic benchmarks against established datasets (MedMentions, BC5CDR, domain-specific LCA corpora). Values reproducibility, statistical rigor, and structured outputs suitable for publication. Uses PromptPotter to compare pipeline variants with p-values and per-query breakdowns, then exports results for inclusion in research papers.

---

## Key Terms

This document uses terms defined in the [Project Charter](project-charter.md): **Campaign**, **Trial**, **PROMPT_STATE**, and **Evaluation dataset**. Refer to the charter's Key Terms table for definitions.

**Additional terms used in requirements:**

| Term | Definition |
|------|-----------|
| **Configuration** | The complete set of tunable parameters for an optimization target: prompt text, few-shot examples, temperature, retrieval counts, similarity thresholds, and any other non-code values that affect LLM behavior. |
| **Evaluator** | A scoring function that compares actual outputs to expected outputs. Examples: exact string match, LLM-as-judge with criteria, custom metric functions. |
| **Candidate** | A proposed new configuration generated during optimization, paired with a rationale explaining what it changes and why. |

---

## P0 — Must Have (Core Optimizer)

### P0.1: Evaluation on Dataset

**As a** developer, **I want to** evaluate a configuration against a labeled dataset **so that** I have a quantitative baseline before optimization.

**What the system does:**
- Accepts an initial configuration and an evaluation dataset (list of input/expected-output pairs, loaded from the consuming project)
- Executes the configured LLM call against each dataset item
- Scores outputs using one or more evaluators (exact match, LLM-as-judge, custom)
- Returns per-item scores and aggregate metrics
- Logs all evaluations to Langfuse with parent-child trace hierarchy

The core operation for the TermNorm validation (SC5) is evaluating both **Variant A** (table reranker only) and **Variant B** (table reranker + LLM2 semantic reranking) against the same dataset to produce comparable scores. Development and testing uses the **BC5CDR 500-term subset** as the primary benchmark (well-known ground truth, scientifically reproducible, suitable for archival publication). MedMentions 500-term subset serves as an additional biomedical benchmark. LCA dataset validation follows when deploying to real-world use.

**Acceptance criteria:**
1. Given a configuration and a dataset of at least 500 items, the system returns aggregate scores and per-item results
2. At least two evaluator types are supported: exact match and LLM-as-judge
3. Every evaluation is logged to Langfuse as a trace with scores attached
4. Evaluation datasets are loaded from an external source (file path or URL); PromptPotter does not host them

### P0.2: Failure Analysis

**As a** developer, **I want** the system to analyze where my configuration fails **so that** I understand what to improve before generating candidates.

**What the system does:**
- Given evaluation results, identifies failing examples (score below a configurable threshold)
- Categorizes failures into patterns (e.g., wrong format, missing information, hallucination, edge cases, ambiguous inputs)
- Produces a structured analysis report with specific failure examples cited
- Passes the analysis forward to the candidate generation step

**Acceptance criteria:**
1. Analysis identifies at least three distinct failure categories with example citations from the dataset
2. The failure report is structured (not free-text prose) so it can be consumed programmatically
3. Analysis is stored as part of the optimization trace in Langfuse

### P0.3: Candidate Generation

**As a** developer, **I want** the system to generate improved prompt configurations **so that** I do not have to manually rewrite parameters through trial and error.

**What the system does:**

The candidate generation process has two stages:

1. **Initialization** — An AI agent analyzes the user-provided context (domain description, task requirements, constraints) and produces structured prompt components via structured output parsing:
   - `task_intent` — what the prompt needs to accomplish
   - `instruction` — step-by-step reasoning directive (e.g., "Let's think step by step.")
   - `answer_format` — output format specification (e.g., "Wrap your final answer in `<ANS>` tags.")

2. **Grow/Filter** — Given the current prompt state's **Layer 1 fields** (persona, task_intent, problem_description, instruction, thinking_style, answer_format), enriches and expands the prompt. This node operates on structured prompt components, not raw text, enabling targeted modifications.

Candidates are generated as structured prompt states with typed fields, not opaque prompt strings. Each field can be independently modified based on analysis feedback.

**Acceptance criteria:**
1. Initialization produces structured prompt components from arbitrary context input
2. Grow/Filter produces enriched prompt states with all required fields populated
3. In Phase 1 (linear mode), N independent runs produce meaningfully different prompt variants through breadth
4. Candidates can modify any prompt component field (persona, task_intent, problem_description, instruction, thinking_style) when analysis suggests it
5. Each candidate state is a valid PromptState with lineage tracking via `parent_id`

### P0.4: Optimization Loop

**As a** developer, **I want** an automated DAG-based optimization loop that initializes, evaluates, analyzes, and adapts **so that** optimization runs without manual intervention.

**What the system does:**
- Implements a DAG-based iterative workflow (derived from the reference n8n design in `docs/design/optimization-workflow.n8n.json`)
- **Initialization:** AI agent analyzes context and produces structured prompt components
- **Main loop per iteration:** prompt state flows through Grow/Filter --> Analysis + Evaluation --> counter increment --> stop condition check
- **Feedback routing:** Analysis produces a `next_action` decision that routes to one of three feedback paths:
  - `"generate"` — **Layer 1**: loop back to main_data to create new variants
  - `"refine context"` — **Layer 2**: update context, then update plan, then loop back
  - `"modify plan"` — **Layer 3**: update the optimization plan, then loop back
- **Stop condition:** counter-based (counter >= N), configurable iteration limit
- Layer 3 ships with `OptimizationDefaults` providing sensible strategy parameters (n_variants, creativity, selection_strategy, improvement_threshold, max_iterations). These should rarely need changing.
- Returns the best-performing prompt state with full lineage

**Phased rollout:**
- **Phase 1 (M2):** Linear mode — initialization --> grow/filter --> analysis+evaluation --> output. No feedback cycling. Run N independent times for breadth-first exploration.
- **Phase 2 (post-M2):** Full cycling mode with feedback paths enabled. Counter threshold set to N (configurable). Iterative depth-first refinement.

**Acceptance criteria:**
1. Phase 1: linear mode runs end-to-end and returns a scored prompt state
2. Phase 1: N independent linear runs produce diverse candidates; the best is selectable by score
3. Phase 2: the full DAG with feedback cycling runs and stops when counter >= N
4. Each iteration's results are traceable in Langfuse with parent-child relationships
5. The returned result includes the full lineage from initialization to best configuration
6. The `next_action` routing correctly dispatches to the three feedback paths (Phase 2)

### P0.5: PROMPT_STATE Tracking

**As a** developer, **I want** each configuration version to carry structured metadata **so that** I can trace how parameters evolved across trials.

**What the system does:**
- Maintains a PROMPT_STATE model that snapshots the full configuration at each trial: structured prompt components organized into three optimization layers (Generate, Refine Context, Modify Plan), plus few-shot examples and a `parameters` dictionary for all other tunable values (temperature, retrieval counts, thresholds, etc.)
- Each optimization trial produces a new PROMPT_STATE
- State transitions are logged with diffs showing what changed between parent and child

**Acceptance criteria:**
1. PROMPT_STATE includes a `parameters` dictionary that can hold arbitrary key-value pairs for non-prompt configuration (e.g., temperature, retrieval_count, similarity_threshold)
2. Every trial references its parent state and describes the changes made
3. Given two PROMPT_STATE snapshots, the system can produce a structured diff

---

## P1 — Should Have (Registry and Integration)

### P1.1: File-Based Registry

**As a** developer, **I want** optimization campaigns and trials persisted to disk **so that** I can review, compare, and audit runs after they complete.

**What the system does:**
- Implements the campaign/trial hierarchy described in the registry design document
- Campaigns contain metadata, configuration, and a list of trials
- Trials contain PROMPT_STATE snapshots, scores, and parent references
- Results are stored in JSONL format (OpenAI Evals standard)
- Lineage tracking records which trial spawned which

**Acceptance criteria:**
1. Starting an optimization creates a campaign directory with metadata
2. Each trial writes its metadata and per-item results
3. Lineage is tracked so the full parent-child tree can be reconstructed
4. Progress events are written as an append-only event stream
5. Campaign data can be listed, retrieved, and exported through the API

### P1.2: Workflow-Based Optimization

**As a** developer, **I want** to optimize parameters for a single step within a multi-step pipeline **so that** I can improve complex systems like TermNorm's retrieval-ranking-classification chain.

**What the system does:**
- Accepts a workflow definition and identifies which step to optimize
- Runs the full workflow end-to-end for each evaluation (not just the target step in isolation)
- Modifies only the target step's configuration between iterations; other steps remain fixed
- Scores the overall workflow output, not just the target step's output

**Acceptance criteria:**
1. A developer can specify which step in a workflow to optimize
2. The full workflow executes during evaluation, with only the target step's parameters changing
3. Scoring reflects the end-to-end workflow output quality

### P1.3: Human-in-the-Loop Gates

**As a** developer, **I want** to review and approve parameter candidates before they are promoted **so that** I maintain control over configuration quality.

**What the system does:**
- After candidate generation, pauses the optimization loop and presents candidates for review
- The developer can approve, reject, or edit candidates
- Approved candidates proceed to evaluation; rejected ones are discarded
- Works through both the API (polling-based) and notebooks (interactive)

**Acceptance criteria:**
1. Optimization can be configured to require human approval before evaluating candidates
2. The API reports candidates with a status indicating they are awaiting review
3. A follow-up call accepts approve/reject/edit decisions for each candidate
4. Rejected candidates are not evaluated, saving LLM costs
5. In notebook mode, the system generates LLM-powered suggestions after each optimization
   round: failure pattern analysis, parameter change recommendations, and prompt phrase
   fragments (atomic text snippets) the user can select or modify before the next iteration
6. Campaign configuration is exposed as a single editable JSON object containing all
   pipeline parameters, optimization settings, and eval LLM settings

### P1.4: Real Web Search Provider

**As a** developer, **I want** the web search node to use a real search API **so that** research workflows produce actual results.

**What the system does:**
- Integrates at least one real search provider (Brave Search or SearxNG)
- The web search node uses the configured provider instead of the current mock
- Provider selection is configurable through environment variables

**Acceptance criteria:**
1. At least one real search provider is supported
2. Provider is selected through configuration, not code changes
3. When no API key is configured, the system falls back gracefully to mock results with a warning

### P1.5: Candidate Population and Selection

**As a** developer, **I want** multiple candidate evaluation strategies **so that** I can choose the selection approach that best fits my optimization task.

**What the system does:**
- Supports multiple strategies for selecting the best candidate: best-of-N (simple score ranking), tournament (ELO-style head-to-head comparison), and weighted multi-metric scoring
- Strategy is configurable per optimization campaign
- Selection rationale is recorded in trial metadata

**Acceptance criteria:**
1. At least two selection strategies are implemented and usable
2. Strategy is configurable at campaign start
3. Each selection decision includes a rationale explaining why that candidate was chosen

### P1.6: Ablation Comparison

**As a** developer, **I want to** remove a pipeline component and compare the results against the full pipeline **so that** I can determine whether that component justifies its cost and latency.

**What the system does:**
- Accepts experiment data (evaluation results from a pipeline run) and a component to ablate (skip)
- Replays the experiment with the specified component removed (e.g., skip the LLM2 reranking step)
- Computes paired ML metrics: hit@k, MRR, latency, confidence
- Runs statistical significance tests (McNemar's test for classification agreement, Wilcoxon signed-rank for latency) and reports p-values
- Returns a structured comparison (JSON) with per-query classification (where the removed component helped, hurt, or made no difference)

**User story:** A developer runs a TermNorm pipeline with web search, entity profiling (LLM1), token matching, and LLM2 semantic reranking. They want to know if LLM2 is worth the extra cost. They upload the experiment results, select "skip LLM2," and the system replays all queries without LLM2, then produces a statistical comparison showing hit@1 with McNemar's p-value and latency savings with Wilcoxon's p-value, plus a per-query breakdown of where LLM2 helped or hurt.

**Acceptance criteria:**
1. Given experiment data and a component to skip, the system produces ablated variant results
2. Comparison includes hit@1 with McNemar's test p-value and latency with Wilcoxon p-value
3. Per-query classification shows which queries were affected by the ablation
4. All results are structured JSON consumable by any client (CLI, notebook, JS frontend)
5. Report is reproducible from saved results without re-running the pipeline

---

## P2 — Nice to Have (Advanced Capabilities)

### P2.1: Reflection-Based Learning

**As a** developer, **I want** the system to generate natural language reflections after each iteration **so that** accumulated insights improve candidate generation over time.

**What the system does:**
- After each iteration, the system generates a structured reflection summarizing what was learned: which changes helped, which did not, and what patterns are emerging
- Reflections are chained across iterations and fed into the next iteration's generation context
- The reflection chain is stored as part of the campaign record

**User story:** A developer optimizing TermNorm's system prompt runs a 5-iteration campaign. By iteration 3, the reflection chain has accumulated insights like "adding explicit instructions for rare disease abbreviations improved accuracy on edge cases but slightly degraded performance on common terms." The generator in iteration 4 uses this history to propose candidates that balance both concerns.

**Acceptance criteria:**
1. Each iteration produces a structured reflection (not just raw scores)
2. The reflection from iteration N is included in the generation context for iteration N+1
3. The full reflection chain is stored and retrievable from the campaign record
4. Reflections reference specific failure patterns and candidate changes, not generic observations

### P2.2: Evolutionary Operators

**As a** developer, **I want** genetic algorithm operators applied to configuration evolution **so that** the optimizer explores a wider search space than single-candidate rewriting allows.

**What the system does:**
- Maintains a population of configurations across generations (not just a single "best so far")
- Applies crossover (combining parts of two configurations) and mutation (random perturbation of parameters) to produce new candidates
- Supports both genetic algorithm (GA) and differential evolution (DE) strategies, inspired by the EvoPrompt framework
- Selection pressure is applied each generation to keep the population focused on high-performing regions

**User story:** A developer has a pipeline with 6 tunable parameters. Single-candidate rewriting tends to get stuck in local optima. They switch to evolutionary mode, which maintains a population of 10 configurations. Crossover combines the prompt from one high-scorer with the temperature and retrieval count from another, discovering a combination that neither parent had.

**Acceptance criteria:**
1. The optimizer can maintain a population of N configurations across generations (configurable N)
2. Crossover produces child configurations by combining elements from two parents
3. Mutation introduces controlled random changes to parameters
4. Both GA and DE strategies are selectable at campaign start
5. Population fitness improves over generations on the evaluation dataset

### P2.3: MCP Server Mode

**As a** developer, **I want** PromptPotter exposed as an MCP server **so that** Claude Code and other MCP-capable clients can invoke optimization directly from the development environment.

**What the system does:**
- Exposes core optimization capabilities (start campaign, check status, get results) as MCP tools
- MCP clients can discover and invoke these tools without manual API calls
- Campaign results are returned in structured format consumable by the MCP client

**User story:** A developer working in Claude Code on a prompt engineering task types a command that triggers a PromptPotter optimization campaign through MCP. The results come back directly in the IDE context, showing the best configuration and score trajectory without switching to a browser or terminal.

**Acceptance criteria:**
1. PromptPotter runs as an MCP server alongside or integrated with the FastAPI service
2. At minimum, three MCP tools are exposed: start a campaign, check campaign status, get campaign results
3. An MCP client (e.g., Claude Code) can discover and invoke these tools
4. Results include the best configuration and score trajectory in structured format

### P2.4: Streamlit Dashboard

**As a** developer, **I want** a visual dashboard for browsing optimization results **so that** I can quickly compare campaigns and understand score trajectories without writing code.

**What the system does:**
- Provides a Streamlit application with three views: campaign browser, trial comparison, and dataset explorer
- Campaign browser shows all campaigns with their score trajectories over time
- Trial comparison displays side-by-side diffs of configurations and scores between any two trials
- Dataset explorer shows per-item scores with filtering and sorting to identify persistent failure cases

**User story:** After running three optimization campaigns against the TermNorm MedMentions dataset, a developer opens the dashboard to compare them. The campaign browser shows that campaign 2 achieved the highest final score. Drilling into trial comparison, they see that the key improvement was adding few-shot examples for rare disease abbreviations. The dataset explorer reveals 12 items that failed across all campaigns, suggesting a dataset quality issue rather than a configuration problem.

**Acceptance criteria:**
1. Campaign browser lists all campaigns with metadata and a score trajectory chart
2. Trial comparison view shows structured diffs between two selected trials (configuration changes and score deltas)
3. Dataset explorer displays per-item scores with the ability to filter by score range and sort by any metric
4. Dashboard reads from the file-based registry (P1.1) with no additional data store required

### P2.5: Non-Prompt Optimization Targets

**As a** developer, **I want** to optimize non-prompt parameters (schemas, scoring functions, fuzzy matching thresholds, retrieval queries, GA settings) using the same optimization loop **so that** I can improve any tunable configuration, not just prompts.

**What the system does:**
- Accepts a pluggable parameter type (state schema) that defines the tunable parameters for the optimization target
- Runs the same DAG-based analyze-generate-evaluate loop regardless of parameter type
- Evaluates using the same evaluator framework (exact match, LLM-as-judge, custom metrics)
- Tracks state lineage and scores identically to prompt optimization

**Acceptance criteria:**
1. At least one non-prompt parameter type can be optimized using the same DAG loop (e.g., scoring function weights or fuzzy matching thresholds)
2. The optimization produces measurable improvement on the evaluation dataset
3. State lineage and scoring work identically to prompt optimization
4. No changes to the core optimization loop are required to support the new parameter type

### P2.6: Public Deployment Readiness

**As a** platform operator, **I want** the API designed for stateless public deployment **so that** PromptPotter can eventually serve as an accessible optimization service.

**What the system does:**
- All API endpoints handle requests statelessly — no server-side session state between requests
- API key authentication middleware is available (disabled by default for local use)
- Rate limiting hooks are defined but not enforced in M1-M4
- API contracts are stable and versioned for external consumers

**Acceptance criteria:**
1. All API endpoints are stateless — no in-memory session state across requests
2. API key authentication middleware exists and can be enabled via configuration
3. Rate limiting middleware exists as a no-op placeholder configurable for future enforcement
4. API versioning (e.g., `/api/v1/`) is consistent across all endpoints
5. Three access tiers are supported: **anonymous** (health/ready only), **authenticated** (full API scoped to own data), and **admin** (user management, global config, system metrics)
6. Authenticated users' data (campaigns, backends, executions, project store) is isolated — no cross-user data access

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Single evaluation (500-item dataset) | Completes within 10 minutes |
| Full optimization run (5 iterations, 500 items) | Completes within 60 minutes |
| Registry storage per campaign | Less than 10 MB |
| Concurrent optimizations | 1 for MVP; 3+ in future |
| LLM provider support | Any provider exposing the OpenAI chat completions API (Groq, OpenAI, Anthropic) |
| Python version | 3.10+ |
| Langfuse trace coverage | 100% of trials have associated traces and scores |
| Dataset location | External (consuming project's repository, not PromptPotter) |
| API design | Stateless request handling; no server-side session state between requests |

---

## Traceability Matrix

This matrix maps PRD requirements to charter success criteria (bidirectional).

**Requirements to Charter Success Criteria:**

| Requirement | SC1: Measurable Improvement | SC2: Reproducibility | SC3: Langfuse Observability | SC4: Time to First Optimization | SC5: TermNorm Validation | SC6: Generalization Beyond Prompts |
|-------------|:---:|:---:|:---:|:---:|:---:|:---:|
| P0.1 Evaluation on Dataset | x | x | x | x | x | |
| P0.2 Failure Analysis | x | | x | | x | |
| P0.3 Candidate Generation | x | | | | x | |
| P0.4 Optimization Loop | x | x | x | x | x | x |
| P0.5 PROMPT_STATE Tracking | | x | | | | |
| P1.1 File-Based Registry | | x | | | | |
| P1.2 Workflow-Based Optimization | | | | | x | |
| P1.3 Human-in-the-Loop Gates | | | | | | |
| P1.4 Real Web Search Provider | | | | | | |
| P1.5 Candidate Population and Selection | x | | | | x | |
| P1.6 Ablation Comparison | x | x | | | x | |
| P2.1 Reflection-Based Learning | x | | | | | |
| P2.2 Evolutionary Operators | x | | | | | |
| P2.3 MCP Server Mode | | | | x | | |
| P2.4 Streamlit Dashboard | | x | | | | |
| P2.5 Non-Prompt Optimization Targets | x | | | | | x |
| P2.6 Public Deployment Readiness | | | | x | | |

**Charter Success Criteria to Requirements (reverse mapping):**

| Charter Success Criterion | Required By (P0/P1) | Enhanced By (P2) |
|--------------------------|---------------------|------------------|
| SC1: Measurable Improvement | P0.1, P0.2, P0.3, P0.4, P1.5 | P2.1, P2.2, P2.5 |
| SC2: Reproducibility | P0.1, P0.4, P0.5, P1.1 | P2.4 |
| SC3: Langfuse Observability | P0.1, P0.2, P0.4 | — |
| SC4: Time to First Optimization | P0.1, P0.4 | P2.3, P2.6 |
| SC5: TermNorm Validation | P0.1, P0.2, P0.3, P0.4, P1.2, P1.5, P1.6 | — |
| SC6: Generalization Beyond Prompts | P0.4 | P2.5 |

**Coverage notes:**
- P1.3 (HITL Gates) and P1.4 (Web Search) do not directly map to a success criterion. They are included as P1 because they support the charter's vision of human-controlled optimization and full workflow support, respectively.
- SC6 is post-M4 and has P0.4 as its foundation (the optimization loop must be target-agnostic by design). P2.5 is the specific implementation requirement.
- All six success criteria have at least one P0 requirement ensuring they are achievable or foundationally supported.
