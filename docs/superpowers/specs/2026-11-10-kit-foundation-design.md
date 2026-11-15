# Agentic Dev Kit: Foundation Design

Date: 2026-11-10
Status: draft, awaiting approval
Source brief: `agentic-dev-kit-brief.md` (Nov 2026 planning brief)

This is the first of several designs. It covers the foundation only: how the
kit is packaged, how it is installed on the machine, how a project is
initialized, the wall that keeps Claude away from secrets and system config,
the GitHub door, and notifications. Later designs build on it (see "Later
projects").

Decisions are marked **[Eero]** when the owner made them and **[Claude's
call]** when Claude chose a default that the owner has not explicitly agreed
to. Everything else is description.

## 1. Intent and why

Eero wants one central, personal Claude Code setup that can be switched on for
any project he works on, instead of rebuilding rules, skills and settings per
repo. The setup must make Claude trustworthy to leave working: it cannot see
credentials, cannot damage the desktop it runs on, and tells him when it needs
him.

What bites today **[Eero]**:

- Claude writes code against wrong or outdated library APIs.
- Claude claims work is done without proof.
- The same setup gets repeated in every project.

The first two are solved by later projects (docs enforcement, verification).
The foundation solves the third and builds the safety boundary the others
depend on.

Usage picture **[Eero]**: the kit will be the main consumer of the Claude
subscription (Pro plan). It never invents work. It only acts on tasks Eero
hands it. It is used by Eero alone, on one always-on Linux machine, on private
repos. Project types: JS/TS web apps, Python, backend and CLI tools, Expo
Android apps.

## 2. Goals

1. **One home.** The kit lives in this repo, is maintained on GitHub, and is
   installed from it. Projects hold almost nothing.
2. **One-pass project init.** In any repo, Claude inspects the project,
   proposes a profile, and Eero confirms or corrects it once.
3. **Secrets wall.** Claude can never read credentials or secrets, in any
   session, through any tool. This is a hard rule, enforced by the operating
   system and Claude Code's permission system, not by instructions.
4. **Desktop stays intact.** Claude cannot change system or desktop
   configuration. Writes outside the project are controlled by an allow list.
5. **GitHub door.** Claude can push branches and open PRs as a bot account
   without ever seeing a token, and can never change `main`.
6. **Notifications.** Telegram messages when Claude needs input, finishes, opens
   a PR or fails, sent only when Eero is away from the session.
7. **Provable.** Each guard has a test that shows it holds, and a self-check
   Eero can run at any time.

## 3. Non-goals (for this design)

- Docs enforcement (Context7 guard, docs-researcher, docs-auditor).
- Verification gates (verify-before-stop, Playwright, definition of done).
- Model routing, quota tracking, the scheduler, backlog intake.
- A container or separate OS user for unattended runs. **[Claude's call]** The
  wall in this design applies to every session on Eero's account, attended or
  not. Whether unattended runs additionally get a container is decided in the
  unattended-work design. Docker is installed, so both paths stay open.
- Design skills, scraping, Expo device testing.
- Designing *how* Claude uses desktop apps or Eero's real browser. The
  foundation only provides the off-by-default switch for them (6.9).
- Use on other machines, by other people, or in Anthropic cloud sessions.

## 4. Anti-goals

The kit has failed, even if it technically works, if **[Eero]**:

- a secret is exposed to Claude;
- Claude breaks desktop behaviour or touches things it was not allowed to;
- it needs constant babysitting (too many prompts, blocked hooks, noise);
- it produces PRs that look finished but are not;
- the kit itself keeps breaking as Claude Code changes and becomes a
  maintenance job.

Quota running out for Eero's own sessions is *not* a major concern. A simple,
easily changed reserve setting (start at 80% of the weekly limit) is enough,
and it belongs to the later quota design. The foundation only has to provide
the config file it will live in.

## 5. The three layers

A Claude Code plugin cannot carry permission rules, sandbox settings,
environment variables, a status line or always-loaded instructions (verified,
see section 12). So the kit has three layers:

| Layer | Holds | Lives in | Changed by |
|---|---|---|---|
| Kit plugin | skills, subagents, hooks, helper executables | this repo, installed as a plugin | releasing a new kit version |
| Machine layer | the wall (secrets, sandbox lock, off-limits rules), kit hooks' secrets, kit config | `/etc/claude-code/` and Eero's home | the kit installer, run by Eero |
| Project profile | stack, check commands, key handling, switches | one small file in each project | project init, or by hand |

### 5.1 Kit plugin

- This repo is both the plugin and its own marketplace.
- **[Claude's call]** The machine installs the plugin from the GitHub repo at a
  tagged release, at user scope, so it is active in every project. Work on the
  kit itself happens in this local clone and is tried out with a local plugin
  load before a release is tagged. Reason: a bad hook can block every session,
  so projects should only ever see versions that passed the kit's own tests.
- Kit hooks do nothing workflow-related in a project that has no profile. The
  wall applies everywhere regardless, because it is not in the plugin.
- The kit depends on Superpowers (already installed). **[Claude's call]** The
  installer records the Superpowers version the kit was tested with, and the
  self-check reports drift. It does not vendor or fork Superpowers.
- The existing global Context7 rule and `find-docs` skill are left as they are.
  The docs-enforcement design decides their future.

### 5.2 Machine layer

**[Eero]** The kit owns machine-level Claude Code config. It is applied by an
installer that Eero runs himself in a terminal. Claude never applies it,
because system config is off-limits to Claude and the managed settings location
needs root.

The installer:

- shows a diff of every file it will create or change and takes a backup first;
- is small and readable, since it runs with root rights for the managed
  settings part;
- is safe to run again (it converges, it does not duplicate);
- has an uninstall that restores the backups.

What it installs:

1. **Managed settings** (`/etc/claude-code/`, root-owned): the wall. Project
   files cannot loosen these. **[Claude's call]** The hooks that are part of
   the wall (path checks, integration switches) are declared here and their
   scripts are installed to a root-owned location, not shipped in the plugin.
   Disabling or changing the plugin can then never weaken the wall. Only
   workflow hooks (notifications, later gates) live in the plugin.
2. **User settings** (`~/.claude/settings.json`): the kit plugin, the
   outside-write allow list, anything else that must be adjustable without
   root.
3. **Kit config** (a single human-editable file under Eero's config directory):
   Telegram chat details, the weekly reserve (80 by default, used later), the
   role-to-model map (used later, Opus on top for now **[Eero]**, roles
   configurable **[Eero]**).
4. **Kit secrets** (a separate file, owner-only): the Telegram bot token and
   the bot account's GitHub token. Inside the wall's closed area, so Claude
   cannot read it.

**Who runs what.** Three kinds of action change the machine layer:

- *The installer* (managed settings, wall hooks): Eero, in a terminal, with
  root for the `/etc` part.
- *Kit commands* that change user-level lists (allow a folder, never allow a
  folder, switch an integration on for a project): Eero, either in a terminal
  or inside a Claude session with the `!` prefix. Commands typed with `!` are
  run by Eero outside the sandbox; Claude cannot type them. **[Claude's
  call]** Whether `!` commands can be sent from the phone through Remote
  Control is to be verified at plan time and is not promised.
- *Everything else*: Claude, inside the wall.

### 5.3 Project profile

One small file per project, created by init, committed with the repo. It
records:

- stack and package manager(s);
- the commands for test, lint, typecheck, build and dev server (for later
  gates; recorded now so the profile format is stable);
- key handling for this project **[Eero: decided per project]**: either
  "fake values only" (default) or "real dev keys injected";
- whether the GitHub door is enabled and which bot-accessible repo it maps to;
- the command Eero runs to start the project's local services (for example its
  database containers);
- a read-only view of which integrations are switched on for this project
  (6.9). The switches themselves live at machine level.

File format and exact name are left to the builder. It must be easy to read and
edit by hand.

## 6. The wall

The wall is the centre of this design. It has to hold against mistakes and
against instructions hidden in web pages, docs or issues Claude reads.

### 6.1 What is behind it

**[Eero]** All four kinds exist on this machine and all must be walled off:

- real keys in project `.env` files;
- production credentials (cloud accounts, deploy keys, production databases);
- SSH keys, GitHub and npm authentication in the home directory;
- browser profiles and the password manager.

Also behind it **[Claude's call]**: Claude Code's own login file
(`~/.claude/.credentials.json`), the kit secrets file, and the desktop
keyring.

### 6.2 How it is enforced

No single Claude Code mechanism covers every path (verified, section 12), so
the wall uses two that cover each other's gaps, both locked in managed
settings:

- **Permission deny rules** stop Claude's file tools and the file commands
  Claude Code recognizes in the shell.
- **The OS sandbox** stops every shell process Claude starts, including
  scripts, from reading walled paths, and strips or masks listed environment
  variables. It is switched on, set to fail closed if it cannot start, and the
  "retry without sandbox" escape is switched off. bubblewrap and socat are
  already installed.

**[Eero] The home directory is closed by default, and the project folder is
always open.** Instead of listing every secret location and hoping none is
forgotten, the wall closes Eero's whole home directory to reads and re-opens
only what work needs: the current project, toolchain and package caches, and
similar. Reason: Claude Code has no built-in list of credential locations, so a
deny-list protects only what someone remembered. The cost is that a tool may
fail until its folder is re-opened; the self-check (6.6) is there to find those
quickly. Inside the re-opened areas, secret file patterns such as `.env` files
stay closed.

The two enforcement sides implement this differently:

- *Shell commands:* the sandbox denies reads of the home directory and
  re-opens the listed folders.
- *Claude's file tools:* a deny rule cannot be used for "all of home", because
  deny beats allow and the project could then never be re-opened. **[Claude's
  call]** The file tools are instead limited to the working directories (the
  project plus the allow list) with Claude Code's own setting for blocking
  reads outside them, plus deny rules for secret patterns inside. At plan time
  the builder must check what that setting does to things Claude legitimately
  reads outside the project (skill files in the plugin folder, its memory
  folder). If it breaks those, the fallback is a fail-closed path-checking wall
  hook that covers every file-reading tool.

**Environment variables.** Sessions inherit Eero's shell environment. The wall
strips credential-looking variables from everything Claude runs. An
`ANTHROPIC_API_KEY` in the environment is also stripped, since it would
silently move headless runs to API billing.

**Network.** The sandbox only allows outbound connections to an allow list.
**[Claude's call]** The kit ships a starting list (package registries, GitHub,
documentation hosts) and new domains ask. This also limits where anything could
be sent if a secret did leak.

### 6.3 Claude cannot move the wall

Hooks, status line commands and MCP servers run outside the sandbox with Eero's
full access. If Claude could add or edit one, it could read anything in the
next session. So Claude must not be able to write to:

- any Claude Code settings file, at user or project level;
- the installed kit plugin and the kit config and secrets;
- project MCP server definitions and git hooks.

Changes to those are made by Eero, by the installer, or by kit commands Eero
runs himself. This repo is the one place Claude edits kit code, and nothing
runs from here until a release is installed.

### 6.4 Writes outside the project

**[Eero]** There is an allow list of folders. A write anywhere else outside the
project asks, and the choices are: add to the allow list, allow this once, or
never allow.

Claude Code's built-in permission dialog cannot be given custom choices
(verified). **[Claude's call]** So the three choices are delivered as:

- the prompt itself gives "allow once" and "no", with a reason line that names
  the folder;
- two kit commands, run by Eero, add a folder to the allow list or to a
  never-allow list. After that the prompt no longer appears for that folder: it
  is either allowed or refused outright.

The lists live at machine level, in user settings, changed without root. A
project cannot widen them: with the sandbox locked from managed settings,
Claude Code ignores repository settings that loosen it (verified), the wall
hook reads only the machine-level lists, and Claude cannot write project
settings anyway (6.3). In a headless session, anything that would ask is
refused.

This applies to Claude's file tools. A shell command that tries to write
outside the allowed folders simply fails, because the sandbox blocks it at OS
level without asking.

### 6.5 System, desktop and global installs

- **System and desktop config is off-limits** **[Eero]**: no root, no system
  packages, no changes to shell profiles, desktop settings, services or startup
  items. The sandbox enforces this for shell commands; deny rules cover the file
  tools.
- **Global installs should ask** **[Eero]**. Because the sandbox cannot be
  bypassed from inside a session, a global install cannot be approved with a
  click. **[Claude's call]** Claude stops, shows the exact command and why it
  wants it, and Eero runs it himself, in the session with the `!` prefix or in
  a terminal. This keeps the wall without a hole, at the cost of one
  copy-paste.
- **Docker.** Eero's projects use Docker for local databases **[Eero]**.
  Access to the Docker socket is equal to root on the machine and would let a
  container read the whole home directory, so Claude does not get it.
  **[Claude's call]** Eero starts the containers himself (again `!` or a
  terminal) and Claude connects to them on local ports, which the network
  allow list permits. Init records the project's start command in the profile
  so Claude can tell Eero exactly what to run.

### 6.5a Everyday feel inside the wall

**[Claude's call]** Inside the project, Claude works without asking: edits to
project files and sandboxed shell commands run without prompts. The wall, not
the prompt, is the protection, which is what keeps babysitting low. Prompts
are reserved for things that cross the wall (outside writes, new network
domains). Bypass-permissions mode is locked off in managed settings. The kit
does not otherwise force a permission mode.

### 6.6 Proving the wall holds

The kit plants canary files (fake secrets with a recognizable marker) in walled
locations and a canary environment variable. A self-check command:

- tries to read each canary from a sandboxed shell, by direct read and by
  script, and expects every attempt to fail;
- runs a headless Claude session that is asked to find and print the canaries,
  and fails if the marker appears in its output;
- tries the forbidden writes (settings files, outside folders, system paths);
- checks that the sandbox is active and locked, and reports the Claude Code and
  Superpowers versions against the versions the kit was tested with;
- lists common tool folders that are closed and may need re-opening.

The same checks run in the kit's own test suite before a release is tagged.
Eero can run the self-check any time, and the installer runs it at the end.

**Trying a wall before it is live.** Managed settings are machine-wide, so
there is no second account to stage them on. **[Claude's call]** A candidate
wall is tried by starting Claude with the candidate settings passed on the
command line against the throwaway fixture project, which locks the sandbox
the same way managed settings do (verified) without touching `/etc`. Only a
candidate that passes is installed.

**Eero is the hands for wall tests.** A session behind the wall cannot start
another Claude session (its login file is closed) and cannot install anything
to `/etc`. So during the build and at each release Eero runs two things in a
terminal: the wall test command, and the installer. Hook logic that can be
tested with recorded inputs is tested by Claude in-session. If a bad wall ever
blocks every session, the uninstall is a plain terminal command that does not
need Claude.

### 6.7 What the wall does not cover

Stated plainly so there is no false comfort:

- **Code Claude wrote, run by Eero outside a Claude session,** runs with Eero's
  full access. The protection there is review: PRs are merged only by Eero.
- **Secrets Eero pastes into a prompt** are in the conversation. The kit can
  warn on key-shaped text in prompts later; it is not in this design.
- **"Real dev keys injected" projects.** Any process Claude starts can print
  its own environment, so in those projects Claude could see those specific
  keys. That is why "fake values only" is the default and the other mode is an
  explicit per-project choice. The injection mechanism itself is designed with
  verification, when Claude starts running apps for tests. Until then, every
  project behaves as "fake values only".
- **A root-level compromise** of the machine. Eero has root, so he can always
  change the managed settings. The wall is against Claude, not against him.

### 6.8 Running apps with fake values

With real `.env` files closed, apps need another source of settings. A project
may keep a clearly named, committed-or-ignored agent environment file with fake
or throwaway values, which is readable. The kit provides a way to start a
command with that file loaded. Details are the builder's.

### 6.9 Integrations that run outside the sandbox

Connectors, MCP servers, browser control and computer use act with their own
access and are not contained by the sandbox. The wall controls which of them
exist at all:

- **claude.ai connectors stay on** **[Eero]**. They act with whatever access
  each connector was given in Eero's Claude account.
- **Claude in Chrome and computer use are off, and easy to switch on for a
  single project** **[Eero]**. A machine-level list records which projects
  have which integration on. Eero changes it with one kit command; Claude
  cannot. The project profile shows the current state.
- **[Claude's call]** Other MCP servers and third-party plugins are limited to
  what the installer lists (the kit and Superpowers to start). Adding one is an
  installer change, not something a project file can do.

## 7. The GitHub door

- **[Eero]** A separate GitHub bot identity, with narrow access limited to
  chosen repos, unable to change `main`, its token held where Claude cannot
  read it.
- **[Eero]** Eero merges every PR. No auto-merge.
- **[Eero]** The repos are private, under Eero's personal account on the Free
  plan, and use no private packages or private git dependencies.
- **[Claude's call]** What kind of identity: on a personal account, a second
  user added as collaborator gets full write access, and narrowly scoped
  tokens may not be available for repos that user does not own. A GitHub App
  owned by Eero and installed only on chosen repos may be the only way to get
  a truly narrow identity. The builder verifies the current GitHub rules at
  plan time and picks whichever gives the narrowest access. Either satisfies
  "bot account".
- **Protection of `main` on the Free plan.** GitHub's server-side branch
  protection for private repos is, as far as known, a paid feature (to verify
  at plan time). **[Claude's call]** The door ships anyway. The guarantee then
  rests on two things: only the door holds the token, and the door is fixed
  code Claude cannot edit that refuses any push to `main`. If Eero later moves
  to a paid plan, the installer adds server-side protection on top.
- Eero's own SSH keys and GitHub login are behind the wall, so Claude cannot
  push as Eero at all.
- Claude reaches GitHub only through a narrow kit command that runs outside the
  sandbox, holds the token itself and offers a fixed set of actions: fetch the
  project's own repo, push the current non-`main` branch, open or update a PR,
  comment, read issues and PR feedback. It refuses anything else, and it scans
  what it is about to send
  (diff, PR text) for secret-shaped strings. **[Claude's call]** This is
  preferred over Claude Code's "mask the variable and inject it at the proxy"
  feature, which depends on an experimental setting. The builder should
  re-check that feature at plan time and may switch if it has become stable.
- `gh` is not installed on the machine. The installer either installs it for
  the door's use or the door talks to git and the GitHub API directly:
  builder's choice.

This is the last milestone of the foundation. It can be split into its own plan
if the foundation grows too large.

## 8. Project init

**[Eero]** Detect, then confirm.

1. Eero runs the kit's init in a repo.
2. Claude inspects the repo (manifests, lockfiles, scripts, CI config, existing
   Claude files) and drafts the profile.
3. Claude shows the draft and asks only about what it could not determine and
   about the per-project choices: key handling, whether to enable the GitHub
   door.
4. On confirmation it writes the profile and a short project instructions
   stub, and reports what it did.

Init never writes settings files (6.3). If a project needs something at that
level, init prints the kit command for Eero to run. Re-running init updates the
profile and shows the difference. Init must cope with JS/TS, Python, mixed
repos and Expo projects; for stacks it does not recognize it asks rather than
guesses.

## 9. Notifications

- **[Eero]** Channel: Telegram.
- **[Eero]** Only when away. Claude Code's notification events already fire
  only when the user appears to be away (a few seconds after an unanswered
  permission prompt, about a minute after Claude finishes with no typing). The
  kit forwards those rather than building its own presence detection.
- Events in the foundation: needs input, finished and waiting, PR opened (from
  the GitHub door), and session stopped on an error. Quota events come with
  the quota design.
- **[Claude's call]** The foundation covers sessions Eero started
  interactively, including ones he walked away from. Headless runs do not
  produce the same away-based events; notifying for them needs a run wrapper,
  which belongs to the unattended-work design. In the foundation, "unattended"
  only means a headless session, and its one rule is that anything that would
  ask is refused.
- Message content is deliberately thin: project name, event, a short title, a
  PR link when there is one. Never code, command output or file contents, so a
  message cannot carry a secret.
- The sending hook runs outside the sandbox and reads the bot token from the
  kit secrets file.
- **[Eero]** Full remote control from the phone: Telegram for alerts, Claude's
  own Remote Control for steering live sessions. The installer documents and
  checks the Remote Control setup. **[Claude's call]** Remote Control's own
  push notifications are turned off so alerts do not arrive twice.

## 10. How it gets made

- **[Claude's call]** Hooks, installer and self-check are written in Python
  using only the standard library. Reasons: system Python 3.12 is at a fixed
  path that hooks can rely on (Node is often only on the PATH of an interactive
  shell), there is nothing to install, and startup is fast enough for hooks.
- Skills and subagent definitions are Markdown, as Claude Code requires.
- The kit has its own automated tests: each hook is tested with recorded
  inputs for both the allow and the block case; the wall tests of 6.6 run
  against a throwaway fixture project. A release is tagged only when they pass.
- Hooks fail in a defined direction: guards that protect the wall fail closed
  (block), convenience hooks such as notifications fail open (never block
  work). A broken notification must never stop a session.
- Claude Code facts change quickly. Every mechanism named here must be
  re-verified against `https://code.claude.com/docs/` at plan time. Section 12
  lists what was verified on 2026-11-10.

## 11. Left to the builder

- Repo layout, file names, command names, profile and config file formats.
- The exact deny rules, sandbox paths, environment variable list and network
  allow list, starting from 6.1 and 6.2.
- Which folders the closed home directory re-opens by default.
- Whether the door uses `gh` or the API directly, and the choice of secret
  scanner.
- The order of milestones, with the wall and its self-check first and the
  GitHub door last.

## 12. Verified facts (Claude Code docs, 2026-11-10, version 2.1.322 installed)

- Plugins ship skills, agents, hooks, MCP/LSP config, executables. From a
  plugin's settings file only `agent` and `subagentStatusLine` take effect.
  A plugin's root `CLAUDE.md` is not loaded.
- Plugin-shipped subagents ignore `permissionMode`, `hooks`, `mcpServers`.
- Read/Edit deny rules cover built-in file tools and recognized shell file
  commands, not arbitrary subprocesses. Sandbox `denyRead` covers subprocesses,
  not the Read tool. `allowRead` re-opens paths inside a denied region.
- Sandbox is off by default; `failIfUnavailable` and
  `allowUnsandboxedCommands: false` lock it. Setting the latter in managed
  settings makes the sandbox admin-required, and repository settings that
  loosen it are then ignored. Linux needs bubblewrap and socat.
- Hooks, status line, MCP servers and the built-in file tools run outside the
  sandbox.
- `sandbox.credentials` can deny or mask listed files and environment
  variables for sandboxed commands; masking needs experimental TLS termination.
  `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` strips credentials but leaves GitHub
  tokens.
- Managed settings on Linux: `/etc/claude-code/managed-settings.json` and
  `managed-settings.d/`. Deny rules win over allow rules from any scope. Without
  `allowManagedPermissionRulesOnly`, user-level allow rules and
  `additionalDirectories` still apply.
- The permission dialog cannot be given custom choices. A PreToolUse hook can
  return `ask` with a reason shown in the prompt. File-write approvals last for
  the session. `permissions.additionalDirectories` is the persistent way to add
  writable folders, and the sandbox honours it.
- Bash permission patterns are documented as not a security boundary.
- Hooks from settings and plugins also run for subagents' tool calls.
- Notification hook types include `permission_prompt` (about 6 s) and
  `idle_prompt` (about 60 s), both tied to the user not typing.
- Claude Code's login on Linux is stored in `~/.claude/.credentials.json`.
- Where a plugin's "sensitive" user config is stored on Linux, and whether hook
  environment variables are hidden from the Bash tool, is not documented. That
  is why the kit keeps its own secrets file behind the wall instead.
- In headless runs, a present `ANTHROPIC_API_KEY` is always used.

## 12a. Plan-time findings (2026-11-11)

Found while re-verifying for the first plan. They adjust details above; where
they conflict with an earlier section, this section wins.

1. **The sandbox cannot start on this machine yet.** Ubuntu 24.04 restricts
   unprivileged user namespaces through AppArmor
   (`kernel.apparmor_restrict_unprivileged_userns = 1`), and `bwrap` fails with
   "setting up uid map: Permission denied". The documented fix is an AppArmor
   profile for `/usr/bin/bwrap` at `/etc/apparmor.d/bwrap`. It needs root, so
   Eero applies it, and the installer includes it. Without it and with
   fail-closed on, Claude Code refuses to start; without fail-closed it would
   silently run commands unsandboxed.
2. **Local ports (affects 6.5, Docker).** The docs say that on Linux a
   sandboxed command cannot connect to a server running outside the sandbox,
   and that localhost is private to each command. If the wall tests confirm
   this, "Eero starts the database, Claude connects on a local port" does not
   work as written, and a dev server started in one command is not reachable
   from the next. The first plan measures the real behaviour; the solution
   belongs to the verification design, which is where running apps and
   databases is designed anyway.
3. **Environment variables (replaces part of 6.2).** The built-in scrub switch
   also forces Eero's own `!` commands into the sandbox and switches off
   prompt-free sandboxed commands, so it is not used. The wall instead denies
   an explicit list of variable names to sandboxed commands, built from a
   standard list plus the credential-looking names found in Eero's environment
   at install time. The self-check reports new credential-looking names. Also
   denied: `SSH_AUTH_SOCK` and the keyring socket variables, which would let a
   command use keys without reading them.
4. **Hooks fail open by default.** A crashing or timed-out hook lets the tool
   call through unless it is declared with `onFailure: "block"`. So nothing
   that protects secrets depends on a hook: secrets are protected by settings
   (sandbox, deny rules, the block-reads setting). Hooks only add the
   outside-write prompt, the never-allow list and the per-project integration
   switch, and are declared with `onFailure: "block"`.
5. **Re-opening folders does not need root.** User-level sandbox read entries
   merge with managed ones, so Eero can re-open a tool folder with a kit
   command. The kit refuses to re-open known secret locations.
6. **File patterns on Linux are limited** for the sandbox. The exact form that
   closes `.env`-style files inside a project is settled by the wall tests.
7. **Claude Code already write-protects** settings files, `.mcp.json`, and git
   hooks inside the sandbox. The wall adds matching deny rules for the file
   tools.
8. **Until the GitHub door exists, Claude cannot push** once the wall is
   installed, because Eero's SSH keys and agent are closed. Eero pushes by
   hand in that period.
9. **MCP server allow-listing** uses managed keys whose exact shape is not yet
   verified, and it must not switch off claude.ai connectors. The first plan
   restricts plugin marketplaces (shape verified). MCP allow-listing follows
   in the second plan, once the keys are verified.

## 13. Later projects (each gets its own design)

1. Docs enforcement: per-project lookup ledger keyed by package and version,
   pinned Context7 CLI, fallback when docs are missing.
2. Verification: definition of done, verify-before-stop, Playwright, running
   apps with injected keys, containers for databases.
3. Unattended work: task hand-off (plan files in the repo plus a GitHub issue
   per task **[Eero]**), park-and-move-on for stuck tasks **[Eero]**, quota
   reading and the reserve, permission mode and optional container.
4. Model routing: configurable roles.
5. Specialists: design, scraping, Expo device testing, app control.

## 14. Open questions for Eero

None. The GitHub plan, the closed home directory, integrations and project
dependencies were answered on 2026-11-10 and are recorded above.
