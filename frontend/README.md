# Optional modern Predbat UI

This frontend was ported from [Calvin's modern UI repository](https://github.com/calvind80/predbat-modern-ui-test) at commit `8eb8cb6853c126df287caad7120190d61054976e`.
The Predbat backend remains based on PlainSeer's upstream-equivalent v9.3.3 main at `57ec7bf1806409950bacb56438b8b177753c357e`.

## Development and packaging

Use Node.js 20.19+ or 22.12+, npm and Python 3.
From this directory run:

```sh
npm ci
npm test
npm run lint
npm run bundle
```

The last command checks TypeScript, builds the production frontend and creates `apps/predbat/frontend.zip`.
Archive entries are sorted with fixed timestamps and permissions, so identical build files produce identical archives.
Commit the source, lockfile and rebuilt archive together when changing the UI.
Monaco is pinned to 0.54.0 for compatibility with the YAML worker. DOMPurify is pinned to patched version 3.4.16, with a Vite alias that replaces Monaco's embedded sanitizer in the production build. Recheck the alias, worker compatibility and `npm audit` when updating editor dependencies; a dependency override alone does not replace Monaco's embedded copy.

## Backend regressions

From `coverage/`, with the normal Predbat test dependencies installed:

```sh
python ../apps/predbat/unit_test.py --quick -k web_
python ../apps/predbat/unit_test.py --test modern_ui --test manual_api --test manual_overrides --test plan_why_reason --test plan_json_rate_adjust --test component_health_status --test multi_car_load
```

The `modern_ui` test uses temporary configuration files and mocked Home Assistant controls.
It checks both UI modes, every bundled asset, control types, power-flow signs, read-only plan fetching, archive traversal and editor backups/stale saves.

## Keeping up with upstream

Keep `main` tracking upstream and bring its updates into `feature/modern-ui`.
Review conflicts in `web.py`, `output.py`, `predbat.py` and `config.py` individually.
Retain upstream control, prediction, update and inverter behavior; adapt the UI APIs to it.
Rebuild the bundle, run frontend and backend checks, and review the complete diff against `main` before testing an updated branch.
Do not replace current backend files with files from Calvin's older repository.

Calvin's changes to status-sensor icons and manual-override replanning were deliberately excluded from this port.
Only display metadata was added to output publishing and configuration; existing configuration defaults and inverter definitions are unchanged.

## Current limitations

The UI is beta software.
The Apps settings page embeds the legacy Apps editor in an iframe and adds styling, grouping, descriptions and header save/discard controls.
Its editing, staging and saving still depend on the legacy page's markup and scripts; nested lists and narrow screens need browser testing against the intended installation.
The separate Apps YAML editor uses Monaco with schema hints and entity suggestions, and checks for a stale file before saving with a backup.
The schema is an editor aid, not a complete replacement for Predbat's own configuration validation.
Annual and Chat also embed their existing pages.
The production build reports large editor chunks, and frontend lint reports inherited polling-dependency and render-time clock warnings.
Local tests do not establish compatibility with every Home Assistant ingress/proxy or inverter installation.
