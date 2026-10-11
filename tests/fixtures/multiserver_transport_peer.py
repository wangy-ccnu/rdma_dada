"""JSON-only transport test peer; no supervisor or observation operations.

Only echoes input, emits deterministic protocol failures, or waits for local
transport timeout tests. It creates no files, resources, services or children.
"""
import json
import os
import sys
import time

LIMIT = 1024 * 1024


def main():
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        return 7
    command = json.loads(raw.decode('utf-8'))
    mode = command.get('probe_mode', 'echo')
    if mode == 'fail':
        sys.stderr.write('deterministic JSON-only probe failure\n')
        return 7
    if mode == 'exit255':
        return 255
    if mode == 'sleep':
        time.sleep(30)
    elif mode in ('stdout_overflow', 'stderr_overflow'):
        fd = 1 if mode == 'stdout_overflow' else 2
        for _ in range(257):
            os.write(fd, b'x' * 4096)
        time.sleep(30)
    elif mode == 'raw':
        sys.stdout.buffer.write(bytes.fromhex(command['raw_hex']))
    elif mode == 'wire':
        print(json.dumps({'wire_hex': raw.hex()}))
    elif mode == 'request_length':
        print(json.dumps({'received_bytes': len(raw)}))
    elif mode == 'stderr_boundary':
        for _ in range(256):
            os.write(2, b'x' * 4096)
        print('{}')
    else:
        print(json.dumps(command, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
