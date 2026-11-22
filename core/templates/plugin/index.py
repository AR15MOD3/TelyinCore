# 这一份是模板的入口 —— 把你自己的东西写在这儿。
#
# 那边装载时只认一个名字：`create`（`package.json` 的 `main` 指向本文件 ）。
# `start` ／ `stop` ／ `tools` 三个都是**可选**的（有就调、没有就算）。
#
# ⚠️ **别在这里 `import` 重东西**：这一份是在"装起来"那一刻被 import 的，
# 而那个时候框架正忙着起自己 —— 慢慢来的事放到 `start()` 里 。


def create(ctx):
    """ 那边叫的就是这一个。`ctx` 里有什么见 `docs/plugins.md` 第五节。"""
    note = ctx.get("log") # 先看它在不在，再叫它
    if callable(note):
        note("模板插件起来了")

    def ask(question: str = "", **rest):
        """ 一个工具的 handler：**收关键字参数、交回一句回执**（模型看到的就是这一句）。"""
        return f"你问的是：{question or '（没写问题）'}"

    class Mine:
        """交回去的那一份（那边只认 `start` ／ `stop` ／ `tools` 三个名字 ）。"""

        def start(self):
            """装起来的时候调一次 —— 要做的活儿放这儿（不要放在 `create` 里）。"""
            push = ctx.get("push_event")
            if callable(push):
                # 报一件事：`ts` 由那边替你补（你没有钟，那边有 ）
                push({"type": "template-started", "source": ctx.get("name", ""),
                      "text": "模板插件起来了", "origin": "internal"})

        def stop(self):
            """关掉的时候调一次。"""
            note2 = ctx.get("log")
            if callable(note2):
                note2("模板插件停了")

        def tools(self):
            """ 声明你暴露给模型的工具 —— 交 **`ToolDef`**（那个**有 handler** ）。

            ⚠️ **名字用自家前缀**（比如 `my_`）： 撞上框架自己的、或者别的插件的，
               那**一整串**会被拒（ 而只会拒你这一份，别人照跑 ，并且会说清为什么 ）。
            ⚠️ `parameters` **要显式写出来**（写 `{}` 就是"不收参数"，那是两句不同的话 ）。
            """
            from telyincore.tools import ToolDef, ToolTag
            return [
                ToolDef(name="my_ask", description="问一句，它就答一句。",
                        parameters={"type": "object",
                                    "properties": {"question": {"type": "string"}}},
                        tags=(ToolTag.READ,), handler=ask),
            ]

    return Mine()
