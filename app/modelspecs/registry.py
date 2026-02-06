from __future__ import annotations

from typing import Dict, List

from app.modelspecs.base import ModelSpec
from app.modelspecs.nano_banana import NANO_BANANA
from app.modelspecs.nano_banana_edit import NANO_BANANA_EDIT
from app.modelspecs.nano_banana_pro import NANO_BANANA_PRO


MODEL_SPECS: Dict[str, ModelSpec] = {
    NANO_BANANA.key: NANO_BANANA,
    NANO_BANANA_PRO.key: NANO_BANANA_PRO,
    NANO_BANANA_EDIT.key: NANO_BANANA_EDIT,
}


def list_models() -> List[ModelSpec]:
    return list(MODEL_SPECS.values())


def get_model(key: str) -> ModelSpec | None:
    return MODEL_SPECS.get(key)
