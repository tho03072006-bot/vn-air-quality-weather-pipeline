-- Bảng 1, 4 và 5, Quyết định 1459/QĐ-TCMT: AQI bands, colours and health guidance.
-- Vietnamese text is written with its diacritics: these strings reach a reader verbatim
-- on the history page, and "Kem" or "Nguoi co trieu chung" is not Vietnamese a reader
-- should have to decode. The serving copy stores them as nvarchar.
select
    band_index,
    cast(aqi_low as integer) as aqi_low,
    cast(aqi_high as integer) as aqi_high,
    category_vi,
    category_en,
    colour_rgb,
    colour_hex,
    health_effect_vi,
    advice_general_vi,
    advice_sensitive_vi
from (
    values
        (
            1, 0, 50, 'Tốt', 'Good', '0;228;0', '#00E400',
            'Chất lượng không khí tốt, không ảnh hưởng tới sức khỏe.',
            'Tự do thực hiện các hoạt động ngoài trời.',
            'Tự do thực hiện các hoạt động ngoài trời.'
        ),
        (
            2, 51, 100, 'Trung bình', 'Moderate', '255;255;0', '#FFFF00',
            'Chấp nhận được; nhóm nhạy cảm có thể chịu tác động nhất định.',
            'Tự do thực hiện các hoạt động ngoài trời.',
            'Theo dõi triệu chứng ho hoặc khó thở, vẫn có thể hoạt động bên ngoài.'
        ),
        (
            3, 101, 150, 'Kém', 'Unhealthy for sensitive groups', '255;126;0', '#FF7E00',
            'Nhóm nhạy cảm gặp vấn đề sức khỏe; người bình thường ít ảnh hưởng.',
            'Người có triệu chứng đau mắt, ho, đau họng nên giảm hoạt động ngoài trời.',
            'Giảm hoạt động mạnh và thời gian ở ngoài trời.'
        ),
        (
            4, 151, 200, 'Xấu', 'Unhealthy', '255;0;0', '#FF0000',
            'Người bình thường bắt đầu bị ảnh hưởng; nhóm nhạy cảm nghiêm trọng hơn.',
            'Giảm hoạt động mạnh ngoài trời, nghỉ ngơi nhiều hơn trong nhà.',
            'Nên ở trong nhà; nếu ra ngoài hãy đeo khẩu trang đạt tiêu chuẩn.'
        ),
        (
            5, 201, 300, 'Rất xấu', 'Very unhealthy', '143;63;151', '#8F3F97',
            'Cảnh báo ảnh hưởng sức khỏe: mọi người bị ảnh hưởng nghiêm trọng hơn.',
            'Hạn chế tối đa hoạt động ngoài trời, chuyển vào trong nhà.',
            'Nên ở trong nhà và giảm hoạt động mạnh.'
        ),
        (
            6, 301, 500, 'Nguy hại', 'Hazardous', '126;0;35', '#7E0023',
            'Cảnh báo khẩn cấp: toàn bộ dân số bị ảnh hưởng nghiêm trọng.',
            'Ở trong nhà, đóng cửa ra vào và cửa sổ.',
            'Ở trong nhà, đóng cửa ra vào và cửa sổ.'
        )
) as categories(
    band_index,
    aqi_low,
    aqi_high,
    category_vi,
    category_en,
    colour_rgb,
    colour_hex,
    health_effect_vi,
    advice_general_vi,
    advice_sensitive_vi
)
