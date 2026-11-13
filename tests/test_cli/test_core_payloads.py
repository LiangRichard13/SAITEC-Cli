"""core payload 构建器单测：锁定所有 flags 的映射与空值省略。"""

from __future__ import annotations

import json

import pytest

from saitec_cli.cli.commands.core._payloads import (
    build_caller,
    build_chunking,
    build_corpus_payload,
    build_dataset,
    build_general_payload,
    build_image_batch_payload,
    build_image_payload,
    build_safety_payload,
    build_text_batch_payload,
    build_text_detect_payload,
    build_video_batch_payload,
    build_video_payload,
    parse_judge_callers,
    read_text_or_file,
    resolve_prompts,
)


def test_safety_all_fields():
    payload = build_safety_payload(
        task_name="t",
        model_name="m",
        judge_model_name="j",
        prompts=["p1", "p2"],
        dataset={"path": "/d"},
        caller={"adapter_type": "openai"},
        judge_caller={"adapter_type": "echo"},
        attack_names=["a1", "a2"],
        safety_rules_text="rules",
        risk_categories=["r1", "r2"],
    )
    assert payload == {
        "task_name": "t",
        "model_name": "m",
        "judge_model_name": "j",
        "prompts": ["p1", "p2"],
        "dataset": {"path": "/d"},
        "caller": {"adapter_type": "openai"},
        "judge_caller": {"adapter_type": "echo"},
        "attacks": {"attack_names": ["a1", "a2"]},
        "safety_rules_text": "rules",
        "risk_categories": ["r1", "r2"],
    }
    assert "pipeline_tier" not in payload


def test_safety_omits_empty_fields():
    payload = build_safety_payload(
        task_name="t",
        model_name=None,
        judge_model_name=None,
        prompts=[],
        dataset=None,
        caller=None,
        judge_caller=None,
        attack_names=[],
        safety_rules_text="",
        risk_categories=[],
    )
    assert payload == {"task_name": "t"}


def test_general_all_fields():
    payload = build_general_payload(
        task_name="t",
        model_name="m",
        judge_model_name="j",
        prompts=["p"],
        dataset=None,
        caller={"adapter_type": "openai"},
        judge_caller={"adapter_type": "echo"},
        judge_callers=[{"adapter_type": "openai", "model": "x"}],
        judge_rounds=3,
        dimensions=["d1", "d2"],
        evaluation_mode="social_ideology_long_answer",
        pass_threshold=0.8,
        partial_threshold=0.6,
        evaluation_rubric_text="rubric",
        judge_max_retry=2,
        provider_block_response_text=False,
        result_adapter="adapter",
        adapter_name="name",
        resume_from_task_id="task-0",
        run_tag="tag",
    )
    assert payload == {
        "task_name": "t",
        "model_name": "m",
        "judge_model_name": "j",
        "prompts": ["p"],
        "caller": {"adapter_type": "openai"},
        "judge_caller": {"adapter_type": "echo"},
        "judge_callers": [{"adapter_type": "openai", "model": "x"}],
        "judge_rounds": 3,
        "dimensions": ["d1", "d2"],
        "evaluation_mode": "social_ideology_long_answer",
        "pass_threshold": 0.8,
        "partial_threshold": 0.6,
        "evaluation_rubric_text": "rubric",
        "judge_max_retry": 2,
        "provider_block_response_text": False,
        "result_adapter": "adapter",
        "adapter_name": "name",
        "resume_from_task_id": "task-0",
        "run_tag": "tag",
    }
    assert "pipeline_tier" not in payload


def test_general_omits_unset_optional_fields():
    payload = build_general_payload(
        task_name="t",
        model_name=None,
        judge_model_name=None,
        prompts=[],
        dataset=None,
        caller=None,
        judge_caller=None,
        judge_callers=None,
        judge_rounds=None,
        dimensions=[],
        evaluation_mode=None,
        pass_threshold=None,
        partial_threshold=None,
        evaluation_rubric_text="",
        judge_max_retry=None,
        provider_block_response_text=None,
        result_adapter=None,
        adapter_name=None,
        resume_from_task_id=None,
        run_tag=None,
    )
    assert payload == {"task_name": "t"}


def test_corpus_all_fields():
    payload = build_corpus_payload(
        task_name="t",
        texts=["x"],
        dataset=None,
        judge_model_name="j",
        judge_caller={"adapter_type": "openai"},
        chunking={"enabled": False, "max_chars": 100, "overlap_chars": 10},
        safety_rules_text="rules",
    )
    assert payload == {
        "task_name": "t",
        "judge_model_name": "j",
        "texts": ["x"],
        "judge_caller": {"adapter_type": "openai"},
        "chunking": {"enabled": False, "max_chars": 100, "overlap_chars": 10},
        "safety_rules_text": "rules",
    }


def test_build_caller_defaults_to_openai_when_fields_present():
    assert build_caller(model="deepseek") == {
        "adapter_type": "openai",
        "model": "deepseek",
    }


def test_build_caller_none_when_empty():
    assert build_caller() is None


def test_build_caller_siliconflow_defaults_and_required():
    caller = build_caller(
        adapter_type="siliconflow_image_generation",
        model="flux",
        api_key_env="SF_KEY",
        output_dir="/tmp/out",
    )
    assert caller["adapter_type"] == "siliconflow_image_generation"
    assert caller["base_url"] == "https://api.siliconflow.cn/v1"
    assert caller["timeout_seconds"] == 300
    assert caller["image_size"] == "1024x1024"
    assert caller["batch_size"] == 1
    assert caller["num_inference_steps"] == 20
    assert caller["guidance_scale"] == 7.5
    assert caller["negative_prompt"] == ""

    with pytest.raises(ValueError):
        build_caller(
            adapter_type="siliconflow_image_generation",
            model="flux",
            api_key_env="SF_KEY",
        )


