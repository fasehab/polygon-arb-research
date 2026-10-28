# Owner publication runbook (VPS → public GitHub)

1. Download/extract release ZIP **onto the VPS that has original sources**. Keep source backups separate; never copy source via blind `git add .` from `/root`.
2. In the extracted `polygon-arb-open-source-kit` folder:

```bash
python3 tools/import_vps_sources.py        # preview file locations
python3 tools/import_vps_sources.py --apply
python3 tools/release_guard.py
python3 -m unittest discover -s tests -v
```

3. Confirm original compiler tests independently (if source was imported):

```bash
if [ -f runtime/rust/Cargo.toml ]; then (cd runtime/rust && cargo test); fi
if [ -f contracts/balancer/foundry.toml ]; then (cd contracts/balancer && forge test); fi
```

4. Manually review files, license rights and source integrity, not just scanner output. Never include `.env`, hardcoded RPC endpoint tokens, API keys, private keys, wallet addresses that you regard as private, local logs, generated output JSON, archived artifacts, binary blobs or Git history from prior experiments. Do not publish results claiming profit or production readiness.
5. Initialize a *new history* so past accidental secrets are not inherited:

```bash
git init -b main
git add .github .gitignore .env.example README.md LICENSE SECURITY.md CONTRIBUTING.md docs adapters research runtime contracts tools tests
# Inspect EXACT staged content; abort if anything sensitive appears.
git diff --cached --stat
git diff --cached --check
python3 tools/release_guard.py
git commit -m "Open research release: Polygon arbitrage sources and discovery toolkit"
```

6. Authenticate GitHub CLI in your terminal (not by pasting GitHub tokens in chat), then create a public repository:

```bash
gh auth login
gh repo create polygon-arb-research --public --source=. --remote=origin --push
```

`gh repo create --source=. --public --remote=origin --push` is documented by GitHub CLI. Choose the name/owner you want. If you have already connected GitHub inside ChatGPT, publication can be handled through the connected app instead.

7. In GitHub repository Settings → Code security, check secret scanning / push protection. Review README for accuracy, verify Actions, then tag a release only when the source inventory and tests are clean.

**Never assume an imported tree is immediately plug-and-play.** Normal infrastructure deployment and private orderflow are deliberately left to operators. This repo does not enable live signing or relay submission.
