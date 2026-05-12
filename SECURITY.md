# Security Policy

## Reporting vulnerabilities

Found a security issue? Please **do not** open a public issue. Instead, email
the maintainer:

- Harry Vass (hvass97@gmail.com)

Include:

- A description of the vulnerability
- Reproduction steps or proof-of-concept
- The version of apra-mcp affected (`server_version` from any DataResponse)
- Your suggested fix, if any

Expect an initial reply within 72 hours.

## Scope

apra-mcp:

- Does **not** authenticate against any APRA system. All data is fetched
  from public apra.gov.au URLs over HTTPS.
- Stores no user data. The only on-disk state is the SQLite HTTP cache at
  `~/.apra-mcp/cache.db`, which holds APRA's public XLSX bytes + landing
  page HTML.
- Refuses to fetch any URL outside `apra.gov.au` and `www.apra.gov.au`
  (host-pinned at the client boundary, defense-in-depth against scraper or
  seed-manifest corruption).
- Refuses `file://`, `javascript:`, `data:`, and other non-HTTP schemes
  at the same boundary.
- Validates every MCP tool input with explicit type and shape guards; a
  malformed `dataset_id` like `"../../etc/passwd"` is rejected at the
  regex layer, never reaching the file system.

## Supply-chain notes

- Dependencies are pinned to minimum versions in `pyproject.toml`. Anyone
  uneasy with this should pin tighter in their lockfile.
- The package wheel contains no compiled code — only Python source, YAMLs,
  and a JSON seed manifest. Audit via `unzip -l apra_mcp-0.1.0-py3-none-any.whl`.
- CI runs CodeQL on every push (`.github/workflows/codeql.yml`); findings
  surface under the repo's Security tab.
