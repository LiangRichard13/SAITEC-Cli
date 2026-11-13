"""Core 平台端点常量。

路径与 Core 后端 `/api/v1/skills/*` 契约保持一致；CLI 不自行发明接口。
"""
from __future__ import annotations

HEALTH = "/health"
TASKS = "/api/v1/tasks"
TASK_DETAIL = "/api/v1/tasks/{task_id}"
TASK_FILES = "/api/v1/tasks/{task_id}/files"
TASK_CANCEL = "/api/v1/tasks/{task_id}/cancel"
TESTED_MODELS = "/api/v1/tested-models"

FILE_UPLOAD = "/api/v1/skills/file-manage/upload"
FILE_LIST = "/api/v1/skills/file-manage/files"
FILE_DOWNLOAD = "/api/v1/skills/file-manage/files/{file_id}"
FILE_CONTENT = "/api/v1/skills/file-manage/files/{file_id}/content"

SKILLS: dict[str, dict[str, str]] = {
    "text-detect": {
        "detect": "/api/v1/skills/text-detect/detect",
        "batch": "/api/v1/skills/text-detect/batch",
    },
    "image-detect": {
        "detect": "/api/v1/skills/image-detect/detect",
        "batch": "/api/v1/skills/image-detect/batch",
    },
    "video-detect": {
        "detect": "/api/v1/skills/video-detect/detect",
        "batch": "/api/v1/skills/video-detect/batch",
    },
    "safety-eval": {
        "eval": "/api/v1/skills/safety-eval/eval",
        "credentials": "/api/v1/skills/safety-eval/credentials",
    },
    "general-eval": {
        "eval": "/api/v1/skills/general-eval/eval",
        "credentials": "/api/v1/skills/general-eval/credentials",
    },
    "corpus-safety-eval": {
        "eval": "/api/v1/skills/corpus-safety-eval/eval",
        "credentials": "/api/v1/skills/corpus-safety-eval/credentials",
    },
}

VALID_FILE_TYPES = ("image", "video", "dataset", "output", "log", "report")


def skill_url(skill: str, action: str) -> str:
    """取某个 skill 的端点路径；未知 skill/action 直接报错（编程错误）。"""
    try:
        return SKILLS[skill][action]
    except KeyError as exc:
        raise ValueError(f"Unknown skill endpoint: {skill}.{action}") from exc
