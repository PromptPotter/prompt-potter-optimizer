# Hypothesis — each optimization method has its own most efficient model

**Status: hypothesis, 2026-10-30.** Two measurements point to it; neither is replicated. If it hardens, it is
a candidate for the paper's title claim.

**The claim:** the optimizer model is not a property of the task or of "a good model". It belongs to the
method, and to each step within a method. A model that is fastest and cheapest for one method's calls can
be several times more expensive for another method's calls, or wrong for another step of the same method.
A fair comparison between methods therefore gives each one its own best model, never one shared model.

## The evidence so far

1. **Across methods** (jl-optmodel-1 plus a replay of h2h-7's banked calls, `benchmarks.md`):
   - For potter's L1 (long prompts, structured output, run at `qwen3.7-flash @ medium`), `minimax-m3.1-flash`
     cost the same per round and its calls were about 20× faster (2.6 s vs 55 s). Its held-out lift was
     +0.073 against qwen's +0.013, paired +0.060 [−0.008, +0.128].
   - For CAPO and LEVI (short calls at `qwen3.7-flash @ none`), the same MiniMax calls cost 3–12× more.
     CAPO gained no speed; LEVI ran 2–4× faster.
2. **Across steps of one method** (L4 `promptpotter-self.panel__116d44`): the MiniMax that won on L1
   made potter's L2 abort 5 of 9 inner runs. It sent a `terminate_proposal` that blamed a backend fault no
   node showed. The qwen sandbox had 0 such aborts in 3 runs. The head-to-head never exercised L2.

3. **The unit is the model on one host, not the model.** The same L4 outer `l1_generate` prompt,
   replayed on MiniMax, varied by host (2026-10-30, `.scratch/probe_outer_l1.py`, 3 replays per host):
   - DigitalOcean, the `:nitro` pick: ~1 s, no reasoning, 3 of 6 replays a schema skeleton;
   - Together: 5–11 s, no reasoning, 3 of 3 with real edits;
   - Fireworks: 27–33 s with ~3k reasoning tokens at `low`, 3 of 3 with real edits;
   - SambaNova: 43–92 s.

   jl-optmodel-1's 2.6 s MiniMax arm was the DigitalOcean host without reasoning.

## What would harden it

- A second replicate of jl-optmodel-1 (minimax vs qwen), so the L1 lift separates.
- Each peer method screened on the model menu by lift per dollar and per minute, not by the cost per call alone.
- A per-step model split inside potter (L1 vs L2/L3) measured against a single model on every step.