def test_build_caller_rejects_siliconflow_fields_for_openai():
    with pytest.raises(ValueError):
        build_caller(adapter_type="openai", model="m", image_size="512x512")


def test_parse_judge_callers():
    assert parse_judge_callers(None) is None
    assert parse_judge_callers([]) is None
    assert parse_judge_callers(['{"adapter_type": "openai"}']) == [
        {"adapter_type": "openai"}
    ]
    with pytest.raises(ValueError):
        parse_judge_callers(["not-json"])
    with pytest.raises(ValueError):
        parse_judge_callers(["[]"])


def test_parse_judge_callers_rejects_unsafe_or_ambiguous_entries():
    with pytest.raises(ValueError, match="adapter_type"):
        parse_judge_callers(['{"model": "x"}'])
    with pytest.raises(ValueError, match="api_key"):
        parse_judge_callers(
            ['{"adapter_type": "openai", "api_key": "sk-plaintext"}']
        )
    with pytest.raises(ValueError, match="siliconflow"):
        parse_judge_callers(
            ['{"adapter_type": "siliconflow_image_generation"}']
        )


def test_build_dataset_infers_format():
    with pytest.raises(ValueError, match="dataset-path"):
        build_dataset("/x/data.csv")
    assert build_dataset("/x/data.jsonl") == {
        "source_type": "server_path",
        "path": "/x/data.jsonl",
        "file_format": "jsonl",
        "data_format": "case_list",
    }
    assert build_dataset("/x/data.json")["file_format"] == "json"
    assert build_dataset(None) is None
    with pytest.raises(ValueError):
        build_dataset("/x", file_format="csv")
    with pytest.raises(ValueError):
        build_dataset("/x", data_format="bad")


def test_resolve_prompts_file_and_conflict(tmp_path):
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    assert resolve_prompts([], p) == ["a", "b"]
    with pytest.raises(ValueError):
        resolve_prompts(["x"], p)


def test_read_text_or_file_conflict(tmp_path):
    p = tmp_path / "rules.txt"
    p.write_text("rules", encoding="utf-8")
    assert read_text_or_file(None, p, "rules") == "rules"
    with pytest.raises(ValueError):
        read_text_or_file("inline", p, "rules")


def test_text_payloads():
    assert build_text_detect_payload(
        text="x", method=None, threshold=None, language=None, task_name=None
    ) == {"text": "x"}
    assert build_text_detect_payload(
        text="x", method="sample", threshold=0.5, language="zh", task_name="t"
    ) == {
        "text": "x",
        "method": "sample",
        "threshold": 0.5,
        "language": "zh",
        "task_name": "t",
    }


def test_text_batch_requires_exactly_one_mode():
    with pytest.raises(ValueError):
        build_text_batch_payload(
            method=None, threshold=None, task_name=None, items=None, dataset=None
        )
    with pytest.raises(ValueError, match="不能为空"):
        build_text_batch_payload(
            method=None, threshold=None, task_name=None, items=[], dataset=None
        )
    with pytest.raises(ValueError):
        build_text_batch_payload(
            method=None,
            threshold=None,
            task_name=None,
            items=[{"text": "x"}],
            dataset={"path": "/x"},
        )
    assert build_text_batch_payload(
        method="sample",
        threshold=0.5,
        task_name="t",
        items=[{"text": "x"}],
        dataset=None,
    ) == {
        "method": "sample",
        "threshold": 0.5,
        "task_name": "t",
        "items": [{"text": "x"}],
    }


def test_image_payloads_do_not_include_unsupported_batch_fields():
    assert build_image_payload(
        image_uri="/x.png", method=None, threshold=None, return_visuals=None
    ) == {"image_uri": "/x.png"}
    assert build_image_batch_payload(
        method=None, items=[{"image_uri": "/x.png"}]
    ) == {"items": [{"image_uri": "/x.png"}]}
    with pytest.raises(ValueError):
        build_image_batch_payload(method=None, items=[])


def test_video_payloads_do_not_include_unsupported_batch_fields():
    assert build_video_payload(
        video_uri="/x.mp4", method=None, threshold=None
    ) == {"video_uri": "/x.mp4"}
    assert build_video_batch_payload(items=[{"video_uri": "/x.mp4"}]) == {
        "items": [{"video_uri": "/x.mp4"}]
    }
    with pytest.raises(ValueError):
        build_video_batch_payload(items=[])


def test_build_chunking():
    assert build_chunking(enabled=None, max_chars=None, overlap_chars=None) is None
    assert build_chunking(enabled=False, max_chars=100, overlap_chars=0) == {
        "enabled": False,
        "max_chars": 100,
        "overlap_chars": 0,
    }
    with pytest.raises(ValueError):
        build_chunking(enabled=True, max_chars=0, overlap_chars=None)
    with pytest.raises(ValueError):
        build_chunking(enabled=True, max_chars=None, overlap_chars=-1)


def test_load_items_file_accepts_utf8_bom(tmp_path):
    from saitec_cli.cli.commands.core._payloads import load_items_file

    p = tmp_path / "items.json"
    p.write_bytes(b"\xef\xbb\xbf" + b'[{"id": "1", "text": "x"}]')
    assert load_items_file(p) == [{"id": "1", "text": "x"}]
