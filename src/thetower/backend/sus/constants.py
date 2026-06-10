from enum import Enum


class IdChangeReason(Enum):
    GAME_CHANGED_ID = "game_changed_id"
    FIXING_TYPO = "fixing_typo"
    STARTING_FRESH = "starting_fresh"
    NEW_GAME_INSTANCE = "new_game_instance"


REASON_LABELS = {
    IdChangeReason.GAME_CHANGED_ID: "The game changed my ID",
    IdChangeReason.FIXING_TYPO: "I'm fixing a typo",
    IdChangeReason.STARTING_FRESH: "I'm starting fresh",
    IdChangeReason.NEW_GAME_INSTANCE: "I'm adding a new game instance",
}

REASON_DESCRIPTIONS = {
    IdChangeReason.NEW_GAME_INSTANCE: "I play on multiple devices and want to link them together",
    IdChangeReason.GAME_CHANGED_ID: "The game assigned me a new ID, but I have results on my old ID too",
    IdChangeReason.FIXING_TYPO: "I entered the wrong ID when verifying. My old ID has no real data",
    IdChangeReason.STARTING_FRESH: "I have a new game account and want to use this ID. Please retire/hide my old ID",
}

# NEW_GAME_INSTANCE is self-service; all others require mod review
REASON_REQUIRES_MOD_REVIEW = {
    IdChangeReason.GAME_CHANGED_ID,
    IdChangeReason.FIXING_TYPO,
    IdChangeReason.STARTING_FRESH,
}
