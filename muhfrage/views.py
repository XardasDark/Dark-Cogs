"""
views.py – Embed- & View-Builder (reine Funktionen)

Baut die veröffentlichte Teilnehmen-Nachricht, deren persistenten Button sowie
Übersichts-Embeds. Der Teilnehmen-Button trägt eine strukturierte custom_id und
wird zentral über on_interaction im Cog geroutet (übersteht Neustarts).
"""

from typing import Any, Dict, Optional

import discord

from .constants import (
    COLOR_OPEN, COLOR_DRAFT, COLOR_CLOSED, COLOR_INFO,
    STATUS_LABELS, VISIBILITY_OPTIONS, TIMING_OPTIONS,
    QUESTION_TYPES, option_letter,
)
from . import models, results

JOIN_PREFIX = "muhfrage_join"   # custom_id: f"{JOIN_PREFIX}:{slug}"


def _status_color(status: str) -> int:
    return {"draft": COLOR_DRAFT, "open": COLOR_OPEN, "closed": COLOR_CLOSED}.get(status, COLOR_INFO)


# ─────────────────────────────────────────────────────────────────────────────
# ÖFFENTLICHE TEILNEHMEN-NACHRICHT
# ─────────────────────────────────────────────────────────────────────────────

def build_public_embed(survey: Dict[str, Any], response_count: int,
                       responses: Optional[Dict[str, Dict[str, Any]]] = None,
                       guild: Optional["discord.Guild"] = None) -> discord.Embed:
    # Live nur öffentlich zeigen, wenn Ergebnisse auch öffentlich sind (kein Manager-Leak)
    is_live = (survey.get("results_timing") == "live"
               and survey.get("results_visibility") == "public")
    embed = discord.Embed(
        title=f"📊 {survey['title']}",
        description=survey.get("description") or None,
        color=_status_color(survey["status"]),
    )

    # Fragen-Übersicht (bei Live-Umfragen mit aktuellem Zwischenstand)
    if is_live and responses is not None:
        for pos, q in enumerate(survey["questions"], start=1):
            embed.add_field(
                name=f"{pos}. {q['text']}",
                value=results.live_result_value(q, responses, survey.get("anonymous", False), guild),
                inline=False,
            )
    else:
        lines = []
        for pos, q in enumerate(survey["questions"], start=1):
            meta = QUESTION_TYPES.get(q["type"], {})
            lines.append(f"**{pos}.** {q['text']}  ·  _{meta.get('name', q['type'])}_")
            if q.get("options"):
                opts = "  ".join(f"`{option_letter(i)}` {o}" for i, o in enumerate(q["options"]))
                lines.append(opts)
        if lines:
            embed.add_field(name="Fragen", value="\n".join(lines)[:1024], inline=False)

    hinweise = []
    if survey.get("anonymous"):
        hinweise.append("🕵️ Anonyme Abstimmung")
    if survey.get("allow_change"):
        hinweise.append("🔁 Antwort änderbar bis zum Ende")
    if is_live:
        hinweise.append("👀 Ergebnisse sind live sichtbar")
    if hinweise:
        embed.add_field(name="Hinweise", value=" · ".join(hinweise), inline=False)

    # Wer darf teilnehmen (Allowlist)
    users = survey.get("allowed_user_ids", [])
    roles = survey.get("allowed_role_ids", [])
    if users or roles:
        teile = []
        if roles:
            teile.append(" ".join(f"<@&{r}>" for r in roles))
        if users:
            teile.append(" ".join(f"<@{u}>" for u in users))
        embed.add_field(name="🔒 Berechtigt zur Teilnahme", value="\n".join(teile)[:1024], inline=False)

    # Automatisches Ende (nur bei laufenden Umfragen relevant)
    if survey["status"] == "open" and models.has_autoclose(survey):
        dt = models.deadline_dt(survey)
        parts = []
        if dt:
            parts.append(f"⏰ endet <t:{int(dt.timestamp())}:R>")
        ac = survey.get("autoclose", {})
        if ac.get("count"):
            parts.append(f"🔢 spätestens bei {ac['count']} Stimmen")
        if ac.get("all_voted"):
            parts.append("✅ sobald alle Berechtigten abgestimmt haben")
        if parts:
            embed.add_field(name="Automatisches Ende", value=" · ".join(parts), inline=False)

    status = STATUS_LABELS.get(survey["status"], survey["status"])
    embed.set_footer(text=f"{status} · {response_count} Teilnahmen · ID: {survey['id']} · Teilnahme: /muhfrage teilnehmen {survey['id']}")
    return embed


