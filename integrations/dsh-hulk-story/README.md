# dsh-hulk-story

Requires DeepSeek Harness and the `hulk-story` executable on `PATH`. Install the bundle into an explicit profile with `dsh plugin --profile <profile> add <package>` and inspect the result with `dsh --profile <profile> --dump-config`.

The bundle mounts the DSH MCP client in stdio mode and a small Cordis plugin that registers the packaged `hulk-story` skill through `ctx.skills.register`. It was authored against the DSH source contract at commit `5dda764ed3aa172535a7967b06ff95d9cbfe536a`. Runtime compatibility has not been tested because DSH is unavailable in the development environment.

The workbench can be mounted as a local WebView surface because it has no CDN, remote font, or browser-side credential dependency. Start the Core with DSH's local Web origin explicitly allowlisted:

```bash
HULK_FRAME_ANCESTORS=http://127.0.0.1:3080 hulk-story --root ./books serve --port 8765
```

The default allows only same-origin framing. `HULK_FRAME_ANCESTORS` accepts a comma-separated list of complete HTTP(S) origins and rejects paths and wildcards. The DSH plugin still needs a target-Harness integration test to wire its own WebView lifecycle to the local Core URL; current DSH public plugin guidance documents capability registration but not a stable external-panel registration API, so this package does not claim that runtime bridge exists yet.
