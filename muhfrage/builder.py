"""
builder.py – Interaktives Erstell-/Bearbeitungs-Panel

Ein ephemeres Panel (nur für den Ersteller sichtbar) zum Konfigurieren einer
Umfrage: Einstellungen umschalten, Fragen hinzufügen/entfernen, Teilnehmer
beschränken. Jede Änderung wird sofort in die Config gespeichert, sodass der
Entwurf per `/muhfrage bearbeiten` jederzeit fortgesetzt werden kann.
"""

from typing import Any, Callable, Dict, List, Optional

import discord

from . import models
from .constants import COLOR_DRAFT, QUESTION_TYPES, OPTION_BASED_TYPES
from .views import build_overview_embed


# ─────────────────────────────────────────────────────────────────────────────
# TITEL-MODAL (für Erstellung und Titel-Bearbeitung)
# ─────────────────────────────────────────────────────────────────────────────

class TitleModal(discord.ui.Modal):
    def __init__(self, on_done: Callable, title_default: str = "", desc_default: str = ""):
        super().__init__(title="Umfrage benennen")
        self._on_done = on_done
        self.f_title = discord.ui.TextInput(label="Titel", max_length=100,
                                            default=title_default or None, required=True)
        self.f_desc = discord.ui.TextInput(label="Beschreibung (optional)",
                                           style=discord.TextStyle.paragraph, max_length=500,
                                           default=desc_default or None, required=False)
        self.add_item(self.f_title)
        self.add_item(self.f_desc)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_done(interaction, str(self.f_title.value), str(self.f_desc.value or ""))


# ─────────────────────────────────────────────────────────────────────────────
# FRAGE-MODAL (typ-spezifische Felder)
# ─────────────────────────────────────────────────────────────────────────────

