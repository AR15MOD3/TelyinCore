"""Apply Core lifecycle choices while retaining the installed Linux adapter."""
from scripts.linux_qq import LinuxQQControl as InstalledQQControl, listener_pid


class LinuxQQControl(InstalledQQControl):
    def ensure_snowluma_on_wake(self):
        if listener_pid(self.release_core, {self.management_port}) is not None:
            return True
        if not (self.release_core / 'linux-components').is_dir():
            if (self.release_core.parent / 'candidate.json').is_file():
                raise ValueError('缺少随包 SnowLuma 组件，请检查 Linux 部署包')
            return False
        self.action("snowluma_start_service")
        if listener_pid(self.release_core, {self.management_port}) is None:
            raise ValueError('SnowLuma 启动后尚未确认管理服务就绪，请查看 QQ 插件日志')
        return True

    def start_snowluma_with_bot(self, *, enabled=False):
        if enabled and self.ensure_snowluma_on_wake():
            super().start_snowluma_with_bot(enabled=True)

    def stop_owned_bridge(self, *, required=False):
        import select
        import signal
        from scripts.linux_sl_companion import managed_watcher
        with managed_watcher(self.release_core, self.management_port) as fd:
            if fd is None:
                if required:
                    raise ValueError('此 SL 由独立终端启动，请在原窗口休止')
                return
            self.recovery.cancel()
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not select.select([fd], [], [], 12)[0]:
                raise ValueError('SL 正在收尾，请稍后刷新')
