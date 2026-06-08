mod feed;
mod poster;

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;
use poise::serenity_prelude::Http;
use log::{error, info};

pub async fn poll_task(
    http: Arc<Http>,
    client: reqwest::Client,
    nitter_base_url: String,
    x_base_url: String,
    usernames: Vec<String>,
    channel_id: u64,
    poll_time: u64
) {
    let mut seen_ids: HashSet<String> = HashSet::new();
    let mut interval = tokio::time::interval(Duration::from_secs(poll_time));
    let mut first_run = true;

    loop {
        interval.tick().await;

        for username in &usernames {
            match feed::fetch_feed(&client, &nitter_base_url, &x_base_url, username).await {
                Ok(tweets) => {
                    for tweet in tweets {
                        if seen_ids.insert(tweet.id.clone()) && !first_run {
                            if let Err(e) = poster::post_tweet(&http, channel_id, &tweet).await {
                                error!("Failed to post tweet from @{}: {}", username, e);
                            }
                        }
                    }
                }
                Err(e) => error!("Failed to fetch feed for @{}: {}", username, e),
            }
        }

        if first_run {
            info!("Twitter poller initialized, tracking {} user(s)", usernames.len());
            first_run = false;
        }
    }
}
