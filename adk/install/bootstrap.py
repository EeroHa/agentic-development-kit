# bootstrap.py: the first verified read of the installer one-liner. Standard library only.
# Run as: /usr/bin/python3 -I -B -E -s bootstrap.py <repo> <dest> <40-hex-commit>
# Before any git command it refuses a .git that is not a plain folder, or that holds commondir,
# info/grafts, shallow, a non-empty alternates file or refs/replace entries: git would run config
# or hooks, or read objects, from a place Claude Code controls. Git then runs with no system or
# global config, no pager, no fsmonitor and no hooks.
# It reads the commit, its tree, the adk and adk/install trees and the blob adk/install/export.py
# through ONE `git cat-file --batch` process, each object once. It recomputes every object id as
# sha1("<type> <len>" + NUL + data) and refuses on any difference. Only then does it write
# those verified bytes to <dest>/export.py and run them: export.py export <repo> <dest>/src <commit>.
import hashlib, os, re, shutil, subprocess, sys

def refuse(why):
    sys.exit("bootstrap refused: " + why)

def tree_entries(data):
    out, i = {}, 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        name = data[sp + 1:nul]
        if name in out or len(data[nul + 1:nul + 21]) != 20:
            raise ValueError("a tree object is malformed")
        out[name] = (data[i:sp], data[nul + 1:nul + 21].hex())
        i = nul + 21
    return out

def refuse_redirects(git_dir):
    if os.path.islink(git_dir) or not os.path.isdir(git_dir):
        refuse(git_dir + " is not a plain folder")
    for rel in ("commondir", "info/grafts", "shallow"):
        if os.path.lexists(os.path.join(git_dir, rel)):
            refuse(rel + " exists in " + git_dir)
    alt = os.path.join(git_dir, "objects", "info", "alternates")
    if os.path.lexists(alt) and (os.path.islink(alt) or not os.path.isfile(alt) or os.path.getsize(alt) != 0):
        refuse(alt + " is not an empty file")
    replace = os.path.join(git_dir, "refs", "replace")
    if os.path.lexists(replace) and (os.path.islink(replace) or not os.path.isdir(replace) or os.listdir(replace)):
        refuse(replace + " has entries")
    try:
        with open(os.path.join(git_dir, "packed-refs"), "rb") as f:
            if b"refs/replace/" in f.read():
                refuse("packed-refs has refs/replace entries")
    except FileNotFoundError:
        pass

def main(repo, dest, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        refuse("the commit must be 40 lowercase hex characters")
    git_dir = os.path.join(repo, ".git")
    refuse_redirects(git_dir)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_DIR=git_dir, GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_NO_LAZY_FETCH="1")
    cmd = ["git", "--no-pager", "-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    try:
        session = subprocess.Popen(cmd + ["cat-file", "--batch"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=repo)
    except OSError as err:
        refuse("cannot start git: %s" % err)

    def read(kind, oid):
        session.stdin.write(oid.encode() + b"\n")
        session.stdin.flush()
        head = session.stdout.readline().split()
        if head[:2] != [oid.encode(), kind.encode()] or len(head) != 3 or not head[2].isdigit():
            refuse("cannot read %s %s" % (kind, oid))
        data = session.stdout.read(int(head[2]))
        if len(data) != int(head[2]) or session.stdout.read(1) != b"\n":
            refuse("%s %s is cut short" % (kind, oid))
        if hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).hexdigest() != oid:
            refuse("%s %s does not match its own hash (the object was replaced)" % (kind, oid))
        return data

    try:
        first = read("commit", commit).split(b"\n", 1)[0]
        if not re.fullmatch(rb"tree [0-9a-f]{40}", first):
            refuse("the commit does not start with a tree line")
        oid = first[5:].decode()
        for name, want in ((b"adk", b"40000"), (b"install", b"40000"), (b"export.py", None)):
            mode, oid = tree_entries(read("tree", oid)).get(name, (b"", ""))
            if mode != want and not (want is None and mode in (b"100644", b"100755")):
                refuse("%s is not a plain entry of the commit" % name.decode())
        code = read("blob", oid)
    except (ValueError, OSError) as err:
        refuse(str(err))
    finally:
        session.kill()
        session.wait()

    if os.path.basename(dest) != "install-" + commit or os.path.islink(dest) or os.path.islink(os.path.dirname(dest)):
        refuse("the destination must be a real folder named install-<hash>")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    os.mkdir(dest, 0o700)
    target = os.path.join(dest, "export.py")
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as f:
        f.write(code)
    done = subprocess.run([sys.executable, "-I", "-B", "-E", "-s", target, "export", repo,
                           os.path.join(dest, "src"), commit], stdin=subprocess.DEVNULL)
    sys.exit(done.returncode)

if len(sys.argv) != 4:
    refuse("usage: bootstrap.py <repo> <dest> <commit>")
main(*sys.argv[1:])
