import unittest
from pathlib import Path

from adk.paths import resolve
from adk.wall.generate import build_managed_settings, credential_like, load_policy

P = resolve(home=Path("/h"), root=Path("/r"))

REQUIRED_KEYS = {
    "reopen_read", "reopen_write", "secret_dirs", "secret_file_names",
    "readable_env_files", "env_deny", "env_name_patterns", "env_never_deny",
    "allowed_domains", "marketplaces",
}


def build(env_names=()):
    return build_managed_settings(load_policy(), P, env_names)


class WallGenerateTest(unittest.TestCase):
    def test_sandbox_is_enabled_locked_and_fail_closed(self):
        sb = build()["sandbox"]
        self.assertIs(sb["enabled"], True)
        self.assertIs(sb["failIfUnavailable"], True)
        self.assertIs(sb["allowUnsandboxedCommands"], False)
        self.assertEqual(sb["network"]["allowedDomains"], load_policy()["allowed_domains"])

    def test_home_is_denied_and_reopen_list_is_allowed(self):
        policy = load_policy()
        fs = build()["sandbox"]["filesystem"]
        self.assertEqual(fs["denyRead"][0], "~/")
        self.assertEqual(fs["denyRead"][1:1 + len(policy["secret_dirs"])], policy["secret_dirs"])
        self.assertEqual(
            fs["allowRead"],
            policy["reopen_read"] + [f"~/**/{n}" for n in policy["readable_env_files"]],
        )
        self.assertEqual(fs["allowWrite"], policy["reopen_write"])

    def test_ca_bundle_is_reopened(self):
        # A wildcard denyRead such as ~/**/*.pem also masks the target of any symlink
        # under home that matches it (measured: a Steam runtime cert.pem pointing at the
        # system CA bundle), which breaks TLS for every sandboxed command.
        self.assertIn("/etc/ssl/certs/ca-certificates.crt", build()["sandbox"]["filesystem"]["allowRead"])

    def test_env_names_from_environment_are_added(self):
        out = build(["MY_API_KEY", "PATH", "CLAUDE_CODE_MESSAGING_TOKEN"])
        entries = out["sandbox"]["credentials"]["envVars"]
        names = [e["name"] for e in entries]
        self.assertIn("MY_API_KEY", names)
        self.assertNotIn("PATH", names)
        self.assertNotIn("CLAUDE_CODE_MESSAGING_TOKEN", names)
        self.assertIn("SSH_AUTH_SOCK", names)
        self.assertTrue(all(e["mode"] == "deny" for e in entries))
        self.assertEqual(names, sorted(set(names)))

    def test_never_deny_wins_over_env_deny(self):
        policy = load_policy()
        policy["env_deny"] = policy["env_deny"] + ["XAUTHORITY"]
        out = build_managed_settings(policy, P, [])
        names = [e["name"] for e in out["sandbox"]["credentials"]["envVars"]]
        self.assertNotIn("XAUTHORITY", names)

    def test_credential_like_is_case_insensitive_sorted_and_unique(self):
        got = credential_like(
            ["b_token", "A_Password", "PATH", "b_token", "HOME", "my_credentials"],
            load_policy(),
        )
        self.assertEqual(got, ["A_Password", "b_token", "my_credentials"])

    def test_secret_file_patterns_emitted_for_both_sandbox_and_read_rules(self):
        policy = load_policy()
        out = build()
        deny_read = out["sandbox"]["filesystem"]["denyRead"]
        deny = out["permissions"]["deny"]
        for name in policy["secret_file_names"]:
            self.assertIn("~/**/" + name, deny_read)
            self.assertIn("Read(//**/" + name + ")", deny)
        for entry in policy["secret_dirs"]:
            self.assertIn(entry, deny_read)
            self.assertIn("Read(" + entry + ")", deny)
        self.assertIn("Read(~/.ssh/**)", deny)
        self.assertIn("Read(~/.config/adk/**)", deny)
        self.assertNotIn("Read(~/.claude.json/**)", deny)
        self.assertNotIn("Read(~/.claude/.credentials.json/**)", deny)
        self.assertEqual(len(deny), len(set(deny)))
        self.assertEqual(len(deny_read), len(set(deny_read)))

    def test_secret_files_inside_reopened_tool_folders_stay_closed(self):
        policy = load_policy()
        out = build()
        deny_read = out["sandbox"]["filesystem"]["denyRead"]
        deny = out["permissions"]["deny"]
        for parent, name in (("~/.android", "adbkey"), ("~/.gradle", "gradle.properties")):
            entry = f"{parent}/{name}"
            self.assertIn(parent, policy["reopen_read"])  # the folder is re-opened ...
            self.assertIn(entry, policy["secret_dirs"])   # ... and the secret file stays closed
            self.assertIn(entry, deny_read)
            self.assertIn(f"Read({entry})", deny)
        # A name without an extension also gets a /** rule (harmless for a file);
        # a file with an extension gets only the exact rule.
        self.assertIn("Read(~/.android/adbkey/**)", deny)
        self.assertNotIn("Read(~/.gradle/gradle.properties/**)", deny)
        # The public half and the rest of the folders are not denied.
        self.assertNotIn("~/.android/adbkey.pub", deny_read)
        self.assertNotIn("Read(~/.gradle/**)", deny)

    def test_env_variants_are_denied_and_only_the_agent_file_is_reopened(self):
        policy = load_policy()
        out = build()
        deny_read = out["sandbox"]["filesystem"]["denyRead"]
        deny = out["permissions"]["deny"]
        for name in (".env", ".env.*", ".envrc", "*.tfvars", ".dev.vars", "*.env"):
            self.assertIn(name, policy["secret_file_names"])
            self.assertIn("~/**/" + name, deny_read)
            self.assertIn("Read(//**/" + name + ")", deny)
        # `*.env` also matches the readable agent file, so the sandbox re-opens it by name.
        self.assertEqual(policy["readable_env_files"], ["agent.env"])
        self.assertIn("~/**/agent.env", out["sandbox"]["filesystem"]["allowRead"])
        self.assertNotIn("Read(//**/agent.env)", deny)
        self.assertNotIn("~/**/agent.env", deny_read)

    def test_edit_deny_rules_cover_settings_mcp_and_git_hooks(self):
        deny = build()["permissions"]["deny"]
        for rule in (
            "Edit(//**/.claude/settings.json)",
            "Edit(//**/.claude/settings.local.json)",
            "Edit(//**/.mcp.json)",
            "Edit(//**/.git/hooks/**)",
            "Edit(//**/.git/config)",
            "Edit(~/.config/adk/**)",
        ):
            self.assertIn(rule, deny)

    def test_edit_deny_rules_cover_code_that_runs_outside_the_sandbox(self):
        deny = build()["permissions"]["deny"]
        for rule in (
            "Edit(//**/.git/modules/**/config*)",
            "Edit(//**/.git/modules/**/hooks/**)",
            "Edit(//**/.git/worktrees/*/config*)",
            "Edit(//**/.git/worktrees/**/hooks/**)",
            "Edit(~/.bashrc)", "Edit(~/.bash_profile)", "Edit(~/.profile)", "Edit(~/.zshrc)",
            "Edit(~/.config/autostart/**)", "Edit(~/.config/systemd/user/**)",
            "Edit(~/.local/bin/**)", "Edit(~/.claude/plugins/**)",
        ):
            self.assertIn(rule, deny)

    def test_plugins_stay_readable(self):
        out = build()
        text = repr(out["permissions"]["deny"]) + repr(out["sandbox"]["filesystem"]["denyRead"])
        self.assertNotIn("Read(~/.claude/plugins", text)
        self.assertNotIn("~/.claude/plugins/**'", repr(out["sandbox"]["filesystem"]["denyRead"]))

    def test_permissions_flags(self):
        perms = build()["permissions"]
        self.assertIs(perms["blockReadsOutsideWorkingDirectories"], True)
        self.assertEqual(perms["disableBypassPermissionsMode"], "disable")

    def test_hooks_use_python_isolated_mode_and_block_on_failure(self):
        pre = build()["hooks"]["PreToolUse"]
        self.assertEqual(
            [h["matcher"] for h in pre],
            ["Edit|Write|NotebookEdit", "mcp__(claude-in-chrome|computer-use)__.*"],
        )
        scripts = ["outside_write_guard.py", "integration_guard.py"]
        for entry, script in zip(pre, scripts):
            self.assertEqual(len(entry["hooks"]), 1)
            hook = entry["hooks"][0]
            self.assertEqual(hook["type"], "command")
            self.assertEqual(hook["command"], "/usr/bin/python3")
            self.assertEqual(hook["args"][0], "-I")
            self.assertEqual(
                Path(hook["args"][1]), P.lib_dir / "adk" / "hooks" / script
            )
            self.assertTrue(Path(hook["args"][1]).is_relative_to(P.lib_dir))
            self.assertEqual(hook["timeout"], 10)
            self.assertEqual(hook["onFailure"], "block")

    def test_marketplaces_restriction_comes_from_policy(self):
        self.assertEqual(
            build()["strictKnownMarketplaces"], load_policy()["marketplaces"]
        )

    def test_forbidden_keys_are_absent(self):
        out = build()
        self.assertEqual(
            set(out), {"sandbox", "permissions", "hooks", "strictKnownMarketplaces"}
        )
        flat = repr(out)
        for key in (
            "allowManagedPermissionRulesOnly",
            "allowManagedHooksOnly",
            "blockWritesOutsideWorkingDirectories",
            "allowedMcpServers",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
        ):
            self.assertNotIn(key, flat)

    def test_output_is_deterministic(self):
        names = ["Z_TOKEN", "A_SECRET", "PATH"]
        a = build(names)
        b = build(list(reversed(names)))
        self.assertEqual(a, b)
        env = [e["name"] for e in a["sandbox"]["credentials"]["envVars"]]
        self.assertEqual(env, sorted(env))

    def test_packaged_policy_loads_and_has_all_keys(self):
        policy = load_policy()
        self.assertEqual(set(policy), REQUIRED_KEYS)
        for key in REQUIRED_KEYS:
            self.assertTrue(policy[key], key)

    def test_load_policy_reads_given_path(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.json"
            f.write_text(json.dumps({"x": 1}))
            self.assertEqual(load_policy(f), {"x": 1})


if __name__ == "__main__":
    unittest.main()
