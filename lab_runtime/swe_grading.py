"""Pure SWE grading over caller-supplied, completed test observations.

This does not run tests or certify isolation/completeness. The caller must verify
the execution protocol before setting protocol_complete. Structured full node
IDs and an exhaustive collection inventory are mandatory for a valid result:
native parser dictionaries have already lost collisions and cannot satisfy this
contract. Required keys are source-native,
never arbitrary prefixes of structured node IDs.
"""
from collections.abc import Mapping


SMITH_PARSER = "swesmith.profiles.python.PythonProfile.log_parser"
GYM_PARSERS = frozenset(
    "swebench.harness.log_parsers.parse_log_" + name
    for name in ("conan", "dask", "dvc", "hydra", "modin", "monai",
                 "moto", "mypy", "pandas", "pydantic")
)
# test_passed in the pinned official grading.py of each source. These are
# independent explicit policies, not an assumed general pytest XFAIL rule.
# Smith: SWE-bench/SWE-smith@057f0478b6918bfcd89a51ceeec7229c60bb1028
# Gym: SWE-Gym/SWE-Bench-Fork@242429c188fcfd06aad13fce9a54d450470bf0ac
PASSING_STATUSES = {
    "smith": frozenset({"PASSED", "XFAIL"}),
    "gym": frozenset({"PASSED", "XFAIL"}),
}
KNOWN_STATUSES = frozenset({"PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL"})


def _node_id(value):
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError("test identity must be a nonempty, unpadded single-line string")
    return value


def native_test_key(node_id, *, source, parser_identity):
    """Project to the exact first-whitespace-token identity of frozen parsers.

    Smith uses a non-whitespace regex group; the selected Gym aliases use
    split(), including pydantic. No unsupported parser normalization is guessed.
    """
    if not ((source == "smith" and parser_identity == SMITH_PARSER)
            or (source == "gym" and parser_identity in GYM_PARSERS)):
        raise ValueError("unsupported source/parser identity")
    return _node_id(node_id).split()[0]


def grade_swe_tests(*, source, parser_identity, fail_to_pass, pass_to_pass,
                    observations, protocol_complete, collected_node_ids=None):
    """Return deterministic diagnostics and reward (None for invalid runs).

    observations is a mapping or iterable of (full node ID, exact status) pairs.
    Pairs retain contradictory repeated observations. collected_node_ids must
    contain the exhaustive full-node collection, obtained independently from
    the status map, including every colliding node. Its absence yields invalid.
    protocol_complete=True certifies that the caller verified this collection
    and exhaustive full-node observations; a lossy native map cannot qualify.
    Missing required tests, unknown statuses, conflicts and incomplete protocols
    invalidate a run; complete known nonpassing results receive reward zero.
    """
    native_test_key("validation", source=source, parser_identity=parser_identity)
    if type(protocol_complete) is not bool:
        raise ValueError("protocol_complete must be a boolean")
    groups = {}
    for name, values in (("FAIL_TO_PASS", fail_to_pass), ("PASS_TO_PASS", pass_to_pass)):
        if isinstance(values, (str, bytes)):
            raise ValueError("required tests must be a sequence")
        groups[name] = sorted({_node_id(value) for value in values})
    if not groups["FAIL_TO_PASS"]:
        raise ValueError("FAIL_TO_PASS must not be empty")
    pairs = observations.items() if isinstance(observations, Mapping) else observations
    statuses = {}
    unknown = {}
    for node, status in pairs:
        node = _node_id(node)
        statuses.setdefault(node, set())
        if not isinstance(status, str) or status not in KNOWN_STATUSES:
            unknown.setdefault(node, set()).add(repr(status))
        else:
            statuses[node].add(status)
    nodes = set(statuses)
    collected = set()
    if collected_node_ids is not None:
        if isinstance(collected_node_ids, (str, bytes)):
            raise ValueError("collection must be a sequence")
        collected = {_node_id(node) for node in collected_node_ids}
        nodes.update(collected)
    uncollected = sorted(set(statuses) - collected) if collected_node_ids is not None else []
    identity_map = {}
    for node in sorted(nodes):
        key = native_test_key(node, source=source, parser_identity=parser_identity)
        identity_map.setdefault(key, []).append(node)
    missing = {group: [key for key in keys if key not in identity_map]
               for group, keys in groups.items()}
    required = set(groups["FAIL_TO_PASS"]) | set(groups["PASS_TO_PASS"])
    unreported = sorted(collected - set(statuses))
    conflicts = {node: sorted(values) for node, values in sorted(statuses.items()) if len(values) > 1}
    nonpassing = {}
    for key in sorted(required):
        failures = {node: sorted(statuses[node]) for node in identity_map.get(key, [])
                    if node in statuses and statuses[node] - PASSING_STATUSES[source]}
        if failures:
            nonpassing[key] = failures
    invalid = (collected_node_ids is None or uncollected or not protocol_complete
               or any(missing.values()) or unreported or unknown or conflicts)
    outcome = "invalid" if invalid else "failed" if nonpassing else "passed"
    return {
        "outcome": outcome, "reward": None if invalid else int(not nonpassing),
        "protocol_complete": protocol_complete, "source": source,
        "parser_identity": parser_identity, "passing_statuses": sorted(PASSING_STATUSES[source]),
        "required": groups, "missing": missing, "unreported_nodes": unreported,
        "missing_inventory": collected_node_ids is None, "uncollected_nodes": uncollected,
        "identity_map": identity_map,
        "collisions": {key: values for key, values in identity_map.items() if len(values) > 1},
        "conflicts": conflicts, "unknown_statuses": {node: sorted(values) for node, values in sorted(unknown.items())},
        "nonpassing": nonpassing,
    }
