use std::collections::{HashMap, HashSet};
use std::time::Duration;
use poise::{CreateReply, ReplyHandle};
use poise::futures_util::StreamExt;
use poise::serenity_prelude::{ButtonStyle, ComponentInteractionCollector, CreateActionRow, CreateButton, CreateInteractionResponse, CreateInteractionResponseMessage, UserId};
use rand::Rng;
use crate::types::bot::{Context, Error};

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

#[poise::command(
    prefix_command,
    slash_command,
    category = "Gambling",
)]
pub async fn start_gambling_game(ctx: Context<'_>, #[description = "Max number that can be rolled"] #[min = 2] max_roll: Option<u32>) -> Result<(), Error> {
    let max_roll = max_roll.unwrap_or(100);

    // Unique ID for command invocation (to separate seperate games played at once)
    let button_id = format!("session-{}", ctx.id());

    let mut players = HashSet::new();
    players.insert(ctx.author().id);

    let player_list = players.iter()
        .map(|id| format!("<@{}>", id))
        .collect::<Vec<_>>()
        .join("\n");


    let embed = crate::helper::create_base_embed(&ctx)
        .title(format!("{}'s Gambling Session!", ctx.author().name))
        .description(format!("Max is {}, Click the button below to join!", max_roll))
        .field(format!("Current Players ({})", players.len()), player_list, true);

    let buttons = vec![CreateActionRow::Buttons(vec![
        CreateButton::new(&button_id)
            .label("Join / Leave Session")
            .style(ButtonStyle::Success)
            .emoji('🎲')
    ])];

    let reply = ctx.send(CreateReply::default().embed(embed).components(buttons.clone())).await?;

    // This loop terminates after 60s, then the session ends
    let mut collector = ComponentInteractionCollector::new(ctx)
        .timeout(Duration::from_secs(60))
        .filter(move |press| press.data.custom_id == button_id)
        .stream();
    while let Some(press) = collector.next().await {
        let user = &press.user;

        // Lets them leave if they are already entered.
        if players.contains(&user.id) {
            players.remove(&user.id);
            press.create_response(
                ctx,
                CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new()
                        .content("You left the lobby.")
                        .ephemeral(true)
                )
            ).await?;
        } else {
            players.insert(user.id);
            press.create_response(
                ctx,
                CreateInteractionResponse::Message(
                    CreateInteractionResponseMessage::new()
                        .content("You joined the lobby.")
                        .ephemeral(true)
                )
            ).await?;
        }

        // Update UI
        let player_list = if players.is_empty() {
            String::from("No players")
        } else {
            players.iter()
                .map(|id| format!("<@{}>", id))
                .collect::<Vec<_>>()
                .join("\n")
        };

        let edited_embed = crate::helper::create_base_embed(&ctx)
            .title(format!("{}'s Gambling Session!", ctx.author().name))
            .description(format!("Max is {}, Click the button below to join!", max_roll))
            .field(format!("Current Players ({})", players.len()), player_list, true);

        reply.edit(ctx, CreateReply::default().embed(edited_embed).components(buttons.clone())).await?;
    }

    if players.len() < 2 {
        let embed = crate::helper::create_base_embed(&ctx)
            .title("Gambling Session Cancelled")
            .description("Not enough players joined the session (Minimum 2).");

        reply.edit(ctx, CreateReply::default().embed(embed).components(vec![])).await?;
        return Ok(())
    }

    let roll_button_id = format!("game-{}", ctx.id());

    let final_player_list = players.iter()
        .map(|id| format!("<@{}>", id))
        .collect::<Vec<_>>()
        .join(", ");

    let edited_embed = crate::helper::create_base_embed(&ctx)
        .title(format!("{}'s Gambling Session!", ctx.author().name))
        .description(format!("Max is {}, Click the button below to roll!", max_roll))
        .field(format!("Current Players ({})", players.len()), format!("{}", final_player_list), true);

    let roll_buttons = vec![CreateActionRow::Buttons(vec![
        CreateButton::new(&roll_button_id)
            .label("Roll")
            .style(ButtonStyle::Secondary)
            .emoji('🎲')
    ])];

    // Game Started
    reply.edit(ctx, CreateReply::default().embed(edited_embed).components(roll_buttons.clone())).await?;

    let mut has_rolled = HashSet::new();
    let mut scores = HashMap::new();

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

        let mut sorted_scores: Vec<_> = scores.iter().collect();
        sorted_scores.sort_by(|a, b| b.1.cmp(a.1));
        let mapping = sorted_scores.iter().map(|(user_id, roll)| {
            (format!("<@{}>", user_id), format!("{}", roll), false)
        });
        let edited_embed = crate::helper::create_base_embed(&ctx)
            .title(format!("{}'s Gambling Session!", ctx.author().name))
            .description(format!("Max is {}, Click the button below to roll!", max_roll))
            .fields(mapping);

        reply.edit(ctx, CreateReply::default().embed(edited_embed).components(roll_buttons.clone())).await?;
    }

    if scores.len() < 2 {
        let embed = crate::helper::create_base_embed(&ctx)
            .title("Gambling Session Cancelled")
            .description("Not enough players rolled (Minimum 2).");

        reply.edit(ctx, CreateReply::default().embed(embed).components(vec![])).await?;
        return Ok(());
    }

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

