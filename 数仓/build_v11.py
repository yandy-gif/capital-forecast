# -*- coding: utf-8 -*-
"""build_v11.py —— 合成 v11 单文件前端

输入：数仓/v11/{shell.html, app.css, app.js, ui.js}
输出：生态资金计划系统v11.html

注意：v11 的数据不再内嵌，一律由 数仓/v11/server11.py 提供（读 warehouse_v11.db）。
先跑 extract_seed.py 生成种子，再由 server11.py 首次启动时灌入数仓。
"""
import os
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "v11")
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "生态资金计划系统v11.html")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def main():
    html = (read("shell.html")
            .replace("/*__CSS__*/", read("app.css"))
            .replace("/*__APP__*/", read("app.js"))
            .replace("/*__UI__*/", read("ui.js")))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = html.replace("</head>", "<!-- build %s -->\n</head>" % stamp)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("built:", OUT, "%.1f KB" % (os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
