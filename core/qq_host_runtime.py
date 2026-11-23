"""Host-side lifetime of the optional QQ adapter's control page and encounter index."""
import logging
import threading

LOG = logging.getLogger(__name__)


class QQPanel:
    def __init__(self, create):
        self._create = create
        self._control = None
        self._lock = threading.RLock()

    def get(self):
        with self._lock:
            if self._control is None:
                try:
                    self._control = self._create()
                except Exception as exc:
                    raise ValueError(f'QQ 控制组件暂不可用：{exc}') from exc
            return self._control

    def disable(self):
        with self._lock:
            if self._control is not None:
                self._control.stop_owned_bridge()
                self.close()

    def close(self):
        with self._lock:
            if self._control is not None:
                self._control.close()
                self._control = None

    def action(self, route, values, *, bot, enabled, reload_adapter, describe_upload):
        from telyincore import plugins

        if route == "/settings":
            with plugins.lifecycle_transaction():
                control = self.get()
                control.save(values, reload=reload_adapter)
                control.sync_voice_services(enabled=enabled())
                return control.read()
        if route == "/routes":
            return self.get().save_routes(values, reload=reload_adapter)
        if route == "/voice-setup-check":
            return self.get().voice_setup_check(values)
        if route == "/tts-install":
            if "action" in values:
                return self.get().tts_installer.control(values["action"], values.get("source"))
            return self.get().tts_installer.start(values.get("source", "mirror"))
        if route == "/asr-install":
            if "action" in values:
                return self.get().asr_installer.control(values["action"], values.get("source"))
            return self.get().asr_installer.start(values.get("source", "mirror"))
        if route == "/check":
            return self.get().check_connection(verify_websocket=True)
        if route == "/import-connection":
            return self.get().import_connection(
                reload=reload_adapter)
        if route == "/process":
            action = str(values.get("action") or "")
            with plugins.lifecycle_transaction():
                if action in ("snowluma_start", "snowluma_start_service", "snowluma_recover",
                              "asr_start", "tts_start") and not enabled():
                    raise ValueError("请先启用 QQ 插件")
                return self.get().action(action)
        if route == "/initial-password":
            if values:
                raise ValueError("请求格式无效")
            return self.get().initial_snowluma_password()
        if route == "/clear-management-session":
            if values != {"active": bot}:
                raise ValueError("当前 Bot 已变化，请刷新后重试")
            return self.get().clear_snowluma_session()
        if route == "/recovery":
            return self.get().save_recovery(values)
        if route == "/snowluma-folder":
            return self.get().open_snowluma_folder()
        if route == "/voice-folder":
            return self.get().open_voice_folder()
        if route == "/model-folder":
            return self.get().open_model_folder(values.get("kind"), values.get("model"))
        if route == "/stickers":
            with plugins.lifecycle_transaction():
                instance = dict(plugins._active_items()).get("core-infinity-qq")
                if instance is None or not callable(getattr(instance, 'sticker_panel', None)):
                    raise ValueError("请启用新版 QQ 插件并连接当前账号")
                prepared = instance.sticker_panel(values, bot)
            from qq_sticker_panel import UploadPreview
            if not isinstance(prepared, UploadPreview):
                return prepared

            # The model runs on the console loop, outside the plugin lifecycle lock.
            try:
                answer = describe_upload(prepared)
            except (ValueError, RuntimeError) as error:
                raise ValueError(f"表情识别未完成：{error}") from None
            with plugins.lifecycle_transaction():
                if dict(plugins._active_items()).get("core-infinity-qq") is not instance:
                    raise ValueError("识别期间 QQ 插件已切换，请重新添加")
                return instance.sticker_panel(values, bot, generated=answer)
        if route == "/voice-corpus":
            return self.get().voice_corpus(values)
        if route == "/tts-preview":
            if "text" in values:
                return self.get().preview_voice(values.get("model"), values.get("voice"),
                                                values["text"])
            return self.get().preview_voice(values.get("model"), values.get("voice"))
        raise ValueError("扩展操作不存在")


class EncounterIndex:
    def __init__(self, home, events):
        self.home = home
        self.events = events
        self._index = None
        self._lock = threading.RLock()
        events.on_append(self.observe)

    def switch(self, running):
        with self._lock:
            if not running:
                self._index = None
                return
            if self._index is None:
                try:
                    from qq_encounters import GroupEncounters
                    index = GroupEncounters(self.home)
                    index.attach(self.events, subscribe=False)
                    self._index = index
                except Exception:
                    LOG.exception('QQ 相遇记录暂不可用')

    def observe(self, event):
        if event.source != 'qq':
            return
        with self._lock:
            if self._index is not None:
                try:
                    self._index.observe(event)
                except Exception:
                    LOG.exception('QQ 相遇记录写入失败')

    def hint(self, batch):
        with self._lock:
            if self._index is not None:
                try:
                    return self._index.hint(batch)
                except Exception:
                    LOG.exception('QQ 相遇提示暂不可用')
            return ''

    def people(self, rows, book):
        with self._lock:
            try:
                index = self._index
                if index is None and (self.home/'qq-group-encounters.sqlite3').is_file():
                    from qq_encounters import GroupEncounters
                    index = GroupEncounters(self.home, readonly=True)
                return index.people(rows, book) if index is not None else rows
            except Exception:
                LOG.exception('QQ 相遇画像读取失败')
                return rows
