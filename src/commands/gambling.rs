use std::collections::{HashMap, HashSet};
use std::time::Duration;
use poise::{CreateReply, ReplyHandle};
use poise::futures_util::StreamExt;
use poise::serenity_prelude::{ButtonStyle, ComponentInteractionCollector, CreateActionRow, CreateButton, CreateEmbed, CreateInteractionResponse, CreateInteractionResponseMessage, UserId};
use rand::Rng;
use crate::types::bot::{Context, Error};

/// Roll a dice up to a max value or to 100 by default
#[poise::command(
    prefix_command,
    slash_command,
    category = "Gambling",
)]
pub async fn roll(ctx: Context<'_>, #[description = "Max number that can be rolled"] #[min = 2] max_roll: Option<u32>) -> Result<(), Error> {
    let max_roll = max_roll.unwrap_or(100);
    let roll = get_inclusive_roll(max_roll);

    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("{} has rolled!", &ctx.author().name))
        .field("", format!("{}", roll), false);
    ctx.send(CreateReply::default().embed(embed)).await?;
    Ok(())
}

/// Start a gambling session
#[poise::command(
    prefix_command,
    slash_command,
    category = "Gambling",
)]
pub async fn gamble(ctx: Context<'_>, #[description = "Max number that can be rolled"] #[min = 2] max_roll: Option<u32>) -> Result<(), Error> {
    let max_roll = max_roll.unwrap_or(100);

    let (players, reply) = run_lobby(ctx, max_roll).await?;
    if players.len() < 2 {
        let embed = crate::helper::create_base_embed(&ctx)
            .title("Gambling Session Cancelled")
            .description("Not enough players joined the session (Minimum 2).");

        reply.edit(ctx, CreateReply::default().embed(embed).components(vec![])).await?;
        return Ok(())
    }

    let (scores, reply) = run_game(ctx, reply, players, max_roll).await?;
    if scores.len() < 2 {
        let embed = crate::helper::create_base_embed(&ctx)
            .title("Gambling Session Cancelled")
            .description("Not enough players rolled (Minimum 2).");

        reply.edit(ctx, CreateReply::default().embed(embed).components(vec![])).await?;
        return Ok(());
    }

    show_results(ctx, reply, scores).await?;
    Ok(())
}

async fn run_lobby<'a>(ctx: Context<'a>, max_roll: u32) -> Result<(HashSet<UserId>, ReplyHandle<'a>), Error> {
    let join_button_id = format!("session-join-{}", ctx.id());
    let start_button_id = format!("session-start-{}", ctx.id());
    let start_button_id_press = start_button_id.clone();

    let mut players = HashSet::new();
    players.insert(ctx.author().id);

    let buttons = vec![CreateActionRow::Buttons(vec![
        CreateButton::new(&start_button_id)
            .label("Start Game")
            .style(ButtonStyle::Secondary)
            .emoji('🎲'),
        CreateButton::new(&join_button_id)
            .label("Join / Leave Session")
            .style(ButtonStyle::Success)
            .emoji('✔'),
    ])];

    let reply = ctx.send(
        CreateReply::default()
            .embed(create_lobby_embed(&ctx, &players, max_roll))
            .components(buttons.clone())
    ).await?;

    // Lobby loop
    let mut collector = ComponentInteractionCollector::new(ctx)
        .timeout(Duration::from_secs(60))
        .filter(move |press| press.data.custom_id == start_button_id_press || press.data.custom_id == join_button_id)
        .stream();
    while let Some(press) = collector.next().await {
        let user_id = press.user.id;

        if press.data.custom_id == start_button_id {
            if user_id == ctx.author().id {
                press.create_response(ctx, CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new().content("Starting game!.").ephemeral(true)
                )).await?;
                break;
            } else {
                press.create_response(ctx, CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new().content("You are not the session owner, ask them to start the game!.").ephemeral(true)
                )).await?;
                continue;
            }
        }

        if players.contains(&user_id) {
            players.remove(&user_id);
            press.create_response(ctx, CreateInteractionResponse::Message(
                CreateInteractionResponseMessage::new().content("Left lobby.").ephemeral(true)
            )).await?;
        } else {
            players.insert(user_id);
            press.create_response(ctx, CreateInteractionResponse::Message(
                CreateInteractionResponseMessage::new().content("Joined lobby.").ephemeral(true)
            )).await?;
        }

        // Update UI
        reply.edit(ctx, CreateReply::default()
            .embed(create_lobby_embed(&ctx, &players, max_roll))
            .components(buttons.clone())
        ).await?;
    }

    Ok((players, reply))
}

