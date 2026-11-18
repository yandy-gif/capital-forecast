"""资金预测系统启动器（自包含，Windows 友好）

用法：
    .venv\\Scripts\\python.exe 数仓\\launch.py

作用：
    1. 以独立进程方式拉起 数仓/server.py（监听 127.0.0.1:8765）
    2. 拉起一个转发层（127.0.0.1:8766），供内置预览面板 / 需要 HEAD 探测的工具使用
    3. 等待健康检查通过后退出，服务进程脱离控制台继续运行

说明：
    项目自带的 start.ps1 使用 Start-Process，在部分 Windows PowerShell 5.1 环境下
    会因 PATH / Path 环境变量大小写重复而报错（已添加项，字典中的关键字"path"）。
    本脚本改用 subprocess + DETACHED_PROCESS 启动，规避该问题，行为与 start.ps1 一致。
"""
import http.server
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

WH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WH_DIR)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SERVICE_PORT = int(os.environ.get("WH_PORT") or 8765)
PROXY_PORT = int(os.environ.get("WH_PROXY_PORT") or 8766)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "keep-alive"}


# ---------------------------------------------------------------- 转发层
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _relay(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        target = f"http://127.0.0.1:{SERVICE_PORT}{self.path}"
        req = urllib.request.Request(target, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() not in HOP_HEADERS:
                req.add_header(key, value)
        try:
            resp = urllib.request.urlopen(req, timeout=180)
            data, code, headers = resp.read(), resp.status, resp.headers
        except urllib.error.HTTPError as err:
            data, code, headers = err.read(), err.code, err.headers
        except Exception as err:
            data, code, headers = str(err).encode(), 502, {}
        self.send_response(code)
        for key, value in headers.items():
            if key.lower() not in HOP_HEADERS:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._relay("GET")

    def do_POST(self):
        self._relay("POST")

    def do_PUT(self):
        self._relay("PUT")

    def do_DELETE(self):
        self._relay("DELETE")

    def do_PATCH(self):
        self._relay("PATCH")


def run_proxy():
    http.server.ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), _Handler).serve_forever()


# ---------------------------------------------------------------- 启动辅助
def probe(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def health_detail(port=SERVICE_PORT):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def spawn(args, cwd, log_path, env=None):
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    return subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def wait_ready(port, pid, label, tries=80):
    for _ in range(tries):
        time.sleep(0.25)
        if probe(port):
            print(f"[ok]   {label} 已就绪  pid={pid}  http://127.0.0.1:{port}")
            return True
    print(f"[fail] {label} 启动超时  pid={pid}  port={port}")
    return False


def main():
    if not os.path.exists(PY):
        print(f"[fail] 未找到虚拟环境：{PY}")
        print("       请先执行：python -m venv .venv 并安装 requirements.txt")
        return 1

    ok = True

    if probe(SERVICE_PORT):
        print(f"[skip] 数仓服务已在运行  http://127.0.0.1:{SERVICE_PORT}")
    else:
        env = dict(os.environ)
        env["WH_PORT"] = str(SERVICE_PORT)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = spawn(
            [PY, "-u", "server.py"],
            WH_DIR,
            os.path.join(WH_DIR, "server.stdout.log"),
            env=env,
        )
        try:
            with open(os.path.join(WH_DIR, "server.pid"), "w", encoding="ascii") as f:
                f.write(str(proc.pid))
        except OSError:
            pass
        ok = wait_ready(SERVICE_PORT, proc.pid, "数仓服务") and ok

    if probe(PROXY_PORT):
        print(f"[skip] 预览转发层已在运行  http://127.0.0.1:{PROXY_PORT}")
    else:
        proc = spawn(
            [PY, "-u", os.path.abspath(__file__), "--proxy"],
            ROOT,
            os.path.join(WH_DIR, "proxy.log"),
        )
        ok = wait_ready(PROXY_PORT, proc.pid, "预览转发层") and ok

    detail = health_detail()
    if detail:
        stats = detail.get("stats", {})
        eco = detail.get("eco", {})
        print(
            f"[data] 流水行={stats.get('flow_rows')} 计划行={stats.get('plan_rows')} "
            f"人工确认={stats.get('manual_rows')} 主体={eco.get('entities')} "
            f"科目={eco.get('subjects')} 参数={eco.get('params')} 模板={eco.get('templates')}"
        )
        print(f"[db]   {detail.get('db')}")
    print()
    print(f"请在浏览器打开：http://127.0.0.1:{SERVICE_PORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--proxy" in sys.argv:
        run_proxy()
    else:
        sys.exit(main())
