"""Local SnowLuma credentials and native QQ process evidence for the Linux panel."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import stat
import subprocess

from runtime_identity import safe_path
from scripts.linux_qq_desktop import desktop_command, qq_identity, qq_processes, valid_qq
from scripts.linux_snowluma import listener_pid
from snowluma_recovery import Recovery


def initial_password(core, port):
    state = core.parent / 'runtime/linux-snowluma'
    server = listener_pid(core, {port})
    if server is None:
        return {'ok': False, 'said': '请先启动本部署的 SnowLuma。'}
    config = safe_path(state, state / 'config/webui.json')
    raw = config.read_bytes()
    saved = json.loads(raw)
    if saved.get('mustChangePassword') is not True:
        return {'ok': False, 'said': '管理密码已修改，请使用修改后的密码。'}
    log = safe_path(state, state / 'launcher.log')
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - 2**20))
            lines = stream.read().splitlines()
        for line in reversed(lines):
            match = re.search(rb'\[WebUI\]\s+initial credentials: user=admin password=([0-9a-f]{16})\s*$', line)
            if match:
                password = match[1]
                digest = hashlib.scrypt(password, salt=bytes.fromhex(saved['passwordSalt']),
                                        n=16384, r=8, p=1, dklen=64)
                if (hmac.compare_digest(digest.hex(), saved['passwordHash']) and
                        config.read_bytes() == raw and listener_pid(core, {port}) == server):
                    return {'ok': True, 'username': 'admin', 'password': password.decode('ascii')}
                break
    return {'ok': False, 'said': '未找到与当前管理端匹配的初始密码。手动启动的 SL 请查看其终端，或休止后从启动器重新唤醒。'}


def protect_with(path, raw, *, decrypt=False):
    from cryptography.fernet import Fernet
    import tempfile
    if not decrypt and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            try:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    pass
            finally:
                tmp.unlink(missing_ok=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Linux 管理凭据密钥权限异常')
        box = Fernet(f.read(65))
    return box.decrypt(raw) if decrypt else box.encrypt(raw)


class LinuxRecovery(Recovery):
    qq_image_name = 'qq'
    learn_process_path = False  # AppImage mount paths expire when QQ exits.

    def valid_qq_path(self, path):
        return valid_qq(path)

    def __init__(self, control, path, core, root, port):
        key = core.parent / 'runtime/linux-snowluma/management.key'
        super().__init__(path, processes=qq_processes, discover=lambda: desktop_command()[0],
                         launch=lambda path: subprocess.Popen([sys.executable, core/'scripts/linux_qq_desktop.py', path],
                                                              start_new_session=True),
                         protect=lambda raw, **kw: protect_with(key, raw, **kw), management_port=port,
                         trusted_management=lambda: listener_pid(core, {port}) is not None,
                         validate_target=control._validate_scoped_target, qq_process_probe=qq_identity,
                         store_root=root.parent, identity_base=root/'data',
                         mutation_root=root/'data', account_root=root/'data')

    def mutation_store(self, target):
        # Each exact native QQ identity has its own attempt slots. Keep old
        # attempts for diagnostics without blocking a freshly started QQ.
        if not re.fullmatch(r'[0-9a-f]{64}', target):
            raise ValueError('QQ 进程身份摘要无效')
        return self.path.with_name('qq-recovery-target-' + target)

    def state(self):
        result = super().state()
        if not result.get('targetUin') and getattr(self, 'legacy_account', None):
            result['targetUin'] = self.legacy_account()
        return result
