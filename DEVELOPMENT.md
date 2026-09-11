# Development

## Scope and prerequisites

Recordings, sessions, databases and pairing tokens are private local data. Use isolated MEMORYOS_DATA for checks. Tests can require models, devices or providers; inspect fixtures before running the full suite. requirements.txt uses ranges, not a reproducible lock.

Commands below run from the repository root unless a `cd` is shown. Install the runtime required by the package manifest. For Node projects, preserve package-lock.json with `npm ci`; Quiet Orbit uses its pnpm lockfile; the voice service uses uv.lock. Installation may require network access.

## Commands

```text
python -m venv .venv
# Windows PowerShell
.venv/Scripts/python.exe -m pip install -r requirements.txt
# See existing README for server/device setup and select isolated tests.
```

These commands come from the current manifests; they are not a claim that all checks passed during documentation setup. Check LOG.md for dated results. Builds may require configuration, downloads or external services.

## Configuration

Configuration shape: `.env.example`. See the comments about which process loads these variables.

Use dummy examples for configuration shape only; supply real credentials outside version control. Client-prefixed variables are public. Export environment variables explicitly when the app has no dotenv loader.

## Source map

server.py: FastAPI service; memory.py: indexing and retrieval; enrich.py: enrichment; static/: browser client; desktop/: Electron shell; android/: Android client; tests/: service and capture coverage.

## Next verification

Run the capture-to-citation flow with disposable recordings and verify restart, recovery and citation accuracy.

## Repository check

`python scripts/check_repository.py` checks the documentation contract and tracked local-secret filenames. GitHub Actions runs this baseline check; it does not certify runtime or deployment readiness.

## PR scope note

This PR contains the repository setup and any explicitly listed consolidation changes. Other local feature work was excluded. LOG.md preserves local historical observations; those observations do not establish that this PR includes or verifies every feature mentioned there. Consult the PR description for its exact scope.
