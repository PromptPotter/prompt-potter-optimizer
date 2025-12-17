Prevalent Registry Strategies for Prompt Optimization

  Based on industry standards (DSPy, OpenAI Evals, MLflow), here's the registry pattern that addresses your needs:

  Core Pattern: Parent-Child Run Hierarchy

  The standard approach uses hierarchical run tracking:

  optimization_run_001/                    # Parent run (optimization campaign)
  ├── metadata.json                        # Optimization config & initial conditions
  ├── parent_metrics.json                  # Overall optimization progress
  ├── trials/                              # Child runs (individual evaluations)
  │   ├── trial_001/
  │   │   ├── metadata.json               # Prompt variant, hyperparams
  │   │   ├── evaluation_results.jsonl    # Per-sample results
  │   │   ├── metrics.json                # Aggregate metrics (MRR, etc.)
  │   │   └── traces/                     # Step-by-step execution traces
  │   ├── trial_002/
  │   └── trial_003/
  └── lineage.json                         # Tracks branching relationships

  ---
  Detailed Structure for Your Use Case

  1. Evaluation Registry (baseline/standard eval)

  evaluation/
  ├── datasets/
  │   └── test_cases.jsonl                # Standard JSONL format (OpenAI Evals style)
  │
  └── results/
      └── eval_baseline_2025-01-15/
          ├── metadata.json               # Run configuration
          ├── results.jsonl               # Per-sample results
          ├── metrics.json                # Aggregate metrics
          └── traces/                     # Detailed execution traces
              ├── query_001_trace.json
              └── query_002_trace.json

  metadata.json (evaluation):
```
  {
    "run_id": "eval_baseline_2025-01-15",
    "run_type": "evaluation",
    "timestamp": "2025-01-15T10:30:00Z",
    "dataset": "datasets/test_cases.jsonl",
    "config": {
      "step1_prompt_version": "extraction_v2",
      "step3_prompt_version": "reranker_v1",
      "model": "groq/llama-3.3-70b",
      "deterministic_generator": "fuzzy_v1"
    },
    "parent_run": null,
    "lineage": []
  }
```
  ---
  2. Optimization Registry (the complex case)
```
  optimization/
  ├── campaigns/
  │   └── optim_campaign_001/                      # Parent optimization run
  │       ├── metadata.json                        # Initial conditions, config
  │       ├── progress.jsonl                       # Step-by-step optimization log
  │       ├── lineage.json                         # Tree structure of trials
  │       ├── best_variant.json                    # Pointer to best trial
  │       │
  │       └── trials/                              # All trial evaluations
  │           ├── trial_001_baseline/
  │           │   ├── metadata.json
  │           │   ├── results.jsonl
  │           │   ├── metrics.json
  │           │   └── traces/
  │           │
  │           ├── trial_002_step1_enhanced/
  │           │   ├── metadata.json               # parent_trial: trial_001
  │           │   ├── results.jsonl
  │           │   ├── metrics.json
  │           │   └── traces/
  │           │
  │           ├── trial_003_step3_enhanced/       # Branched from trial_001
  │           │   └── ...
  │           │
  │           ├── trial_004_combined/             # Branched from trial_002 + trial_003
  │           │   └── ...
  │           │
  │           └── trial_005_alternative_branch/   # Breadth-first exploration
  │               └── ...
```
  metadata.json (optimization campaign):
```
  {
    "run_id": "optim_campaign_001",
    "run_type": "optimization",
    "timestamp": "2025-01-15T14:00:00Z",
    "optimizer_config": {
      "algorithm": "breadth_first_tree_search",
      "max_trials": 20,
      "early_stopping": {"metric": "mrr", "threshold": 0.95}
    },
    "initial_conditions": {
      "baseline_run": "eval_baseline_2025-01-15",
      "seed_prompts": {
        "step1": "prompts/extraction_v2.txt",
        "step3": "prompts/reranker_v1.txt"
      },
      "failure_cases_source": "eval_baseline_2025-01-15/failed_traces"
    },
    "dataset": "datasets/test_cases.jsonl",
    "total_trials": 5,
    "best_trial": "trial_004_combined"
  }
```
  lineage.json (tracks branching):
