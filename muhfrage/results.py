"""
results.py – Auswertung & Darstellung der Umfrage-Ergebnisse

Aggregiert die abgegebenen Antworten je Frage-Typ und baut daraus Embeds sowie
Export-Texte (TXT/CSV) für die manuelle Weiterverarbeitung.
"""

import csv
import io
from typing import Any, Dict, List, Optional, Tuple

import discord

from .constants import COLOR_INFO, STATUS_LABELS, option_letter


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATION
# ─────────────────────────────────────────────────────────────────────────────

def _collect(responses: Dict[str, Dict[str, Any]], qid: str) -> List[Any]:
    """Alle abgegebenen Antworten für eine bestimmte Frage."""
    out = []
    for user_answers in responses.values():
        if qid in user_answers and user_answers[qid] is not None:
            out.append(user_answers[qid])
    return out


def _point_totals(question: Dict[str, Any], answers: List[Any]) -> Dict[int, float]:
    """Punkt-Summen pro Options-Index für punktbasierte Frage-Typen."""
    qtype   = question["type"]
    cfg     = question.get("config", {})
    totals: Dict[int, float] = {i: 0 for i in range(len(question.get("options", [])))}

    if qtype == "points_pool":
        for ans in answers:
            for k, v in ans.items():
                totals[int(k)] = totals.get(int(k), 0) + v
    elif qtype == "plus_minus":
        value = cfg.get("value", 1)
        for ans in answers:
            for i in ans.get("plus", []):
                totals[i] = totals.get(i, 0) + value
            for i in ans.get("minus", []):
                totals[i] = totals.get(i, 0) - value
    elif qtype == "ranked":
        rank_values = cfg.get("rank_values", [])
        for ans in answers:
            for pos, idx in enumerate(ans):
                if pos < len(rank_values):
                    totals[idx] = totals.get(idx, 0) + rank_values[pos]
    return totals


