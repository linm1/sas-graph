import json

import conftest  # noqa: F401
import pytest

from sas_graph.cli import main


def _write_graph(tmp_path):
    graph = {
        "schema_version": "0.2.0",
        "run_status": "COMPLETE",
        "nodes": [
            {"id": "variable:work.a.flag", "type": "Variable", "label": "work.a.flag", "source": None},
            {"id": "step:001", "type": "Step", "label": "derive", "source": None},
            {"id": "variable:work.b.flag", "type": "Variable", "label": "work.b.flag", "source": None},
        ],
        "edges": [
            {"id": "edge:001", "type": "reads_variable", "from": "variable:work.a.flag", "to": "step:001"},
            {"id": "edge:002", "type": "writes_variable", "from": "step:001", "to": "variable:work.b.flag"},
        ],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def _write_lineage_graph(tmp_path):
    graph = {
        "schema_version": "0.2.0",
        "run_status": "COMPLETE",
        "nodes": [
            {"id": "dataset:raw.a", "type": "Dataset", "label": "raw.a", "source": None},
            {"id": "step:001", "type": "Step", "label": "derive", "source": None},
            {"id": "dataset:work.a", "type": "Dataset", "label": "work.a", "source": None},
        ],
        "edges": [
            {"id": "edge:001", "type": "reads_dataset", "from": "dataset:raw.a", "to": "step:001"},
            {"id": "edge:002", "type": "writes_dataset", "from": "step:001", "to": "dataset:work.a"},
        ],
    }
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def _write_failed_graph(tmp_path):
    path = _write_graph(tmp_path)
    graph = json.loads(path.read_text(encoding="utf-8"))
    graph["run_status"] = "FAILED"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return path


def test_query_search_prints_bare_json(capsys, tmp_path):
    graph = _write_graph(tmp_path)

    assert main(["query-search", "--graph", str(graph), "--query", "STEP"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "query": "STEP",
        "matches": [{"id": "step:001", "type": "Step", "label": "derive", "source": None}],
    }


def test_query_impact_serializes_existing_walk_shape(capsys, tmp_path):
    graph = _write_graph(tmp_path)

    assert main(["query-impact", "--graph", str(graph), "--variable", "variable:work.a.flag"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["start"] == "variable:work.a.flag"
    assert result["reached_variables"] == ["variable:work.a.flag", "variable:work.b.flag"]


def test_query_lineage_serializes_successful_result(capsys, tmp_path):
    graph = _write_lineage_graph(tmp_path)

    assert main([
        "query-lineage",
        "--graph",
        str(graph),
        "--node",
        "dataset:work.a",
        "--direction",
        "upstream",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["start"] == "dataset:work.a"
    assert [node["id"] for node in result["upstream_nodes"]] == [
        "step:001", "dataset:raw.a"
    ]
    assert result["downstream_nodes"] == []


def test_query_failure_writes_one_line_stderr_and_no_stdout(capsys, tmp_path):
    graph = _write_graph(tmp_path)

    assert main(["query-lineage", "--graph", str(graph), "--node", "variable:work.a.flag"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()
    assert "Dataset, Step, or SqlStatement" in captured.err


def test_query_missing_graph_returns_failure_instead_of_traceback(capsys, tmp_path):
    missing = tmp_path / "missing.json"

    assert main(["query-search", "--graph", str(missing), "--query", "step"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "query-search" in captured.err


@pytest.mark.parametrize(
    ("command", "query_args"),
    [
        ("query-search", ["--query", "step"]),
        ("query-lineage", ["--node", "step:001"]),
        ("query-impact", ["--variable", "variable:work.a.flag"]),
    ],
)
def test_query_failed_run_is_a_clear_stderr_failure_for_each_subcommand(
    capsys, tmp_path, command, query_args
):
    graph = _write_failed_graph(tmp_path)

    assert main([command, "--graph", str(graph), *query_args]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{command}: graph run_status is FAILED; query a completed graph\n"
