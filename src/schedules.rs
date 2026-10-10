use chrono::{DateTime, Duration as ChronoDuration};
use chrono_tz::{America::New_York, Tz};
use cron::Schedule;

/// Closest together two fire times of one schedule may be. Every fire time is a message or forum
/// post, so without a floor a mistyped expression (`* * * * * * *`) would post every second.
const MIN_INTERVAL_HOURS: i64 = 1;

/// Example shown to users when their expression can't be read: Fridays at 9:00 AM.
const EXAMPLE_CRON: &str = "0 0 9 * * Fri *";

/// The current time in the zone every schedule is evaluated in. `America/New_York` rather than a
/// fixed UTC offset, so a schedule like "9:00 AM Friday" keeps landing at 9:00 AM local time
/// across the EST/EDT switch.
pub fn now() -> DateTime<Tz> {
    chrono::Utc::now().with_timezone(&New_York)
}

/// Parses a user-supplied quartz-style cron expression, rejecting ones that never fire or fire
/// more often than `MIN_INTERVAL_HOURS`. The error is written to be shown to that user.
pub fn parse_schedule(expression: &str) -> Result<Schedule, String> {
    let schedule = expression.parse::<Schedule>().map_err(|e| {
        format!(
            "Couldn't read that as a cron expression ({e}). Use the quartz-style format \
             `sec min hour day-of-month month day-of-week [year]`, e.g. `{EXAMPLE_CRON}`."
        )
    })?;

    let upcoming: Vec<_> = schedule.after(&now()).take(6).collect();
    if upcoming.is_empty() {
        return Err("That schedule never fires in the future.".to_string());
    }
    if upcoming.windows(2).any(|pair| pair[1] - pair[0] < ChronoDuration::hours(MIN_INTERVAL_HOURS)) {
        return Err(format!("That schedule fires more than once every {MIN_INTERVAL_HOURS} hour(s), which is too often."));
    }

    Ok(schedule)
}

/// The next time `schedule` fires.
pub fn next_fire_time(schedule: &Schedule) -> Option<DateTime<Tz>> {
    schedule.after(&now()).next()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_schedule_accepts_weekly_and_rejects_unusable_expressions() {
        assert!(parse_schedule(EXAMPLE_CRON).is_ok());
        assert!(parse_schedule("0 30 17 * * Tue,Fri *").is_ok());
        // Hourly is exactly at the floor, so allowed.
        assert!(parse_schedule("0 0 * * * * *").is_ok());

        assert!(parse_schedule("every friday").unwrap_err().contains("cron expression"));
        assert!(parse_schedule("* * * * * * *").unwrap_err().contains("too often"));
        assert!(parse_schedule("0 0,30 9 * * Fri *").unwrap_err().contains("too often"));
        assert!(parse_schedule("0 0 9 * * Fri 2020").unwrap_err().contains("never fires"));
    }
}
