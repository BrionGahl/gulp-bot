-- The nights a guild raids, all starting and ending at the same Eastern times (`/settings raid_nights`).
-- raid_days holds the days of the week, Monday 0 to Sunday 6. An end earlier than the start means the raid
-- runs past midnight. Set and cleared together; NULL means not registered.
ALTER TABLE guild_settings
    ADD COLUMN raid_days  SMALLINT[],
    ADD COLUMN raid_start TIME,
    ADD COLUMN raid_end   TIME;
