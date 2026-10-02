#!/bin/zsh
set -u
unsetopt BG_NICE

QC_PROJECT="$(cd -- "$(dirname -- "$0")" && pwd)"
QC_PYTHON="$QC_PROJECT/../.venvs/ljqcapp/bin/python"
QC_RUNTIME="$QC_PROJECT/output/runtime"
QC_URL="http://127.0.0.1:8501"
QC_PID=""

cleanup() {
  if [[ -n "$QC_PID" ]] && kill -0 "$QC_PID" 2>/dev/null; then
    kill "$QC_PID" 2>/dev/null || true
    wait "$QC_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM

cd "$QC_PROJECT" || exit 1
if [[ ! -x "$QC_PYTHON" ]]; then
  print "本机运行环境不完整，请联系维护人员。"
  exit 1
fi
if /usr/bin/curl --fail --silent --max-time 1 "$QC_URL/_stcore/health" >/dev/null 2>&1; then
  print "质控软件已在运行：$QC_URL"
  [[ "${LJQC_NO_BROWSER:-0}" == "1" ]] || /usr/bin/open "$QC_URL"
  exit 0
fi
if /usr/bin/nc -z -w 1 127.0.0.1 8501 >/dev/null 2>&1; then
  print "启动端口正在使用，请关闭占用端口的程序后重试。"
  exit 1
fi
mkdir -p "$QC_RUNTIME/logs" "$QC_RUNTIME/plot-cache"
print "正在打开邦德盛质控软件……"
print "请保留本窗口，关闭它会结束软件。"
MPLCONFIGDIR="$QC_RUNTIME/plot-cache" "$QC_PYTHON" -m streamlit run \
  "$QC_PROJECT/app.py" --server.headless=true \
  --server.address=127.0.0.1 --server.port=8501 --server.fileWatcherType=none \
  --browser.gatherUsageStats=false \
  >"$QC_RUNTIME/logs/service.log" 2>&1 &
QC_PID=$!
QC_READY="false"
for ATTEMPT in {1..120}; do
  if ! kill -0 "$QC_PID" 2>/dev/null; then
    print "启动未完成，请联系维护人员。运行日志保存在output/runtime/logs。"
    exit 1
  fi
  if /usr/bin/curl --fail --silent --max-time 1 "$QC_URL/_stcore/health" >/dev/null 2>&1; then
    QC_READY="true"
    break
  fi
  /bin/sleep 0.5
done
if [[ "$QC_READY" != "true" ]]; then
  print "启动等待超时，请关闭本窗口后重试。"
  exit 1
fi
print "软件已就绪：$QC_URL"
if [[ "${LJQC_NO_BROWSER:-0}" != "1" ]]; then
  /usr/bin/open "$QC_URL" || print "请在浏览器中打开上面的地址。"
fi
wait "$QC_PID"
