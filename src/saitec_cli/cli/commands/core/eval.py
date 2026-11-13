"""core eval — safety / general / corpus 评测。

所有请求字段都展开为 CLI flags；judge_callers 是对象列表，用可重复的
--judge-caller-json 表达。pipeline_tier 不暴露（服务端覆盖）。
"""
from __future__ import annotations

from pathlib import Path

import typer

from ._payloads import (
    build_caller,
    build_chunking,
    build_corpus_payload,
    build_dataset,
    build_general_payload,
    build_safety_payload,
    parse_judge_callers,
    read_text_or_file,
    resolve_prompts,
)
from ._shared import (
    render_submit,
    render_task,
    run_core_command,
    submit_and_maybe_wait,
)

app = typer.Typer(help="LLM 评测（safety/general/corpus）")


async def _resolve_dataset(
    ops,
    *,
    dataset_file: Path | None,
    dataset_path: str | None,
    source_type: str,
    file_format: str | None,
    data_format: str,
) -> dict | None:
    if dataset_file and dataset_path:
        raise ValueError("--dataset-file 与 --dataset-path 只能二选一")
    if dataset_file and source_type != "server_path":
        raise ValueError("--dataset-source-type 只对 --dataset-path 生效")
    if dataset_file:
        _, dataset = await ops.upload_dataset(
            dataset_file, file_format=file_format, data_format=data_format
        )
        return dataset
    return build_dataset(
        dataset_path,
        source_type=source_type,
        file_format=file_format,
        data_format=data_format,
    )


def _reject_siliconflow_judge(adapter_type: str | None) -> None:
    if adapter_type == "siliconflow_image_generation":
        raise ValueError("judge_caller 不支持 siliconflow_image_generation")


def _reject_siliconflow_caller(adapter_type: str | None, skill: str) -> None:
    if adapter_type == "siliconflow_image_generation":
        raise ValueError(
            f"{skill} caller 不支持 siliconflow_image_generation；仅 general 支持"
        )




