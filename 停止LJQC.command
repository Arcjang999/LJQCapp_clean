#!/bin/zsh

LJQC_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
LJQC_PIDS=$(/usr/sbin/lsof -nP -t -iTCP:8501 -sTCP:LISTEN 2>/dev/null)
if [[ -z "$LJQC_PIDS" ]]; then
    echo "LJQC 已停止：8501 端口没有运行中的服务。"
    exit 0
fi

LJQC_EXIT=0
for LJQC_PID in ${(f)LJQC_PIDS}; do
    LJQC_CMD=$(/bin/ps -p "$LJQC_PID" -o command= 2>/dev/null)
    LJQC_CWD=$(/usr/sbin/lsof -a -p "$LJQC_PID" -d cwd -Fn 2>/dev/null | /usr/bin/sed -n 's/^n//p')
    # 同时核对项目目录、Streamlit 命令和入口文件，不按 Python 名称批量停止。
    if [[ "$LJQC_CWD" != "$LJQC_DIR" || "$LJQC_CMD" != *"-m streamlit run "* ||
          ( "$LJQC_CMD" != *"streamlit run app.py "* && "$LJQC_CMD" != *"streamlit run $LJQC_DIR/app.py "* ) ]]; then
        echo "无法确认 8501 上的服务属于本项目，未停止该进程（PID $LJQC_PID）。"
        LJQC_EXIT=1
        continue
    fi

    echo "正在停止 LJQC（PID $LJQC_PID）……"
    if ! kill -TERM "$LJQC_PID"; then
        echo "无法停止服务，请查看上方提示。"
        LJQC_EXIT=1
        continue
    fi
    for LJQC_WAIT in {1..10}; do
        kill -0 "$LJQC_PID" 2>/dev/null || break
        /bin/sleep 1
    done
    if kill -0 "$LJQC_PID" 2>/dev/null; then
        # 防止等待期间 PID 被复用，只对身份仍一致的进程执行超时终止。
        LJQC_CURRENT_CMD=$(/bin/ps -p "$LJQC_PID" -o command= 2>/dev/null)
        LJQC_CURRENT_CWD=$(/usr/sbin/lsof -a -p "$LJQC_PID" -d cwd -Fn 2>/dev/null | /usr/bin/sed -n 's/^n//p')
        if [[ "$LJQC_CURRENT_CMD" == "$LJQC_CMD" && "$LJQC_CURRENT_CWD" == "$LJQC_DIR" ]]; then
            echo "正常停止超时，正在结束卡住的 LJQC 服务……"
            if kill -KILL "$LJQC_PID"; then
                /bin/sleep 1
                if /usr/sbin/lsof -nP -a -p "$LJQC_PID" -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1; then
                    echo "端口仍未释放，请查看进程状态。"
                    LJQC_EXIT=1
                else
                    echo "LJQC 已停止。浏览器标签页可以关闭。"
                fi
            else
                LJQC_EXIT=1
            fi
        else
            echo "进程状态已变化，未执行强制停止，请重新运行此文件。"
            LJQC_EXIT=1
        fi
    else
        echo "LJQC 已停止。浏览器标签页可以关闭。"
    fi
done

if [[ "$LJQC_EXIT" -ne 0 && -t 0 ]]; then
    read -r "LJQC_REPLY?按回车键关闭……"
fi
exit "$LJQC_EXIT"
