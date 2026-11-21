"""P6 helper: a process that starts a fake server through ServerManager, prints its pid, then idles."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "integration"))

import helpers  # noqa: E402
from llama_launcher.server import ServerManager, State  # noqa: E402

tmp = Path(sys.argv[1])
m = ServerManager(tmp / "logs", tmp / "cwd")
m.start(helpers.spec(tmp))
helpers.wait_for(lambda: m.status.state is State.RUNNING, 20)
print(m.status.pid, flush=True)
time.sleep(600)
