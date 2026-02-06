from __future__ import annotations

from app.modelspecs.base import ModelSpec, OptionSpec, OptionValue


NANO_BANANA_EDIT = ModelSpec(
    key='nano_banana_edit',
    provider='kie',
    model_id='google/nano-banana-edit',
    model_type='image',
    display_name='Nano Banana Edit',
    tagline='Редактирование по вашим фотографиям.',
    options=[
        OptionSpec(
            key='output_format',
            label='Формат',
            default='png',
            values=[
                OptionValue('png', 'PNG', 'output_format_png'),
                OptionValue('jpeg', 'JPEG', 'output_format_jpeg'),
            ],
        ),
        OptionSpec(
            key='image_size',
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
    ],
    requires_reference_images=True,
    image_input_key='image_urls',
    max_reference_images=10,
    allows_n=False,
)
