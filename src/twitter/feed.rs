use reqwest::Client;

pub struct Tweet {
    pub id: String,
    pub author: String,
    pub content: String,
    pub url: String,
    pub image_url: Option<String>,
}

pub async fn fetch_feed(
    client: &Client,
    nitter_base: &str,
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
            let url = entry.links.into_iter().next()?.href;
            let (content, image_url) = if let Some(summary) = &entry.summary {
                (clean_html(&summary.content), extract_image_url(&summary.content))
            } else {
                (entry.title?.content, None)
            };
            Some(Tweet { id, author: username.to_string(), content, url, image_url })
        })
        .collect();

    Ok(tweets)
}

fn clean_html(html: &str) -> String {
    let mut text = String::new();
    let mut in_tag = false;
    for ch in html.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => text.push(ch),
            _ => {}
        }
    }
    text.trim()
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&nbsp;", " ")
}

fn extract_image_url(html: &str) -> Option<String> {
    let prefix = "<img src=\"";
    let start = html.find(prefix)? + prefix.len();
    let end = start + html[start..].find('"')?;
    Some(html[start..end].to_string())
}