def build_join_view(survey: Dict[str, Any]) -> discord.ui.View:
    """View mit persistentem Teilnehmen-Button (Callback läuft über on_interaction)."""
    view = discord.ui.View(timeout=None)
    disabled = survey["status"] != "open"
    view.add_item(discord.ui.Button(
        label="Teilnehmen" if not disabled else "Umfrage beendet",
        emoji="🗳️" if not disabled else None,
        style=discord.ButtonStyle.primary if not disabled else discord.ButtonStyle.secondary,
        custom_id=f"{JOIN_PREFIX}:{survey['id']}",
        disabled=disabled,
    ))
    return view


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-ÜBERSICHT
# ─────────────────────────────────────────────────────────────────────────────

def build_overview_embed(survey: Dict[str, Any], response_count: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{STATUS_LABELS.get(survey['status'], '')}  {survey['title']}",
        description=survey.get("description") or None,
        color=_status_color(survey["status"]),
    )
    embed.add_field(name="ID", value=f"`{survey['id']}`", inline=True)
    embed.add_field(name="Teilnahmen", value=str(response_count), inline=True)
    embed.add_field(name="Sichtbarkeit", value=VISIBILITY_OPTIONS.get(survey["results_visibility"], "?"), inline=True)
    embed.add_field(name="Anonym", value="Ja" if survey.get("anonymous") else "Nein", inline=True)
    embed.add_field(name="Ergebnisse", value=TIMING_OPTIONS.get(survey["results_timing"], "?"), inline=True)
    embed.add_field(name="Änderbar", value="Ja" if survey.get("allow_change") else "Nein", inline=True)
    embed.add_field(name="Runde", value=str(survey.get("run", 1)), inline=True)
    embed.add_field(name="Ergebnisse behalten", value="Ja" if survey.get("keep_history") else "Nein", inline=True)

    # Automatisches Ende
    embed.add_field(name="⏰ Automatisches Ende", value=models.autoclose_summary(survey), inline=False)

    # Teilnehmer-Beschränkung
    users = survey.get("allowed_user_ids", [])
    roles = survey.get("allowed_role_ids", [])
    if users or roles:
        teile = []
        if roles:
            teile.append("Rollen: " + ", ".join(f"<@&{r}>" for r in roles))
        if users:
            teile.append("Mitglieder: " + ", ".join(f"<@{u}>" for u in users))
        embed.add_field(name="🔒 Teilnehmer-Beschränkung", value="\n".join(teile)[:1024], inline=False)
    else:
        embed.add_field(name="Teilnehmer", value="Alle dürfen teilnehmen", inline=False)

    # Fragen
    if survey["questions"]:
        qlines = []
        for pos, q in enumerate(survey["questions"], start=1):
            meta = QUESTION_TYPES.get(q["type"], {})
            qlines.append(
                f"**{pos}.** {q['text']}\n"
                f"   {meta.get('emoji', '')} {meta.get('name', q['type'])} — {models.question_summary(q)}"
                + (f"\n   Kandidaten: {', '.join(q['options'])}" if q.get("options") else "")
            )
        embed.add_field(name=f"Fragen ({len(survey['questions'])})", value="\n".join(qlines)[:1024], inline=False)
    else:
        embed.add_field(name="Fragen", value="_Noch keine Fragen. Nutze den Builder, um welche hinzuzufügen._", inline=False)

    return embed


def build_list_embed(surveys: Dict[str, Dict[str, Any]], counts: Dict[str, int]) -> discord.Embed:
    embed = discord.Embed(title="📋 Umfragen", color=COLOR_INFO)
    if not surveys:
        embed.description = "_Es gibt noch keine Umfragen. Erstelle eine mit `/muhfrage erstellen`._"
        return embed
    lines = []
    for slug, s in surveys.items():
        status = STATUS_LABELS.get(s["status"], s["status"])
        run = s.get("run", 1)
        run_tag = f" · Runde {run}" if run > 1 else ""
        lines.append(f"`{slug}` · {status}{run_tag} · **{s['title']}** · "
                     f"{len(s['questions'])} Fragen · {counts.get(slug, 0)} Teilnahmen")
    embed.description = "\n".join(lines)[:4000]
    return embed
