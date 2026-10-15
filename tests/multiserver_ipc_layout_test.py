"""Check the installed PSRDADA ABI before any shared-memory allocation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.multiserver.pipeline_runtime import _sync_bytes

PROBE = None


def verify_layout(value):
    fields = {'readers', 'transfers', 'sync_bytes', 'key_bytes'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('invalid native PSRDADA layout report')
    if any(type(value[k]) is not int or value[k] <= 0 for k in fields):
        raise ValueError('invalid native PSRDADA layout values')
    if not (value['readers'] <= 64 and value['transfers'] <= 64):
        raise ValueError('unsupported PSRDADA constants')
    expected = _sync_bytes(dict(max_readers=value['readers'], header_buffers=value['transfers']))
    if value['key_bytes'] != 4 or value['sync_bytes'] != expected:
        raise ValueError('PSRDADA ABI differs from ownership footprint model')


class LayoutTest(unittest.TestCase):
    def test_invalid_or_mismatched_layout_is_rejected(self):
        value = dict(readers=8, transfers=8, key_bytes=4,
                     sync_bytes=_sync_bytes(dict(max_readers=8, header_buffers=8)))
        verify_layout(value)
        for changed in (dict(value, key_bytes=8), dict(value, sync_bytes=value['sync_bytes']+8),
                        dict(value, readers=True), dict(value, transfers=65), {}, dict(value, extra=1)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verify_layout(changed)

    def test_installed_native_layout(self):
        if PROBE is None:
            self.skipTest('requires explicit compiled PSRDADA --probe; ABI NOT VERIFIED')
        result = subprocess.run([str(PROBE)], check=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=5, encoding='utf-8')
        value = json.loads(result.stdout)
        verify_layout(value)
        print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', type=Path)
    args, rest = parser.parse_known_args()
    if args.probe:
        PROBE = args.probe.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]] + rest)
