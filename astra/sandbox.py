"""Narrow, opt-in execution of the fixed test profile in a disposable container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid


MACHINE_NAME = "podman-machine-default"
TEST_IMAGE = (
    "docker.io/library/python:3.12-slim@"
    "sha256:1148dbb014615300fa7e713b2f94f550c0d1c69780c0805f84f093ceee34dd0b"
)
PROFILE = "unit_tests"
WALL_TIMEOUT_SECONDS = 180
OUTPUT_LIMIT_BYTES = 64 * 1024
PREFLIGHT = r"""
import os, resource, socket, sys, unittest

def require(condition, message):
    if not condition:
        raise RuntimeError("Sandbox preflight failed: " + message)

require(os.getuid() == 1000, "expected unprivileged UID 1000")
status = open("/proc/self/status", encoding="ascii").read()
require("CapEff:\t0000000000000000" in status, "effective capabilities are not empty")
require("NoNewPrivs:\t1" in status, "no-new-privileges is not active")
cpu_limit = resource.getrlimit(resource.RLIMIT_CPU)
process_limit = resource.getrlimit(resource.RLIMIT_NPROC)
require(cpu_limit[0] == 300 and cpu_limit[1] == 300, "CPU time limit is not 300 seconds")
require(process_limit[0] == 128 and process_limit[1] == 128, "process limit is not 128")

def must_be_read_only(path, label):
    probe = path + "/.astra-read-only-probe-" + str(os.getpid())
    try:
        with open(probe, "xb"):
            pass
    except OSError:
        return
    else:
        os.unlink(probe)
        raise RuntimeError("Sandbox preflight failed: " + label + " is writable")

must_be_read_only("/workspace", "project mount")
must_be_read_only("/etc", "container root")
require(not os.path.exists("/mnt/c"), "unexpected host drive mount")
mounts = open("/proc/mounts", encoding="ascii").read().splitlines()
tmp_mount = next((line.split() for line in mounts if len(line.split()) > 2 and line.split()[1] == "/tmp"), None)
require(tmp_mount is not None and "noexec" in tmp_mount[3].split(","), "/tmp is not a noexec tmpfs")
connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connection.settimeout(1)
network_result = connection.connect_ex(("198.51.100.1", 9))
connection.close()
require(network_result != 0, "network access unexpectedly succeeded")
print("ASTRA_SANDBOX_PREFLIGHT=passed", flush=True)

suite = unittest.TestLoader().discover("tests")
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=0).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""


class SandboxUnavailable(RuntimeError):
    """The configured local container runtime cannot safely run the test profile."""


class SandboxBusy(RuntimeError):
    """A fixed-profile sandbox run is already active."""


class SandboxNotRunning(RuntimeError):
    """There is no active sandbox process to cancel."""


def _runtime_available(name):
    if shutil.which(name):
        return True
    if name == "podman":
        local_appdata = os.environ.get("LOCALAPPDATA")
        return bool(
            local_appdata
            and (Path(local_appdata) / "Programs" / "Podman" / "podman.exe").is_file()
        )
    return False


def execution_readiness(*, permission_configured=False):
    runtimes = {
        name: "present_unverified" if _runtime_available(name) else "not_found"
        for name in ("docker", "podman", "nerdctl")
    }
    runtimes["wsl"] = (
        "present_unverified"
        if _runtime_available("wsl.exe") or _runtime_available("wsl")
        else "not_found"
    )
    if os.name == "nt":
        windows_root = os.environ.get("SystemRoot", r"C:\Windows")
        sandbox_exe = Path(windows_root) / "System32" / "WindowsSandbox.exe"
        runtimes["windows_sandbox"] = (
            "present_unverified" if sandbox_exe.is_file() else "not_found"
        )
    else:
        runtimes["windows_sandbox"] = "not_applicable"

    return {
        "enabled": False,
        "status": "unavailable",
        "permission_configured": permission_configured is True,
        "isolation_verified": False,
        "reason": (
            "No verified fixed-profile sandbox runner is configured; "
            "project commands are not executed."
        ),
        "runtimes": runtimes,
    }


