from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def get_status() -> str:
    result = run(['git', 'status', '--porcelain'])
    if result.returncode != 0:
        return ''
    return result.stdout.strip()


def should_skip() -> bool:
    return os.path.exists('.disable_autocommit')


def main() -> None:
    interval = int(os.getenv('AUTO_COMMIT_INTERVAL_SECONDS', '10'))
    push_enabled = os.getenv('AUTO_COMMIT_PUSH', 'true').lower() in ('1', 'true', 'yes')
    msg_tpl = os.getenv('AUTO_COMMIT_MESSAGE', 'auto: {ts}')

    print('Auto-commit daemon started. Create .disable_autocommit to pause.')
    while True:
        if should_skip():
            time.sleep(interval)
            continue

        status = get_status()
        if not status:
            time.sleep(interval)
            continue

        run(['git', 'add', '-A'])
        msg = msg_tpl.format(ts=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        commit = run(['git', 'commit', '-m', msg])
        if commit.returncode != 0:
            time.sleep(interval)
            continue

        if push_enabled:
            run(['git', 'push'])

        time.sleep(interval)


if __name__ == '__main__':
    main()