async fn run_game<'a>(ctx: Context<'a>, reply: ReplyHandle<'a>, players: HashSet<UserId>, max_roll: u32) -> Result<(HashMap<UserId, u32>, ReplyHandle<'a>), Error> {
    let roll_button_id = format!("game-{}", ctx.id());

    let mut has_rolled = HashSet::new();
    let mut scores = HashMap::new();

    let roll_buttons = vec![CreateActionRow::Buttons(vec![
        CreateButton::new(&roll_button_id)
            .label("Roll")
            .style(ButtonStyle::Secondary)
            .emoji('🎲')
    ])];

    reply.edit(ctx, CreateReply::default()
        .embed(create_game_embed(&ctx, &players, &scores, max_roll))
        .components(roll_buttons.clone())
    ).await?;

    // Game loop
    let mut collector = ComponentInteractionCollector::new(ctx)
        .timeout(Duration::from_secs(120))
        .filter(move |press| press.data.custom_id == roll_button_id)
        .stream();
    while let Some(press) = collector.next().await {
        let user = &press.user;

        if !players.contains(&user.id) {
            press.create_response(
                ctx,
                CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new()
                        .content("You are not in this game!")
                        .ephemeral(true)
                )
            ).await?;
            continue;
        }

        if has_rolled.contains(&user.id) {
            press.create_response(
                ctx,
                CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new()
                        .content("You already rolled!")
                        .ephemeral(true)
                )
            ).await?;
            continue;
        }

        has_rolled.insert(user.id);
        let roll = get_inclusive_roll(max_roll);
        scores.insert(user.id, roll);

        press.create_response(ctx, CreateInteractionResponse::Message(CreateInteractionResponseMessage::new().content(format!("You rolled {}!", roll)).ephemeral(true))).await?;

        if has_rolled.len() == players.len() {
            break;
        }

        let edited_embed = create_game_embed(&ctx, &players, &scores, max_roll);
        reply.edit(ctx, CreateReply::default().embed(edited_embed).components(roll_buttons.clone())).await?;
    }

    Ok((scores, reply))
}

async fn show_results<'a>(ctx: Context<'a>, reply: ReplyHandle<'a>, scores: HashMap<UserId, u32>) -> Result<(), Error> {
    let mut sorted_scores: Vec<_> = scores.iter().collect();
    sorted_scores.sort_by(|a, b| b.1.cmp(a.1));

    let final_mapping = sorted_scores.iter().map(|(user_id, roll)| {
        (format!("<@{}>", user_id), format!("{}", roll), false)
    });

    let max = sorted_scores.first().unwrap();
    let max_user = format!("<@{}>", max.0);

    let min = sorted_scores.last().unwrap();
    let min_user = format!("<@{}>", min.0);

    let edited_embed = crate::helper::create_base_embed(&ctx)
        .title(format!("{}'s Gambling Session!", ctx.author().name))
        .description(format!("Congrats {}! {} owes {} {} gold!", max_user, min_user, max_user, max.1 - min.1))
        .fields(final_mapping);

    reply.edit(ctx, CreateReply::default().embed(edited_embed).components(vec![])).await?;
    Ok(())
}

fn create_lobby_embed(ctx: &Context<'_>, players: &HashSet<UserId>, max_roll: u32) -> CreateEmbed {
    let player_list = if players.is_empty() {
        "No players".to_string()
    } else {
        players.iter()
            .map(|id| format!("<@{}>", id))
            .collect::<Vec<_>>()
            .join("\n")
    };

    crate::helper::create_base_embed(&ctx)
        .title(format!("{}'s Gambling Session!", ctx.author().name))
        .description(format!("Max is {}, Click the button below to join!", max_roll))
        .field(format!("Current Players ({})", players.len()), player_list, true)
}

fn create_game_embed(ctx: &Context<'_>, players: &HashSet<UserId>, scores: &HashMap<UserId, u32>, max_roll: u32) -> CreateEmbed {
    // Sort scores for display
    let mut sorted: Vec<_> = scores.iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(a.1));

    let mapping = sorted.iter().map(|(uid, score)| {
        (format!("<@{}>", uid), score.to_string(), false)
    });

    let player_list_str = players.iter()
        .map(|id| format!("<@{}>", id))
        .collect::<Vec<_>>()
        .join(", ");

    crate::helper::create_base_embed(&ctx)
        .title(format!("{}'s Gambling Session!", ctx.author().name))
        .description(format!("Max is {}, Click the button below to roll!", max_roll))
        .field(format!("Players ({})", players.len()), player_list_str, false)
        .fields(mapping)
}

fn get_inclusive_roll(max: u32) -> u32 {
    let mut rng = rand::rng();
    rng.random_range(1..=max)
}