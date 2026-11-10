#!/usr/bin/env bash
# 本地启停接口进程（api）和任务进程（worker）。
#   ./manage.sh start|stop|restart|status [api|worker]   不写进程名表示两个都操作
#   ./manage.sh migrate                                  执行数据库迁移
# 使用仓库根目录的 .venv，可用 PYTHON_BIN 指定其他解释器。本脚本不安装环境。

set -u
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-../.venv/bin/python}"
RUN_DIR="run"
mkdir -p "$RUN_DIR"
export PYTHONPATH="$PWD"

# 读取 .env 中的监听地址，其余配置由程序自己读取
API_HOST="$(grep -E '^API_HOST=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
API_PORT="$(grep -E '^API_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8210}"

command_of() {
  case "$1" in
    api) echo "$PYTHON_BIN -m uvicorn app.main:app --host $API_HOST --port $API_PORT" ;;
    worker) echo "$PYTHON_BIN worker.py" ;;
  esac
}

is_running() {
  local pid_file="$RUN_DIR/$1.pid"
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start_one() {
  local name="$1"
  if is_running "$name"; then
    echo "$name 已在运行（进程号 $(cat "$RUN_DIR/$name.pid")）"
    return 0
  fi
  # shellcheck disable=SC2046
  nohup $(command_of "$name") >"$RUN_DIR/$name.out" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
  sleep 2
  if is_running "$name"; then
    echo "$name 已启动（进程号 $(cat "$RUN_DIR/$name.pid")）"
  else
    echo "$name 启动失败，输出如下："
    cat "$RUN_DIR/$name.out"
    rm -f "$RUN_DIR/$name.pid"
    return 1
  fi
}

stop_one() {
  local name="$1" pid_file="$RUN_DIR/$1.pid"
  if ! is_running "$name"; then
    echo "$name 未在运行"
    rm -f "$pid_file"
    return 0
  fi
  local pid
  pid="$(cat "$pid_file")"
  kill "$pid"
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid"
    echo "$name 未在 10 秒内退出，已强制结束"
  else
    echo "$name 已停止"
  fi
  rm -f "$pid_file"
}

status_one() {
  if is_running "$1"; then
    echo "$1 运行中（进程号 $(cat "$RUN_DIR/$1.pid")）"
  else
    echo "$1 未运行"
  fi
}

action="${1:-}"
target="${2:-all}"
case "$target" in
  all) names="api worker" ;;
  api | worker) names="$target" ;;
  *) echo "未知的进程名：$target（可用：api、worker）"; exit 2 ;;
esac

result=0
case "$action" in
  start) for n in $names; do start_one "$n" || result=1; done ;;
  stop) for n in $names; do stop_one "$n"; done ;;
  restart) for n in $names; do stop_one "$n"; start_one "$n" || result=1; done ;;
  status) for n in $names; do status_one "$n"; done ;;
  migrate) "$PYTHON_BIN" -m alembic upgrade head || result=1 ;;
  *) echo "用法：$0 start|stop|restart|status [api|worker]   或   $0 migrate"; exit 2 ;;
esac
exit $result
