"""First introduction in the web console, scoped to this Bot's local portrait."""
from datetime import datetime, timezone

from telyincore import memory
from telyincore.source_guard import lease

ACCOUNT = "console:local-owner"
PROFILE = "Console-local-owner"
SOURCE = "private:" + ACCOUNT


class ConsoleIntroduction:
    def __init__(self, home, book, bot):
        self.home = home
        self.book = book
        self.bot = bot

    def __call__(self, submission=None):
        with lease(self.home):
            rows = memory.profile_of(self.home, PROFILE)
            owner = self.book.resolve(ACCOUNT)
            pending = not rows and owner is None
            state = {"bot": self.bot, "pending": pending}
            if submission is None:
                return state
            if not isinstance(submission, dict) or set(submission) != {"bot", "name"}:
                raise ValueError("请填写控制台称呼。")
            if submission["bot"] != self.bot:
                raise FileExistsError("当前 Bot 已切换，请刷新终端。")
            value = submission["name"]
            if not isinstance(value, str) or not value.isprintable() or not 1 <= len(value.strip()) <= 80:
                raise ValueError("称呼须为 1 至 80 个可见字符。")
            name = value.strip()
            if not pending:
                if owner is None and any(row.role == memory.NAME_SAID and row.source == SOURCE
                                         and row.text == name for row in rows):
                    return state
                raise FileExistsError("控制台已有画像，请在画像页查看。")
            # The name itself is the durable completion record. A successful retry
            # can never duplicate it, even from another tab or after a restart.
            memory.remember_person(self.home, PROFILE, name, said=memory.NAME_SAID, source=SOURCE,
                                   ts=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            return {"bot": self.bot, "pending": False}

    def terminal_reader(self, read, display=print):
        """Ask only in an interactive terminal. Preserve EOF and non-keyboard bus wakes."""
        try:
            pending = self()["pending"]
        except (ValueError, OSError):
            display("暂时读不到控制台画像，可稍后在网页终端填写称呼。")
            return read
        if not pending:
            return read
        display(f"{self.bot} · 系统引导\n你好，初次见面！我该怎么称呼你？")
        display("输入常用的名字并回车，我会把它记在你的控制台画像里。直接回车可稍后再填。")

        async def next_line():
            nonlocal pending
            while pending:
                line = await read()
                if not isinstance(line, str) or line == "":
                    return line
                name = line.rstrip("\r\n").strip()
                if name:
                    try:
                        self({"bot": self.bot, "name": name})
                    except FileExistsError as error:
                        display(str(error))
                    except (ValueError, OSError) as error:
                        display(f"称呼未保存，请重填或直接回车跳过。{error}")
                        continue
                    else:
                        display(f"记住了，{name}。")
                pending = False
            return await read()

        return next_line
