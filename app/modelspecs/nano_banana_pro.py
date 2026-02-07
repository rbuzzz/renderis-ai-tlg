from __future__ import annotations

from app.modelspecs.base import ModelSpec, OptionSpec, OptionValue


NANO_BANANA_PRO = ModelSpec(
    key='nano_banana_pro',
    provider='kie',
    model_id='nano-banana-pro',
    model_type='image',
    display_name='Nano Banana Pro',
    tagline='Больше деталей и качество. Можно добавлять референсы.',
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
                OptionValue('2:3', '2:3 (портрет)', 'aspect_2_3'),
                OptionValue('3:4', '3:4 (портрет)', 'aspect_3_4'),
                OptionValue('3:2', '3:2 (ландшафт)', 'aspect_3_2'),
                OptionValue('4:3', '4:3 (ландшафт)', 'aspect_4_3'),
                OptionValue('4:5', '4:5 (портрет)', 'aspect_4_5'),
                OptionValue('5:4', '5:4 (ландшафт)', 'aspect_5_4'),
                OptionValue('9:16', '9:16 (портрет)', 'aspect_9_16'),
                OptionValue('16:9', '16:9 (ландшафт)', 'aspect_16_9'),
                OptionValue('21:9', '21:9 (киношный)', 'aspect_21_9'),
                OptionValue('auto', 'Auto', 'aspect_auto'),
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
            ui_hidden=True,
        ),
    ],
    supports_reference_images=True,
    max_reference_images=8,
    allows_n=False,
)