def _int_or(value: str, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return fallback


class QuestionModal(discord.ui.Modal):
    """Sammelt Fragetext, Kandidaten und typ-spezifische Parameter."""

    def __init__(self, panel: "BuilderPanel", qtype: str):
        meta = QUESTION_TYPES.get(qtype, {})
        super().__init__(title=f"Frage: {meta.get('name', qtype)}"[:45])
        self.panel = panel
        self.qtype = qtype

        self.f_text = discord.ui.TextInput(label="Fragetext", max_length=200, required=True)
        self.add_item(self.f_text)

        if qtype in OPTION_BASED_TYPES:
            self.f_options = discord.ui.TextInput(
                label="Kandidaten / Optionen (eine pro Zeile)",
                style=discord.TextStyle.paragraph, max_length=1500, required=True,
                placeholder="Spieler A\nSpieler B\nSpieler C",
            )
            self.add_item(self.f_options)
        else:
            self.f_options = None

        # bis zu 3 weitere typ-spezifische Felder (Discord-Limit: 5 gesamt)
        self.f_p1 = self.f_p2 = self.f_p3 = None
        if qtype == "points_pool":
            self.f_p1 = discord.ui.TextInput(label="Gesamtpunkte", default="3", max_length=4)
            self.f_p2 = discord.ui.TextInput(label="Max. Punkte pro Kandidat (optional)",
                                             required=False, max_length=4)
        elif qtype == "plus_minus":
            self.f_p1 = discord.ui.TextInput(label="Anzahl Pluspunkte", default="1", max_length=2)
            self.f_p2 = discord.ui.TextInput(label="Anzahl Minuspunkte", default="1", max_length=2)
        elif qtype == "ranked":
            self.f_p1 = discord.ui.TextInput(label="Rang-Punkte (z.B. 5,4,3,2,1 – leer = auto)",
                                             required=False, max_length=60)
        elif qtype == "multiple_choice":
            self.f_p1 = discord.ui.TextInput(label="Max. Auswahl", default="2", max_length=2)
        elif qtype == "scale":
            self.f_p1 = discord.ui.TextInput(label="Skala von", default="1", max_length=3)
            self.f_p2 = discord.ui.TextInput(label="Skala bis", default="5", max_length=3)
        elif qtype == "text":
            self.f_p1 = discord.ui.TextInput(label="Max. Zeichen", default="300", max_length=4)
        for f in (self.f_p1, self.f_p2, self.f_p3):
            if f is not None:
                self.add_item(f)

    def _parse_options(self) -> List[str]:
        if not self.f_options:
            return []
        raw = str(self.f_options.value).splitlines()
        opts = [line.strip() for line in raw if line.strip()]
        return opts[:25]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        qtype = self.qtype
        options = self._parse_options()

        if qtype in OPTION_BASED_TYPES and len(options) < 2:
            await interaction.response.send_message(
                "❌ Bitte mindestens 2 Kandidaten angeben (max. 25).", ephemeral=True)
            return

        cfg: Dict[str, Any] = {}
        if qtype == "points_pool":
            cfg["points_total"] = max(1, _int_or(self.f_p1.value, 3))
            cap = str(self.f_p2.value).strip() if self.f_p2 else ""
            cfg["max_per_option"] = _int_or(cap, 0) or None if cap else None
        elif qtype == "plus_minus":
            cfg["plus_count"] = max(1, _int_or(self.f_p1.value, 1))
            cfg["minus_count"] = max(1, _int_or(self.f_p2.value, 1))
            cfg["value"] = 1
            if cfg["plus_count"] + cfg["minus_count"] > len(options):
                await interaction.response.send_message(
                    "❌ Plus + Minus dürfen die Kandidatenzahl nicht übersteigen.", ephemeral=True)
                return
        elif qtype == "ranked":
            raw = str(self.f_p1.value).strip() if self.f_p1 else ""
            if raw:
                try:
                    values = [int(x) for x in raw.replace(" ", "").split(",") if x]
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Rang-Punkte müssen Zahlen sein, z.B. `5,4,3,2,1`.", ephemeral=True)
                    return
            else:
                values = list(range(len(options), 0, -1))
            values = values[:len(options)]
            if len(values) < 1:
                await interaction.response.send_message("❌ Ungültige Rang-Punkte.", ephemeral=True)
                return
            cfg["rank_values"] = values
        elif qtype == "multiple_choice":
            cfg["max_choices"] = max(1, min(_int_or(self.f_p1.value, 2), len(options)))
        elif qtype == "scale":
            lo = _int_or(self.f_p1.value, 1)
            hi = _int_or(self.f_p2.value, 5)
            if hi <= lo:
                await interaction.response.send_message("❌ „Skala bis“ muss größer als „von“ sein.", ephemeral=True)
                return
            cfg["scale_min"], cfg["scale_max"] = lo, hi
        elif qtype == "text":
            cfg["max_length"] = max(10, min(_int_or(self.f_p1.value, 300), 1000))

        question = models.new_question(qtype, str(self.f_text.value), options, cfg)
        self.panel.survey["questions"].append(question)
        self.panel.mode = "main"
        await self.panel.save_and_refresh(interaction)


# ─────────────────────────────────────────────────────────────────────────────
# BUILDER-PANEL
# ─────────────────────────────────────────────────────────────────────────────

class BuilderPanel(discord.ui.View):
    def __init__(self, cog, guild: discord.Guild, survey: Dict[str, Any]):
        super().__init__(timeout=900)
        self.cog    = cog
        self.guild  = guild
        self.survey = survey
        self.mode   = "main"   # main | add_type | allow
        self.message: Optional[discord.Message] = None

    async def start(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.send_message(embed=self._embed(), view=self, ephemeral=True)
        self.message = await interaction.original_response()

    def _embed(self) -> discord.Embed:
        embed = build_overview_embed(self.survey, response_count=0)
        embed.color = COLOR_DRAFT
        if self.mode == "add_type":
            embed.set_author(name="➕ Fragetyp auswählen")
        elif self.mode == "allow":
            embed.set_author(name="🔒 Teilnehmer beschränken (leer = alle)")
        else:
            embed.set_author(name="🛠️ Umfrage-Builder")
        return embed

    async def save_and_refresh(self, interaction: discord.Interaction) -> None:
        await self.cog.store.save_survey(self.guild, self.survey)
        self._build()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self._embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    # ── Aufbau ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.clear_items()
        if self.mode == "add_type":
            self._build_add_type()
        elif self.mode == "allow":
            self._build_allow()
        else:
            self._build_main()

    def _build_main(self) -> None:
        s = self.survey
        # Einstellungen (Toggle-Select)
        settings = discord.ui.Select(placeholder="⚙️ Einstellung ändern …", row=0, options=[
            discord.SelectOption(label=f"Anonym: {'An' if s['anonymous'] else 'Aus'}", value="anonymous"),
            discord.SelectOption(
                label=f"Ergebnisse: {'Öffentlich' if s['results_visibility']=='public' else 'Nur Manager'}",
                value="visibility"),
            discord.SelectOption(
                label=f"Ergebnis-Zeitpunkt: {'Live' if s['results_timing']=='live' else 'Nach Ende'}",
                value="timing"),
            discord.SelectOption(label=f"Antwort änderbar: {'Ja' if s['allow_change'] else 'Nein'}",
                                 value="allow_change"),
        ])
        settings.callback = self._on_setting
        self.add_item(settings)

        # Frage entfernen (nur wenn vorhanden)
        if s["questions"]:
            rem = discord.ui.Select(placeholder="🗑️ Frage entfernen …", row=1, options=[
                discord.SelectOption(label=f"{i+1}. {q['text'][:80]}", value=q["id"])
                for i, q in enumerate(s["questions"][:25])
            ])
            rem.callback = self._on_remove_question
            self.add_item(rem)

        # Aktions-Buttons
        add_q = discord.ui.Button(label="Frage", emoji="➕", style=discord.ButtonStyle.success, row=2)
        allow = discord.ui.Button(label="Teilnehmer", emoji="🔒", style=discord.ButtonStyle.secondary, row=2)
        title = discord.ui.Button(label="Titel", emoji="✏️", style=discord.ButtonStyle.secondary, row=2)
        done = discord.ui.Button(label="Fertig", emoji="✅", style=discord.ButtonStyle.primary, row=2)
        discard = discord.ui.Button(label="Verwerfen", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)

        async def on_add(interaction):
            self.mode = "add_type"
            self._build()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        async def on_allow(interaction):
            self.mode = "allow"
            self._build()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        async def on_title(interaction):
            await interaction.response.send_modal(
                TitleModal(self._title_done, self.survey["title"], self.survey.get("description", "")))
        async def on_done(interaction):
            hint = ("✅ **Entwurf gespeichert.** Starte die Umfrage mit "
                    f"`/muhfrage starten {self.survey['id']}`.")
            self.clear_items()
            await interaction.response.edit_message(content=hint, embed=self._embed(), view=None)
            self.stop()
        async def on_discard(interaction):
            await self.cog.store.delete_survey(self.guild, self.survey["id"])
            self.clear_items()
            await interaction.response.edit_message(
                content="🗑️ Entwurf verworfen.", embed=None, view=None)
            self.stop()

        add_q.callback = on_add
        allow.callback = on_allow
        title.callback = on_title
        done.callback = on_done
        discard.callback = on_discard
        for b in (add_q, allow, title, done, discard):
            self.add_item(b)

    def _build_add_type(self) -> None:
        sel = discord.ui.Select(placeholder="Fragetyp wählen …", row=0, options=[
            discord.SelectOption(label=meta["name"], value=key, emoji=meta["emoji"],
                                 description=meta["desc"][:100])
            for key, meta in QUESTION_TYPES.items()
        ])
        async def on_type(interaction):
            await interaction.response.send_modal(QuestionModal(self, sel.values[0]))
        sel.callback = on_type
        self.add_item(sel)

        back = discord.ui.Button(label="Zurück", emoji="◀", style=discord.ButtonStyle.secondary, row=1)
        async def on_back(interaction):
            self.mode = "main"
            self._build()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        back.callback = on_back
        self.add_item(back)

    def _build_allow(self) -> None:
        user_sel = discord.ui.UserSelect(placeholder="Berechtigte Mitglieder wählen …",
                                         min_values=0, max_values=25, row=0)
        role_sel = discord.ui.RoleSelect(placeholder="Berechtigte Rollen wählen …",
                                         min_values=0, max_values=25, row=1)
        async def on_users(interaction):
            self.survey["allowed_user_ids"] = [u.id for u in user_sel.values]
            await self.save_and_refresh(interaction)
        async def on_roles(interaction):
            self.survey["allowed_role_ids"] = [r.id for r in role_sel.values]
            await self.save_and_refresh(interaction)
        user_sel.callback = on_users
        role_sel.callback = on_roles
        self.add_item(user_sel)
        self.add_item(role_sel)

        clear = discord.ui.Button(label="Beschränkung aufheben", style=discord.ButtonStyle.danger, row=2)
        back = discord.ui.Button(label="Zurück", emoji="◀", style=discord.ButtonStyle.secondary, row=2)
        async def on_clear(interaction):
            self.survey["allowed_user_ids"] = []
            self.survey["allowed_role_ids"] = []
            await self.save_and_refresh(interaction)
        async def on_back(interaction):
            self.mode = "main"
            self._build()
            await interaction.response.edit_message(embed=self._embed(), view=self)
        clear.callback = on_clear
        back.callback = on_back
        self.add_item(clear)
        self.add_item(back)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    async def _on_setting(self, interaction: discord.Interaction) -> None:
        # der Select liegt in items – Wert aus interaction.data
        value = interaction.data["values"][0]
        s = self.survey
        if value == "anonymous":
            s["anonymous"] = not s["anonymous"]
        elif value == "visibility":
            s["results_visibility"] = "manager" if s["results_visibility"] == "public" else "public"
        elif value == "timing":
            s["results_timing"] = "on_close" if s["results_timing"] == "live" else "live"
        elif value == "allow_change":
            s["allow_change"] = not s["allow_change"]
        await self.save_and_refresh(interaction)

    async def _on_remove_question(self, interaction: discord.Interaction) -> None:
        qid = interaction.data["values"][0]
        self.survey["questions"] = [q for q in self.survey["questions"] if q["id"] != qid]
        await self.save_and_refresh(interaction)

    async def _title_done(self, interaction: discord.Interaction, title: str, desc: str) -> None:
        self.survey["title"] = title
        self.survey["description"] = desc
        await self.save_and_refresh(interaction)
