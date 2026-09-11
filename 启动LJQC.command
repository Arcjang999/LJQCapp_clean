#!/bin/zsh

# Finder 双击或从任意 Terminal 目录运行，均以本文件所在目录为准。
LJQC_DIR="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
cd -- "$LJQC_DIR" || exit 1
LJQC_URL="http://127.0.0.1:8501/"

if /usr/bin/curl --fail --silent --max-time 2 "${LJQC_URL}_stcore/health" >/dev/null 2>&1; then
    echo "LJQC 已在运行，正在打开浏览器：$LJQC_URL"
    echo "如需停止后台服务，请双击同文件夹的「停止LJQC.command」。"
    /usr/bin/open "$LJQC_URL"
    exit 0
fi

LJQC_PYTHON=""
for candidate in "$LJQC_DIR/.venv/bin/python" "$LJQC_DIR/../.venvs/ljqcapp/bin/python"; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import streamlit' >/dev/null 2>&1; then
        LJQC_PYTHON="$candidate"
        break
    fi
done

if [[ -z "$LJQC_PYTHON" ]]; then
    echo "未找到已安装 Streamlit 的 Python 环境。"
    echo "请按 README 创建 .venv 并安装 requirements-dev.txt，再双击此文件。"
    read -r "LJQC_REPLY?按回车键关闭……"
    exit 1
fi

export MPLCONFIGDIR="$LJQC_DIR/.cache/matplotlib"
mkdir -p -- "$MPLCONFIGDIR" || exit 1
echo "正在启动 LJQC：$LJQC_URL"
echo "运行期间请保留此终端窗口；按 Control+C 停止应用。"
"$LJQC_PYTHON" -m streamlit run "$LJQC_DIR/app.py" \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.headless false \
    --browser.gatherUsageStats false
LJQC_EXIT=$?
if [[ "$LJQC_EXIT" -ne 0 ]]; then
    echo "启动未成功，请查看上方提示（例如 8501 端口是否被占用）。"
    read -r "LJQC_REPLY?按回车键关闭……"
fi
exit "$LJQC_EXIT"
