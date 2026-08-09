"""
participation.py – Interaktive Teilnahme-Session

Eine ephemere View führt den Teilnehmer Frage für Frage durch die Umfrage. Pro
Frage-Typ werden passende Komponenten (Selects/Buttons/Modal) aufgebaut; vor dem
Abschicken wird jede Antwort validiert. Discord-Select-Limit: max. 25 Kandidaten
pro Frage.
"""

from typing import Any, Dict, List, Optional

import discord

from . import models
from .constants import COLOR_INFO, COLOR_ERROR, QUESTION_TYPES, option_letter


class ParticipationView(discord.ui.View):
    """Ephemere, schrittweise Teilnahme an einer Umfrage."""

    def __init__(self, cog, guild: discord.Guild, survey: Dict[str, Any],
                 member: discord.Member, existing: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=600)
        self.cog     = cog
        self.guild   = guild
        self.survey  = survey
        self.member  = member
        self.working: Dict[str, Any] = dict(existing or {})
        self.index   = 0
        self.active_option: Optional[int] = None   # für points_pool / scale
        self.error: str = ""
        self.message: Optional[discord.Message] = None

    # ── Lebenszyklus ──────────────────────────────────────────────────────────

    async def start(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.send_message(embed=self._embed(), view=self, ephemeral=True)
        self.message = await interaction.original_response()

    @property
    def question(self) -> Dict[str, Any]:
        return self.survey["questions"][self.index]

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self._embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    # ── Embed ─────────────────────────────────────────────────────────────────

    def _embed(self) -> discord.Embed:
        q = self.question
        meta = QUESTION_TYPES.get(q["type"], {})
        total = len(self.survey["questions"])
        embed = discord.Embed(
            title=f"🗳️ {self.survey['title']}",
            description=f"**Frage {self.index + 1}/{total}:** {q['text']}",
            color=COLOR_ERROR if self.error else COLOR_INFO,
        )

        body = self._answer_state_text(q)
        if body:
            embed.add_field(name=f"{meta.get('emoji','')} {meta.get('name', q['type'])}", value=body, inline=False)

        hint = self._hint_text(q)
        if hint:
            embed.add_field(name="ℹ️ Anleitung", value=hint, inline=False)

        if self.error:
            embed.add_field(name="⚠️ Hinweis", value=self.error, inline=False)
        return embed

    def _answer_state_text(self, q: Dict[str, Any]) -> str:
        qtype = q["type"]
        options = q.get("options", [])
        ans = self.working.get(q["id"])

        if qtype == "points_pool":
            total = q["config"]["points_total"]
            alloc = ans or {}
            used = sum(alloc.values())
            lines = []
            for i, name in enumerate(options):
                pts = alloc.get(str(i), 0)
                marker = "▶️ " if i == self.active_option else ""
                lines.append(f"{marker}`{option_letter(i)}` {name} — **{pts}**")
            lines.append(f"\n**Verbleibend: {total - used} / {total} Punkte**")
            return "\n".join(lines)

        if qtype == "plus_minus":
            plus = (ans or {}).get("plus", [])
            minus = (ans or {}).get("minus", [])
            lines = []
            for i, name in enumerate(options):
                tag = ""
                if i in plus:
                    tag = " ➕"
                elif i in minus:
                    tag = " ➖"
                lines.append(f"`{option_letter(i)}` {name}{tag}")
            return "\n".join(lines)

        if qtype == "ranked":
            ranking = ans or []
            rank_values = q["config"]["rank_values"]
            lines = []
            for pos, idx in enumerate(ranking):
                pts = rank_values[pos] if pos < len(rank_values) else 0
                lines.append(f"**{pos+1}.** {options[idx]}  ( {pts} Pkt. )")
            remaining = [options[i] for i in range(len(options)) if i not in ranking]
            if len(ranking) < len(rank_values):
                lines.append("_Noch zu platzieren:_ " + ", ".join(remaining))
            return "\n".join(lines) if lines else "_Noch nichts platziert._"

        if qtype in ("single_choice", "multiple_choice"):
            picks = ans if isinstance(ans, list) else ([ans] if ans is not None else [])
            lines = []
            for i, name in enumerate(options):
                mark = "✅" if i in picks else "▫️"
                lines.append(f"{mark} `{option_letter(i)}` {name}")
            return "\n".join(lines)

        if qtype == "scale":
            lo, hi = q["config"]["scale_min"], q["config"]["scale_max"]
            ratings = ans or {}
            lines = []
            for i, name in enumerate(options):
                val = ratings.get(str(i))
                marker = "▶️ " if i == self.active_option else ""
                shown = f"**{val}**" if val is not None else "–"
                lines.append(f"{marker}`{option_letter(i)}` {name} — {shown}")
            return "\n".join(lines)

        if qtype == "text":
            return f"**Deine Antwort:**\n> {ans}" if ans else "_Noch keine Antwort._"

        return ""

    def _hint_text(self, q: Dict[str, Any]) -> str:
        qtype = q["type"]
        cfg = q["config"]
        if qtype == "points_pool":
            return (f"Wähle einen Kandidaten aus dem Menü und passe seine Punkte mit ➖/➕ an. "
                    f"Insgesamt {cfg['points_total']} Punkte.")
        if qtype == "plus_minus":
            return (f"Wähle {cfg.get('plus_count',1)} Kandidat(en) für Plus und "
                    f"{cfg.get('minus_count',1)} für Minus.")
        if qtype == "ranked":
            return "Wähle nacheinander deine Platzierungen (Platz 1 zuerst)."
        if qtype == "single_choice":
            return "Wähle genau eine Option."
        if qtype == "multiple_choice":
            return f"Wähle bis zu {cfg.get('max_choices')} Option(en)."
        if qtype == "scale":
            return f"Wähle einen Kandidaten und vergib eine Bewertung ({cfg['scale_min']}–{cfg['scale_max']})."
        if qtype == "text":
            return "Klicke auf „Antwort eingeben“."
        return ""

    # ── Komponenten-Aufbau ────────────────────────────────────────────────────

    def _build(self) -> None:
        self.clear_items()
        q = self.question
        builder = {
            "points_pool":     self._build_points,
            "plus_minus":      self._build_plus_minus,
            "ranked":          self._build_ranked,
            "single_choice":   self._build_single,
            "multiple_choice": self._build_multiple,
            "scale":           self._build_scale,
            "text":            self._build_text,
        }.get(q["type"])
        if builder:
            builder(q)
        self._build_nav()

    def _candidate_options(self, q: Dict[str, Any], suffix=None) -> List[discord.SelectOption]:
        opts = []
        for i, name in enumerate(q["options"][:25]):
            desc = suffix(i) if suffix else None
            opts.append(discord.SelectOption(label=f"{option_letter(i)} · {name}"[:100],
                                             value=str(i), description=desc))
        return opts

    def _build_points(self, q: Dict[str, Any]) -> None:
        alloc = self.working.get(q["id"], {})
        sel = discord.ui.Select(
            placeholder="Kandidat auswählen …",
            options=self._candidate_options(q, suffix=lambda i: f"{alloc.get(str(i),0)} Punkte"),
            row=0,
        )
        async def on_pick(interaction: discord.Interaction):
            self.active_option = int(sel.values[0])
            self.error = ""
            await self._refresh(interaction)
        sel.callback = on_pick
        self.add_item(sel)

        minus = discord.ui.Button(label="−1", style=discord.ButtonStyle.danger, row=1,
                                  disabled=self.active_option is None)
        plus = discord.ui.Button(label="+1", style=discord.ButtonStyle.success, row=1,
                                 disabled=self.active_option is None)
        reset = discord.ui.Button(label="Zurücksetzen", style=discord.ButtonStyle.secondary, row=1)

        async def adjust(interaction: discord.Interaction, delta: int):
            q2 = self.question
            alloc = dict(self.working.get(q2["id"], {}))
            key = str(self.active_option)
            total = q2["config"]["points_total"]
            cap = q2["config"].get("max_per_option")
            cur = alloc.get(key, 0)
            used = sum(alloc.values())
            newval = cur + delta
            if newval < 0:
                newval = 0
            if delta > 0 and used - cur + newval > total:
                self.error = f"Nur noch {total - (used - cur)} Punkte übrig."
                await self._refresh(interaction)
                return
            if cap is not None and newval > cap:
                self.error = f"Maximal {cap} Punkte pro Kandidat."
                await self._refresh(interaction)
                return
            if newval == 0:
                alloc.pop(key, None)
            else:
                alloc[key] = newval
            self.working[q2["id"]] = alloc
            self.error = ""
            await self._refresh(interaction)

        async def on_minus(i): await adjust(i, -1)
        async def on_plus(i): await adjust(i, +1)
        async def on_reset(interaction: discord.Interaction):
            self.working[self.question["id"]] = {}
            self.active_option = None
            self.error = ""
            await self._refresh(interaction)
        minus.callback = on_minus
        plus.callback = on_plus
        reset.callback = on_reset
        self.add_item(minus)
        self.add_item(plus)
        self.add_item(reset)

    def _build_plus_minus(self, q: Dict[str, Any]) -> None:
        ans = self.working.get(q["id"], {})
        plus_sel = discord.ui.Select(
            placeholder="➕ Pluspunkt(e) vergeben …",
            options=self._candidate_options(q),
            min_values=q["config"].get("plus_count", 1),
            max_values=q["config"].get("plus_count", 1),
            row=0,
        )
        minus_sel = discord.ui.Select(
            placeholder="➖ Minuspunkt(e) vergeben …",
            options=self._candidate_options(q),
            min_values=q["config"].get("minus_count", 1),
            max_values=q["config"].get("minus_count", 1),
            row=1,
        )
        async def on_plus(interaction: discord.Interaction):
            cur = dict(self.working.get(q["id"], {}))
            cur["plus"] = [int(v) for v in plus_sel.values]
            self.working[q["id"]] = cur
            self.error = ""
            await self._refresh(interaction)
        async def on_minus(interaction: discord.Interaction):
            cur = dict(self.working.get(q["id"], {}))
            cur["minus"] = [int(v) for v in minus_sel.values]
            self.working[q["id"]] = cur
            self.error = ""
            await self._refresh(interaction)
        plus_sel.callback = on_plus
        minus_sel.callback = on_minus
        self.add_item(plus_sel)
        self.add_item(minus_sel)

    def _build_ranked(self, q: Dict[str, Any]) -> None:
        ranking = self.working.get(q["id"], [])
        remaining = [i for i in range(len(q["options"])) if i not in ranking]
        needed = len(q["config"]["rank_values"])
        if remaining and len(ranking) < needed:
            next_pos = len(ranking) + 1
            sel = discord.ui.Select(
                placeholder=f"Platz {next_pos} auswählen …",
                options=[discord.SelectOption(label=f"{option_letter(i)} · {q['options'][i]}"[:100], value=str(i))
                         for i in remaining],
                row=0,
            )
            async def on_pick(interaction: discord.Interaction):
                rk = list(self.working.get(q["id"], []))
                rk.append(int(sel.values[0]))
                self.working[q["id"]] = rk
                self.error = ""
                await self._refresh(interaction)
            sel.callback = on_pick
            self.add_item(sel)

        reset = discord.ui.Button(label="Rangfolge zurücksetzen", style=discord.ButtonStyle.secondary, row=1)
        async def on_reset(interaction: discord.Interaction):
            self.working[q["id"]] = []
            self.error = ""
            await self._refresh(interaction)
        reset.callback = on_reset
        self.add_item(reset)

    def _build_single(self, q: Dict[str, Any]) -> None:
        cur = self.working.get(q["id"])
        sel = discord.ui.Select(placeholder="Option wählen …", options=[
            discord.SelectOption(label=f"{option_letter(i)} · {name}"[:100], value=str(i),
                                 default=(cur == i))
            for i, name in enumerate(q["options"][:25])
        ], row=0)
        async def on_pick(interaction: discord.Interaction):
            self.working[q["id"]] = int(sel.values[0])
            self.error = ""
            await self._refresh(interaction)
        sel.callback = on_pick
        self.add_item(sel)

    def _build_multiple(self, q: Dict[str, Any]) -> None:
        cur = self.working.get(q["id"], [])
        maxc = min(q["config"].get("max_choices", len(q["options"])), len(q["options"]), 25)
        sel = discord.ui.Select(placeholder="Optionen wählen …",
                                min_values=1, max_values=maxc, row=0, options=[
            discord.SelectOption(label=f"{option_letter(i)} · {name}"[:100], value=str(i),
                                 default=(i in cur))
            for i, name in enumerate(q["options"][:25])
        ])
        async def on_pick(interaction: discord.Interaction):
            self.working[q["id"]] = [int(v) for v in sel.values]
            self.error = ""
            await self._refresh(interaction)
        sel.callback = on_pick
        self.add_item(sel)

    def _build_scale(self, q: Dict[str, Any]) -> None:
        ratings = self.working.get(q["id"], {})
        cand = discord.ui.Select(
            placeholder="Kandidat auswählen …",
            options=self._candidate_options(q, suffix=lambda i: (f"Bewertung: {ratings.get(str(i))}"
                                                                 if str(i) in ratings else "noch nicht bewertet")),
            row=0,
        )
        async def on_cand(interaction: discord.Interaction):
            self.active_option = int(cand.values[0])
            self.error = ""
            await self._refresh(interaction)
        cand.callback = on_cand
        self.add_item(cand)

        lo, hi = q["config"]["scale_min"], q["config"]["scale_max"]
        val_sel = discord.ui.Select(
            placeholder="Bewertung vergeben …",
            options=[discord.SelectOption(label=str(v), value=str(v)) for v in range(lo, hi + 1)][:25],
            row=1,
            disabled=self.active_option is None,
        )
        async def on_val(interaction: discord.Interaction):
            if self.active_option is None:
                return
            ratings = dict(self.working.get(q["id"], {}))
            ratings[str(self.active_option)] = int(val_sel.values[0])
            self.working[q["id"]] = ratings
            self.error = ""
            await self._refresh(interaction)
        val_sel.callback = on_val
        self.add_item(val_sel)

    def _build_text(self, q: Dict[str, Any]) -> None:
        btn = discord.ui.Button(label="✍️ Antwort eingeben", style=discord.ButtonStyle.primary, row=0)
        async def on_click(interaction: discord.Interaction):
            await interaction.response.send_modal(_TextModal(self, q))
        btn.callback = on_click
        self.add_item(btn)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _build_nav(self) -> None:
        total = len(self.survey["questions"])
        back = discord.ui.Button(label="◀ Zurück", style=discord.ButtonStyle.secondary, row=4,
                                 disabled=self.index == 0)
        async def on_back(interaction: discord.Interaction):
            self.index = max(0, self.index - 1)
            self.active_option = None
            self.error = ""
            await self._refresh(interaction)
        back.callback = on_back
        self.add_item(back)

        if self.index < total - 1:
            nxt = discord.ui.Button(label="Weiter ▶", style=discord.ButtonStyle.primary, row=4)
            async def on_next(interaction: discord.Interaction):
                ok, err = models.validate_answer(self.question, self.working.get(self.question["id"]))
                if not ok:
                    self.error = err
                    await self._refresh(interaction)
                    return
                self.index += 1
                self.active_option = None
                self.error = ""
                await self._refresh(interaction)
            nxt.callback = on_next
            self.add_item(nxt)
        else:
            submit = discord.ui.Button(label="✅ Abschicken", style=discord.ButtonStyle.success, row=4)
            submit.callback = self._on_submit
            self.add_item(submit)

        cancel = discord.ui.Button(label="Abbrechen", style=discord.ButtonStyle.danger, row=4)
        async def on_cancel(interaction: discord.Interaction):
            self.clear_items()
            await interaction.response.edit_message(
                content="❌ Teilnahme abgebrochen.", embed=None, view=None)
            self.stop()
        cancel.callback = on_cancel
        self.add_item(cancel)

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        # Alle Fragen validieren
        for i, q in enumerate(self.survey["questions"]):
            ok, err = models.validate_answer(q, self.working.get(q["id"]))
            if not ok:
                self.index = i
                self.active_option = None
                self.error = f"Frage {i+1}: {err}"
                await self._refresh(interaction)
                return

        await self.cog.store.save_response(self.guild, self.survey["id"], self.member.id, self.working)
        self.clear_items()
        await interaction.response.edit_message(
            content="✅ **Danke für deine Teilnahme!** Deine Antworten wurden gespeichert.",
            embed=None, view=None)
        self.stop()
        await self.cog.on_response_saved(self.guild, self.survey["id"])


class _TextModal(discord.ui.Modal, title="Deine Antwort"):
    def __init__(self, view: ParticipationView, question: Dict[str, Any]):
        super().__init__()
        self.view = view
        self.question = question
        self.answer = discord.ui.TextInput(
            label="Antwort",
            style=discord.TextStyle.paragraph,
            max_length=question["config"].get("max_length", 300),
            default=view.working.get(question["id"]) or None,
            required=True,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view.working[self.question["id"]] = str(self.answer.value)
        self.view.error = ""
        await self.view._refresh(interaction)
