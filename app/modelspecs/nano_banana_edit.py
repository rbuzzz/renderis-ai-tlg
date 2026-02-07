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
    ],
    requires_reference_images=True,
    image_input_key='image_urls',
    max_reference_images=10,
    allows_n=False,
)