class ProjectSandboxRunner:
    """Run only the repository's unittest suite using a pinned, isolated Podman image."""

    def __init__(self, repository):
        self.repository = repository
        self._run_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_process = None
        self._active_run_id = None
        self._cancel_requested = False

    @staticmethod
    def _podman_path():
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                installed = Path(local_appdata) / "Programs" / "Podman" / "podman.exe"
                if installed.is_file():
                    return str(installed)
        executable = shutil.which("podman")
        if executable:
            return executable
        raise SandboxUnavailable("Podman executable was not found")

    def _environment(self):
        environment = os.environ.copy()
        for variable in (
            "CONTAINER_HOST",
            "CONTAINER_CONNECTION",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
        ):
            environment.pop(variable, None)
        return environment

    def _machine_command(self, *arguments):
        return [self._podman_path(), *arguments]

    def _connected_command(self, *arguments):
        return [
            self._podman_path(),
            "--connection",
            MACHINE_NAME,
            *arguments,
        ]

    def _capture(self, command, timeout=10):
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=self._environment(),
        )

    def readiness(self):
        permission_configured = self.repository.permissions.execute is True
        result = {
            "enabled": False,
            "status": "unavailable",
            "permission_configured": permission_configured,
            "isolation_verified": False,
            "runner": "podman",
            "machine": MACHINE_NAME,
            "profile": PROFILE,
            "image": TEST_IMAGE,
            "resource_limits": {
                "machine_cpus": 2,
                "machine_memory_mib": 4096,
                "container_cpu_seconds": 300,
                "container_processes": 128,
                "container_memory_limit": False,
                "wall_timeout_seconds": WALL_TIMEOUT_SECONDS,
                "output_limit_bytes": OUTPUT_LIMIT_BYTES,
            },
            "reason": "",
        }
        try:
            machine_list = self._capture(
                self._machine_command("machine", "list", "--format", "json")
            )
            if machine_list.returncode:
                raise SandboxUnavailable("Could not inspect local Podman machines")
            machines = json.loads(machine_list.stdout)
            machine = next(
                (item for item in machines if item.get("Name") == MACHINE_NAME),
                None,
            )
            if (
                machine is None
                or machine.get("VMType") != "wsl"
                or machine.get("Running") is not True
                or machine.get("CPUs") != 2
                or int(machine.get("Memory", 0)) != 4 * 1024**3
            ):
                raise SandboxUnavailable(
                    "The expected running WSL machine and resource bounds were not verified"
                )
            inspected = self._capture(
                self._machine_command("machine", "inspect", MACHINE_NAME)
            )
            if inspected.returncode:
                raise SandboxUnavailable("Could not inspect Podman machine isolation")
            inspected_machine = json.loads(inspected.stdout)
            if not isinstance(inspected_machine, list) or len(inspected_machine) != 1:
                raise SandboxUnavailable("Podman returned unexpected machine inspection data")
            details = inspected_machine[0]
            resources = details.get("Resources", {})
            if (
                details.get("Name") != MACHINE_NAME
                or details.get("State") != "running"
                or details.get("Rootful") is not False
                or resources.get("CPUs") != 2
                or resources.get("Memory") != 4096
            ):
                raise SandboxUnavailable("Rootless WSL machine configuration changed")
            info = self._capture(
                self._connected_command("info", "--format", "json")
            )
            if info.returncode:
                raise SandboxUnavailable("Could not verify the rootless Podman service")
            info_data = json.loads(info.stdout)
            if info_data.get("host", {}).get("security", {}).get("rootless") is not True:
                raise SandboxUnavailable("Podman service is not verified rootless")
            image = self._capture(
                self._connected_command("image", "exists", TEST_IMAGE)
            )
            if image.returncode:
                raise SandboxUnavailable("Pinned offline Python test image is unavailable")
            if self.repository.changes.audit_store is None:
                raise SandboxUnavailable("Persistent project sandbox audit is not configured")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
            result["reason"] = f"Sandbox readiness check failed: {exc}"
            return result
        except SandboxUnavailable as exc:
            result["reason"] = str(exc)
            return result

        result["status"] = "ready" if permission_configured else "permission_required"
        result["enabled"] = permission_configured
        permission_note = (
            ""
            if permission_configured
            else "Set ASTRA_PROJECT_EXECUTE=true and restart ASTRA to opt in. "
        )
        result["reason"] = permission_note + (
            "Pinned offline runner and rootless WSL limits verified. A per-run "
            "confirmation and in-container isolation preflight are required. "
            "The 4 GiB limit applies to the VM; Podman does not provide a verified "
            "per-container memory limit in this configuration."
        )
        return result

    @staticmethod
    def _container_command(podman, root, name):
        return [
            podman,
            "--connection",
            MACHINE_NAME,
            "run",
            "--pull=never",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            "1000:1000",
            "--ulimit",
            "nproc=128:128",
            "--ulimit",
            "cpu=300:300",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=1g",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--volume",
            f"{root}:/workspace:ro",
            "--workdir",
            "/workspace",
            TEST_IMAGE,
            "python",
            "-c",
            PREFLIGHT,
        ]

    @staticmethod
    def _drain_output(stream, captured):
        total = 0
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            total += len(chunk)
            remaining = OUTPUT_LIMIT_BYTES - sum(map(len, captured))
            if remaining > 0:
                captured.append(chunk[:remaining])
        return total > OUTPUT_LIMIT_BYTES

    def cancel_tests(self, *, confirmed):
        self.repository.permissions.require_execute()
        if confirmed is not True:
            raise PermissionError("Explicit confirmation is required to cancel sandbox tests")
        with self._active_lock:
            process = self._active_process
            if process is None or process.poll() is not None:
                raise SandboxNotRunning("No sandbox test run is active")
            self._cancel_requested = True
            try:
                process.kill()
            except OSError:
                self._cancel_requested = False
                raise
            return {"requested": True, "run_id": self._active_run_id}

    def _finish_active(self, process):
        with self._active_lock:
            cancelled = (
                self._active_process is process
                and self._cancel_requested
            )
            if self._active_process is process:
                self._active_process = None
                self._active_run_id = None
                self._cancel_requested = False
            return cancelled

    def run_tests(self, *, confirmed, profile):
        self.repository.permissions.require_execute()
        if confirmed is not True:
            raise PermissionError("Explicit confirmation is required to run project tests")
        if profile != PROFILE:
            raise ValueError("Only the fixed unit_tests profile is available")
        audit_store = self.repository.changes.audit_store
        if audit_store is None:
            raise SandboxUnavailable("Persistent project sandbox audit is not configured")
        if not self._run_lock.acquire(blocking=False):
            raise SandboxBusy("A sandbox test run is already active")
        try:
            with self._active_lock:
                self._cancel_requested = False
            run_id = uuid.uuid4().hex
            started = time.monotonic()
            readiness = self.readiness()
            if not readiness["enabled"]:
                audit_store.append_sandbox_run(
                    run_id=run_id,
                    profile=profile,
                    image_digest=TEST_IMAGE.rsplit("@", 1)[1],
                    state="runner_error",
                    exit_code=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    output_truncated=False,
                )
                raise SandboxUnavailable(readiness["reason"])
            podman = self._podman_path()
            container_name = "astra-test-" + run_id[:20]
            command = self._container_command(
                podman, str(self.repository.root), container_name,
            )
            state = "runner_error"
            exit_code = None
            captured = []
            truncated = False
            cleanup_error = None
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=self._environment(),
                )
                with self._active_lock:
                    self._active_process = process
                    self._active_run_id = run_id
                reader_result = []
                reader = threading.Thread(
                    target=lambda: reader_result.append(
                        self._drain_output(process.stdout, captured)
                    ),
                    daemon=True,
                )
                reader.start()
                try:
                    exit_code = process.wait(timeout=WALL_TIMEOUT_SECONDS)
                    cancelled = self._finish_active(process)
                    reader.join(timeout=5)
                    if reader.is_alive():
                        raise SandboxUnavailable("Sandbox output stream did not close")
                    truncated = bool(reader_result[0]) if reader_result else False
                    if cancelled:
                        state = "cancelled"
                        exit_code = None
                    else:
                        state = "completed" if exit_code == 0 else "failed"
                except subprocess.TimeoutExpired:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    cancelled = self._finish_active(process)
                    reader.join(timeout=5)
                    if reader.is_alive():
                        raise SandboxUnavailable("Sandbox output stream did not close after timeout")
                    truncated = bool(reader_result[0]) if reader_result else False
                    state = "cancelled" if cancelled else "timed_out"
                    exit_code = None
            except OSError as exc:
                captured.append(f"Could not start sandbox runner: {exc}\n".encode()[:OUTPUT_LIMIT_BYTES])
            finally:
                try:
                    cleanup = self._capture(
                        self._connected_command("rm", "--force", "--ignore", container_name),
                        timeout=15,
                    )
                    cleanup_failed = cleanup.returncode != 0
                except (OSError, subprocess.SubprocessError):
                    cleanup_failed = True
                if cleanup_failed:
                    cleanup_error = "Podman could not confirm disposable-container cleanup"
                    state = "runner_error"
                duration_ms = int((time.monotonic() - started) * 1000)
                audit_store.append_sandbox_run(
                    run_id=run_id,
                    profile=profile,
                    image_digest=TEST_IMAGE.rsplit("@", 1)[1],
                    state=state,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    output_truncated=truncated,
                )
            output = b"".join(captured).decode("utf-8", errors="replace")
            if cleanup_error:
                output += ("\n" if output else "") + cleanup_error
            return {
                "run_id": run_id,
                "profile": profile,
                "state": state,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "output": output,
                "output_truncated": truncated,
                "isolation_preflight_passed": "ASTRA_SANDBOX_PREFLIGHT=passed" in output,
                "memory_limit_note": (
                    "The Podman VM is limited to 4 GiB; no per-container memory limit "
                    "is available in this rootless configuration."
                ),
            }
        finally:
            with self._active_lock:
                active_process = self._active_process
                self._active_process = None
                self._active_run_id = None
                self._cancel_requested = False
            try:
                if active_process is not None and active_process.poll() is None:
                    try:
                        active_process.kill()
                    except ProcessLookupError:
                        pass
            finally:
                self._run_lock.release()
