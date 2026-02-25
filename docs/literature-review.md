> **Status: Archived** — Last updated 2025-12-17 (pre-M3). Review for context
> only; do not update unless actively surveying new frameworks.

# Related Work: Prompt Optimization Frameworks

*Last updated: 2025-12-17*

## Taxonomy

Prompt optimization methods fall into five paradigms:

- **Evolutionary**: Population-based search with selection/mutation (GEPA, EvoPrompt, PromptBreeder)
- **Gradient-inspired**: LLM-as-critic feedback loops (TextGrad, APO)
- **Reinforcement Learning**: Reward-driven model fine-tuning (ART)
- **Generate-and-Select**: Candidate pool generation + scoring (APE, gpt-prompt-engineer, OPRO)
- **Human-in-loop**: Manual iteration with tooling support (PromptPotter)

## Workflow Optimization

Most tools optimize **single prompts**. Only a few support **multi-step workflows** (e.g., websearch → LLM → reranker):

- **GEPA**: Full workflow support via multi-component co-evolution, per-component traces, RAG adapter
- **DSPy**: Full pipeline programs with sequential optimizers (requires DSPy lock-in)
- **ART**: Agent trajectories via GRPO (model-level, not prompt-level)
- **TextGrad**: Partial - gradient flow through chains (implicit)
- **Single-prompt only**: gpt-prompt-engineer, EvoPrompt, PromptBreeder, APE, OPRO, APO

## Comparison Matrix

| Framework | Paradigm | How Best Selected | How New Generated | Benchmark Results | Workflow |
|-----------|----------|-------------------|-------------------|-------------------|----------|
| [GEPA](https://github.com/gepa-ai/gepa) ⭐ | Evolutionary | Keep non-dominated across metrics | LLM analyzes traces → suggests edits | MATH 93%, +10% vs GRPO | Full |
| [DSPy](https://github.com/stanfordnlp/dspy) | Bayesian | Bayesian optimizer picks promising | Example-constrained generation | +25-65% vs few-shot | Full (lock-in) |
| [TextGrad](https://github.com/zou-group/textgrad) | Gradient-inspired | Pick lowest error score | Error→critique→edit prompt | GPQA +4%, MMLU +3-4% | Partial |
| [ART](https://github.com/OpenPipe/ART) | RL (GRPO) | Weight by reward signal | Fine-tune LoRA weights | Agent trajectories | Agent |
| [EvoPrompt](https://github.com/beeevita/EvoPrompt) | Evolutionary | Rank by score, keep top N | Crossover/combine two prompts | BBH +25% peak | Single |
| [gpt-prompt-engineer](https://github.com/mshumer/gpt-prompt-engineer) | Generate-Select | ELO head-to-head tournament | LLM generates variations | — | Single |
| [PromptBreeder](https://arxiv.org/abs/2309.16797) | Evolutionary | Population fitness ranking | Self-improving mutation prompts | GSM8K 83.9% (+20 vs CoT) | Single |
| [OPRO](https://github.com/google-deepmind/opro) | Meta-prompt | Track and keep best-so-far | LLM proposes from history | GSM8K +8%, BBH +50% | Single |
| [APO](https://github.com/microsoft/LMOps) | Gradient-inspired | Beam search + exploration | Text gradient from errors | +31% max improvement | Single |
| [Medprompt](https://github.com/microsoft/promptbase) | Ensemble | Voting across shuffled runs | Auto-select examples per query | MMLU 90.1% | Single |
| **PromptPotter** | Human-in-loop | Human decision | Manual rewrite | — | Full (goal) |

## Gap Analysis for PromptPotter

| Gap | Field Standard | Opportunity |
|-----|----------------|-------------|
| No automated mutation | LLM-guided (GEPA, TextGrad) | Add trace-guided reflection |
| No selection mechanism | ELO, Pareto, beam search | Implement ELO ranking |
| Single-path iteration | Population-based search | Add candidate pool |
| No MCP integration | GEPA-MCP exists | Build MCP server |
| Placeholder optimizer | Full implementations | Port GEPA/EvoPrompt logic |

## Borrowable Components

1. **GEPA**: Trace-guided reflection fits perfectly with Langfuse traces
2. **gpt-prompt-engineer**: ELO system for candidate ranking
3. **EvoPrompt**: GA/DE operators for population evolution
4. **TextGrad**: LLM-as-critic feedback pattern
5. **DSPy MIPRO**: TPE Bayesian optimization for efficient search
6. **ART RULER**: Automatic reward generation for unlabeled tasks

## References

[DSPy](https://arxiv.org/abs/2310.03714) | [TextGrad](https://arxiv.org/abs/2406.07496) | [EvoPrompt](https://arxiv.org/abs/2309.08532) | [PromptBreeder](https://arxiv.org/abs/2309.16797) | [APE](https://arxiv.org/abs/2211.01910) | [OPRO](https://arxiv.org/abs/2309.03409)
