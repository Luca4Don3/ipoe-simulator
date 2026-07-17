#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "错误: macOS/Linux 需要 root/sudo；本程序不会自动提权" >&2
    exit 5
fi

PYTHON_BIN=${PYTHON_BIN:-python3}
LEGACY_RUNTIME=/opt/ipoe-simulator/runtime/bin/python3

if [ "$(uname -s)" = "Linux" ] && [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    VERSION_MAJOR=$(printf '%s' "${VERSION_ID:-0}" | cut -d. -f1)
    LEGACY=0
    case "${ID:-unknown}" in
        rhel|centos)
            [ "$VERSION_MAJOR" -lt 8 ] && LEGACY=1
            ;;
        ubuntu)
            [ "$VERSION_MAJOR" -lt 18 ] && LEGACY=1
            ;;
        sles|sled|opensuse*)
            [ "$VERSION_MAJOR" -lt 15 ] && LEGACY=1
            ;;
    esac
    if [ "$LEGACY" -eq 1 ]; then
        if [ ! -x "$LEGACY_RUNTIME" ]; then
            echo "错误: 遗留 Linux 缺少自带 runtime: $LEGACY_RUNTIME" >&2
            exit 5
        fi
        PYTHON_BIN=$LEGACY_RUNTIME
    elif [ -x "$LEGACY_RUNTIME" ]; then
        PYTHON_BIN=$LEGACY_RUNTIME
    fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [ ! -x "$PYTHON_BIN" ]; then
    echo "错误: 找不到 Python 运行时: $PYTHON_BIN" >&2
    exit 5
fi

exec "$PYTHON_BIN" "$ROOT_DIR/coordinator.py" "$@"
