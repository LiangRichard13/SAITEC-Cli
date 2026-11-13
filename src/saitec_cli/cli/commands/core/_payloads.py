"""CLI flags → Core 请求体。

所有函数只做纯数据转换，便于单测锁定“每个 flag 都进入 payload”。
空值不写入 payload；pipeline_tier 永远不传。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _include(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "" and value != []:
        payload[key] = value


def read_text_list(path: Path | str) -> list[str]:
    """读取 prompt/text 列表：.json 数组或逐行文本。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"文件不存在: {p}")
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p} 不是合法 JSON: {exc}") from exc
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError(f"{p} 必须是字符串数组")
        return [x for x in data if x.strip()]
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_text_or_file(
    text: str | None, file: Path | str | None, field_name: str
) -> str | None:
    if text and file:
        raise ValueError(f"{field_name} 与 {field_name}-file 只能二选一")
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            raise ValueError(f"文件不存在: {p}")
        return p.read_text(encoding="utf-8-sig")
    return text


def resolve_prompts(
    prompts: Iterable[str] | None, prompts_file: Path | str | None
) -> list[str]:
    values = [p for p in (prompts or []) if p and p.strip()]
    if values and prompts_file:
        raise ValueError("prompt 与 prompts-file 只能二选一")
    if prompts_file:
        return read_text_list(prompts_file)
    return values


def parse_judge_callers(raw_values: Iterable[str] | None) -> list[dict[str, Any]] | None:
    result: list[dict[str, Any]] = []
    for raw in raw_values or []:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"judge-caller-json 不是合法 JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("judge-caller-json 必须是 JSON object")
        if "api_key" in value:
            raise ValueError(
                "judge-caller-json 不能包含明文 api_key；请用 api_key_env 引用已注入凭证"
            )
        adapter_type = value.get("adapter_type")
        if not isinstance(adapter_type, str) or not adapter_type:
            raise ValueError(
                "judge-caller-json 必须显式包含 adapter_type（如 openai/echo）"
            )
        if adapter_type == "siliconflow_image_generation":
            raise ValueError("judge_caller 不支持 siliconflow_image_generation")
        result.append(value)
    return result or None


def build_caller(
    *,
    adapter_type: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    timeout_seconds: int | None = None,
    image_size: str | None = None,
    batch_size: int | None = None,
    num_inference_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    output_dir: str | None = None,
) -> dict[str, Any] | None:
    """构造 CallerConfig；无任何字段时返回 None。

    SiliconFlow 图像 caller 的额外字段只在 adapter_type 匹配时允许。
    """
    base: dict[str, Any] = {}
    _include(base, "adapter_type", adapter_type)
    _include(base, "model", model)
    _include(base, "api_key_env", api_key_env)
    _include(base, "base_url", base_url)
    _include(base, "timeout_seconds", timeout_seconds)

    extra: dict[str, Any] = {}
    _include(extra, "image_size", image_size)
    _include(extra, "batch_size", batch_size)
    _include(extra, "num_inference_steps", num_inference_steps)
    _include(extra, "guidance_scale", guidance_scale)
    _include(extra, "negative_prompt", negative_prompt)
    _include(extra, "seed", seed)
    _include(extra, "output_dir", output_dir)

    if not base and not extra:
        return None

    adapter = base.get("adapter_type") or "openai"
    base["adapter_type"] = adapter

    if adapter == "siliconflow_image_generation":
        for required in ("model", "api_key_env"):
            if not base.get(required):
                raise ValueError(
                    f"siliconflow_image_generation caller 需要 --caller-{required.replace('_', '-')}"
                )
        if not extra.get("output_dir"):
            raise ValueError(
                "siliconflow_image_generation caller 需要 --caller-output-dir"
            )
        body = {
            "adapter_type": adapter,
            "model": base["model"],
            "api_key_env": base["api_key_env"],
            "output_dir": extra["output_dir"],
            "base_url": base.get("base_url") or "https://api.siliconflow.cn/v1",
            "timeout_seconds": base.get("timeout_seconds", 300),
            "image_size": extra.get("image_size", "1024x1024"),
            "batch_size": extra.get("batch_size", 1),
            "num_inference_steps": extra.get("num_inference_steps", 20),
            "guidance_scale": extra.get("guidance_scale", 7.5),
            "negative_prompt": extra.get("negative_prompt", ""),
        }
        if "seed" in extra:
            body["seed"] = extra["seed"]
        return body

    if extra:
        raise ValueError(
            "SiliconFlow caller 字段（image-size/batch-size/...）仅适用于 "
            "--caller-adapter-type siliconflow_image_generation"
        )
    return base


