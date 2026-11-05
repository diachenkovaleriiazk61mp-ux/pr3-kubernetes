CREATE TABLE IF NOT EXISTS vaccinations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_code VARCHAR(200) NOT NULL CHECK (length(trim(patient_code)) > 0),
    vaccine VARCHAR(200) NOT NULL CHECK (length(trim(vaccine)) > 0),
    batch VARCHAR(200) NOT NULL CHECK (length(trim(batch)) > 0),
    dose_number SMALLINT NOT NULL CHECK (dose_number BETWEEN 1 AND 4),
    administration_date DATE NOT NULL CHECK (administration_date <= CURRENT_DATE),
    facility VARCHAR(200) NOT NULL CHECK (length(trim(facility)) > 0)
);
CREATE INDEX IF NOT EXISTS vaccinations_vaccine_id_idx ON vaccinations(vaccine, id);
