import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import windows_relocation as relocation


class RelocationTests(unittest.TestCase):
    def test_scripts_keep_entrypoint_and_zip_on_second_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'new path'
            old=root.parent/'old'
            folder=root/'core/.venv/Scripts'
            folder.mkdir(parents=True)
            (folder/'python.exe').touch()
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as archive:archive.writestr('__main__.py','print(123)')
            before=b'MZ'+b'#!'+str(old/'core/.venv/Scripts/python.exe').encode()+b'\n\r\n'+package.getvalue()
            exe=folder/'entry.exe';exe.write_bytes(before)
            self.assertEqual(relocation.repair_scripts(root/'core',old),1)
            self.assertEqual(relocation.repair_scripts(root/'core',old),0)
            with zipfile.ZipFile(exe) as archive:self.assertEqual(archive.read('__main__.py'),b'print(123)')
            self.assertIn(b'#!"'+str(folder/'python.exe').encode()+b'"\n',exe.read_bytes())
            self.assertEqual(next((root/'runtime/relocation-backups/files/core/.venv/Scripts/entry.exe').iterdir()).read_bytes(),before)

    def test_last_successful_root_and_independent_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'first'
            (root/'core').mkdir(parents=True)
            with patch.object(relocation,'verify_bot'),patch.object(relocation,'previous_installation',return_value=None),patch.object(relocation,'repair_profile',return_value=1) as profile,patch.object(relocation,'repair_voice',side_effect=ValueError('test')),patch.object(relocation,'repair_tasks',return_value=0),patch.object(relocation,'repair_scripts',return_value=0):
                relocation.reconcile(root/'core','IRIS',strict=True)
                moved=root.with_name('second')
                root.rename(moved)
                with self.assertRaisesRegex(RuntimeError,'voice_models'):relocation.reconcile(moved/'core','IRIS',strict=True)
                profile.assert_called_once_with(moved/'core',root,'IRIS')
                with patch.object(relocation,'repair_voice',return_value=1) as voice:
                    self.assertEqual(relocation.reconcile(moved/'core','IRIS',strict=True),{'voice_models':1})
                    voice.assert_called_once_with(moved/'core',root)
                    self.assertEqual(relocation.reconcile(moved/'core','IRIS',strict=True),{})

    def test_reservation_must_belong_to_same_account_and_old_store(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'current'
            old=root.parent/'previous'
            core=root/'core'
            base=core/'data'
            store=root/'bots/IRIS/infinity/processes/snowluma-recovery.json'
            store.parent.mkdir(parents=True)
            store.write_text(json.dumps({'targetUin':'123456'}))
            key=hashlib.sha256(b'configured-qq-account-v1\0' + b'123456').hexdigest()
            record=base/'qq-configured-account-reservations'/(key+'.json')
            record.parent.mkdir(parents=True)
            row=dict(version=1,account=key,scope=relocation._digest_path(old/'core/data'),store='wrong-profile')
            record.write_text(json.dumps(row))
            with patch.object(relocation,'verify_bot',return_value=base):
                with self.assertRaisesRegex(ValueError,'不匹配'):
                    relocation.repair_profile(core,old,'IRIS')
                self.assertEqual(json.loads(record.read_text()),row)
                row['store']=relocation._digest_path(old/store.relative_to(root))
                record.write_text(json.dumps(row))
                other=root/'bots/Telyin/infinity/processes/snowluma-recovery.json'
                other.parent.mkdir(parents=True)
                other.write_bytes(store.read_bytes())
                with self.assertRaisesRegex(ValueError,'多个 Bot'):
                    relocation.repair_profile(core,old,'IRIS')
                other.unlink()
                self.assertEqual(relocation.repair_profile(core,old,'IRIS'),1)
                self.assertEqual(relocation.repair_profile(core,old,'IRIS'),0)
                self.assertEqual(json.loads(record.read_text())['store'],relocation._digest_path(store))
