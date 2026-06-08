use reqwest::Client;

pub struct Tweet {
    pub id: String,
    pub url: String,
}

pub async fn fetch_feed(
    client: &Client,
    nitter_base: &str,
    x_base: &str,
    username: &str,
) -> Result<Vec<Tweet>, Box<dyn std::error::Error + Send + Sync>> {
    let url = format!("{}/{}/rss", nitter_base.trim_end_matches('/'), username);
    let bytes = client.get(&url).send().await?.bytes().await?;
    let feed = feed_rs::parser::parse(&bytes[..])?;

    let tweets = feed
        .entries
        .into_iter()
        .filter_map(|entry| {
            let id = entry.id;
            let url = to_x_url(&entry.links.into_iter().next()?.href, nitter_base, x_base);
            Some(Tweet { id, url })
        })
        .collect();

    Ok(tweets)
}

fn to_x_url(nitter_url: &str, nitter_base: &str, x_base: &str) -> String {
    let path = nitter_url
        .trim_start_matches(nitter_base.trim_end_matches('/'))
        .split('#')
        .next()
        .unwrap_or("");
    format!("{}{}", x_base.trim_end_matches('/'), path)
}
