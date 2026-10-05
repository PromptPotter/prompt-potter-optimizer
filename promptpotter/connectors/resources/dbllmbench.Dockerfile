# TypeDB's own benchmark runner (github.com/typedb/db-llm-bench), built at one
# pinned commit. The commit is measurement identity: a dataset names the image
# `dbllmbench:<commit>` in node config, and connectors/dbllmbench.py refuses a
# cell whose image was not built from the commit its tag names.
#
#   docker build -f dbllmbench.Dockerfile --build-arg DB_LLM_BENCH_COMMIT=<sha> \
#       -t dbllmbench:<sha> .
#
# `--build-arg DB_LLM_BENCH_PATCHES="dbllmbench-repetitions.patch dbllmbench-cached-tokens.patch"
# -t dbllmbench:<sha>-search` builds the SEARCH image: upstream plus a `repetitions:` config key,
# default three, and the provider's cached-token count on every `tokens` object. A cell on it is
# not the published harness, so a reported cell runs the unpatched tag.
FROM rust:1.95-bookworm AS build
ARG DB_LLM_BENCH_COMMIT
WORKDIR /src
RUN test -n "$DB_LLM_BENCH_COMMIT" \
    && git init -q . \
    && git remote add origin https://github.com/typedb/db-llm-bench.git \
    && git fetch -q --depth 1 origin "$DB_LLM_BENCH_COMMIT" \
    && git checkout -q FETCH_HEAD
ARG DB_LLM_BENCH_PATCHES=
COPY *.patch /patches/
RUN for patch in $DB_LLM_BENCH_PATCHES; do git apply "/patches/$patch" || exit 1; done
RUN cargo build --release --locked -p bench-cli

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/db-llm-bench /src/target/release/verify /usr/local/bin/
# The prompt templates, schemas, examples and questions the binaries read, at the same commit:
# a cell names them by path in here, so no dataset carries a second copy that could drift.
COPY --from=build /src/data /opt/db-llm-bench/data
ARG DB_LLM_BENCH_COMMIT
ARG DB_LLM_BENCH_PATCHES=
LABEL org.promptpotter.db-llm-bench.commit="$DB_LLM_BENCH_COMMIT" \
      org.promptpotter.db-llm-bench.patches="$DB_LLM_BENCH_PATCHES"
