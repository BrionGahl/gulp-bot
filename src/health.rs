use std::env;

use log::{error, info};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

const RESPONSE: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK";

/// Cloud Run requires the container to listen on `$PORT`. The bot itself is a long-lived
/// gateway connection with nothing to serve over HTTP, so this just answers any request
/// with 200 OK to satisfy Cloud Run's container startup/liveness checks.
pub async fn serve() {
    let port = env::var("PORT").unwrap_or_else(|_| "8080".to_string());
    let addr = format!("0.0.0.0:{}", port);

    let listener = match TcpListener::bind(&addr).await {
        Ok(listener) => listener,
        Err(e) => {
            error!("Failed to bind health check listener on {}: {}", addr, e);
            return;
        }
    };
    info!("Health check listener bound to {}", addr);

    loop {
        let Ok((mut socket, _)) = listener.accept().await else {
            continue;
        };

        tokio::spawn(async move {
            let mut buf = [0u8; 1024];
            // Drain (and discard) whatever request was sent; we don't route on it.
            let _ = socket.read(&mut buf).await;
            let _ = socket.write_all(RESPONSE).await;
        });
    }
}
