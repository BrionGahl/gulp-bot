//! Who may use the assistant, who counts as an Officer and who as a Developer. Plain `std` only, so
//! it can be tested without Discord; roles are their numeric IDs.

/// What a member may do with the assistant.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    /// Not a raider: the assistant doesn't answer.
    Denied,
    /// A raider: may talk to the assistant.
    Raider,
    /// An Officer: may talk to it too, and may ask it to change things.
    Officer,
    /// A Developer: everything an Officer may, and the only one who may run the `/ai` commands that
    /// switch the assistant off, change its model, reset limits and so on.
    Developer,
}

impl Access {
    pub fn is_allowed(self) -> bool {
        self != Access::Denied
    }

    /// Whether the member has the standing of an Officer, which a Developer has too.
    pub fn is_officer(self) -> bool {
        matches!(self, Access::Officer | Access::Developer)
    }

    /// Higher is more trusted: what is said to a member is shown only to members of this level or
    /// higher (see `history::History`).
    pub fn level(self) -> u8 {
        match self {
            Access::Denied => 0,
            Access::Raider => 1,
            Access::Officer => 2,
            Access::Developer => 3,
        }
    }
}

/// The access of a member holding `member_roles`, given the roles the server registered with
/// `/settings`. A Developer is a holder of the registered developer role, and nobody else: not even
/// an administrator is one, so that developer powers are only ever given on purpose. An Officer is a
/// holder of the registered moderator role or a server administrator, the same two the bot treats
/// alike everywhere else (`/settings`, `/wcl`). A role that isn't registered grants nothing, so a
/// server that hasn't set up its roles gets no assistant for its members rather than one open to
/// everybody; administrators are still Officers there, so that somebody can always use it.
pub fn access(
    member_roles: &[u64],
    raider_role: Option<u64>,
    officer_role: Option<u64>,
    developer_role: Option<u64>,
    is_administrator: bool,
) -> Access {
    let holds = |role: Option<u64>| role.is_some_and(|role| member_roles.contains(&role));

    if holds(developer_role) {
        Access::Developer
    } else if is_administrator || holds(officer_role) {
        Access::Officer
    } else if holds(raider_role) {
        Access::Raider
    } else {
        Access::Denied
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RAIDER: u64 = 10;
    const OFFICER: u64 = 20;
    const TRIAL: u64 = 30;
    const DEVELOPER: u64 = 40;

    fn access_of(member_roles: &[u64]) -> Access {
        access(member_roles, Some(RAIDER), Some(OFFICER), Some(DEVELOPER), false)
    }

    #[test]
    fn an_administrator_is_an_officer_whatever_roles_they_hold() {
        assert_eq!(access(&[], Some(RAIDER), Some(OFFICER), None, true), Access::Officer);
        assert_eq!(access(&[RAIDER], Some(RAIDER), Some(OFFICER), None, true), Access::Officer);
        // Even in a server that hasn't registered its roles.
        assert_eq!(access(&[], None, None, None, true), Access::Officer);
        assert!(access(&[], None, None, None, true).is_officer());
    }

    #[test]
    fn the_more_trusted_have_the_higher_level() {
        assert!(Access::Denied.level() < Access::Raider.level());
        assert!(Access::Raider.level() < Access::Officer.level());
    }

    #[test]
    fn a_raider_is_allowed_but_is_not_an_officer() {
        let access = access_of(&[1, RAIDER]);
        assert_eq!(access, Access::Raider);
        assert!(access.is_allowed());
        assert!(!access.is_officer());
    }

    #[test]
    fn an_officer_is_allowed_with_or_without_the_raider_role() {
        assert_eq!(access_of(&[OFFICER]), Access::Officer);
        assert_eq!(access_of(&[RAIDER, OFFICER]), Access::Officer);
        assert!(access_of(&[OFFICER]).is_officer());
    }

    #[test]
    fn everybody_else_is_denied() {
        assert_eq!(access_of(&[]), Access::Denied);
        assert_eq!(access_of(&[1, 2, 3]), Access::Denied);
        // A trial is not a raider.
        assert_eq!(access_of(&[TRIAL]), Access::Denied);
        assert!(!access_of(&[]).is_allowed());
        assert!(!access_of(&[]).is_officer());
    }

    #[test]
    fn roles_that_are_not_registered_grant_nothing() {
        assert_eq!(access(&[RAIDER, OFFICER, DEVELOPER], None, None, None, false), Access::Denied);
        assert_eq!(access(&[RAIDER], None, Some(OFFICER), None, false), Access::Denied);
        assert_eq!(access(&[OFFICER], Some(RAIDER), None, None, false), Access::Denied);
        assert_eq!(access(&[RAIDER], Some(RAIDER), None, None, false), Access::Raider);
    }

    #[test]
    fn a_developer_is_above_an_officer_and_still_an_officer() {
        let developer = access_of(&[DEVELOPER]);
        assert_eq!(developer, Access::Developer);
        assert!(developer.is_allowed());
        assert!(developer.is_officer());
        assert!(Access::Officer.level() < Access::Developer.level());

        // The developer role wins over every other role the member holds.
        assert_eq!(access_of(&[RAIDER, OFFICER, DEVELOPER]), Access::Developer);
        assert_eq!(access(&[DEVELOPER], Some(RAIDER), Some(OFFICER), Some(DEVELOPER), true), Access::Developer);
    }

    #[test]
    fn nobody_else_is_a_developer() {
        assert_ne!(access_of(&[RAIDER]), Access::Developer);
        assert_ne!(access_of(&[RAIDER, OFFICER]), Access::Developer);
        assert_ne!(access_of(&[]), Access::Developer);
        // Not even an administrator.
        assert_eq!(access(&[], Some(RAIDER), Some(OFFICER), Some(DEVELOPER), true), Access::Officer);
        assert_ne!(access(&[], None, None, None, true), Access::Developer);
    }

    #[test]
    fn an_unregistered_developer_role_makes_nobody_a_developer() {
        assert_eq!(access(&[DEVELOPER], Some(RAIDER), Some(OFFICER), None, false), Access::Denied);
        assert_eq!(access(&[DEVELOPER, RAIDER], Some(RAIDER), Some(OFFICER), None, false), Access::Raider);
    }
}
