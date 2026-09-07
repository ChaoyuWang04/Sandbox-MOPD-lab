# Official smoke fixture, never training data

Source: https://github.com/harbor-framework/harbor/tree/4407eb5227a2ff4f0d3f16b2eb48849382fdf276/examples/tasks/hello-world

Harbor v0.22.0, Apache-2.0 (LICENSE preserved). Benchmark canary retained.
Six task files compared against the fixed upstream raw files on 2026-09-07:
instruction, solve.sh and test.sh byte-identical; Dockerfile, task.toml and
test_state.py differ only in whitespace. No changes to task behavior.

This fixture is reserved for M0 infrastructure checks, not M1 training/evaluation
pool construction. Resource and time overrides are controller configuration,
not edits to the upstream task. The upstream verifier installs dependencies
online and does not pin all transitive dependencies or the Python version;
passing this fixture does not establish a frozen G4 environment.
