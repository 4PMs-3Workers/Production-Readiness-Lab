import copy

import pytest
from tools.load.export_probe import PHASES, burst_verdict, save_report


def report():
    return [
        {
            "phase": phase,
            "samples": [
                {"sample": f"{phase}-{i}", "status": 200, "scope_ok": True, "latency_seconds": 1.0}
                for i in range(80)
            ],
        }
        for phase in PHASES
    ]


def test_complete_burst_protocol_passes():
    assert burst_verdict(report())


@pytest.mark.parametrize("fault", ["warmup", "missing", "duplicate", "nan", "slow", "scope"])
def test_bad_evidence_never_passes(fault):
    bursts = copy.deepcopy(report())
    selected = bursts[1]["samples"]
    if fault == "warmup":
        selected[0]["status"] = 503
    elif fault == "missing":
        selected.pop()
    elif fault == "duplicate":
        selected[0]["sample"] = selected[1]["sample"]
    elif fault == "nan":
        selected[0]["latency_seconds"] = float("nan")
    elif fault == "slow":
        for sample in selected:
            sample["latency_seconds"] = 2.6
    else:
        selected[0]["scope_ok"] = False
    assert not burst_verdict(bursts)


def test_each_run_preserves_previous_output(tmp_path):
    first = save_report({"run": 1}, tmp_path)
    second = save_report({"run": 2}, tmp_path)
    assert first != second and '"run": 1' in first.read_text()
