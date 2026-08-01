select * from (
    values
        ('pm25', 'PM2.5'),
        ('pm10', 'PM10'),
        ('no2', 'NO2'),
        ('o3', 'O3')
) as pollutants(pollutant, display_name)
