#!/bin/bash
# 全量流水线：prune → fetch → summarize → rss → gen-index → sync → size
#
# 两个设计要点：
#
# 1. 每次执行前激活项目自己的 venv：venv 在 .venv/，bin 目录前置到 PATH 即可，
#    不污染调用者的 shell 环境（脚本退出后 PATH 恢复）。
#
# 2. 各阶段独立执行，不因单个阶段失败而中断整条链。原先用 `&&` 串联，一旦
#    summarize（需要登录 cookie）因 cookie 过期退出 1，后面的 rss / gen-index /
#    sync 就全都不会跑——代码明明是好的，播客却停止更新。cookie 过期、网络抖动
#    都不是代码问题，不该阻塞其余阶段。最后统一以退出码反映整体结果：有阶段
#    失败则退出 1，同时打印每个阶段的状态，供定时任务分析。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

if [ ! -d "$VENV" ]; then
    echo "error: venv not found at $VENV — run: uv venv .venv && uv pip install -e ./vendor/bilibili-api-main -e '.[dev]'" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

cd "$ROOT"

STAGES=(b2p-prune b2p-fetch b2p-summarize b2p-rss b2p-gen-index b2p-sync b2p-size)
STATUS=()
FAILED=0

for stage in "${STAGES[@]}"; do
    echo "===> stage: $stage"
    "$stage"
    code=$?
    STATUS+=("$stage=$code")
    if [ $code -ne 0 ]; then
        FAILED=1
        echo "===> stage $stage failed with exit code $code (continuing with the remaining stages)"
    fi
done

echo "===> stage summary: ${STATUS[*]}"
if [ $FAILED -ne 0 ]; then
    echo "===> pipeline finished with failures"
    exit 1
fi
echo "===> pipeline finished successfully"
exit 0
