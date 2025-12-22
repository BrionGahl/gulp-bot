use serde::Deserialize;

#[derive(Deserialize, Debug)]
pub struct Raids {
    pub raids: Vec<Raid>,
}

#[derive(Deserialize, Debug)]
pub struct Raid {
    pub id: u32,
    pub date: String,
    pub start_time: String,
    pub end_time: String,
    pub instance: String,
    pub optional: bool,
    pub difficulty: String,
    pub status: String,
    pub present_size: u32,
    pub total_size: u32,
    pub notes: Option<String>,
    pub selections_image: Option<String>,
    pub signups: Option<Vec<Signup>>,
}

#[derive(Deserialize, Debug)]
pub struct Signup {
    pub character: Character,
    pub status: String,
    pub comment: Option<String>,
    pub selected: bool,
    pub class: String,
    pub role: String,
}

#[derive(Deserialize, Debug)]
pub struct Character {
    pub id: u32,
    pub name: String,
    pub realm: String,
    pub class: String,
    pub role: String,
    pub guest: bool,
}
