import importlib.util
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('configure', ROOT / 'scripts/configure.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'env.example').write_text('PIPER_PORT=5050\nPIPER_API_KEY=\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_preserves_existing_key_custom_settings_and_ports(self):
        path = self.root / '.env'
        path.write_text('PIPER_PORT=5252\nPIPER_HTTPS_PORT=0\nPIPER_API_KEY=my-test-key\nCUSTOM_FLAG=keep\n')
        self.assertEqual(config.configure(self.root), (5252, 0))
        self.assertEqual(config.read_env(path)['PIPER_API_KEY'], 'my-test-key')
        self.assertEqual(config.read_env(path)['CUSTOM_FLAG'], 'keep')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_new_install_avoids_occupied_port(self):
        with socket.socket() as listener:
            listener.bind(('0.0.0.0', 0))
            port = listener.getsockname()[1]
            self.assertNotEqual(config.free_port(port, set()), port)

    def test_https_zero_and_explicit_http_are_kept(self):
        self.assertEqual(config.configure(self.root, 5353, 0), (5353, 0))
        self.assertEqual(config.read_env(self.root / '.env')['TTS_CONCURRENCY'], '1')

    def test_invalid_ports_do_not_write_configuration(self):
        for http, https in [(0, 0), (65536, 5443), (5050, 5050), (5050, -1)]:
            with self.assertRaises(ValueError):
                config.configure(self.root, http, https)
            self.assertFalse((self.root / '.env').exists())


class InstallerTests(unittest.TestCase):
    def test_help_is_available_without_system_changes(self):
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('PI TTS Pack', result.stdout)

    def test_unknown_flag_fails(self):
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--unknown'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_host_check_does_not_install(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp)
            for name, source in {
                'uname': '#!/bin/sh\n[ "$1" = -s ] && echo Linux || echo aarch64\n',
                'id': '#!/bin/sh\necho 1000\n',
                'apt-get': '#!/bin/sh\nexit 99\n',
                'sudo': '#!/bin/sh\nexit 99\n',
            }.items():
                path = fake_bin / name
                path.write_text(source)
                path.chmod(0o755)
            env = {**os.environ, 'PATH': f'{temp}:{os.environ["PATH"]}'}
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--check', '--dir', f'{temp}/untouched'],
                                    capture_output=True, text=True, env=env)
            # Host fixture uses the actual Python: supported 3.11-3.13, or a clear version rejection.
            if os.sys.version_info[:2] <= (3, 13):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Host check passed', result.stdout)
            else:
                self.assertIn('Python 3.11 to 3.13', result.stderr)
            self.assertFalse((fake_bin / 'untouched').exists())


if __name__ == '__main__':
    unittest.main()