def build_dataset(
    path: str | None,
    *,
    source_type: str = "server_path",
    file_format: str | None = None,
    data_format: str = "case_list",
) -> dict[str, Any] | None:
    if not path:
        return None
    if file_format is None:
        lower = path.lower()
        if lower.endswith(".jsonl"):
            file_format = "jsonl"
        elif lower.endswith(".json"):
            file_format = "json"
        else:
            raise ValueError(
                "dataset-path 必须是 .json/.jsonl；请先用 core file upload 上传"
                " dataset，或传 --dataset-file-format json/jsonl"
            )
    if file_format not in ("json", "jsonl"):
        raise ValueError(f"dataset-file-format 必须是 json/jsonl，当前为 {file_format!r}")
    if data_format not in ("prompt_list", "case_list"):
        raise ValueError(
            f"dataset-data-format 必须是 prompt_list/case_list，当前为 {data_format!r}"
        )
    return {
        "source_type": source_type,
        "path": path,
        "file_format": file_format,
        "data_format": data_format,
    }


def build_text_detect_payload(
    *,
    text: str,
    method: str | None,
    threshold: float | None,
    language: str | None,
    task_name: str | None,
) -> dict[str, Any]:
    payload = {"text": text}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    _include(payload, "language", language)
    _include(payload, "task_name", task_name)
    return payload


def build_text_batch_payload(
    *,
    method: str | None,
    threshold: float | None,
    task_name: str | None,
    items: list[dict[str, Any]] | None,
    dataset: dict[str, Any] | None,
) -> dict[str, Any]:
    if items is None and dataset is None:
        raise ValueError("text-batch 的 items 与 dataset 必须二选一")
    if items is not None and dataset is not None:
        raise ValueError("text-batch 的 items 与 dataset 必须二选一")
    if items is not None and not items:
        raise ValueError("items 不能为空")
    payload: dict[str, Any] = {}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    _include(payload, "task_name", task_name)
    if items is not None:
        payload["items"] = items
    if dataset is not None:
        payload["dataset"] = dataset
    return payload


def build_image_payload(
    *,
    image_uri: str,
    method: str | None,
    threshold: float | None,
    return_visuals: bool | None,
) -> dict[str, Any]:
    payload = {"image_uri": image_uri}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    _include(payload, "return_visuals", return_visuals)
    return payload


def build_image_batch_payload(
    *, method: str | None, items: list[dict[str, Any]]
) -> dict[str, Any]:
    if not items:
        raise ValueError("image-batch 需要 items 或 --files")
    payload: dict[str, Any] = {"items": items}
    _include(payload, "method", method)
    return payload


def build_video_payload(
    *,
    video_uri: str,
    method: str | None,
    threshold: float | None,
) -> dict[str, Any]:
    payload = {"video_uri": video_uri}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    return payload


