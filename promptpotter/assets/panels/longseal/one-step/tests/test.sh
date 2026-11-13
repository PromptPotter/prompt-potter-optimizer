#!/bin/bash
# Non-empty answer artifact => 1; correctness is the campaign's call, not this container's.
# Echoes because `_digest` tails this into the optimizer's view and warns when the tail is empty —
# a silent script fires that warning on every healthy cell and masks real layout drift.
if [ -s "/logs/artifacts/answer.txt" ]; then
  echo "answer: $(head -c 200 "/logs/artifacts/answer.txt")"
  echo 1 > /logs/verifier/reward.txt
else
  echo "answer: MISSING - the episode ended without writing /logs/artifacts/answer.txt"
  echo 0 > /logs/verifier/reward.txt
fi
