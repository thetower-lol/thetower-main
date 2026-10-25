"""A player's linked social accounts as their public profiles show them: the website player page and the bot's /lookup
and /profile.

Each account carries the player's own ``public`` choice (off by default). Profiles show only public accounts, except
to the player themselves and to moderators, who see every active account with the private ones marked. Shared here so
the website and the bot name and link accounts the same way, for every platform an account can be linked on.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

PRIVATE_MARK = "🔒 private"

# Sites named by their brand when a link points at them; any other link is named by its domain
_SITE_NAMES = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "twitch.tv": "Twitch",
    "reddit.com": "Reddit",
    "x.com": "X",
    "twitter.com": "X",
    "tiktok.com": "TikTok",
    "github.com": "GitHub",
    "discord.gg": "Discord",
    "discord.com": "Discord",
    "kick.com": "Kick",
}


def link_site_name(url: str) -> str:
    """What a link is called when nothing better is given: the site's brand ("YouTube"), else its domain."""
    host = (urlparse(url).hostname or url).lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, name in _SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def account_text(platform: str, account_id: str, name: str | None) -> str:
    """How an account is named on a profile: ``u/name`` on Reddit, ``@name`` on Discord and Twitter, else the name."""
    shown = name or account_id
    if platform == "reddit":
        return f"u/{shown}"
    if platform in ("discord", "twitter"):
        return f"@{shown}"
    return shown


def account_url(platform: str, account_id: str, name: str | None) -> str | None:
    """Where an account's name links to, or None for a platform without a public profile page."""
    if platform == "discord":
        return f"https://discord.com/users/{quote(account_id)}"
    shown = quote(name or account_id)
    if platform == "reddit":
        return f"https://www.reddit.com/user/{shown}"
    if platform == "twitch":
        return f"https://www.twitch.tv/{shown}"
    if platform == "twitter":
        return f"https://x.com/{shown}"
    return None


def profile_accounts(player, include_private: bool, include_discord: bool = True) -> list[dict]:
    """The player's active linked accounts a profile should show, Discord first, then each platform's primary first.

    Args:
        player: The KnownPlayer
        include_private: True for the player themselves and moderators; everyone else sees public accounts only
        include_discord: False where Discord accounts are already shown another way (the bot's mentions)

    Returns:
        One dict per account: platform, label (the platform's name), text, url (or None) and public
    """
    from thetower.backend.sus.models import LinkedAccount

    accounts = LinkedAccount.objects.filter(player=player, active=True)
    if not include_private:
        accounts = accounts.filter(public=True)
    if not include_discord:
        accounts = accounts.exclude(platform=LinkedAccount.Platform.DISCORD)

    platform_order = {value: i for i, value in enumerate(LinkedAccount.Platform.values)}
    labels = dict(LinkedAccount.Platform.choices)
    ordered = sorted(accounts, key=lambda la: (platform_order.get(la.platform, len(platform_order)), not la.primary, la.id))
    return [
        {
            "platform": la.platform,
            "label": labels.get(la.platform, la.platform),
            "text": account_text(la.platform, la.account_id, la.display_name),
            "url": account_url(la.platform, la.account_id, la.display_name),
            "public": la.public,
        }
        for la in ordered
    ]