def build_video_batch_payload(*, items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        raise ValueError("video-batch 需要 items 或 --files")
    return {"items": items}


def build_safety_payload(
    *,
    task_name: str,
    model_name: str | None,
    judge_model_name: str | None,
    prompts: list[str] | None,
    dataset: dict[str, Any] | None,
    caller: dict[str, Any] | None,
    judge_caller: dict[str, Any] | None,
    attack_names: list[str] | None,
    safety_rules_text: str | None,
    risk_categories: list[str] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"task_name": task_name}
    _include(payload, "model_name", model_name)
    _include(payload, "judge_model_name", judge_model_name)
    _include(payload, "prompts", prompts)
    _include(payload, "dataset", dataset)
    _include(payload, "caller", caller)
    _include(payload, "judge_caller", judge_caller)
    if attack_names:
        payload["attacks"] = {"attack_names": list(attack_names)}
    _include(payload, "safety_rules_text", safety_rules_text)
    _include(payload, "risk_categories", risk_categories)
    return payload


def build_general_payload(
    *,
    task_name: str,
    model_name: str | None,
    judge_model_name: str | None,
    prompts: list[str] | None,
    dataset: dict[str, Any] | None,
    caller: dict[str, Any] | None,
    judge_caller: dict[str, Any] | None,
    judge_callers: list[dict[str, Any]] | None,
    judge_rounds: int | None,
    dimensions: list[str] | None,
    evaluation_mode: str | None,
    pass_threshold: float | None,
    partial_threshold: float | None,
    evaluation_rubric_text: str | None,
    judge_max_retry: int | None,
    provider_block_response_text: bool | None,
    result_adapter: str | None,
    adapter_name: str | None,
    resume_from_task_id: str | None,
    run_tag: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"task_name": task_name}
    _include(payload, "model_name", model_name)
    _include(payload, "judge_model_name", judge_model_name)
    _include(payload, "prompts", prompts)
    _include(payload, "dataset", dataset)
    _include(payload, "caller", caller)
    _include(payload, "judge_caller", judge_caller)
    _include(payload, "judge_callers", judge_callers)
    _include(payload, "judge_rounds", judge_rounds)
    _include(payload, "dimensions", dimensions)
    _include(payload, "evaluation_mode", evaluation_mode)
    _include(payload, "pass_threshold", pass_threshold)
    _include(payload, "partial_threshold", partial_threshold)
    _include(payload, "evaluation_rubric_text", evaluation_rubric_text)
    _include(payload, "judge_max_retry", judge_max_retry)
    if provider_block_response_text is not None:
        payload["provider_block_response_text"] = provider_block_response_text
    _include(payload, "result_adapter", result_adapter)
    _include(payload, "adapter_name", adapter_name)
    _include(payload, "resume_from_task_id", resume_from_task_id)
    _include(payload, "run_tag", run_tag)
    return payload


def build_corpus_payload(
    *,
    task_name: str,
    texts: list[str] | None,
    dataset: dict[str, Any] | None,
    judge_model_name: str | None,
    judge_caller: dict[str, Any] | None,
    chunking: dict[str, Any] | None,
    safety_rules_text: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"task_name": task_name}
    _include(payload, "judge_model_name", judge_model_name)
    _include(payload, "texts", texts)
    _include(payload, "dataset", dataset)
    _include(payload, "judge_caller", judge_caller)
    _include(payload, "chunking", chunking)
    _include(payload, "safety_rules_text", safety_rules_text)
    return payload


def build_chunking(
    *,
    enabled: bool | None,
    max_chars: int | None,
    overlap_chars: int | None,
) -> dict[str, Any] | None:
    if enabled is None and max_chars is None and overlap_chars is None:
        return None
    if max_chars is not None and max_chars <= 0:
        raise ValueError("chunking-max-chars 必须 > 0")
    if overlap_chars is not None and overlap_chars < 0:
        raise ValueError("chunking-overlap-chars 必须 >= 0")
    body: dict[str, Any] = {}
    if enabled is not None:
        body["enabled"] = enabled
    if max_chars is not None:
        body["max_chars"] = max_chars
    if overlap_chars is not None:
        body["overlap_chars"] = overlap_chars
    return body


def load_items_file(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError(f"{p} 必须是 object 数组")
    return data
