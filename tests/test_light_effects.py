from __future__ import annotations

from custom_components.quilt.light import (
    _EFFECT_TO_LIGHT_ANIMATION,
    _LIGHT_ANIMATION_TO_EFFECT,
    _color_code_from_rgbw,
    _rgbw_from_color_code,
)


def test_rgbw_roundtrip() -> None:
    code = _color_code_from_rgbw(0xFF, 0x46, 0x00, 0x64)
    assert code == 0xFF460064
    assert _rgbw_from_color_code(code) == (0xFF, 0x46, 0x00, 0x64)


def test_light_animation_mappings_are_consistent() -> None:
    # Bidirectional mapping must be stable so UI can round-trip.
    for k, v in _LIGHT_ANIMATION_TO_EFFECT.items():
        assert _EFFECT_TO_LIGHT_ANIMATION[v] == k