def _fmt_num(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def aggregate_field(question: Dict[str, Any], responses: Dict[str, Dict[str, Any]]) -> str:
    """Baut den Ergebnis-Text (Embed-Field-Value) für eine Frage."""
    qtype   = question["type"]
    options = question.get("options", [])
    answers = _collect(responses, question["id"])

    if not answers:
        return "_Noch keine Antworten._"

    if qtype in ("points_pool", "plus_minus", "ranked"):
        totals = _point_totals(question, answers)
        ranking = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        lines = []
        for rank, (idx, score) in enumerate(ranking, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}.`")
            lines.append(f"{medal} **{option_letter(idx)}** {options[idx]} — **{_fmt_num(score)}** Pkt.")
        return "\n".join(lines)

    if qtype in ("single_choice", "multiple_choice"):
        counts = {i: 0 for i in range(len(options))}
        for ans in answers:
            picks = ans if isinstance(ans, list) else [ans]
            for i in picks:
                counts[i] = counts.get(i, 0) + 1
        total_votes = sum(counts.values()) or 1
        lines = []
        for idx, cnt in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            share = cnt / total_votes
            bar = "▰" * round(share * 10) + "▱" * (10 - round(share * 10))
            lines.append(f"**{option_letter(idx)}** {options[idx]} — {bar} **{cnt}**")
        return "\n".join(lines)

    if qtype == "scale":
        sums = {i: [] for i in range(len(options))}
        for ans in answers:
            for k, v in ans.items():
                sums[int(k)].append(v)
        lines = []
        rows = []
        for idx, vals in sums.items():
            avg = sum(vals) / len(vals) if vals else 0
            rows.append((idx, avg, len(vals)))
        for idx, avg, cnt in sorted(rows, key=lambda r: r[1], reverse=True):
            lines.append(f"**{option_letter(idx)}** {options[idx]} — ⌀ **{_fmt_num(avg)}** ({cnt} Bew.)")
        return "\n".join(lines)

    if qtype == "text":
        shown = answers[:10]
        lines = [f"• {str(a)[:150]}" for a in shown]
        if len(answers) > len(shown):
            lines.append(f"_… und {len(answers) - len(shown)} weitere (siehe Export)._")
        return "\n".join(lines)

    return "_Keine Auswertung verfügbar._"


# ─────────────────────────────────────────────────────────────────────────────
# EMBEDS
# ─────────────────────────────────────────────────────────────────────────────

def build_results_embed(survey: Dict[str, Any], responses: Dict[str, Dict[str, Any]]) -> discord.Embed:
    """Ergebnis-Embed für eine Umfrage (ein Feld pro Frage)."""
    embed = discord.Embed(
        title=f"📊 Ergebnisse: {survey['title']}",
        description=survey.get("description") or None,
        color=COLOR_INFO,
    )
    count = len(responses)
    embed.set_footer(text=f"{count} Teilnehmer · Umfrage-ID: {survey['id']} · {STATUS_LABELS.get(survey['status'], survey['status'])}")

    for pos, question in enumerate(survey["questions"], start=1):
        value = aggregate_field(question, responses)
        # Discord-Field-Limit: 1024 Zeichen
        if len(value) > 1024:
            value = value[:1015] + " …"
        embed.add_field(name=f"{pos}. {question['text']}", value=value, inline=False)

    if not survey["questions"]:
        embed.add_field(name="Keine Fragen", value="Diese Umfrage enthält keine Fragen.", inline=False)
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT  (pro-Stimme-Aufschlüsselung)
# ─────────────────────────────────────────────────────────────────────────────

def _answer_to_text(question: Dict[str, Any], answer: Any) -> str:
    """Menschenlesbare Darstellung einer einzelnen Antwort."""
    qtype   = question["type"]
    options = question.get("options", [])

    def opt(i: int) -> str:
        return options[i] if 0 <= i < len(options) else f"#{i}"

    if qtype == "points_pool":
        return ", ".join(f"{opt(int(k))}={v}" for k, v in answer.items())
    if qtype == "plus_minus":
        plus  = ", ".join(opt(i) for i in answer.get("plus", []))
        minus = ", ".join(opt(i) for i in answer.get("minus", []))
        return f"+[{plus}] -[{minus}]"
    if qtype == "ranked":
        return " > ".join(opt(i) for i in answer)
    if qtype == "single_choice":
        return opt(answer)
    if qtype == "multiple_choice":
        return ", ".join(opt(i) for i in answer)
    if qtype == "scale":
        return ", ".join(f"{opt(int(k))}={v}" for k, v in answer.items())
    if qtype == "text":
        return str(answer)
    return str(answer)


def _voter_name(guild: Optional[discord.Guild], anonymous: bool, user_id_str: str, index: int) -> str:
    if anonymous:
        return f"Teilnehmer {index}"
    if guild:
        member = guild.get_member(int(user_id_str))
        if member:
            return member.display_name
    return f"User {user_id_str}"


def build_export_txt(survey: Dict[str, Any], responses: Dict[str, Dict[str, Any]],
                     guild: Optional[discord.Guild]) -> str:
    """Detaillierte, pro-Stimme-Aufschlüsselung als Klartext."""
    anon = survey.get("anonymous", False)
    lines = [
        f"Umfrage: {survey['title']}  (ID: {survey['id']})",
        f"Teilnehmer: {len(responses)}   Anonym: {'Ja' if anon else 'Nein'}",
        "=" * 60,
        "",
    ]
    for pos, question in enumerate(survey["questions"], start=1):
        lines.append(f"Frage {pos}: {question['text']}")
        answers = _collect(responses, question["id"])
        # Aggregiertes Ranking obenan
        agg = aggregate_field(question, responses)
        for aline in agg.replace("**", "").replace("`", "").splitlines():
            lines.append(f"   {aline}")
        lines.append("   ---")
        for i, (uid, ua) in enumerate(responses.items(), start=1):
            if question["id"] in ua and ua[question["id"]] is not None:
                name = _voter_name(guild, anon, uid, i)
                lines.append(f"   {name}: {_answer_to_text(question, ua[question['id']])}")
        lines.append("")
    return "\n".join(lines)


def build_export_csv(survey: Dict[str, Any], responses: Dict[str, Dict[str, Any]],
                     guild: Optional[discord.Guild]) -> str:
    """Pro-Stimme-Aufschlüsselung als CSV (eine Zeile pro Teilnehmer)."""
    anon = survey.get("anonymous", False)
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["Teilnehmer"] + [f"F{p+1}: {q['text']}" for p, q in enumerate(survey["questions"])]
    writer.writerow(header)
    for i, (uid, ua) in enumerate(responses.items(), start=1):
        name = _voter_name(guild, anon, uid, i)
        row = [name]
        for question in survey["questions"]:
            ans = ua.get(question["id"])
            row.append(_answer_to_text(question, ans) if ans is not None else "")
        writer.writerow(row)
    return buf.getvalue()


def export_files(survey: Dict[str, Any], responses: Dict[str, Dict[str, Any]],
                 guild: Optional[discord.Guild]) -> List[discord.File]:
    """Erzeugt TXT- und CSV-Datei-Objekte für den Versand."""
    txt = build_export_txt(survey, responses, guild)
    csv_text = build_export_csv(survey, responses, guild)
    return [
        discord.File(io.BytesIO(txt.encode("utf-8")), filename=f"muhfrage_{survey['id']}.txt"),
        discord.File(io.BytesIO(csv_text.encode("utf-8")), filename=f"muhfrage_{survey['id']}.csv"),
    ]
