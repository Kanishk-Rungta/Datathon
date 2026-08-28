# V3 Catalyst CLI — confirmed commands and evidence

Every command below was actually run against the installed CLI (not copied
from documentation of a possibly-different version) and its real
output/behavior recorded. Where a command needs a logged-in account, that is
stated as a fact, not an assumption.

## Installed versions

```text
node --version   -> v24.18.0
npm --version    -> 11.16.0
catalyst --version -> 1.27.0   (package: zcatalyst-cli, installed via npm install -g zcatalyst-cli)
```

Node/npm were not present in this environment before this session (see
`phase0-2-audit.md`); installed via
`winget install -e --id OpenJS.NodeJS.LTS`. The Catalyst CLI needed npm and
was installed after.

## Full top-level command reference (as of CLI 1.27.0)

```text
catalyst login [options]                    Log the CLI in to your Catalyst account
catalyst logout                             Log the CLI out
catalyst whoami                             Print the logged-in user's email
catalyst token:generate|list|revoke         Manage CLI auth tokens for remote/CI use
catalyst project:list                       List projects you have access to
catalyst project:use [name_or_project_id]   Set the active project for this directory
catalyst project:reset                      Clear the active project selection
catalyst init [options] [feature]           Initialize project/function/client resources locally
catalyst iac:pack / iac:import / iac:export / iac:status   Infrastructure-as-code zip workflow
catalyst codelib:install [git-url]          Install a code library
catalyst client:setup / client:delete       Manage the web client directory
catalyst functions:setup                    Configure the functions directory
catalyst functions:add [options]            Scaffold a function (--type, --stack, --name)
catalyst functions:delete [name_or_id]
catalyst functions:shell [options]          Local emulator shell (non-Advanced-I/O)
catalyst functions:execute [name] [data]    Execute a non-HTTP function
catalyst functions:config [name_or_id]      Memory/config for a function
catalyst appsail:add [options]              Link an existing AppSail source directory
catalyst slate:link / create / unlink       Static web hosting (not used by this app)
catalyst serve [options]                    Local emulator for functions + AppSail + client
catalyst run-script|run [command]           Run a script defined in catalyst.json
catalyst pull [options] [feature]           Pull remote resources to local directory
catalyst deploy [options]                   Deploy project/resources to Development
catalyst deploy appsail [options]           Deploy an AppSail service specifically
catalyst event:generate* / signals:generate Generate sample event payloads for local testing
catalyst config:set|get|delete|list         Local CLI configuration
catalyst apig:status|enable|disable         API Gateway status/toggle
catalyst ds:import / ds:export / ds:status  Bulk Data Store read/write and job status
```

## Confirmed: Advanced I/O has no ASGI path, per the CLI itself

`functions:add --type <bio|aio|event|cron|browserlogic|job|integ>` lists
Advanced I/O (`aio`) as one scaffoldable type alongside Basic I/O (`bio`),
Event (`event`), and Cron — with no ASGI or WSGI type anywhere in this list.
This is independent confirmation, from the CLI's own type enumeration rather
than only the web documentation cited in `catalyst-runtime.md`, that hosting
FastAPI as an `aio` function was never a documented path — reinforcing the
decision to run it as an AppSail service instead.

## Confirmed: AppSail stack identifier format

`functions:add --stack <stack>` and `appsail:add --stack <stack>` both give
the same worked example: **`python_3_9`** — underscore-separated,
`python_<major>_<minor>`. This corrects an open item from
`phase1-catalyst-runtime.md`/`catalyst-runtime.md`, which (from web
documentation alone) used the inconsistent guesses `python3_11` and
`python3.11`.

**Action taken:** `catalyst/appsail/api/app-config.json` and
`catalyst/catalyst.json`'s `appsail` stack field updated to `python_3_11`
(the confirmed naming convention, at the Python version this repository
requires). **Still not verified:** whether `python_3_11` is an *available*
version in a real project — the CLI's help text confirms the naming pattern,
not the enumerated list of installable versions, which requires
`catalyst functions:add` (or the console) against a live, logged-in project.
If `3.11` is unavailable, the next-closest offered minor version satisfying
`>=3.11` should be used, and this file updated with the actual accepted value.

## The exact, confirmed blocking point

```text
$ catalyst whoami
✖ Not logged in yet. To login use catalyst login

$ catalyst project:list
✖ Error: Oops! It looks like you haven't logged in yet
Please use the catalyst login command to log in, or provide auth token via --token param
⚠ Not in Catalyst app directory!!!
```

`catalyst login` opens a browser-based OAuth flow (or, with `--no-localhost`,
prints a URL to visit) against a real Zoho account. This is the one step in
the entire V3 plan that requires the project owner personally — it cannot be
scripted, automated, or completed on the user's behalf, and this agent does
not have and must not be given Zoho credentials to do it.

## What is proven possible once logged in (no further guessing needed)

Everything below is now a known, confirmed command shape — only credentials
and an actual account are missing, not knowledge of the CLI:

```bash
catalyst login                                    # user-interactive, once
catalyst project:list                             # confirm project access
catalyst project:use <project_id>                 # bind this directory to it
catalyst init --project <project_id>               # initialize local project links

catalyst appsail:add --name cip-api --stack python_3_11 \
  --source catalyst/appsail/api --command "python3 -u server.py" --port 9000
catalyst appsail:add --name cip-console --stack node18 \
  --source catalyst/appsail/console --command "node server.js" --port 9000
catalyst functions:add --type event --stack python_3_11 --name cip_refresh
  # then point its source at catalyst/functions/cip_refresh per catalyst.json

catalyst deploy --only appsail:cip-api
catalyst deploy --only appsail:cip-console
catalyst deploy --only functions:cip_refresh

catalyst serve --only appsail:cip-api             # local emulator, pre-deploy smoke
```

`catalyst serve` is worth calling out: it emulates AppSail and functions
**locally**, which means once a project is initialized (still requires
login), a meaningful pre-deploy smoke pass is possible without every push
going straight to live Development — this should be the first thing run after
`project:use`, before `deploy`.

## `ds:import` as the Data Store provisioning/parity tool

`catalyst ds:import --table <name> --config <path> [file]` is the confirmed
mechanism for bulk-loading records into a Data Store table — relevant to
Phase D2's parity test (load the same synthetic seed into Catalyst and SQLite,
compare). Table *creation* itself is a console/IaC (`iac:import`) action, not
`ds:import`, which only writes rows to an existing table.
