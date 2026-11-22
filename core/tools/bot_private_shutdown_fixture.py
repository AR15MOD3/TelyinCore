"""Spawn target: synthetic WebService on own port 0; no Bot/config/model/QQ."""
import asyncio


def worker(client, ready, cleanup_entered, permit_cleanup, cleaned, *, shutdown_receiver):
    import launch
    from telyincore.web import WebService

    def synthetic_impl(argv, *, shutdown_receiver, **options):
        async def body():
            service = WebService(host='127.0.0.1', port=0, web_root=None).start()
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                while not permit_cleanup.is_set():
                    await asyncio.sleep(0.01)
                if not service.stop(timeout=2):
                    raise RuntimeError('synthetic service cleanup incomplete')
                cleaned.set()
        return asyncio.run(shutdown_receiver.run(body))

    # Only this self-created child is patched; launch.main's admission/finally
    # and the real WebService stop execute. No full Bot assembly is invoked.
    launch._main_impl = synthetic_impl
    raise SystemExit(launch.main([], shutdown_receiver=shutdown_receiver))
