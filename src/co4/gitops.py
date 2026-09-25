from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import sys
from co4.security import PATTERNS

class GitError(RuntimeError):
    pass

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

class Repository:
    def __init__(self, path: Path, *, token_env="CO4_GIT_TOKEN", demo=False):
        self.path, self.token_env, self.demo = path, token_env, demo

    def command(self, *args: str, cwd=None, check=True, timeout=120, stdin: str | None = None) -> str:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        # Explicitly disable hooks and global credential helpers for orchestration commands.
        cmd = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "credential.helper=",
               "-c", "commit.gpgsign=false", "-c", "status.renames=false", "-c", "protocol.file.allow=" + ("always" if self.demo else "never"),
               "-c", "protocol.ext.allow=never", "-c", "user.name=Co4 contributor", "-c", "user.email=contributor@users.noreply.github.com"]
        access = os.getenv(self.token_env, "")
        if access:
            import base64
            header = "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + access).encode()).decode()
            # Environment-only Git config; never put credentials in argv, .git/config or remote URLs.
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0=header)
        result = subprocess.run(cmd + list(args), cwd=cwd or self.path, env=env,
                                capture_output=True, text=True, timeout=timeout, input=stdin)
        if check and result.returncode:
            # stderr can contain user paths and tokens. Keep it local to the encrypted trace.
            raise GitError(f"git {args[0] if args else ''} failed (exit {result.returncode}): {result.stderr[-1500:]}")
        return result.stdout.rstrip("\n")

    def prepare(self, upstream: str, push_repository: str, branch: str, default_branch: str,
                *, local_source: str | None = None, handover: dict | None = None):
        if not REPO_PATTERN.fullmatch(upstream) or not REPO_PATTERN.fullmatch(push_repository):
            raise GitError("Invalid GitHub repository name")
        if not branch.startswith("co4/work/"):
            raise GitError("Work branch is outside the allocated namespace")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        source = local_source if self.demo and local_source else f"https://github.com/{upstream}.git"
        if self.path.exists():
            raise GitError("Unrecognized existing workspace; refusing to overwrite it")
        self.command("clone", "--no-recurse-submodules", "--branch", default_branch, "--", source, str(self.path), cwd=self.path.parent)
        self.command("checkout", "-b", branch)
        # Worktrees preserve replayable handover history but start fresh evidence on upstream baseline.
        if handover and not self.demo:
            repo, sha = handover["repository"], handover["sha"]
            if not REPO_PATTERN.fullmatch(repo) or not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise GitError("Invalid handover checkpoint")
            self.command("fetch", "--no-tags", "--", f"https://github.com/{repo}.git", sha)
            self.command("branch", "co4-handover", "FETCH_HEAD")
            private = self.path / ".co4-private"
            private.mkdir(exist_ok=True)
            (private / "handover.diff").write_text(self.command("diff", "HEAD...co4-handover"), encoding="utf-8")
        if not self.demo:
            self.command("remote", "add", "co4-push", f"https://github.com/{push_repository}.git")
        exclude = self.path / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        with exclude.open("a") as f:
            f.write("\n.co4-private/\n.co4/\n.env\n.env.*\n__pycache__/\n.pytest_cache/\n")

    def sha(self):
        return self.command("rev-parse", "HEAD")

    def checkpoint(self, branch: str, message: str, push_repository: str) -> dict:
        current = self.command("branch", "--show-current")
        if current != branch:
            raise GitError("Harness changed the work branch; refusing to commit or push")
        # Keep credentials, hooks, symlinks and generated binaries out of auto-checkpoint publication.
        paths = self.command("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
        for item in paths:
            if not item:
                continue
            rel = item[3:]
            if rel.startswith((".co4/", ".co4-private/")):
                continue
            p = self.path / rel
            if p.is_symlink():
                raise GitError("Changed symlinks require manual review before checkpointing")
            lower = rel.lower()
            if any(x in lower.split("/") for x in (".ssh", ".aws", ".azure")) or lower.endswith((".pem", ".p12", ".key")) or Path(lower).name.startswith(".env"):
                raise GitError("Potential secret file changed; checkpoint publication stopped")
        # Excluded directory pathspecs can make Git error when an ignored directory exists.
        # Enumerate only real candidate paths and send literal NUL-separated pathspecs
        # through stdin, avoiding argv limits and pathspec injection from filenames.
        names = self.command("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
        names = sorted({name for name in names if name and name.split("/")[0] not in {".co4", ".co4-private"}})
        if names:
            self.command("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul",
                         stdin="".join(":(literal)" + name + "\0" for name in names))
        if self.command("diff", "--cached", "--name-only", "--", ".co4-private", ".co4"):
            raise GitError("Private coordinator files were staged; checkpoint publication stopped")
        patch = self.command("diff", "--cached", "--no-ext-diff", "--no-textconv")
        if len(patch.encode()) > 2_000_000:
            raise GitError("Checkpoint exceeds 2 MB review limit; split the contribution")
        if "GIT binary patch" in patch or "Binary files " in patch:
            raise GitError("Binary changes require a manual contribution workflow")
        if any(p.search(patch) for p in PATTERNS):
            raise GitError("Potential secret detected; checkpoint push stopped for manual inspection")
        if patch:
            self.command("commit", "-m", message)
        if not self.demo:
            # Explicit refspec, no force, no push.default or harness-controlled remote name.
            self.command("push", "--", f"https://github.com/{push_repository}.git", f"HEAD:refs/heads/{branch}")
        return {"repository": push_repository, "branch": branch, "sha": self.sha()}

    def diff(self, baseline: str) -> str:
        return self.command("diff", "--no-ext-diff", "--no-textconv", baseline + "..HEAD")
