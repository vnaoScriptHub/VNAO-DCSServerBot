ALTER TABLE greenieboard ADD COLUMN IF NOT EXISTS wire TEXT;
UPDATE greenieboard SET comment=REGEXP_REPLACE(TRIM(REGEXP_REPLACE(comment, 'LSO: GRADE:.*:', '')), 'WIRE# [1234]', ''), wire=substring(comment FROM NULLIF(position('WIRE' IN comment), 0) + 6 FOR 1)::INTEGER;
UPDATE greenieboard SET grade = '--' WHERE grade = '---';
