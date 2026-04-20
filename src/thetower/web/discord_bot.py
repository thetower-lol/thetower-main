import streamlit as st


def discord_bot_page():
    st.title("🤖 The Tower Discord Bot")
    st.markdown(
        "Add the **The Tower** Discord bot to your server! "
        "The bot provides tournament stats, live results, player lookups, and more - "
        "all directly in your Discord server."
    )

    st.link_button("➕ Add the bot to your server", "https://bot.thetower.lol", type="primary", use_container_width=True)

    st.markdown("---")
    st.markdown("### Features")
    st.markdown(
        "- 📊 **Live results** - tournament standings and bracket data during tourney\n"
        "- 🔍 **Player lookups** - wave history, stats, and league progression\n"
        "- 🎭 **More to Come**\n"
    )

    st.markdown("### Getting Started")
    st.markdown("Visit **[bot.thetower.lol](https://bot.thetower.lol)** to add the bot to your server and start using its features.")


discord_bot_page()
