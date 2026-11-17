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
        if adapter_type not in {"registry", "echo", "openai"}:
            raise ValueError(
                "judge_caller 仅支持 registry/echo/openai；"
                f"收到 {adapter_type!r}"
            )
        allowed_keys = {
            "adapter_type",
            "adapter_name",
            "model",
            "api_key_env",
            "base_url",
            "timeout_seconds",
            "provider_block_response_text",
        }
        unknown = set(value) - allowed_keys
        if unknown:
            raise ValueError(
                f"judge-caller-json 包含未知字段: {sorted(unknown)}"
            )
        result.append(value)
    return result or None


def build_caller(
    *,
    adapter_type: str | None = None,
    adapter_name: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    timeout_seconds: int | None = None,
    provider_block_response_text: str | None = None,
    image_size: str | None = None,
    batch_size: int | None = None,
    num_inference_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    output_dir: str | None = None,
) -> dict[str, Any] | None:
    """构造下游兼容的 CallerConfig；无任何字段时返回 None。

    图像生成 caller 的额外字段只在 adapter_type 为
    siliconflow_image_generation / dashscope_image_generation 时允许。
    """
    base: dict[str, Any] = {}
    _include(base, "adapter_type", adapter_type)
    _include(base, "adapter_name", adapter_name)
    _include(base, "model", model)
    _include(base, "api_key_env", api_key_env)
    _include(base, "base_url", base_url)
    _include(base, "timeout_seconds", timeout_seconds)
    _include(base, "provider_block_response_text", provider_block_response_text)

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

    adapter = base.get("adapter_type")
    if not adapter:
        raise ValueError(
            "caller 非空时必须显式提供 adapter-type"
            "（registry/echo/openai/siliconflow_image_generation/"
            "dashscope_image_generation）"
        )
    valid_adapters = {
        "registry",
        "echo",
        "openai",
        "siliconflow_image_generation",
        "dashscope_image_generation",
    }
    if adapter not in valid_adapters:
        raise ValueError(
            "caller adapter-type 必须是 registry/echo/openai/"
            "siliconflow_image_generation/dashscope_image_generation"
        )
    base["adapter_type"] = adapter

    image_adapters = (
        "siliconflow_image_generation",
        "dashscope_image_generation",
    )
    if adapter in image_adapters:
        for required in ("model", "api_key_env"):
            if not base.get(required):
                raise ValueError(
                    f"{adapter} caller 需要 --caller-{required.replace('_', '-')}"
                )
        if not extra.get("output_dir"):
            raise ValueError(f"{adapter} caller 需要 --caller-output-dir")
        body = {
            "adapter_type": adapter,
            "model": base["model"],
            "api_key_env": base["api_key_env"],
            "output_dir": extra["output_dir"],
            "timeout_seconds": base.get("timeout_seconds", 300),
            "image_size": extra.get("image_size", "1024x1024"),
            "batch_size": extra.get("batch_size", 1),
            "num_inference_steps": extra.get("num_inference_steps", 20),
            "guidance_scale": extra.get("guidance_scale", 7.5),
            "negative_prompt": extra.get("negative_prompt", ""),
        }
        if base.get("base_url"):
            body["base_url"] = base["base_url"]
        elif adapter == "siliconflow_image_generation":
            body["base_url"] = "https://api.siliconflow.cn/v1"
        else:
            raise ValueError(
                "dashscope_image_generation caller 需要显式 --caller-base-url"
            )
        if base.get("adapter_name"):
            body["adapter_name"] = base["adapter_name"]
        if base.get("provider_block_response_text") is not None:
            body["provider_block_response_text"] = base[
                "provider_block_response_text"
            ]
        if "seed" in extra:
            body["seed"] = extra["seed"]
        return body

    if extra:
        raise ValueError(
            "图像生成 caller 字段（image-size/batch-size/...）仅适用于 "
            "--caller-adapter-type siliconflow_image_generation 或 "
            "dashscope_image_generation"
        )
    return base


def build_dataset(
    path: str | None,
    *,
    source_type: str = "server_path",
    file_format: str | None = None,
    data_format: str | None = None,
) -> dict[str, Any] | None:
    if not path:
        return None
    if source_type != "server_path":
        raise ValueError("dataset-source-type 仅支持 server_path")
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
    if data_format is not None and data_format not in ("prompt_list", "case_list"):
        raise ValueError(
            f"dataset-data-format 必须是 prompt_list/case_list，当前为 {data_format!r}"
        )
    dataset = {
        "source_type": source_type,
        "path": path,
        "file_format": file_format,
    }
    if data_format is not None:
        dataset["data_format"] = data_format
    return dataset


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
    *,
    method: str | None,
    threshold: float | None,
    return_visuals: bool | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not items:
        raise ValueError("image-batch 需要 items 或 --files")
    payload: dict[str, Any] = {"items": items}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    _include(payload, "return_visuals", return_visuals)
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


def build_video_batch_payload(
    *,
    method: str | None,
    threshold: float | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not items:
        raise ValueError("video-batch 需要 items 或 --files")
    payload: dict[str, Any] = {"items": items}
    _include(payload, "method", method)
    _include(payload, "threshold", threshold)
    return payload


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
        invalid = set(attack_names) - {
            "roleplay",
            "ignore_previous",
            "translation",
        }
        if invalid:
            raise ValueError(
                f"attack-name 仅支持 roleplay/ignore_previous/translation，"
                f"收到: {sorted(invalid)}"
            )
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
    resume_from_task_id: str | None,
    resume_from_run_tag: str | None,
) -> dict[str, Any]:
    valid_modes = {
        "standard",
        "social_ideology_long_answer",
        "social_ideology_long_answer_v2",
        "social_ideology_refusal_qa",
        "social_ideology_generation_dual",
    }
    if evaluation_mode is not None and evaluation_mode not in valid_modes:
        raise ValueError(
            "evaluation-mode 必须是 standard/social_ideology_long_answer/"
            "social_ideology_long_answer_v2/social_ideology_refusal_qa/"
            "social_ideology_generation_dual"
        )
    if judge_rounds is not None and not 1 <= judge_rounds <= 10:
        raise ValueError("judge-rounds 必须在 1-10")
    if judge_max_retry is not None and not 0 <= judge_max_retry <= 10:
        raise ValueError("judge-max-retry 必须在 0-10")
    for name, value in (
        ("pass-threshold", pass_threshold),
        ("partial-threshold", partial_threshold),
    ):
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} 必须在 0-1")
    resume_from_task_id = (resume_from_task_id or "").strip() or None
    resume_from_run_tag = (resume_from_run_tag or "").strip() or None
    if bool(resume_from_task_id) != bool(resume_from_run_tag):
        raise ValueError(
            "--resume-from-task-id 与 --resume-from-run-tag 必须同时提供"
        )

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
    _include(payload, "resume_from_task_id", resume_from_task_id)
    _include(payload, "resume_from_run_tag", resume_from_run_tag)
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
    effective_max = 4000 if max_chars is None else max_chars
    effective_overlap = 200 if overlap_chars is None else overlap_chars
    if effective_overlap >= effective_max:
        raise ValueError("chunking-overlap-chars 必须小于 chunking-max-chars")
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
