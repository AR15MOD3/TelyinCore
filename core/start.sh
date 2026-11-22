#!/usr/bin/env bash
set -euo pipefail
core_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [[ "$(uname -s)" != Linux ]]; then
    echo '此入口需要 Linux；Windows 请使用启动 .bat。' >&2
    exit 2
fi
python_path="$core_dir/.venv-linux/bin/python"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

# Called after preparation. Hold the deployment lock while routing and running the selected service.
start_profile() {
    cd -- "$core_dir"
    exec 9>"$core_dir/.linux-acceptance-launch.lock"
    flock -n 9 || { echo '此部署的 Core 已在运行或正在维护。' >&2; exit 1; }
    case "${1:-}" in
        ''|--port|--no-browser|--qq|--qq-setup)
            if ! "$python_path" "$core_dir/scripts/linux_sl_companion.py" --with-parent "$$"; then
                if [[ "${1:-}" == --qq-setup ]]; then exit 1; fi
                echo '[SL] 自动启动失败，Core 将继续启动；再次选择「唤醒」可重试。' >&2
            fi
            ;;
    esac
    local entry=linux_acceptance.py
    case "${1:-}" in
        --qq-setup) shift; exec "$python_path" "$core_dir/scripts/linux_qq.py" --setup "$@" ;;
        --qq) shift; entry=linux_qq.py ;;
        ''|--port|--no-browser)
            entry=linux_qq.py
            ;;
    esac
    exec "$python_path" "$core_dir/scripts/$entry" "$@"
}

if [[ "${1:-}" == --acceptance ]]; then
    shift
    if [[ -f "$core_dir/../runtime/package-updates/pending.json" ]]; then
        python3 "$core_dir/scripts/online_update.py" || exit 1
    fi
    case "${1:-}" in
        --install-launcher) exec python3 "$core_dir/scripts/linux_shortcut.py" ;;
        --snowluma) exec python3 "$core_dir/scripts/linux_snowluma.py" --start ;;
        --start-qq) shift; exec python3 "$core_dir/scripts/linux_qq_desktop.py" "$@" ;;
        --check-qq) exec python3 "$core_dir/scripts/linux_snowluma.py" --check ;;
        --launch-selected)
            # Internal continuation of the menu's successful --prepare call.
            shift
            start_profile "$@"
            ;;
    esac
    if [[ $# == 0 && -t 0 && -t 1 ]]; then
        menu_python="$python_path"
        if [[ ! -x "$menu_python" ]]; then menu_python=python3; fi
        if ! "$menu_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
            echo '启动器需要 Python 3.11 或更新版本。' >&2
            exit 2
        fi
        exec "$menu_python" "$core_dir/scripts/linux_launcher.py"
    fi
    release=true
else
    release=false
    if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
        exec python3 "$core_dir/scripts/linux_dev.py" "$@"
    fi
fi

# Parallel launch windows must not install into the same venv or model at once.
exec 8>"$core_dir/.linux-preparation.lock"
flock 8
check_args=()
if [[ "${1:-}" == --check ]]; then check_args+=(--check); fi
if [[ ! -x "$python_path" ]]; then
    if [[ "${1:-}" == --check ]]; then
        echo 'Linux 环境尚未创建。请正常启动一次完成准备。' >&2
        exit 1
    fi
    python3 -m venv "$core_dir/.venv-linux"
fi
cd -- "$core_dir"
"$python_path" "$core_dir/scripts/linux_bootstrap.py" "${check_args[@]}"
if [[ "$release" == false ]]; then
    exec 8>&-
    exec "$python_path" "$core_dir/scripts/linux_dev.py" "$@"
fi

semantic_args=("${check_args[@]}")
if [[ "${1:-}" == --check-documents ]]; then semantic_args+=(--check); fi
if [[ "${1:-}" != --qq-setup ]]; then
    "$python_path" "$core_dir/scripts/setup_semantic.py" "${semantic_args[@]}"
fi
if [[ "$(uname -m)" == x86_64 && ( -d "$core_dir/linux-components" || -f "$core_dir/../candidate.json" ) ]]; then
    "$python_path" "$core_dir/scripts/linux_snowluma.py" "${check_args[@]}"
fi
exec 8>&-
case "${1:-}" in
    --prepare) exec "$python_path" "$core_dir/scripts/linux_acceptance.py" --check ;;
    --check|--check-documents) exec "$python_path" "$core_dir/scripts/linux_acceptance.py" "$@" ;;
    *) start_profile "$@" ;;
esac