```
  {
    "tree_structure": [
      {
        "trial_id": "trial_001_baseline",
        "parent": null,
        "children": ["trial_002_step1_enhanced", "trial_003_step3_enhanced"],
        "branch_reason": "baseline evaluation",
        "metrics": {"mrr": 0.78}
      },
      {
        "trial_id": "trial_002_step1_enhanced",
        "parent": "trial_001_baseline",
        "children": ["trial_004_combined"],
        "branch_reason": "improved step1 extraction",
        "changes": {"step1_prompt": "extraction_v3"},
        "metrics": {"mrr": 0.82}
      },
      {
        "trial_id": "trial_003_step3_enhanced",
        "parent": "trial_001_baseline",
        "children": ["trial_004_combined"],
        "branch_reason": "improved step3 reranking",
        "changes": {"step3_prompt": "reranker_v2"},
        "metrics": {"mrr": 0.80}
      },
      {
        "trial_id": "trial_004_combined",
        "parent": ["trial_002_step1_enhanced", "trial_003_step3_enhanced"],
        "children": [],
        "branch_reason": "merge best variants",
        "changes": {"step1_prompt": "extraction_v3", "step3_prompt": "reranker_v2"},
        "metrics": {"mrr": 0.89}
      }
    ]
  }

  progress.jsonl (optimization log):
```
  {"timestamp": "2025-01-15T14:00:00Z", "event": "optimization_start", "config": {...}}
  {"timestamp": "2025-01-15T14:05:00Z", "event": "trial_complete", "trial_id": "trial_001_baseline", "mrr": 0.78}
  {"timestamp": "2025-01-15T14:10:00Z", "event": "branch_decision", "strategy": "parallel_branches", "branches": ["step1_enhancement", "step3_enhancement"]}
  {"timestamp": "2025-01-15T14:15:00Z", "event": "trial_complete", "trial_id": "trial_002_step1_enhanced", "mrr": 0.82, "improvement": 0.04}
  {"timestamp": "2025-01-15T14:20:00Z", "event": "trial_complete", "trial_id": "trial_003_step3_enhanced", "mrr": 0.80, "improvement": 0.02}
  {"timestamp": "2025-01-15T14:25:00Z", "event": "merge_decision", "parent_trials": ["trial_002", "trial_003"], "reason": "both improved"}
  {"timestamp": "2025-01-15T14:30:00Z", "event": "trial_complete", "trial_id": "trial_004_combined", "mrr": 0.89, "improvement": 0.11}
  {"timestamp": "2025-01-15T14:30:10Z", "event": "optimization_complete", "best_trial": "trial_004_combined", "total_improvement": 0.11}
```
  ---
  3. Trial-Level Metadata (individual optimization trial)

  trials/trial_002_step1_enhanced/metadata.json:

```
  {
    "trial_id": "trial_002_step1_enhanced",
    "parent_campaign": "optim_campaign_001",
    "parent_trial": "trial_001_baseline",
    "timestamp": "2025-01-15T14:10:00Z",

    "lineage": {
      "ancestor_trials": ["trial_001_baseline"],
      "branching_strategy": "step1_prompt_enhancement",
      "inherited_from": {
        "trial_001_baseline": ["step3_prompt", "deterministic_generator"]
      }
    },

    "changes_from_parent": {
      "step1_prompt": {
        "old": "prompts/extraction_v2.txt",
        "new": "prompts/extraction_v3.txt",
        "diff_summary": "Added explicit material extraction instruction"
      }
    },

    "config": {
      "step1_prompt_version": "extraction_v3",
      "step3_prompt_version": "reranker_v1",
      "model": "groq/llama-3.3-70b",
      "deterministic_generator": "fuzzy_v1"
    },

    "dataset": "datasets/test_cases.jsonl",

    "source_data": {
      "optimization_data_source": "trial_001_baseline/failed_traces",
      "num_failed_cases_analyzed": 11,
      "optimization_focus": "material extraction accuracy"
    },

    "metrics": {
      "mrr": 0.82,
      "hit_at_5": 0.94,
      "ndcg_at_5": 0.87,
      "improvement_over_parent": {
        "mrr": 0.04,
        "hit_at_5": 0.02
      }
    }
  }
```
  ---
  Standard Format Choices (Based on Industry)

  JSONL for Results (OpenAI Evals standard)
```
  {"query": "stainless steel pipe", "expected": "stainless piping", "predicted_rank_1": "stainless piping", "rank_of_expected": 1, "mrr": 1.0, "trace_file": 
  "traces/query_001.json"}
  {"query": "aluminum tube", "expected": "aluminum tubing", "predicted_rank_1": "alu tube", "rank_of_expected": 3, "mrr": 0.33, "trace_file": 
  "traces/query_002.json"}
