-- Bang 1, 4 and 5, Quyet dinh 1459/QD-TCMT: AQI bands, colours and health guidance.
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
            1, 0, 50, 'Tot', 'Good', '0;228;0', '#00E400',
            'Chat luong khong khi tot, khong anh huong toi suc khoe.',
            'Tu do thuc hien cac hoat dong ngoai troi.',
            'Tu do thuc hien cac hoat dong ngoai troi.'
        ),
        (
            2, 51, 100, 'Trung binh', 'Moderate', '255;255;0', '#FFFF00',
            'Chap nhan duoc; nhom nhay cam co the chiu tac dong nhat dinh.',
            'Tu do thuc hien cac hoat dong ngoai troi.',
            'Theo doi trieu chung ho hoac kho tho, van co the hoat dong ben ngoai.'
        ),
        (
            3, 101, 150, 'Kem', 'Unhealthy for sensitive groups', '255;126;0', '#FF7E00',
            'Nhom nhay cam gap van de suc khoe; nguoi binh thuong it anh huong.',
            'Nguoi co trieu chung dau mat, ho, dau hong nen giam hoat dong ngoai troi.',
            'Giam hoat dong manh va thoi gian o ngoai troi.'
        ),
        (
            4, 151, 200, 'Xau', 'Unhealthy', '255;0;0', '#FF0000',
            'Nguoi binh thuong bat dau bi anh huong; nhom nhay cam nghiem trong hon.',
            'Giam hoat dong manh ngoai troi, nghi ngoi nhieu hon trong nha.',
            'Nen o trong nha; neu ra ngoai hay deo khau trang dat tieu chuan.'
        ),
        (
            5, 201, 300, 'Rat xau', 'Very unhealthy', '143;63;151', '#8F3F97',
            'Canh bao anh huong suc khoe: moi nguoi bi anh huong nghiem trong hon.',
            'Han che toi da hoat dong ngoai troi, chuyen vao trong nha.',
            'Nen o trong nha va giam hoat dong manh.'
        ),
        (
            6, 301, 500, 'Nguy hai', 'Hazardous', '126;0;35', '#7E0023',
            'Canh bao khan cap: toan bo dan so bi anh huong nghiem trong.',
            'O trong nha, dong cua ra vao va cua so.',
            'O trong nha, dong cua ra vao va cua so.'
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
