"""
util.py – kleine Helfer für KuhHilfe

Emoji-Handling: Nutzer geben Emojis in Text-/Modalfeldern ein. Dort wird ein
Custom-Emoji-Kürzel wie ``:wow:`` NICHT automatisch in ``<:wow:123…>`` umgewandelt.
Discord lehnt rohe Kürzel als Komponenten-Emoji (Select/Button) mit Fehler 50035
ab. Darum:

* ``resolve_emoji`` – beim Speichern: löst ``:name:`` über die Server-Emojis auf,
  akzeptiert Unicode und fertige ``<:name:id>``-Formen, meldet Ungültiges zurück.
* ``clean_emoji`` – beim Rendern: gibt nur etwas zurück, das garantiert als
  Komponenten-Emoji taugt (sonst ``None``), damit alte/kaputte Werte nichts crashen.
"""

import re
from typing import Optional, Tuple

import discord

_CUSTOM_RE = re.compile(r"<a?:\w{2,32}:\d{15,25}>")


def _shortcode_to_unicode(shortcode: str) -> Optional[str]:
    """Löst ein Standard-Emoji-Kürzel (z. B. ':video_game:') in Unicode auf.

    Nutzt die optionale ``emoji``-Bibliothek; fehlt sie, wird None zurückgegeben
    (dann greift der Hinweis, das Emoji direkt einzufügen).
    """
    try:
        import emoji as _emoji
    except Exception:
        return None
    try:
        out = _emoji.emojize(shortcode, language="alias")
    except TypeError:  # ältere emoji-API ohne language-Parameter
        out = _emoji.emojize(shortcode, use_aliases=True)
    except Exception:
        return None
    if out and out != shortcode and not out.isascii():
        return out
    return None


def clean_emoji(raw: Optional[str]) -> Optional[str]:
    """Render-sicher: nur gültige Komponenten-Emojis durchlassen, sonst None."""
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    if _CUSTOM_RE.fullmatch(raw):
        return raw
    # Reiner ASCII-Text (z. B. ":wow:", "x") ist kein gültiges Emoji
    if raw.isascii():
        return None
    # Nicht-ASCII → als Unicode-Emoji behandeln
    return raw


def emoji_for_input(raw: Optional[str]) -> Optional[str]:
    """Für Modal-Vorbelegung: Custom-Emoji ``<:name:id>`` als kurzes ``:name:`` zeigen."""
    if not raw:
        return None
    m = re.fullmatch(r"<a?:(\w{2,32}):\d{15,25}>", str(raw).strip())
    if m:
        return f":{m.group(1)}:"
    return str(raw).strip() or None


def resolve_emoji(guild: discord.Guild, raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Eingabe-seitig: wandelt eine Nutzereingabe in einen speicherbaren Emoji-Wert.
    Rückgabe (wert, fehler): bei Erfolg (wert, None), sonst (None, fehlertext).
    """
    if not raw:
        return None, None
    raw = str(raw).strip()
    if not raw:
        return None, None
    if _CUSTOM_RE.fullmatch(raw):
        return raw, None
    if raw.isascii():
        name = raw.strip(":")
        if name:
            # 1) Custom-Emoji dieses Servers
            emoji = discord.utils.get(guild.emojis, name=name)
            if emoji is not None:
                return str(emoji), None
            # 2) Standard-Emoji-Kürzel (z. B. :video_game: → 🎮)
            uni = _shortcode_to_unicode(f":{name}:")
            if uni is not None:
                return uni, None
        return None, (f"Emoji `{raw}` konnte ich nicht zuordnen. Füge das Emoji direkt ein "
                      "(z. B. 🎮) oder nutze ein Custom-Emoji **dieses** Servers.")
    # Nicht-ASCII → Unicode-Emoji
    return raw, None
