# AIME 2025 — Competition Mathematics

Solve competition-level math problems from the American Invitational Mathematics Examination (2025). These are significantly harder than grade school math — they require deep mathematical reasoning, creative problem solving, and precise computation.

## Domain

- Input: a competition math problem (algebra, number theory, combinatorics, geometry)
- Output: a single integer answer in [0, 999]
- Challenge: multi-step reasoning with advanced mathematical concepts, no multiple choice

## Success criteria

- Exact Match: predicted integer equals gold integer
- All AIME answers are integers between 0 and 999 inclusive
- Standard answer format: `\boxed{N}` (scorer extracts from `\boxed{}` first, falls back to last number)

## Key failure modes

- Algebraic manipulation errors (polynomial expansion, factoring, simplification)
- Modular arithmetic mistakes (wrong modulus, sign errors)
- Combinatorial overcounting or undercounting
- Number theory gaps (divisibility, prime factorization, Euler's totient)
- Geometric reasoning errors (coordinate geometry, trigonometric identities)
- Premature rounding or losing precision in intermediate steps
- Not verifying the answer falls in [0, 999]
