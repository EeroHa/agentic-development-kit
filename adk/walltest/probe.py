"""Runs inside the sandbox, started by one Bash call: python3 probe.py

Tries to read, write, connect and run things, and prints one JSON object that maps
attempt name to {"ok": bool, "leaked": bool, "detail": str}. "ok" means the attempt
succeeded; "leaked" means the bytes read contained the canary prefix. It never prints
file contents. Its settings come from probe-config.json next to it.
"""
import json
import os
import socket
import subprocess
import sys

# Built in two parts so this file never matches the grep it runs.
PREFIX = b"ADK-" + b"CANARY-"
REPORT = {}


def record(name, ok, leaked=False, detail=""):
    REPORT[name] = {"ok": bool(ok), "leaked": bool(leaked), "detail": str(detail)[:200]}


def run(cmd, timeout=30):
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except Exception as e:
        return None, b"", type(e).__name__.encode()


def err(e):
    return f"{type(e).__name__}: {getattr(e, 'strerror', None) or ''}".strip()


def read_open(name, path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception as e:
        record(name, False, False, err(e))
    else:
        record(name, True, PREFIX in data, f"read {len(data)} bytes")


def read_cat(name, path):
    rc, out, errb = run(["cat", path])
    if rc == 0:
        record(name, True, PREFIX in out, f"read {len(out)} bytes")
    else:
        record(name, False, PREFIX in out, f"rc={rc}")


def grep(name, target):
    rc, out, _ = run(["grep", "-rl", PREFIX.decode(), target])
    files = [x for x in out.decode("utf-8", "replace").splitlines() if x]
    record(name, rc == 0, bool(files), f"rc={rc} matching files={len(files)}")


def try_write(name, path, keep=False):
    created_dir = None
    try:
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
            created_dir = parent
        with open(path, "x") as fh:
            fh.write("adk write probe\n")
    except Exception as e:
        record(name, False, False, err(e))
    else:
        if keep:
            # The runner checks on the host that the file did not persist, then removes it.
            record(name, True, False, "created inside the sandbox")
        else:
            os.remove(path)
            record(name, True, False, "created and removed")
    finally:
        if created_dir and not keep:
            try:
                os.rmdir(created_dir)
            except OSError:
                pass


def command(name, cmd, first_line=True):
    rc, out, errb = run(cmd)
    text = out.decode("utf-8", "replace").strip().splitlines()
    record(name, rc == 0, False, f"rc={rc} {text[0] if text and first_line else ''}".strip())


def http_code(name, url):
    rc, out, errb = run(["curl", "-sS", "-m", "10", "-o", "/dev/null", "-w", "%{http_code}", url], 20)
    code = out.decode().strip()
    record(name, code[:1] in ("2", "3"), False,
           f"code={code} rc={rc} {errb.decode('utf-8', 'replace').strip()[:120]}")


def connect_tcp(name, port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except Exception as e:
        record(name, False, False, err(e))
    else:
        record(name, True, False, f"connected to 127.0.0.1:{port}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, "probe-config.json")))
    project = cfg["project"]
    os.chdir(project)

    for cname, path in cfg["canaries"].items():
        read_open(f"read-open:{cname}", path)
        read_cat(f"read-cat:{cname}", path)
    grep("grep:canary-dir", cfg["canary_dir"])
    grep("grep:project", ".")

    for var in ("ADK_CANARY_TOKEN", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(var)
        record(f"env:{var}", value is not None, bool(value) and PREFIX.decode() in value,
               "present" if value is not None else "absent")

    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(3)
        s.connect("/var/run/docker.sock")
        s.close()
        record("socket:docker", True, False, "connected")
    except Exception as e:
        record("socket:docker", False, False, err(e))
    rc, _, _ = run(["ssh-add", "-l"])
    record("ssh-add", rc == 0, False, f"rc={rc}")

    for tname, path in cfg["write_targets"].items():
        try_write(tname, path, keep=tname in ("write:home", "write:home-claude"))

    for cname, path in (("control:read-readme", "README.md"), ("control:read-env-agent", "agent.env")):
        try:
            text = open(path, encoding="utf-8").read()
            record(cname, cfg["control"] in text, False, "control found" if cfg["control"] in text else "control missing")
        except Exception as e:
            record(cname, False, False, err(e))
    try_write_scratch = os.path.join(project, "scratch.txt")
    try:
        with open(try_write_scratch, "w") as fh:
            fh.write("scratch\n")
        os.remove(try_write_scratch)
        record("control:write-scratch", True, False, "written and removed")
    except Exception as e:
        record("control:write-scratch", False, False, err(e))
    command("control:node", ["node", "--version"])
    command("control:npm", ["npm", "--version"])
    command("control:pnpm", ["pnpm", "--version"])
    rc, out, _ = run(["git", "status", "--short"])
    record("control:git-status", rc == 0, False, f"rc={rc}")
    rc, out, _ = run(["python3", "-c", "print(1)"])
    record("control:python3", rc == 0 and out.strip() == b"1", False, f"rc={rc}")

    http_code("net:registry", "https://registry.npmjs.org/")
    http_code("net:example", "https://example.com/")

    try:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        connect_tcp("port:inside", server.getsockname()[1])
        server.close()
    except Exception as e:
        record("port:inside", False, False, err(e))
    connect_tcp("port:outside", cfg["outside_port"])

    try:
        names = os.listdir(cfg["projects_dir"])
        record("measure:projects-listdir", True, False, f"{len(names)} entries")
    except Exception as e:
        record("measure:projects-listdir", False, False, err(e))
    read_open("measure:repo-readme", cfg["repo_readme"])
    read_open("measure:sibling-file", cfg["sibling_file"])

    json.dump(REPORT, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
