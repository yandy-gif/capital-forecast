# -*- coding: utf-8 -*-
"""构建 生态资金计划系统v10.html：复制 v9 + 改抬头 + 内联 eco_module.js

跑法：C:\\...\\python.exe 数仓\\build_v10.py
幂等：每次从 v9 重新生成，v9 保持不动作为回退。
"""
import os
import io

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
SRC = os.path.join(ROOT, "珀研Q4资金预测看板v9.html")
DST = os.path.join(ROOT, "生态资金计划系统v10.html")
MOD = os.path.join(BASE, "eco_module.js")

html = io.open(SRC, encoding="utf-8", newline="").read()
mod = io.open(MOD, encoding="utf-8", newline="").read()

repl = [
    ("<title>珀研 · 资金预测与预实分析看板 v2</title>",
     "<title>生态多主体 · 资金计划与预实分析系统 v10</title>"),
    ("<h1>珀研生物 · 资金预测与预实分析看板</h1>",
     "<h1>生态多主体 · 资金计划与预实分析系统</h1>"),
    # 抬头副标题（v9 里是「三模块：…」，v10 换成带版本号的说法；只匹配前缀，避免菜单调整后失配）
    ("三模块：一 数据", "v10 · 三模块：一 数据"),
]
for a, b in repl:
    if a not in html:
        print("  ! 未找到待替换文本：", a[:40])
    html = html.replace(a, b, 1)

tag = '\n<script>\n/* ==== 生态多主体资金计划模块（内联，源文件：数仓/eco_module.js） ==== */\n' + mod + "\n</script>\n"
if "生态多主体资金计划模块（内联" in html:
    print("  ! v10 已含模块，先回到 v9 重建")
html = html.replace("</body>", tag + "</body>", 1)

io.open(DST, "w", encoding="utf-8", newline="").write(html)
print("已生成：", DST, os.path.getsize(DST), "字节")
