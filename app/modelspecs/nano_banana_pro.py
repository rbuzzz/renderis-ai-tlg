from __future__ import annotations

from app.modelspecs.base import ModelSpec, OptionSpec, OptionValue


NANO_BANANA_PRO = ModelSpec(
    key='nano_banana_pro',
    provider='kie',
    model_id='nano-banana-pro',
    model_type='image',
    display_name='Nano Banana Pro',
    options=[
        OptionSpec(
            key='output_format',
            label='Формат',
            default='png',
            values=[
                OptionValue('png', 'PNG', 'output_format_png'),
                OptionValue('jpg', 'JPG', 'output_format_jpg'),
            ],
        ),
        OptionSpec(
            key='aspect_ratio',
            label='Соотношение сторон',
            default='1:1',
            values=[
                OptionValue('1:1', '1:1 (квадрат)', 'aspect_1_1'),
                OptionValue('3:4', '3:4 (портрет)', 'aspect_3_4'),
                OptionValue('4:3', '4:3 (ландшафт)', 'aspect_4_3'),
                OptionValue('9:16', '9:16 (портрет)', 'aspect_9_16'),
                OptionValue('16:9', '16:9 (ландшафт)', 'aspect_16_9'),
            ],
        ),
        OptionSpec(
            key='resolution',
            label='Разрешение',
            default='1K',
            values=[
                OptionValue('1K', '1K', 'resolution_1k'),
                OptionValue('2K', '2K', 'resolution_2k'),
                OptionValue('4K', '4K', 'resolution_4k'),
            ],
        ),
        OptionSpec(
            key='reference_images',
            label='Референсы',
            default='none',
            values=[
                OptionValue('none', 'Без референсов', 'ref_none'),
                OptionValue('has', 'С референсами', 'ref_has'),
            ],
            required=False,
        ),
    ],
    supports_reference_images=True,
    allows_n=False,
)
