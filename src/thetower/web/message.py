"""
Maintenance mode page for the Tower web interface.

Displayed in place of all normal pages when maintenance mode is active on the public site.
"""

from pathlib import Path

import streamlit as st

from thetower.web.maintenance import DEFAULT_HEADER, DEFAULT_MESSAGE, get_maintenance_state

state = get_maintenance_state()
header = state.get("header") or DEFAULT_HEADER
message = state.get("message") or DEFAULT_MESSAGE

_web_dir = Path(__file__).parent
_logo_path = _web_dir / "static" / "images" / "TT.png"
if _logo_path.exists():
    with st.container(horizontal_alignment="center"):
        st.image(str(_logo_path), width=400)

st.markdown(f"## 🔧 {header}")
st.markdown(message)