async fn run_lobby(ctx: Context<'_>, max_roll: u32) -> Result<(HashSet<UserId>, ReplyHandle<'_>), Error> {
    let button_id = format!("session-{}", ctx.id());

    let mut players = HashSet::new();
    players.insert(ctx.author().id);

    let buttons = vec![CreateActionRow::Buttons(vec![
        CreateButton::new(&button_id)
            .label("Join / Leave Session")
            .style(ButtonStyle::Success)
            .emoji('🎲')
    ])];

    // TODO: Make this a function
    let make_embed = |players: &HashSet<UserId>| {
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
    };

    let reply = ctx.send(
        CreateReply::default()
            .embed(make_embed(&players))
            .components(buttons.clone())
    ).await?;

    let mut collector = ComponentInteractionCollector::new(ctx)
        .timeout(Duration::from_secs(60))
        .filter(move |press| press.data.custom_id == button_id)
        .stream();
    while let Some(press) = collector.next().await {
        let user_id = press.user.id;

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
            .embed(make_embed(&players))
            .components(buttons.clone())
        ).await?;
    }

    Ok((players, reply))
}

// async fn run_game(ctx: Context<'_>, reply: ReplyHandle<'_>, players: HashSet<UserId>, max_roll: u32) -> Result<(HashMap<UserId, u32>, ReplyHandle), Error> {
//     let roll_button_id = format!("game-{}", ctx.id());
//
//     let mut has_rolled = HashSet::new();
//     let mut scores = HashMap::new();
//
//     let roll_buttons = vec![CreateActionRow::Buttons(vec![
//         CreateButton::new(&roll_button_id)
//             .label("Roll")
//             .style(ButtonStyle::Secondary)
//             .emoji('🎲')
//     ])];
//
//     // TODO: Pull into function
//     let make_embed = |scores: &HashMap<UserId, u32>| {
//         // Sort scores for display
//         let mut sorted: Vec<_> = scores.iter().collect();
//         sorted.sort_by(|a, b| b.1.cmp(a.1));
//
//         let mapping = sorted.iter().map(|(uid, score)| {
//             (format!("<@{}>", uid), score.to_string(), false)
//         });
//
//         let player_list_str = players.iter()
//             .map(|id| format!("<@{}>", id))
//             .collect::<Vec<_>>()
//             .join(", ");
//
//         crate::helper::create_base_embed(&ctx)
//             .title(format!("{}'s Gambling Session!", ctx.author().name))
//             .description(format!("Max is {}, Click the button below to roll!", max_roll))
//             .field(format!("Players ({})", players.len()), player_list_str, false)
//             .fields(mapping)
//     };
//
//     let mut collector = ComponentInteractionCollector::new(ctx)
//         .timeout(Duration::from_secs(120))
//         .filter(move |press| press.data.custom_id == roll_button_id)
//         .stream();
//     while let Some(press) = collector.next().await {
//         let user = &press.user;
//
//         if !players.contains(&user.id) {
//             press.create_response(
//                 ctx,
//                 CreateInteractionResponse::Message(
//                     CreateInteractionResponseMessage::new()
//                         .content("You are not in this game!")
//                         .ephemeral(true)
//                 )
//             ).await?;
//             continue;
//         }
//
//         if has_rolled.contains(&user.id) {
//             press.create_response(
//                 ctx,
//                 CreateInteractionResponse::Message(
//                     CreateInteractionResponseMessage::new()
//                         .content("You already rolled!")
//                         .ephemeral(true)
//                 )
//             ).await?;
//             continue;
//         }
//
//         has_rolled.insert(user.id);
//         let roll = get_inclusive_roll(max_roll);
//         scores.insert(user.id, roll);
//
//         press.create_response(ctx, CreateInteractionResponse::Message(CreateInteractionResponseMessage::new().content(format!("You rolled {}!", roll)).ephemeral(true))).await?;
//
//         if has_rolled.len() == players.len() {
//             break;
//         }
//
//         let mut sorted_scores: Vec<_> = scores.iter().collect();
//         sorted_scores.sort_by(|a, b| b.1.cmp(a.1));
//         let mapping = sorted_scores.iter().map(|(user_id, roll)| {
//             (format!("<@{}>", user_id), format!("{}", roll), false)
//         });
//         let edited_embed = crate::helper::create_base_embed(&ctx)
//             .title(format!("{}'s Gambling Session!", ctx.author().name))
//             .description(format!("Max is {}, Click the button below to roll!", max_roll))
//             .fields(mapping);
//
//         reply.edit(ctx, CreateReply::default().embed(edited_embed).components(roll_buttons.clone())).await?;
//     }
//
//     Ok((scores, reply))
// }
//
// async fn show_results(ctx: &Context<'_>, scores: HashMap<UserId, u32>) -> None {
//
// }

fn get_inclusive_roll(max: u32) -> u32 {
    let mut rng = rand::rng();
    rng.random_range(1..=max)
}