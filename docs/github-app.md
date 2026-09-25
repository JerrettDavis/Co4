# GitHub App installation

This guide describes the live integration path implemented in the source. No real GitHub App was registered or repository modified during release verification. Complete this in a disposable staging repository before enabling real contributions.

## Register the App

Choose a public HTTPS origin such as `https://co4.example.org`. In GitHub developer settings, create a GitHub App, choosing whether it can be installed only by its owner or by other accounts. Configure:

| Setting | Value |
|---|---|
| Homepage | Your HTTPS origin |
| Callback URL | `<origin>/auth/github/callback` |
| Webhook URL | `<origin>/webhooks/github` |
| Webhook content | Active; use a cryptographically random secret of at least 32 characters |
| Repository metadata | Read |
| Repository contents | Read and write, for approved snapshot refs |
| Repository issues | Read and write, for intake and status comments |
| Repository pull requests | Read and write, for draft PR publication |
| Subscribe to events | Issues, Issue comment, Pull request, plus installation/repository installation events provided by GitHub |

The collaborator permission lookup uses metadata read access; repository administration access is not required. Confirm endpoint permission behavior in your staging installation. Broader permissions, organization membership write, Actions write, or secret access are not needed by Co4. Do not enable them casually.

Generate the App private key. Record the App ID, exact slug, OAuth client ID, and client secret. Keep private key material outside the repository. Configure `.env.example` values through your host's secret mechanism; `co4 keygen` creates the server encryption key. Production startup rejects missing settings, a short webhook secret, an unreadable key, or a non-HTTPS public URL.

Install on **selected repositories** first. The web app's production install link uses your configured slug. The repository administrator signs in through GitHub and chooses a repository from installations visible to their user authorization. Co4 verifies repository administrator permission before enrollment. The installation binding is checked by immutable IDs in incoming events, not just the repository's display name.

If browser sign-in expires or repository enrollment returns an authorization failure, sign out and sign back in. Automatic OAuth refresh-token rotation is not implemented. Installation access tokens are created as needed from the App private key.

## First contribution

Create an ordinary test repository with a known passing test suite and a small reproducible bug. Create or verify an issue with explicit acceptance criteria. In Co4, import open issues, grant a registered contributor the appropriate role/verification, and validate the issue. Start with manual allocation consent and require a separate maintainer approval.

The contributor's worker has its own Git credential. The App token is **never** distributed to contributors. A scoped token or equivalent GitHub credential must read the source and write `push_repository`. Put it in the configured Git token environment variable, not in the remote URL or source-controlled TOML. The worker uses a transient Git authorization header rather than recording the token in `.git/config`.

## Work branches and forks

The worker pushes to its explicitly approved destination, normally a fork for outside contributors or the upstream repository for authorized collaborators. In live mode the server looks up that repository, confirms it belongs to the enrolled repository's fork network, and verifies the exact work-branch SHA before accepting checkpoints. It derives the review diff from GitHub, rather than trusting an uploaded diff from the device.

**Private repositories and forks require staging validation.** A repository-scoped App token may not see a private fork without an appropriate installation/authorization strategy. The App must also be able to create an upstream snapshot ref for a commit reachable in the fork network. The current code fails closed when GitHub rejects either operation. It does not fall back to an unverified diff, request a user's broad credentials, or auto-expand permissions. For the initial private pilot, use authorized contributors pushing isolated work branches in the enrolled repository, with strict branch and Actions policies.

PR heads use App-created `co4/submission/<lease>/<sha-prefix>` branches so a late worker push cannot alter an already approved head. Restrict writes to this namespace with repository rules where feasible and detect any external head change. The alpha flags PR head drift but does not manage repository rulesets for you. Base branch movement still requires ordinary CI/review; Co4 does not auto-rebase or merge.

## Familiar GitHub interactions

`/co4 validate` queues a pending issue when posted by a registered project owner, maintainer, or triager. `/co4 recover` explicitly recovers the original contributor's eligible attempt. `/co4 approve <40-character-SHA> <64-character-review-digest>` records the same approval as the UI. Copy the command from the review page only after inspecting the whole package. Edited comments are not treated as new commands.

Co4 owns the allocation lease in its database and mirrors progress through a status comment. It does not manipulate GitHub's native assignee field in this release. A future assignee mirror must handle GitHub's collaborator restrictions without treating the assignee list as a distributed lock.

## Staging acceptance gate

Test sign-in and App installation; enrollment by an actual admin; rejection of a non-admin; signed issue ingestion; untrusted ready-label behavior; a real worker checkpoint; fork visibility where used; server-derived review diff; human approval and exact draft head; no PR before approval; App removal and device revocation; failed GitHub delivery and retry; and checkpoint takeover. Repeat the workflow with each of the three real harnesses. Compare any exposed usage with provider-visible accounting without inventing missing counters.

## Primary references

- [GitHub App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)
- [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [Choosing permissions for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/choosing-permissions-for-a-github-app)
- [Repository collaborator permissions endpoint](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user)

Reviewed 2026-09-24. Repository policies, organization restrictions, and App permissions must be verified in the actual installation.

The gateway pins GitHub REST API version `2022-11-28`, which GitHub currently lists as supported through March 10, 2028. Review response changes before upgrading the pin. [API version support](https://docs.github.com/en/rest/about-the-rest-api/api-versions).
