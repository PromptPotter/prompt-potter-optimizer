#!/bin/bash
# Non-empty selection artifact => 1. Which documents were the right ones is graded OUTSIDE the
# container: the gold document set would otherwise sit in `/tests` for the answer step to read.
# Echoes for the same reason as the answer step's verifier — see `_TEST_SH`.
if [ -s "/logs/artifacts/retrieved.txt" ]; then
  echo "selection: $(head -c 200 "/logs/artifacts/retrieved.txt")"
  echo 1 > /logs/verifier/reward.txt
else
  echo "selection: MISSING - the step ended without writing /logs/artifacts/retrieved.txt"
  echo 0 > /logs/verifier/reward.txt
fi
