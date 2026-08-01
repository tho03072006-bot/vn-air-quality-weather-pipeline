select * from (
    values
        ('hanoi', 'Hanoi', 21.0278, 105.8342, 'Asia/Ho_Chi_Minh'),
        ('ho_chi_minh', 'Ho Chi Minh City', 10.8231, 106.6297, 'Asia/Ho_Chi_Minh'),
        ('da_nang', 'Da Nang', 16.0544, 108.2022, 'Asia/Ho_Chi_Minh')
) as cities(city_key, city_name, latitude, longitude, timezone_name)
