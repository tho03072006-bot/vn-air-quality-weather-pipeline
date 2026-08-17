-- A drift computed across two units is arithmetic on incommensurable numbers.
--
-- Both sides are µg/m³ today, and the VN_AQI models already reject anything else, so
-- this can never fire on the current pipeline. That is exactly why it is here: the
-- forecast side and the historical side are fetched by two different client methods
-- with two different variable lists, and a future change to either could introduce a
-- ppb series without touching this model at all. The subtraction would keep working
-- and the answer would be meaningless.
--
-- fct_forecast_vs_analysis carries both units precisely so this test can compare them.
--
-- Restricted to PAIRED rows. A row still waiting for its analysis has no analysis unit
-- to disagree with, and the first version of this test flagged every one of them --
-- a check firing on the normal state of a relation that is mostly waiting.
select
    location_key,
    pollutant,
    unit as forecast_unit,
    analysis_unit,
    count(*) as rows
from {{ ref('fct_forecast_vs_analysis') }}
where pairing_status = 'PAIRED'
  and unit is distinct from analysis_unit
group by 1, 2, 3, 4