```
  JSON for Metadata/Config (MLflow/DSPy standard)

  - metadata.json - Run configuration
  - metrics.json - Aggregate metrics
  - lineage.json - Tree relationships

  JSONL for Event Streams (Industry standard)

  - progress.jsonl - Optimization event log

  ---
  Benefits of This Registry Pattern

  1. Standardized: Follows MLflow/DSPy parent-child pattern
  2. Branching-aware: lineage.json tracks tree search explicitly
  3. Tool-compatible: Can feed into MLflow, custom dashboards, optimization agents
  4. Queryable: Easy to build tools that scan registry for patterns
  5. Reproducible: All configs + lineage preserved
  6. Differential tracking: Each trial logs only changes from parent

  ---
  Tools You Can Leverage

  With this format, you can use:

  1. MLflow UI: Import trials as MLflow runs for visualization
  2. Custom dashboards: Parse JSONL/JSON for analysis
  3. Optimization agents: Read lineage.json + failed_traces/ to make decisions
  4. Version control: Git-friendly (mostly text files)
  5. Jupyter notebooks: Easy exploratory analysis

  ---
  How Your Optimization Agent Uses This
```
  def optimization_agent_decision(campaign_dir: Path):
      """Agent reads registry and decides next optimization step"""

      # Load campaign state
      metadata = json.load(open(campaign_dir / "metadata.json"))
      lineage = json.load(open(campaign_dir / "lineage.json"))

      # Find leaf trials (candidates for next branch)
      leaf_trials = [t for t in lineage["tree_structure"] if not t["children"]]

      # Load best performing leaf
      best_leaf = max(leaf_trials, key=lambda x: x["metrics"]["mrr"])

      # Analyze its failed cases
      best_trial_dir = campaign_dir / "trials" / best_leaf["trial_id"]
      failed_traces = load_failed_traces(best_trial_dir)

      # Pattern analysis: where did it fail?
      failure_analysis = analyze_failures(failed_traces)

      # Decision: which component to optimize?
      if failure_analysis["step1_extraction_errors"] > 0.5:
          return create_trial_config(
              parent=best_leaf["trial_id"],
              change="step1_prompt",
              strategy="enhance_extraction"
          )
      elif failure_analysis["step3_ranking_errors"] > 0.5:
          return create_trial_config(
              parent=best_leaf["trial_id"],
              change="step3_prompt",
              strategy="enhance_reranking"
          )
      else:
          # Try breadth-first alternative branch
          return create_trial_config(
              parent=best_leaf["trial_id"],
              change="deterministic_generator",
              strategy="explore_alternative"
          )
```
  ---
  Summary

  Use this registry structure:

  1. Parent-child hierarchy (MLflow/DSPy standard)
  2. JSONL for results (OpenAI Evals standard)
  3. JSON for metadata/config (universal)
  4. Explicit lineage tracking (for your tree search)
  5. Event log for progress (debugging/auditing)

  This is the most standardized approach that:
  - Follows industry patterns (MLflow, DSPy, OpenAI Evals)
  - Supports branching optimization workflows
  - Enables tool integration
  - Provides programmatic access for optimization agents


# From Github Models new experimental feature



trials/trial_002_step1_enhanced/
├── metadata.json                    # Your existing metadata
├── prompt.yml                       # ADD: GitHub Models format
├── results.jsonl                    # Your existing results  
├── metrics.json                     # Your existing metrics
└── traces/                          # Your existing traces

### trial_002_step1_enhanced/prompt.yml (GitHub Models format):
yamlname: "Trial 002: Step1 Enhanced"
description: "Enhanced material extraction prompt"
model: groq/llama-3.3-70b
modelParameters:
  temperature: 0.0
messages:
  - role: system
    content: |
      {{step1_prompt}}  # Reference to actual prompt content
  - role: user
    content: |
      Query: {{query}}
testData:
  - query: "stainless steel pipe"
    expected: "stainless piping"
  - query: "aluminum tube"  
    expected: "aluminum tubing"
evaluators:
  - name: "MRR threshold"
    uses: custom/mrr
    threshold: 0.8 