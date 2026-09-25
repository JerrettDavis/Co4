from __future__ import annotations
import json
import time
from pathlib import Path
from urllib.parse import quote
import httpx
import jwt
from co4.config import Settings

class GitHubError(RuntimeError):
    pass

class GitHub:
    """Narrow GitHub App gateway. No contributor harness credentials leave the device."""
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=25, follow_redirects=False)

    def request(self, method: str, path: str, token: str, **kwargs):
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        headers.update(kwargs.pop("headers", {}))
        response = self.client.request(method, "https://api.github.com" + path, headers=headers, **kwargs)
        if response.status_code >= 400:
            # Never echo tokens or raw API bodies into public audit/error data.
            raise GitHubError(f"GitHub {method} {path.split('?')[0]} returned {response.status_code}")
        return response

    def app_token(self, installation: int, repository_id: int | None = None) -> str:
        now = int(time.time())
        signed = jwt.encode({"iat": now - 30, "exp": now + 540, "iss": self.settings.app_id},
                            Path(self.settings.private_key_path).read_text(), algorithm="RS256")
        body = {"repository_ids": [repository_id]} if repository_id else {}
        return self.request("POST", f"/app/installations/{installation}/access_tokens", signed, json=body).json()["token"]

    def exchange(self, code: str) -> str:
        response = self.client.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
            json={"client_id": self.settings.client_id, "client_secret": self.settings.client_secret,
                  "code": code, "redirect_uri": self.settings.public_url + "/auth/github/callback"})
        response.raise_for_status()
        data = response.json()
        if "access_token" not in data:
            raise GitHubError("GitHub sign-in failed; retry the login flow")
        return data["access_token"]

    def request_device_code(self, scope: str) -> dict:
        """Start a device flow by POSTing to https://github.com/login/device/code.
        scope example: "read:user user:email"
        Returns the parsed JSON dict from GitHub.
        Uses form-encoded data. Accept: application/json header.
        Tries WITHOUT client_secret first; if GitHub returns invalid_client, retries WITH client_secret.
        Raises GitHubError on failure. Scrub error messages (no tokens, no body).
        """
        base = {"client_id": self.settings.client_id, "scope": scope}
        for with_secret in (False, True):
            data = dict(base)
            if with_secret:
                data["client_secret"] = self.settings.client_secret
            response = self.client.post("https://github.com/login/device/code",
                headers={"Accept": "application/json"}, data=data)
            if response.status_code >= 400:
                raise GitHubError(f"GitHub device code returned {response.status_code}")
            payload = response.json()
            if payload.get("error") != "invalid_client":
                return payload
        raise GitHubError("GitHub device code request rejected client credentials")

    def poll_device_token(self, device_code: str) -> dict:
        """Poll GitHub for an access_token.
        POSTs to https://github.com/login/oauth/access_token with grant_type=urn:ietf:params:oauth:grant-type:device_code.
        Returns a dict. Possible keys:
          {"status": "authorized", "access_token": str}
          {"status": "slow_down", "interval": int}
          {"status": "pending"}
          {"status": "expired"}        # for expired_token OR access_denied
          {"status": "denied"}         # only if error is access_denied
        Raises GitHubError on unexpected error.
        Same client_secret retry pattern as above.
        """
        base = {"client_id": self.settings.client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code}
        for with_secret in (False, True):
            data = dict(base)
            if with_secret:
                data["client_secret"] = self.settings.client_secret
            response = self.client.post("https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"}, data=data)
            if response.status_code >= 400:
                raise GitHubError(f"GitHub device poll returned {response.status_code}")
            payload = response.json()
            if payload.get("error") != "invalid_client":
                break
        else:
            raise GitHubError("GitHub device poll rejected client credentials")
        error = payload.get("error")
        if error == "authorization_pending":
            return {"status": "pending"}
        if error == "slow_down":
            return {"status": "slow_down", "interval": payload.get("interval", 5)}
        if error == "expired_token":
            return {"status": "expired"}
        if error == "access_denied":
            return {"status": "denied"}
        if error:
            raise GitHubError(f"GitHub device poll returned error {error}")
        if "access_token" in payload:
            return {"status": "authorized", "access_token": payload["access_token"]}
        return payload

    def user(self, access: str) -> dict:
        return self.request("GET", "/user", access).json()

    def installations(self, access: str) -> list:
        found = []
        for page in range(1, 101):
            data = self.request("GET", f"/user/installations?per_page=100&page={page}", access).json()["installations"]
            found.extend(data)
            if len(data) < 100:
                return found
        raise GitHubError("Installation listing exceeds supported pagination limit")

    def repositories(self, access: str, installation: int) -> list:
        found = []
        for page in range(1, 101):
            data = self.request("GET", f"/user/installations/{installation}/repositories?per_page=100&page={page}", access).json()["repositories"]
            found.extend(data)
            if len(data) < 100:
                return found
        raise GitHubError("Repository listing exceeds supported pagination limit")

    def can_manage(self, installation: int, repository_id: int, repository: str, login: str) -> bool:
        access = self.app_token(installation, repository_id)
        data = self.request("GET", f"/repos/{repository}/collaborators/{quote(login, safe='')}/permission", access).json()
        return data.get("permission") in {"admin", "maintain", "write"}

    def inspect_checkpoint(self, project: dict, checkpoint: dict, *, with_diff: bool = False) -> str:
        access = self.app_token(project["installation_id"], project["repository_id"])
        repo = checkpoint["repository"]
        info = self.request("GET", f"/repos/{repo}", access).json()
        family = {info.get("id"), info.get("parent", {}).get("id"), info.get("source", {}).get("id")}
        if project["repository_id"] not in family:
            raise GitHubError("Checkpoint must belong to the enrolled repository's fork network")
        head = self.request("GET", f"/repos/{repo}/git/ref/heads/{quote(checkpoint['branch'], safe='/')}", access).json()
        if head["object"]["sha"] != checkpoint["sha"]:
            raise GitHubError("Checkpoint branch moved; refresh evidence before continuing")
        if with_diff:
            base = quote(project["default_branch"], safe="")
            target = quote(checkpoint["sha"], safe="")
            result = self.request("GET", f"/repos/{project['repository']}/compare/{base}...{target}", access,
                headers={"Accept": "application/vnd.github.diff"})
            if len(result.content) > 2_000_000:
                raise GitHubError("Diff is too large for this release's review gate; split the work")
            return result.text
        return ""

    def status_comment(self, project: dict, number: int, work_id: str, body: str) -> None:
        access = self.app_token(project["installation_id"], project["repository_id"])
        root = f"/repos/{project['repository']}"
        marker = f"<!-- co4:work:{work_id} -->"
        # Reconcile before write, so retries do not produce a comment per heartbeat.
        for page in range(1, 101):
            comments = self.request("GET", f"{root}/issues/{number}/comments?per_page=100&page={page}", access).json()
            for comment in comments:
                if marker in (comment.get("body") or "") and comment.get("user", {}).get("login") == self.settings.app_slug + "[bot]":
                    self.request("PATCH", f"{root}/issues/comments/{comment['id']}", access, json={"body": marker + "\n" + body})
                    return
            if len(comments) < 100:
                break
        else:
            raise GitHubError("Comment pagination exceeded; refusing a duplicate status comment")
        self.request("POST", f"{root}/issues/{number}/comments", access, json={"body": marker + "\n" + body})

    def publish(self, project: dict, lease_id: str, sha: str, number: int, title: str, body: str) -> str:
        """Publish from an App-created snapshot branch, not the worker's moving work branch."""
        access = self.app_token(project["installation_id"], project["repository_id"])
        root = f"/repos/{project['repository']}"
        branch = f"co4/submission/{lease_id}/{sha[:12]}"
        head = f"{project['repository'].split('/')[0]}:{branch}"
        # Creation of a ref is idempotently reconciled, as is creation of the PR.
        try:
            ref = self.request("GET", f"{root}/git/ref/heads/{branch}", access).json()
        except GitHubError as error:
            if "returned 404" not in str(error):
                raise
            try:
                self.request("POST", f"{root}/git/refs", access, json={"ref": "refs/heads/" + branch, "sha": sha})
            except GitHubError as create_error:
                if "returned 422" not in str(create_error):
                    raise
            ref = self.request("GET", f"{root}/git/ref/heads/{branch}", access).json()
        if ref["object"]["sha"] != sha:
            raise GitHubError("Submission branch drifted from the human-approved commit")
        existing = self.request("GET", f"{root}/pulls", access, params={"head": head, "state": "all"}).json()
        if existing:
            if existing[0]["head"]["sha"] != sha:
                raise GitHubError("Existing PR does not match approved SHA")
            return existing[0]["html_url"]
        created = self.request("POST", f"{root}/pulls", access, json={
            "title": title[:240], "body": body, "head": branch, "base": project["default_branch"],
            "draft": True, "maintainer_can_modify": False}).json()
        return created["html_url"]

    def issues(self, project: dict) -> list:
        access = self.app_token(project["installation_id"], project["repository_id"])
        issues = []
        for page in range(1, 101):
            batch = self.request("GET", f"/repos/{project['repository']}/issues?state=open&per_page=100&page={page}", access).json()
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                return issues
        raise GitHubError("Issue import exceeds supported pagination limit")

class DemoGitHub:
    """Explicit offline fixture gateway. Never contacts or impersonates real GitHub."""
    def __init__(self):
        self.comments = []
        self.publications = []
    def inspect_checkpoint(self, project, checkpoint, *, with_diff=False):
        return ""  # Demo complete keeps the fixture diff supplied by the deterministic worker.
    def status_comment(self, project, number, work_id, body):
        self.comments.append((number, body))
    def publish(self, project, lease_id, sha, number, title, body):
        self.publications.append({"lease_id": lease_id, "sha": sha, "body": body})
        return f"/demo/submissions/{lease_id}"
