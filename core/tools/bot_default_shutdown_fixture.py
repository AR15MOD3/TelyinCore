"""Only a synthetic async body in a self-created child, no Bot/config/service."""
import asyncio
from pathlib import Path
import sys


def main():
    import launch
    import bot_launcher_child
    marker = Path(sys.argv[1])
    def implementation(argv, *, shutdown_receiver, **kw):
        async def body():
            print('READY', flush=True)
            try:
                await asyncio.Event().wait()
            finally:
                marker.write_text('synthetic-cleanup-complete', encoding='utf-8')
        return asyncio.run(shutdown_receiver.run(body))
    launch._main_impl = implementation
    return bot_launcher_child.main([])


if __name__ == '__main__':
    raise SystemExit(main())
