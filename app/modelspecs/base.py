from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class OptionValue:
    value: str
    label: str
    price_key: str


@dataclass
class OptionSpec:
    key: str
    label: str
    values: List[OptionValue]
    default: str
    required: bool = True


@dataclass
class ModelSpec:
    key: str
    provider: str
    model_id: str
    model_type: str
    display_name: str
    options: List[OptionSpec]
    supports_reference_images: bool = False
    allows_n: bool = False

    def option_by_key(self, key: str) -> Optional[OptionSpec]:
        for opt in self.options:
            if opt.key == key:
                return opt
        return None

    def validate_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        validated: Dict[str, Any] = {}
        for opt in self.options:
            value = options.get(opt.key, opt.default)
            allowed = {v.value for v in opt.values}
            if value not in allowed:
                value = opt.default
            validated[opt.key] = value
        return validated

    def build_input(self, prompt: str, options: Dict[str, Any], image_inputs: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = {'prompt': prompt}
        for opt in self.options:
            # reference_images is a UI-only toggle until public image hosting is implemented
            if opt.key == 'reference_images':
                continue
            value = options.get(opt.key, opt.default)
            payload[opt.key] = value
        if self.supports_reference_images and image_inputs:
            payload['image_input'] = image_inputs
        return payload
