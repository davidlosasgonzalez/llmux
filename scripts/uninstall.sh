#!/bin/sh
set -eu

PACKAGE_NAME="llmux"
LLMUX_HOME_DIRNAME=".llmux"
LLMUX_COMMANDS="llmux-server llmux-claude llmux-init llmux-verdict llmux-trace llmux"

dry_run=0
uv_tool_bin=""

show_usage() {
    cat <<'USAGE'
Usage: uninstall.sh [options]

Removes the LLMux uv tool and deletes ~/.llmux/ after removal is verified.
Does not remove uv, Claude Code, the uv-managed Python runtime, or shared PATH entries.

Options:
  --dry-run                Print commands without running them.
  --help                   Show this help text.
USAGE
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

step() {
    printf '\n==> %s\n' "$1"
}

quote_arg() {
    case "$1" in
        *[!A-Za-z0-9_./:@%+=,-]*|"")
            escaped=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')
            printf '"%s"' "$escaped"
            ;;
        *)
            printf '%s' "$1"
            ;;
    esac
}

print_command() {
    printf '+'
    for arg in "$@"; do
        printf ' '
        quote_arg "$arg"
    done
    printf '\n'
}

run() {
    print_command "$@"
    if [ "$dry_run" -eq 1 ]; then
        return 0
    fi

    if "$@"; then
        return 0
    else
        status=$?
    fi
    fail "Command failed with exit code $status: $1"
}

is_missing_uv_tool_error() {
    normalized=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$normalized" in
        *"$PACKAGE_NAME"*"is not installed"*) return 0 ;;
        *) return 1 ;;
    esac
}

add_path_entry() {
    [ -n "$1" ] || return 0
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$1:$PATH" ;;
    esac
}

add_known_uv_paths() {
    if [ -n "${XDG_BIN_HOME:-}" ]; then
        add_path_entry "$XDG_BIN_HOME"
    fi
    add_path_entry "$HOME/.local/bin"
    add_path_entry "$HOME/.cargo/bin"
    export PATH
    hash -r 2>/dev/null || true
}

is_llmux_command_running() {
    command_name=$1

    if command -v pgrep >/dev/null 2>&1; then
        if pgrep -x "$command_name" >/dev/null 2>&1; then
            return 0
        fi
        if pgrep -f "^([^ ]*/)?${command_name}( |$)" >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    ps -A -o comm= 2>/dev/null | grep -qx "$command_name"
}

assert_no_llmux_processes_running() {
    running=""
    for command_name in $LLMUX_COMMANDS; do
        if is_llmux_command_running "$command_name"; then
            running="${running} ${command_name}"
        fi
    done

    if [ -n "$running" ]; then
        fail "LLMux is still running (${running# }). Stop those processes, then rerun uninstall."
    fi
}

initialize_uv_context() {
    add_known_uv_paths

    if [ "$dry_run" -eq 1 ]; then
        print_command uv tool dir --bin
        return 0
    fi

    if ! command -v uv >/dev/null 2>&1; then
        fail "uv is required to remove the LLMux tool. Install uv, then rerun this uninstaller; ~/.llmux was not deleted."
    fi

    print_command uv tool dir --bin
    if uv_tool_bin=$(uv tool dir --bin); then
        :
    else
        status=$?
        fail "Could not determine the uv tool bin directory (exit code $status); ~/.llmux was not deleted."
    fi
    [ -n "$uv_tool_bin" ] || fail "uv returned an empty tool bin directory; ~/.llmux was not deleted."
}

uninstall_llmux() {
    print_command uv tool uninstall "$PACKAGE_NAME"
    if [ "$dry_run" -eq 1 ]; then
        return 0
    fi

    if output=$(uv tool uninstall "$PACKAGE_NAME" 2>&1); then
        if [ -n "$output" ]; then
            printf '%s\n' "$output"
        fi
        return 0
    else
        status=$?
    fi

    if is_missing_uv_tool_error "$output"; then
        printf 'LLMux uv tool is already absent; verifying its entry points.\n'
        return 0
    fi
    if [ -n "$output" ]; then
        printf '%s\n' "$output" >&2
    fi
    fail "uv tool uninstall $PACKAGE_NAME failed with exit code $status; ~/.llmux was not deleted."
}

verify_llmux_commands_removed() {
    if [ "$dry_run" -eq 1 ]; then
        printf '+ verify all LLMux entry points are absent from the uv tool bin directory\n'
        return 0
    fi

    remaining=""
    for command_name in $LLMUX_COMMANDS; do
        command_path="$uv_tool_bin/$command_name"
        if [ -e "$command_path" ] || [ -L "$command_path" ]; then
            remaining="${remaining} ${command_path}"
        fi
    done
    if [ -n "$remaining" ]; then
        fail "LLMux entry points remain after uv uninstall:${remaining}; ~/.llmux was not deleted."
    fi
}

purge_llmux_home() {
    llmux_home="$HOME/$LLMUX_HOME_DIRNAME"
    if [ ! -e "$llmux_home" ]; then
        printf 'No LLMux config directory at %s; skipping purge.\n' "$llmux_home"
        return 0
    fi

    run rm -rf "$llmux_home"
    if [ "$dry_run" -eq 0 ] && [ -e "$llmux_home" ]; then
        fail "LLMux config directory still exists after deletion: $llmux_home"
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dry-run)
                dry_run=1
                ;;
            --help|-h)
                show_usage
                exit 0
                ;;
            *)
                show_usage >&2
                fail "unknown option: $1"
                ;;
        esac
        shift
    done
}

parse_args "$@"
[ -n "${HOME:-}" ] || fail "HOME is not set; cannot locate LLMux data."

step "Checking for running LLMux processes"
assert_no_llmux_processes_running

step "Locating the uv-managed LLMux installation"
initialize_uv_context

step "Removing the LLMux uv tool"
uninstall_llmux

step "Verifying LLMux entry points were removed"
verify_llmux_commands_removed

step "Purging LLMux config and data from ~/.llmux"
purge_llmux_home

if [ "$dry_run" -eq 1 ]; then
    printf '\nDry run complete. No changes were made.\n'
else
    printf '\nLLMux has been removed and verified.\n'
    printf 'uv, Claude Code, the uv-managed Python runtime, and shared PATH entries were left installed.\n'
fi
