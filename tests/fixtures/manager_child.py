"""P6 helper: a process that starts a fake server through ServerManager, prints its pid, then idles."""

import sys
import time
from pathlib import Path

import helpers  # tests/ is on PYTHONPATH (set by the P6 test)

from llama_launcher.server import ServerManager, State

tmp = Path(sys.argv[1])
m = ServerManager(tmp / "logs", tmp / "cwd")
m.start(helpers.spec(tmp))
helpers.wait_for(lambda: m.status.state is State.RUNNING, 20)
print(m.status.pid, flush=True)
time.sleep(600)