@app.command("safety")
def eval_safety_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    task_name: str = typer.Option(..., "--task-name"),
    model_name: str | None = typer.Option(None, "--model-name"),
    judge_model_name: str | None = typer.Option(None, "--judge-model-name"),
    prompts: list[str] = typer.Option([], "--prompt", help="prompt，可重复"),
    prompts_file: Path | None = typer.Option(None, "--prompts-file", help="JSON 数组或逐行文本"),
    dataset_file: Path | None = typer.Option(None, "--dataset-file", help="上传本地 dataset"),
    dataset_path: str | None = typer.Option(None, "--dataset-path", help="已上传 dataset 的 storage_uri"),
    dataset_source_type: str = typer.Option("server_path", "--dataset-source-type"),
    dataset_file_format: str | None = typer.Option(None, "--dataset-file-format", help="json/jsonl（缺省按扩展名）"),
    dataset_data_format: str = typer.Option("case_list", "--dataset-data-format", help="prompt_list/case_list"),
    caller_adapter_type: str | None = typer.Option(None, "--caller-adapter-type"),
    caller_model: str | None = typer.Option(None, "--caller-model"),
    caller_api_key_env: str | None = typer.Option(None, "--caller-api-key-env"),
    caller_base_url: str | None = typer.Option(None, "--caller-base-url"),
    caller_timeout_seconds: int | None = typer.Option(None, "--caller-timeout-seconds"),
    judge_caller_adapter_type: str | None = typer.Option(None, "--judge-caller-adapter-type"),
    judge_caller_model: str | None = typer.Option(None, "--judge-caller-model"),
    judge_caller_api_key_env: str | None = typer.Option(None, "--judge-caller-api-key-env"),
    judge_caller_base_url: str | None = typer.Option(None, "--judge-caller-base-url"),
    judge_caller_timeout_seconds: int | None = typer.Option(None, "--judge-caller-timeout-seconds"),
    attack_names: list[str] = typer.Option([], "--attack-name", help="攻击变体名，可重复"),
    safety_rules_text: str | None = typer.Option(None, "--safety-rules-text"),
    safety_rules_file: Path | None = typer.Option(None, "--safety-rules-file"),
    risk_categories: list[str] = typer.Option([], "--risk-category", help="风险类别，可重复"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🛡️ 提交 LLM 安全评测任务"""

    async def action(ops):
        prompt_list = resolve_prompts(prompts, prompts_file)
        if prompt_list and (dataset_file or dataset_path):
            raise ValueError("prompt 与 dataset 只能二选一")
        dataset = await _resolve_dataset(
            ops,
            dataset_file=dataset_file,
            dataset_path=dataset_path,
            source_type=dataset_source_type,
            file_format=dataset_file_format,
            data_format=dataset_data_format,
        )
        if not prompt_list and dataset is None:
            raise ValueError("需要 --prompt/--prompts-file 或 dataset")
        _reject_siliconflow_caller(caller_adapter_type, "safety")
        caller = build_caller(
            adapter_type=caller_adapter_type,
            model=caller_model,
            api_key_env=caller_api_key_env,
            base_url=caller_base_url,
            timeout_seconds=caller_timeout_seconds,
        )
        _reject_siliconflow_judge(judge_caller_adapter_type)
        judge_caller = build_caller(
            adapter_type=judge_caller_adapter_type,
            model=judge_caller_model,
            api_key_env=judge_caller_api_key_env,
            base_url=judge_caller_base_url,
            timeout_seconds=judge_caller_timeout_seconds,
        )
        rules = read_text_or_file(
            safety_rules_text, safety_rules_file, "safety-rules-text"
        )
        payload = build_safety_payload(
            task_name=task_name,
            model_name=model_name,
            judge_model_name=judge_model_name,
            prompts=prompt_list,
            dataset=dataset,
            caller=caller,
            judge_caller=judge_caller,
            attack_names=attack_names,
            safety_rules_text=rules,
            risk_categories=risk_categories,
        )
        data = await ops.submit_eval("safety-eval", payload)
        return await submit_and_maybe_wait(
            ops, data, wait=wait, interval=interval, timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("general")
def eval_general_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    task_name: str = typer.Option(..., "--task-name"),
    model_name: str | None = typer.Option(None, "--model-name"),
    judge_model_name: str | None = typer.Option(None, "--judge-model-name"),
    prompts: list[str] = typer.Option([], "--prompt", help="prompt，可重复"),
    prompts_file: Path | None = typer.Option(None, "--prompts-file"),
    dataset_file: Path | None = typer.Option(None, "--dataset-file"),
    dataset_path: str | None = typer.Option(None, "--dataset-path"),
    dataset_source_type: str = typer.Option("server_path", "--dataset-source-type"),
    dataset_file_format: str | None = typer.Option(None, "--dataset-file-format"),
    dataset_data_format: str = typer.Option("case_list", "--dataset-data-format"),
    caller_adapter_type: str | None = typer.Option(None, "--caller-adapter-type"),
    caller_model: str | None = typer.Option(None, "--caller-model"),
    caller_api_key_env: str | None = typer.Option(None, "--caller-api-key-env"),
    caller_base_url: str | None = typer.Option(None, "--caller-base-url"),
    caller_timeout_seconds: int | None = typer.Option(None, "--caller-timeout-seconds"),
    caller_image_size: str | None = typer.Option(None, "--caller-image-size"),
    caller_batch_size: int | None = typer.Option(None, "--caller-batch-size"),
    caller_num_inference_steps: int | None = typer.Option(None, "--caller-num-inference-steps"),
    caller_guidance_scale: float | None = typer.Option(None, "--caller-guidance-scale"),
    caller_negative_prompt: str | None = typer.Option(None, "--caller-negative-prompt"),
    caller_seed: int | None = typer.Option(None, "--caller-seed"),
    caller_output_dir: str | None = typer.Option(None, "--caller-output-dir"),
    judge_caller_adapter_type: str | None = typer.Option(None, "--judge-caller-adapter-type"),
    judge_caller_model: str | None = typer.Option(None, "--judge-caller-model"),
    judge_caller_api_key_env: str | None = typer.Option(None, "--judge-caller-api-key-env"),
    judge_caller_base_url: str | None = typer.Option(None, "--judge-caller-base-url"),
    judge_caller_timeout_seconds: int | None = typer.Option(None, "--judge-caller-timeout-seconds"),
    judge_caller_json: list[str] = typer.Option([], "--judge-caller-json", help="judge_callers JSON object，可重复"),
    judge_rounds: int | None = typer.Option(None, "--judge-rounds"),
    dimensions: list[str] = typer.Option([], "--dimension", help="评测维度，可重复"),
    evaluation_mode: str | None = typer.Option(None, "--evaluation-mode"),
    pass_threshold: float | None = typer.Option(None, "--pass-threshold"),
    partial_threshold: float | None = typer.Option(None, "--partial-threshold"),
    evaluation_rubric_text: str | None = typer.Option(None, "--evaluation-rubric-text"),
    evaluation_rubric_file: Path | None = typer.Option(None, "--evaluation-rubric-file"),
    judge_max_retry: int | None = typer.Option(None, "--judge-max-retry"),
    provider_block_response_text: bool = typer.Option(
        False,
        "--provider-block-response-text",
        help="显式设为 true（与 --no-provider-block-response-text 二选一）",
    ),
    no_provider_block_response_text: bool = typer.Option(
        False,
        "--no-provider-block-response-text",
        help="显式设为 false（与 --provider-block-response-text 二选一）",
    ),
    result_adapter: str | None = typer.Option(None, "--result-adapter"),
    adapter_name: str | None = typer.Option(None, "--adapter-name"),
    resume_from_task_id: str | None = typer.Option(None, "--resume-from-task-id"),
    run_tag: str | None = typer.Option(None, "--run-tag"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """🧠 提交 LLM 通用能力评测任务"""

    async def action(ops):
        prompt_list = resolve_prompts(prompts, prompts_file)
        if prompt_list and (dataset_file or dataset_path):
            raise ValueError("prompt 与 dataset 只能二选一")
        dataset = await _resolve_dataset(
            ops,
            dataset_file=dataset_file,
            dataset_path=dataset_path,
            source_type=dataset_source_type,
            file_format=dataset_file_format,
            data_format=dataset_data_format,
        )
        if not prompt_list and dataset is None:
            raise ValueError("需要 --prompt/--prompts-file 或 dataset")
        caller = build_caller(
            adapter_type=caller_adapter_type,
            model=caller_model,
            api_key_env=caller_api_key_env,
            base_url=caller_base_url,
            timeout_seconds=caller_timeout_seconds,
            image_size=caller_image_size,
            batch_size=caller_batch_size,
            num_inference_steps=caller_num_inference_steps,
            guidance_scale=caller_guidance_scale,
            negative_prompt=caller_negative_prompt,
            seed=caller_seed,
            output_dir=caller_output_dir,
        )
        _reject_siliconflow_judge(judge_caller_adapter_type)
        judge_caller = build_caller(
            adapter_type=judge_caller_adapter_type,
            model=judge_caller_model,
            api_key_env=judge_caller_api_key_env,
            base_url=judge_caller_base_url,
            timeout_seconds=judge_caller_timeout_seconds,
        )
        judge_callers = parse_judge_callers(judge_caller_json)
        rubric = read_text_or_file(
            evaluation_rubric_text,
            evaluation_rubric_file,
            "evaluation-rubric-text",
        )
        if provider_block_response_text and no_provider_block_response_text:
            raise ValueError(
                "--provider-block-response-text 与 --no-provider-block-response-text 只能二选一"
            )
        provider_value: bool | None = None
        if provider_block_response_text:
            provider_value = True
        elif no_provider_block_response_text:
            provider_value = False
        payload = build_general_payload(
            task_name=task_name,
            model_name=model_name,
            judge_model_name=judge_model_name,
            prompts=prompt_list,
            dataset=dataset,
            caller=caller,
            judge_caller=judge_caller,
            judge_callers=judge_callers,
            judge_rounds=judge_rounds,
            dimensions=dimensions,
            evaluation_mode=evaluation_mode,
            pass_threshold=pass_threshold,
            partial_threshold=partial_threshold,
            evaluation_rubric_text=rubric,
            judge_max_retry=judge_max_retry,
            provider_block_response_text=provider_value,
            result_adapter=result_adapter,
            adapter_name=adapter_name,
            resume_from_task_id=resume_from_task_id,
            run_tag=run_tag,
        )
        data = await ops.submit_eval("general-eval", payload)
        return await submit_and_maybe_wait(
            ops, data, wait=wait, interval=interval, timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )


@app.command("corpus")
def eval_corpus_cmd(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json"),
    task_name: str = typer.Option(..., "--task-name"),
    texts: list[str] = typer.Option([], "--text", help="待评测语料，可重复"),
    texts_file: Path | None = typer.Option(None, "--texts-file", help="JSON 数组或逐行文本"),
    dataset_file: Path | None = typer.Option(None, "--dataset-file"),
    dataset_path: str | None = typer.Option(None, "--dataset-path"),
    dataset_source_type: str = typer.Option("server_path", "--dataset-source-type"),
    dataset_file_format: str | None = typer.Option(None, "--dataset-file-format"),
    dataset_data_format: str = typer.Option("case_list", "--dataset-data-format"),
    judge_model_name: str | None = typer.Option(None, "--judge-model-name"),
    judge_caller_adapter_type: str | None = typer.Option(None, "--judge-caller-adapter-type"),
    judge_caller_model: str | None = typer.Option(None, "--judge-caller-model"),
    judge_caller_api_key_env: str | None = typer.Option(None, "--judge-caller-api-key-env"),
    judge_caller_base_url: str | None = typer.Option(None, "--judge-caller-base-url"),
    judge_caller_timeout_seconds: int | None = typer.Option(None, "--judge-caller-timeout-seconds"),
    chunking_enabled: bool | None = typer.Option(
        None, "--chunking-enabled/--no-chunking-enabled"
    ),
    chunking_max_chars: int | None = typer.Option(None, "--chunking-max-chars"),
    chunking_overlap_chars: int | None = typer.Option(None, "--chunking-overlap-chars"),
    safety_rules_text: str | None = typer.Option(None, "--safety-rules-text"),
    safety_rules_file: Path | None = typer.Option(None, "--safety-rules-file"),
    wait: bool = typer.Option(False, "--wait"),
    interval: float = typer.Option(5.0, "--interval", min=0.1),
    timeout: float = typer.Option(3600.0, "--timeout", min=0.1),
) -> None:
    """📚 提交语料安全评测任务（不调用被测模型）"""

    async def action(ops):
        text_list = resolve_prompts(texts, texts_file)
        if text_list and (dataset_file or dataset_path):
            raise ValueError("--text/--texts-file 与 dataset 只能二选一")
        dataset = await _resolve_dataset(
            ops,
            dataset_file=dataset_file,
            dataset_path=dataset_path,
            source_type=dataset_source_type,
            file_format=dataset_file_format,
            data_format=dataset_data_format,
        )
        if not text_list and dataset is None:
            raise ValueError("需要 --text/--texts-file 或 dataset")
        _reject_siliconflow_judge(judge_caller_adapter_type)
        judge_caller = build_caller(
            adapter_type=judge_caller_adapter_type,
            model=judge_caller_model,
            api_key_env=judge_caller_api_key_env,
            base_url=judge_caller_base_url,
            timeout_seconds=judge_caller_timeout_seconds,
        )
        chunking = build_chunking(
            enabled=chunking_enabled,
            max_chars=chunking_max_chars,
            overlap_chars=chunking_overlap_chars,
        )
        rules = read_text_or_file(
            safety_rules_text, safety_rules_file, "safety-rules-text"
        )
        payload = build_corpus_payload(
            task_name=task_name,
            texts=text_list,
            dataset=dataset,
            judge_model_name=judge_model_name,
            judge_caller=judge_caller,
            chunking=chunking,
            safety_rules_text=rules,
        )
        data = await ops.submit_eval("corpus-safety-eval", payload)
        return await submit_and_maybe_wait(
            ops, data, wait=wait, interval=interval, timeout=timeout,
            json_output=json_output,
        )

    run_core_command(
        ctx, config_path, json_output, action, human=render_task if wait else render_submit
    )
