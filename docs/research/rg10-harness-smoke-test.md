# RG10 harness: real Docker smoke test

This test runs the Option B harness end to end in real Docker containers. The harness
prepares each output, executes it in the sandbox, and assembles a bundle. TDEA then
evaluates the bundle. Every "model output" in it is a fixed text written for the test,
and the five isolation probes are benign checks of the sandbox itself. It generates
nothing and touches no study data.

## What is verified where

| Behaviour | Stand-in tests (every CI cell) | Real Docker (this test) |
| --- | --- | --- |
| prepare_module and tree hashes agree between harness and TDEA | Yes, end to end with a local child process instead of a container (`tests/research/test_rg10_harness_bundle.py`) | Yes |
| Runner outcomes: pass, fail, collection error, unknown ID, skip, import error, import and test time-outs | Yes, runner run as a local child process (`tests/harness/test_runner.py`) | Yes, inside the container |
| Docker command line: `--network none`, read-only root and mounts, CPU, memory and PID limits, no capabilities, user 65534, no host environment | Only the command text is checked; no container starts (`tests/harness/test_sandbox.py`) | Yes, enforced by Docker and checked by the probes |
| Environment fingerprint: image digest, Python 3.11, pytest version | Parsing only, from recorded answers | Yes, read from the real image |
| Network actually unreachable from model code | No | Yes (p-network) |
| File system actually read-only outside /tmp; host folders invisible | No | Yes (p-filesystem, p-host-path) |
| Unprivileged user; host variables not passed in | Host variables only, for child processes | Yes (p-identity) |
| Memory limit stops an allocation | No | Yes (p-memory) |
| TDEA stores and excludes as FR-58 to FR-61 say | Yes, with the local stand-in (`tests/research/test_rg10_harness_smoke.py`) | Yes |

The CI job "RG10 harness smoke test (real Docker)" runs this test on GitHub's Linux runner
for every push. Running it on your own machine adds evidence for your operating system and
Docker installation.

## Before you start

* Docker: Docker Desktop on Windows or macOS (Linux containers), or Docker Engine on
  Linux. `docker version` must show both Client and Server.
* Python 3.11 and a clone of the repository at the current `main`.
* About 1 GB of disk for the image and 5 minutes.

## Steps (Linux and macOS)

```bash
cd Technical-Debt
git checkout main && git pull
python3.11 -m venv .venv-smoke
.venv-smoke/bin/python -m pip install -e .
docker build -t tdea-rg10-sandbox harness/docker
PYTHONPATH=harness .venv-smoke/bin/python -m rg10_harness smoke --image tdea-rg10-sandbox --workdir /tmp/rg10-smoke-1
```

## Steps (Windows, PowerShell)

```powershell
cd Technical-Debt
git checkout main; git pull
py -3.11 -m venv .venv-smoke
.venv-smoke\Scripts\python -m pip install -e .
docker build -t tdea-rg10-sandbox harness/docker
$env:PYTHONPATH = "harness"
.venv-smoke\Scripts\python -m rg10_harness smoke --image tdea-rg10-sandbox --workdir $env:TEMP\rg10-smoke-1
```

The work folder must not exist yet. Use a new name (`rg10-smoke-2`, ...) for each run.

## Expected result

Every line starts with PASS, and the last lines read:

```
PASS  output g1-good
...
PASS  output p-memory
PASS  every output with a module got a sandbox reply
PASS  probe left no file in the host folder
PASS  TDEA run: every output stored, the missing one excluded (exit 2)
Fingerprint: {'image_digest': 'sha256:...', 'python_version': '3.11.11', ...}
All checks passed; report: .../smoke-report.json
```

The exit status is 0 when every check passes and 1 otherwise. A probe that finds a breach
fails its `output p-...` line, and `smoke-report.json` shows the breach under
`import_errors` (for example `RuntimeError: ISOLATION BREACH: network reachable`). An
isolation failure means real outputs must not be run on that machine until it is fixed.

TDEA's part of the test writes its own key file, lint cache, lock folder and research
store inside the work folder. It never touches `~/.tdea`.

## What to send back

Send `smoke-report.json` from the work folder, together with your operating system and the
output of `docker version`. The report holds the fingerprint, each output's result and
each check. It contains your local work-folder path but no study data.

## If it does not run

| Message | Cause and fix |
| --- | --- |
| `The image tdea-rg10-sandbox is not available locally` | Run the `docker build` step first. |
| `permission denied ... docker.sock` (Linux) | Add your user to the `docker` group, or run Docker rootless. |
| `runs Python 3.12...; RG10 execution needs Python 3.11.x` | The image was changed; rebuild it from `harness/docker`. |
| `TDEA run` fails with a refusal naming Python or Pylint | The venv is not Python 3.11 with the pinned Pylint 4.1.1; recreate it as above. |
| A mount error on macOS or Windows | Allow Docker Desktop to share the temporary folder (Settings, Resources, File sharing). |
