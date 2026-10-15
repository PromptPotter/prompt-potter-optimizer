# Sourced, never run: the ONE uv install path, shared by bootstrap.sh and update.sh.
# The version is pyproject.toml's `[tool.uv] required-version`, the pin CI installs too. Into
# ~/.local/bin (outside .venv, so a sync never wipes it); every sync calls $UV by path, so a
# different uv on PATH never decides how uv.lock resolves.

UV_VERSION="$(sed -n 's/^required-version = "==\(.*\)"$/\1/p' "$(dirname "${BASH_SOURCE[0]}")/../pyproject.toml")"
UV="$HOME/.local/bin/uv"

ensure_uv() {
    [[ -n "$UV_VERSION" ]] || { echo "pyproject.toml pins no uv version" >&2; return 1; }
    [[ "$("$UV" --version 2>/dev/null | cut -d' ' -f2)" == "$UV_VERSION" ]] && return 0
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
        | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
}
