"""Offline source-identity checks; these do not certify image execution."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import unittest
import warnings

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path(os.environ.get("M1_SWE_PROFILE_RESEARCH", str(ROOT / "data/m1/v2/research")))
CONFIG = ROOT / "configs/m1-swe-profiles-v2.json"
FIXTURE = ROOT / "tests/fixtures/m1-swe-profile-expectations.json"


def read(name):
    return json.loads((RESEARCH / name).read_text())


def source_tree(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse((RESEARCH / name).read_text())


def constants():
    # Only literal/container assignments and loops are evaluated. Imports,
    # class definitions, paths and all executable external calls are excluded.
    tree = source_tree("upstream-gym-constants.py")
    body = []
    started = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TEST_PYTEST" for t in node.targets):
            started = True
        if not started or isinstance(node, (ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        for part in ast.walk(node):
            if isinstance(part, ast.Call):
                assert (isinstance(part.func, ast.Name) and part.func.id == "int") or (isinstance(part.func, ast.Attribute) and part.func.attr in {"update", "extend", "lower", "items"}), ast.unparse(part)
            assert not isinstance(part, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Lambda, ast.With, ast.While, ast.Try)), ast.dump(part)
            if isinstance(part, ast.Attribute):
                assert part.attr in {"update", "extend", "lower", "items"}
        body.append(node)
    namespace = {"__builtins__": {}, "int": int}
    exec(compile(ast.Module(body=body, type_ignores=[]), "validated-constant-expressions", "exec"), namespace)
    return namespace


class StandaloneProfilesTests(unittest.TestCase):
    def test_tracked_expectations_cover_selected_ids_and_commands(self):
        self.assertTrue(FIXTURE.is_file(), "tracked source-independent expectations are missing")
        expected = json.loads(FIXTURE.read_text())
        config = json.loads(CONFIG.read_text())
        self.assertEqual(config["status"], "metadata_frozen_runtime_unvalidated")
        self.assertEqual(config["unresolved"], [])
        for kind, count in (("smith", 52), ("gym", 50)):
            self.assertEqual(len(config[kind]), count)
            self.assertEqual(sorted(config[kind]), expected["instance_ids"][kind])
            for instance_id, values in expected["representatives"][kind].items():
                for key, value in values.items():
                    self.assertEqual(config[kind][instance_id][key], value, (instance_id, key))
        for item in config["sources"]:
            self.assertRegex(item["commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")


def exact_research_available():
    if not FIXTURE.is_file():
        return False
    hashes = json.loads(FIXTURE.read_text())["research_sha256"]
    return all((RESEARCH / name).is_file() and hashlib.sha256((RESEARCH / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())


@unittest.skipUnless(exact_research_available(), "full source integration requires exact ignored M1 research assets; standalone tracked checks still run")
class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONFIG.is_file(), "selected SWE execution profiles are not frozen")
        self.config = json.loads(CONFIG.read_text())

    def test_exact_selection_coverage_and_identity(self):
        smith = read("smith-a-selection.json")["selected"] + read("smith-b-selection.json")["selected"]
        gym = read("gym50-frozen.json")["records"]
        self.assertEqual((len(smith), len(gym)), (52, 50))
        for kind, rows in (("smith", smith), ("gym", gym)):
            actual = self.config[kind]
            self.assertEqual(set(actual), {r["instance_id"] for r in rows})
            for row in rows:
                frozen = actual[row["instance_id"]]
                self.assertEqual(frozen["repo"], row["repo"])
                if kind == "smith":
                    self.assertEqual(frozen["image_name"], row["image_name"])
                    self.assertEqual(frozen["generation_method"], row["instance_id"].split(".", 2)[2].split("__", 1)[0])
                    self.assertFalse(frozen["min_testing"])
                else:
                    self.assertEqual(frozen["base_commit"], row["base_commit"])
                    self.assertEqual(frozen["version"], row["version"])

    def test_pinned_sources_and_explicit_runtime_limit(self):
        self.assertEqual(self.config["status"], "metadata_frozen_runtime_unvalidated")
        self.assertEqual(self.config["unresolved"], [])
        for item in self.config["sources"]:
            self.assertEqual(hashlib.sha256((RESEARCH / Path(item["path"]).name).read_bytes()).hexdigest(), item["sha256"])
            self.assertRegex(item["commit"], r"^[0-9a-f]{40}$")

    def test_gym_specs_and_mypy_have_no_generic_fallback(self):
        specs = constants()["MAP_REPO_VERSION_TO_SPECS"]
        for row in read("gym50-frozen.json")["records"]:
            item = self.config["gym"][row["instance_id"]]
            official = specs[row["repo"].lower()][row["version"]]
            self.assertEqual(item["official_spec"], official)
            self.assertEqual(item["verify_reinstall_command"], official.get("install"))
            self.assertEqual(item["eval_commands"], official.get("eval_commands", []))
            self.assertTrue(item["parser_identity"].startswith("swebench.harness.log_parsers."))
            if row["repo"] == "python/mypy":
                keys = re.findall(r'\[case ([^\]]+)\]', row["test_patch"])
                self.assertEqual(item["test_command"], official["test_cmd"] + ' "' + ' or '.join(keys) + '"')
            else:
                directives = re.findall(r"diff --git a/.* b/(.*)", row["test_patch"])
                directives = [d for d in directives if not any(d.endswith(e) for e in constants()["NON_TEST_EXTS"])]
                self.assertEqual(item["test_directives"], directives)
                self.assertEqual(item["test_command"], " ".join([official["test_cmd"], *item["test_directives"]]))

    def test_public_images_are_exact_observed_identities(self):
        images = {r["image"]: r for r in read("swe-image-metadata.json")}
        for kind in ("smith", "gym"):
            for item in self.config[kind].values():
                self.assertEqual(item["image_digest"], images[item["image_name"]]["tag_digest"])
        utility = source_tree("upstream-gym-utils.py")
        method = next(n for n in utility.body if isinstance(n, ast.FunctionDef) and n.name == "get_test_directives")
        self.assertEqual(self.config["gym_directives_source"], ast.get_source_segment((RESEARCH / "upstream-gym-utils.py").read_text(), method))

    def test_smith_class_inheritance_is_exact(self):
        classes = {n.name: n for n in source_tree("upstream-smith-python.py").body if isinstance(n, ast.ClassDef)}
        for item in self.config["smith"].values():
            cls = classes[item["profile_class"].split(".")[-1]]
            self.assertEqual([ast.unparse(b) for b in cls.bases], ["PythonProfile"])
            fields = {n.target.id: ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.AnnAssign)}
            self.assertEqual(set(fields), {"owner", "repo", "commit"})
            self.assertEqual(fields["commit"], item["repository_commit"])
            self.assertEqual(item["repo"], f"swesmith/{fields['owner']}__{fields['repo']}.{fields['commit'][:8]}")
            self.assertEqual(item["test_command"], "source /opt/miniconda3/bin/activate; conda activate testbed; pytest --disable-warnings --color=no --tb=no --verbose")
            self.assertEqual(item["parser_identity"], "swesmith.profiles.python.PythonProfile.log_parser")
            self.assertEqual(item["passing_statuses"], ["PASSED", "XFAIL"])


if __name__ == "__main__":
    unittest.main()
