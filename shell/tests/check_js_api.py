# -*- coding: utf-8 -*-
"""检查所有暴露给 JS 的 api_* 方法签名。

pywebview 调 js_api 方法时会多传一个位置参数；签名不匹配时异常发生在
pywebview 内部，只在 stderr 留一行，界面表现成"什么都没发生"——太难查，
所以用这个脚本钉住契约。
"""

import inspect
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import os
_SHELL_DIR = (os.environ.get("WB_SHELL_DIR")
               or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# --- 仓库布局适配（repo/shell/tests → 内核在 repo 根；生产布局在 <shell>/wb-proxy）
_SHELL_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_KERNEL_DIR = (os.environ.get("WB_KERNEL_DIR")
               or os.path.dirname(os.path.dirname(_SHELL_TESTS_DIR)))
sys.path.insert(0, os.path.join(_SHELL_DIR, "launcher"))

import ui_web  # noqa: E402

bad = []
print("WebWindow 暴露给 JS 的方法：")
for name in sorted(n for n in vars(ui_web.WebWindow) if n.startswith("api_")):
    func = getattr(ui_web.WebWindow, name)
    signature = inspect.signature(func)
    params = list(signature.parameters.values())
    # 第一个是 self；除 self 外必须还能接一个位置参数（或有 *args）
    accepts = any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params[1:])
    accepts = accepts or any(p.kind in (p.POSITIONAL_ONLY,
                                        p.POSITIONAL_OR_KEYWORD)
                             for p in params[1:])
    mark = "OK" if accepts else "不合格"
    print("  [%s] %-22s %s" % (mark, name, signature))
    if not accepts:
        bad.append(name)

print()
print("结论：%s" % ("全部合格" if not bad else "不合格 " + "、".join(bad)))
sys.exit(1 if bad else 0)
