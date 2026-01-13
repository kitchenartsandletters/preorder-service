"""
Centralized selectors for the Edelweiss UI.

These are based on the HTML snippets you provided.
If Edelweiss changes class names, fix them here (and only here).
"""

# Title card (search result / main detail card)
TITLE_LINK_ID = "#title-actions-button"
TITLE_TEXT = ".titleName___t0XBl"
CONTRIBUTORS = ".contributors___d5659"

BIBLIO_BLOCK = ".biblioArea___DaP4J"
BIBLIO_ONE_TWO = ".biblioOneAndTwo___ST1Sp"

# In biblio, there are many .dotDot items — we search by text patterns:
DOTDOT = ".dotDot"

# Cover image
COVER_IMG = ".jacketCover___RhV1i img"

# Modal / tabs
# The body HTML container you referenced:
BODY_TABPANEL = "#title-references-tabpanel-1"
BODY_TABPANEL_CONTENT = "#title-references-tabpanel-1"

# Images tab and image thumbnails
IMAGES_TAB_BUTTON_XPATH = "//button[normalize-space()='Images']"
IMAGES_THUMBNAILS = "button.unstyledButton img"