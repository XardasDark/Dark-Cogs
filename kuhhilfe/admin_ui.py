"""
admin_ui.py – interaktives Verwaltungs-Menü für KuhHilfe

Ephemere, klickbare Oberfläche, mit der Admins den kompletten Kategorie-Baum,
die zuständigen Rollen/User, die Wizard-Felder und die Einstellungen pflegen –
ohne Slash-Befehle mit Pfaden tippen zu müssen.

Navigation: jede „Seite" ist eine View; beim Wechsel wird dieselbe (ephemere)
Nachricht via ``interaction.response.edit_message`` neu bestückt. Die Daten
werden bei jedem Seitenaufbau frisch aus der Config gelesen, sodass Änderungen
sofort sichtbar sind.
"""

from typing import Any, Dict, List, Optional, Tuple

import discord

from . import views, presets
from .util import clean_emoji, resolve_emoji, emoji_for_input
from .constants import (
    COLOR_INFO, COLOR_ERROR, COLOR_PANEL, COLOR_CLOSED,
    KIND_GROUP, CATEGORY_PATH_SEP, MAX_FIELDS, FIELD_STYLE_LABELS,
    DEFAULT_MAX_OPEN, DEFAULT_DELETE_DELAY, DEFAULT_CONFIRM_DELETE,
)

MENU_TIMEOUT = 300  # Sekunden Inaktivität, dann wird das Menü deaktiviert


# ═════════════════════════════════════════════════════════════════════════════
# Basis-View (Autor-Check + einheitlicher Timeout)
# ═════════════════════════════════════════════════════════════════════════════
class _BaseView(discord.ui.View):
    def __init__(self, cog, guild: discord.Guild, author_id: int):
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Dieses Menü gehört zu einer anderen Person. Öffne es selbst mit "
                "`/kuhhilfe-setup verwalten`.", ephemeral=True)
            return False
        return True


def _emoji_prefix(node: Dict[str, Any]) -> str:
    return (node.get("emoji") + " ") if node.get("emoji") else ""


def _parent_path(path: str) -> Optional[str]:
    if CATEGORY_PATH_SEP in path:
        return path.split(CATEGORY_PATH_SEP, 1)[0]
    return None


# ═════════════════════════════════════════════════════════════════════════════
# ROOT / HAUPTMENÜ
# ═════════════════════════════════════════════════════════════════════════════
async def root_screen(cog, guild: discord.Guild, author_id: int
                      ) -> Tuple[discord.Embed, discord.ui.View]:
    cats = await cog.store.list_categories(guild)
    settings = await cog.store.get_settings(guild)
    groups = await cog.store.iter_groups(guild)
    leaves = await cog.store.iter_leaves(guild)

    embed = discord.Embed(
        title="🐄🎫 KuhHilfe – Verwaltung",
        description=("Pflege hier deinen Support per Klick.\n"
                     "**📦 Gruppe** = Oberkategorie (z. B. »Fragen zu einem Spiel«).\n"
                     "**🏷️ Kategorie** = finale Auswahl (trägt Rollen/User & Formular-Felder)."),
        color=COLOR_PANEL)

    if cats:
        lines = []
        for key, node in cats.items():
            if cog.store.is_group(node):
                children = node.get("children") or {}
                lines.append(f"📦 {_emoji_prefix(node)}**{node['name']}** · `{key}`")
                for ck, child in children.items():
                    lines.append(f"　└ 🏷️ {_emoji_prefix(child)}{child['name']} · "
                                 f"`{key}/{ck}` – {_leaf_summary(child)}")
                if not children:
                    lines.append("　└ _(noch keine Unterkategorien)_")
            else:
                lines.append(f"🏷️ {_emoji_prefix(node)}**{node['name']}** · `{key}` – "
                             f"{_leaf_summary(node)}")
        embed.add_field(name="Struktur", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Struktur",
                        value=("_Noch nichts angelegt._\n"
                               "Starte mit **📥 Vorlage** (fertige Standard-Kategorien) "
                               "oder lege mit **➕ Gruppe** / **➕ Kategorie** eigene an."),
                        inline=False)

    embed.add_field(name="Zusammenfassung",
                    value=f"{len(groups)} Gruppe(n) · {len(leaves)} Kategorie(n)", inline=False)
    embed.add_field(name="Einstellungen", value=_settings_summary(guild, settings), inline=False)
    embed.set_footer(text="Menü bleibt 5 Min aktiv.")
    return embed, RootView(cog, guild, author_id, cats)


def _leaf_summary(leaf: Dict[str, Any]) -> str:
    return (f"{len(leaf.get('role_ids', []))} Rolle(n), "
            f"{len(leaf.get('user_ids', []))} User, "
            f"{len(leaf.get('fields', []))} Feld(er)")


def _settings_summary(guild: discord.Guild, s: Dict[str, Any]) -> str:
    def ch(cid):
        c = guild.get_channel(cid) if cid else None
        return c.mention if c else "_nicht gesetzt_"
    limit = s.get("max_open_per_user", DEFAULT_MAX_OPEN)
    dd = s.get("delete_delay_seconds", DEFAULT_DELETE_DELAY)
    delay_txt = "nie" if dd is None else ("sofort" if dd <= 0 else f"{round(dd / 60)} Min")
    cd = s.get("confirm_delete_seconds", DEFAULT_CONFIRM_DELETE)
    inact = s.get("inactivity_hours") or 0
    warn_h = s.get("inactivity_warn_hours") or 0
    inact_txt = ("aus" if inact <= 0
                 else f"{inact}h" + (f" (Warnung {warn_h}h vorher)" if 0 < warn_h < inact else ""))
    cool = s.get("cooldown_seconds") or 0
    cool_txt = "aus" if cool <= 0 else f"{cool}s"
    bl = len(s.get("blacklist") or [])
    return (f"Panel: {ch(s.get('panel_channel_id'))} · Ablage: {ch(s.get('ticket_category_id'))} · "
            f"Archiv: {ch(s.get('transcript_channel_id'))} · Protokoll: {ch(s.get('modlog_channel_id'))}\n"
            f"Limit/Nutzer: {'unbegrenzt' if not limit else limit} · Cooldown: {cool_txt} · "
            f"Löschzeit: {delay_txt} · Bestätigungen: {'dauerhaft' if not cd else str(cd) + ' Sek.'} · "
            f"Auto-Schließen: {inact_txt} · Gesperrt: {bl}")


class RootView(_BaseView):
    def __init__(self, cog, guild, author_id, cats: Dict[str, Any]):
        super().__init__(cog, guild, author_id)

        options: List[discord.SelectOption] = []
        for key, node in list(cats.items())[:25]:
            is_group = cog.store.is_group(node)
            options.append(discord.SelectOption(
                label=node["name"][:100],
                value=key,
                emoji=clean_emoji(node.get("emoji")) or ("📦" if is_group else "🏷️"),
                description=("Gruppe" if is_group else "Kategorie")))
        if options:
            sel = discord.ui.Select(placeholder="📂 Element öffnen/bearbeiten …", options=options)
            sel.callback = self._open
            self.add_item(sel)
            self._select = sel

    async def _open(self, interaction: discord.Interaction) -> None:
        key = interaction.data["values"][0]
        node = await self.cog.store.get_node(self.guild, key)
        if node is None:
            await _reopen_root(self, interaction)
            return
        if self.cog.store.is_group(node):
            embed, view = await group_screen(self.cog, self.guild, self.author_id, key)
        else:
            embed, view = await leaf_screen(self.cog, self.guild, self.author_id, key)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Gruppe", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
    async def add_group(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            NodeModal(self.cog, self.guild, self.author_id, mode="create_group"))

    @discord.ui.button(label="Kategorie", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
    async def add_leaf(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            NodeModal(self.cog, self.guild, self.author_id, mode="create_leaf", target=None))

    @discord.ui.button(label="Einstellungen", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Panel", emoji="📣", style=discord.ButtonStyle.primary, row=1)
    async def panel_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await panel_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Vorlage", emoji="📥", style=discord.ButtonStyle.secondary, row=2)
    async def presets_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        summary = await presets.apply_presets(self.cog.store, self.guild)
        await self.cog.refresh_panel(self.guild)
        embed, view = await root_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)
        if not summary["groups"] and not summary["categories"]:
            note = "ℹ️ Alle Standard-Einträge waren bereits vorhanden."
        else:
            extra = f" · {summary['skipped']} bereits vorhanden" if summary["skipped"] else ""
            note = (f"📥 Vorlage geladen: {summary['groups']} Gruppe(n), "
                    f"{summary['categories']} Kategorie(n) hinzugefügt{extra}.")
        await interaction.followup.send(note, ephemeral=True)


async def _reopen_root(view: _BaseView, interaction: discord.Interaction) -> None:
    embed, new = await root_screen(view.cog, view.guild, view.author_id)
    await interaction.response.edit_message(embed=embed, view=new)


# ═════════════════════════════════════════════════════════════════════════════
# GRUPPEN-SEITE
# ═════════════════════════════════════════════════════════════════════════════
async def group_screen(cog, guild, author_id, path: str
                       ) -> Tuple[discord.Embed, discord.ui.View]:
    node = await cog.store.get_node(guild, path)
    if node is None or not cog.store.is_group(node):
        return await root_screen(cog, guild, author_id)

    children = node.get("children") or {}
    embed = discord.Embed(
        title=f"📦 {_emoji_prefix(node)}{node['name']}",
        description=node.get("description") or "_keine Beschreibung_",
        color=COLOR_INFO)
    embed.add_field(name="Key", value=f"`{path}`", inline=True)
    embed.add_field(name="Unterkategorien", value=str(len(children)), inline=True)
    if children:
        lines = [f"🏷️ {_emoji_prefix(ch)}{ch['name']} · `{path}/{ck}` – {_leaf_summary(ch)}"
                 for ck, ch in children.items()]
        embed.add_field(name="Inhalt", value="\n".join(lines)[:1024], inline=False)
    return embed, GroupView(cog, guild, author_id, path, children)


class GroupView(_BaseView):
    def __init__(self, cog, guild, author_id, path: str, children: Dict[str, Any]):
        super().__init__(cog, guild, author_id)
        self.path = path

        options = []
        for ck, child in list(children.items())[:25]:
            options.append(discord.SelectOption(
                label=child["name"][:100], value=f"{path}{CATEGORY_PATH_SEP}{ck}",
                emoji=clean_emoji(child.get("emoji")) or "🏷️", description="Kategorie"))
        if options:
            sel = discord.ui.Select(placeholder="🏷️ Unterkategorie öffnen …", options=options)
            sel.callback = self._open_child
            self.add_item(sel)

    async def _open_child(self, interaction: discord.Interaction) -> None:
        child_path = interaction.data["values"][0]
        embed, view = await leaf_screen(self.cog, self.guild, self.author_id, child_path)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Unterkategorie", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
    async def add_sub(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            NodeModal(self.cog, self.guild, self.author_id, mode="create_leaf", target=self.path))

    @discord.ui.button(label="Bearbeiten", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        node = await self.cog.store.get_node(self.guild, self.path)
        await interaction.response.send_modal(
            NodeModal(self.cog, self.guild, self.author_id, mode="edit", target=self.path, node=node))

    @discord.ui.button(label="Löschen", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = confirm_screen(
            self.cog, self.guild, self.author_id,
            text=f"Gruppe **{self.path}** inklusive **aller Unterkategorien** löschen?",
            path=self.path, back_to=None)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _reopen_root(self, interaction)


# ═════════════════════════════════════════════════════════════════════════════
# KATEGORIE-SEITE (Leaf) – Rollen/User per Dropdown, Felder-Button
# ═════════════════════════════════════════════════════════════════════════════
async def leaf_screen(cog, guild, author_id, path: str
                      ) -> Tuple[discord.Embed, discord.ui.View]:
    leaf, label = await cog.store.resolve_leaf(guild, path)
    if leaf is None:
        return await root_screen(cog, guild, author_id)

    roles = " ".join(f"<@&{r}>" for r in leaf.get("role_ids", [])) or "_keine_"
    users = " ".join(f"<@{u}>" for u in leaf.get("user_ids", [])) or "_keine_"
    fields = "\n".join(
        f"**{i}.** {f['label']} · {FIELD_STYLE_LABELS.get(f.get('style'), f.get('style'))} · "
        f"{'Pflicht' if f.get('required') else 'optional'}"
        for i, f in enumerate(leaf.get("fields", []), start=1)) or "_keine_"

    embed = discord.Embed(
        title=f"🏷️ {label}",
        description=leaf.get("description") or "_keine Beschreibung_",
        color=COLOR_INFO)
    embed.add_field(name="Pfad", value=f"`{path}`", inline=True)
    embed.add_field(name="Zuständige Rollen", value=roles, inline=False)
    embed.add_field(name="Zuständige User", value=users, inline=False)
    embed.add_field(name=f"Formular-Felder ({len(leaf.get('fields', []))}/{MAX_FIELDS})",
                    value=fields, inline=False)
    embed.set_footer(text="Rollen/User unten direkt auswählen – Änderungen greifen sofort.")
    return embed, LeafView(cog, guild, author_id, path, leaf)


class LeafView(_BaseView):
    def __init__(self, cog, guild, author_id, path: str, leaf: Dict[str, Any]):
        super().__init__(cog, guild, author_id)
        self.path = path

        cur_roles = [guild.get_role(r) for r in leaf.get("role_ids", [])]
        cur_roles = [r for r in cur_roles if r is not None]
        role_sel = discord.ui.RoleSelect(
            placeholder="Zuständige Rollen wählen …", min_values=0, max_values=25,
            default_values=cur_roles[:25], row=0)
        role_sel.callback = self._on_roles
        self.add_item(role_sel)

        cur_users = [guild.get_member(u) for u in leaf.get("user_ids", [])]
        cur_users = [u for u in cur_users if u is not None]
        user_sel = discord.ui.UserSelect(
            placeholder="Zuständige User wählen …", min_values=0, max_values=25,
            default_values=cur_users[:25], row=1)
        user_sel.callback = self._on_users
        self.add_item(user_sel)

    async def _on_roles(self, interaction: discord.Interaction) -> None:
        ids = [int(v) for v in interaction.data.get("values", [])]
        await self.cog.store.set_roles(self.guild, self.path, ids)
        embed, view = await leaf_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_users(self, interaction: discord.Interaction) -> None:
        ids = [int(v) for v in interaction.data.get("values", [])]
        await self.cog.store.set_users(self.guild, self.path, ids)
        embed, view = await leaf_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Felder", emoji="📝", style=discord.ButtonStyle.primary, row=2)
    async def fields_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await fields_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Bearbeiten", emoji="✏️", style=discord.ButtonStyle.secondary, row=2)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        leaf, _ = await self.cog.store.resolve_leaf(self.guild, self.path)
        await interaction.response.send_modal(
            NodeModal(self.cog, self.guild, self.author_id, mode="edit", target=self.path, node=leaf))

    @discord.ui.button(label="Löschen", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = confirm_screen(
            self.cog, self.guild, self.author_id,
            text=f"Kategorie **{self.path}** wirklich löschen?",
            path=self.path, back_to=_parent_path(self.path))
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        parent = _parent_path(self.path)
        if parent:
            embed, view = await group_screen(self.cog, self.guild, self.author_id, parent)
        else:
            embed, view = await root_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ═════════════════════════════════════════════════════════════════════════════
# FELDER-SEITE
# ═════════════════════════════════════════════════════════════════════════════
async def fields_screen(cog, guild, author_id, path: str
                        ) -> Tuple[discord.Embed, discord.ui.View]:
    leaf, label = await cog.store.resolve_leaf(guild, path)
    if leaf is None:
        return await root_screen(cog, guild, author_id)
    fields = leaf.get("fields", [])

    lines = [
        f"**{i}.** {f['label']} · {FIELD_STYLE_LABELS.get(f.get('style'), f.get('style'))} · "
        f"{'Pflicht' if f.get('required') else 'optional'} · max {f.get('max_length')}"
        for i, f in enumerate(fields, start=1)] or ["_noch keine Felder_"]
    embed = discord.Embed(
        title=f"📝 Felder · {label}",
        description="\n".join(lines),
        color=COLOR_INFO)
    embed.set_footer(text=f"{len(fields)}/{MAX_FIELDS} Feldern belegt · Diese Felder füllt der "
                          "Nutzer beim Erstellen des Tickets aus.")
    return embed, FieldsView(cog, guild, author_id, path, fields)


class FieldsView(_BaseView):
    def __init__(self, cog, guild, author_id, path: str, fields: List[Dict[str, Any]]):
        super().__init__(cog, guild, author_id)
        self.path = path

        if fields:
            options = [discord.SelectOption(label=f"{i}. {f['label']}"[:100], value=str(i),
                                            description=FIELD_STYLE_LABELS.get(f.get("style")))
                       for i, f in enumerate(fields, start=1)]
            sel = discord.ui.Select(placeholder="✏️ Feld bearbeiten / verschieben …", options=options[:25])
            sel.callback = self._open_field
            self.add_item(sel)

        self._full = len(fields) >= MAX_FIELDS

    async def _open_field(self, interaction: discord.Interaction) -> None:
        index = int(interaction.data["values"][0])
        embed, view = await field_detail_screen(self.cog, self.guild, self.author_id, self.path, index)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Feld hinzufügen", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self._full:
            await interaction.response.send_message(
                f"⚠️ Maximal {MAX_FIELDS} Felder pro Kategorie (Discord-Limit).", ephemeral=True)
            return
        await interaction.response.send_modal(FieldModal(self.cog, self.guild, self.author_id, self.path))

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await leaf_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)


async def field_detail_screen(cog, guild, author_id, path: str, index: int
                              ) -> Tuple[discord.Embed, discord.ui.View]:
    leaf, label = await cog.store.resolve_leaf(guild, path)
    if leaf is None:
        return await root_screen(cog, guild, author_id)
    fields = leaf.get("fields", [])
    if not (1 <= index <= len(fields)):
        return await fields_screen(cog, guild, author_id, path)
    f = fields[index - 1]
    embed = discord.Embed(title=f"📝 Feld {index}: {f['label']}", color=COLOR_INFO)
    embed.add_field(name="Typ", value=FIELD_STYLE_LABELS.get(f.get("style"), f.get("style")), inline=True)
    embed.add_field(name="Pflicht", value="Ja" if f.get("required") else "Nein", inline=True)
    embed.add_field(name="Max. Zeichen", value=str(f.get("max_length")), inline=True)
    embed.add_field(name="Platzhalter", value=f.get("placeholder") or "_keiner_", inline=False)
    embed.set_footer(text=f"Feld {index} von {len(fields)} · {label}")
    return embed, FieldDetailView(cog, guild, author_id, path, index, len(fields))


class FieldDetailView(_BaseView):
    def __init__(self, cog, guild, author_id, path: str, index: int, total: int):
        super().__init__(cog, guild, author_id)
        self.path = path
        self.index = index
        self.total = total

    @discord.ui.button(label="Bearbeiten", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        leaf, _ = await self.cog.store.resolve_leaf(self.guild, self.path)
        fields = (leaf or {}).get("fields", [])
        if not (1 <= self.index <= len(fields)):
            embed, view = await fields_screen(self.cog, self.guild, self.author_id, self.path)
            await interaction.response.edit_message(embed=embed, view=view)
            return
        await interaction.response.send_modal(
            FieldEditModal(self.cog, self.guild, self.author_id, self.path, self.index,
                           fields[self.index - 1]))

    @discord.ui.button(label="Hoch", emoji="⬆️", style=discord.ButtonStyle.secondary, row=0)
    async def up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        new = await self.cog.store.move_field(self.guild, self.path, self.index, -1)
        embed, view = await field_detail_screen(
            self.cog, self.guild, self.author_id, self.path, new or self.index)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Runter", emoji="⬇️", style=discord.ButtonStyle.secondary, row=0)
    async def down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        new = await self.cog.store.move_field(self.guild, self.path, self.index, +1)
        embed, view = await field_detail_screen(
            self.cog, self.guild, self.author_id, self.path, new or self.index)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Löschen", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.store.remove_field(self.guild, self.path, self.index)
        embed, view = await fields_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await fields_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)


class FieldEditModal(discord.ui.Modal):
    """Bearbeitet ein bestehendes Formular-Feld (vorbefüllt)."""

    def __init__(self, cog, guild, author_id, path: str, index: int, field: Dict[str, Any]):
        super().__init__(title=f"Feld {index} bearbeiten", timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.path = path
        self.index = index

        self.f_label = discord.ui.TextInput(
            label="Beschriftung", required=True, max_length=45, default=field.get("label"))
        self.f_type = discord.ui.TextInput(
            label="Typ: kurz / lang", required=False, max_length=10,
            default="lang" if field.get("style") == "paragraph" else "kurz")
        self.f_required = discord.ui.TextInput(
            label="Pflichtfeld? ja / nein", required=False, max_length=5,
            default="ja" if field.get("required", True) else "nein")
        self.f_maxlen = discord.ui.TextInput(
            label="Max. Zeichen", required=False, max_length=4,
            default=str(field.get("max_length") or 200))
        self.f_placeholder = discord.ui.TextInput(
            label="Platzhalter (optional)", required=False, max_length=100,
            default=field.get("placeholder") or None)
        for item in (self.f_label, self.f_type, self.f_required, self.f_maxlen, self.f_placeholder):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            maxlen = int(str(self.f_maxlen.value).strip() or "200")
        except ValueError:
            maxlen = 200
        await self.cog.store.update_field(
            self.guild, self.path, self.index,
            label=str(self.f_label.value), style=_parse_style(self.f_type.value),
            required=_parse_bool(self.f_required.value),
            placeholder=str(self.f_placeholder.value), max_length=maxlen)
        embed, view = await field_detail_screen(
            self.cog, self.guild, self.author_id, self.path, self.index)
        await interaction.response.edit_message(embed=embed, view=view)


# ═════════════════════════════════════════════════════════════════════════════
# EINSTELLUNGEN-SEITE
# ═════════════════════════════════════════════════════════════════════════════
async def settings_screen(cog, guild, author_id
                          ) -> Tuple[discord.Embed, discord.ui.View]:
    s = await cog.store.get_settings(guild)
    embed = discord.Embed(
        title="⚙️ KuhHilfe – Einstellungen",
        description=_settings_summary(guild, s),
        color=COLOR_INFO)
    embed.add_field(
        name="Hinweise",
        value=("**Ablage** = Discord-Kanalkategorie, unter der die Ticket-Channels einsortiert "
               "werden (optional).\n**Archiv** = Channel für Transcripts beim Schließen (optional).\n"
               "**Protokoll** = Channel, in dem Ticket-Ereignisse (geöffnet/übernommen/geschlossen …) "
               "geloggt werden (optional).\n"
               "**Limit/Löschzeit/Bestätigungen** per Button. *Bestätigungen* = Sekunden, bis die "
               "nur für den Nutzer sichtbaren »blauen« Hinweise automatisch verschwinden."),
        inline=False)
    return embed, SettingsView(cog, guild, author_id, s)


class SettingsView(_BaseView):
    def __init__(self, cog, guild, author_id, s: Dict[str, Any]):
        super().__init__(cog, guild, author_id)

        cur_cat = guild.get_channel(s.get("ticket_category_id")) if s.get("ticket_category_id") else None
        cat_sel = discord.ui.ChannelSelect(
            placeholder="📁 Ticket-Ablage (Kanalkategorie) …",
            channel_types=[discord.ChannelType.category], min_values=0, max_values=1,
            default_values=[cur_cat] if cur_cat else [], row=0)
        cat_sel.callback = self._on_ablage
        self.add_item(cat_sel)

        cur_arch = guild.get_channel(s.get("transcript_channel_id")) if s.get("transcript_channel_id") else None
        arch_sel = discord.ui.ChannelSelect(
            placeholder="🗄️ Archiv-Channel für Transcripts …",
            channel_types=[discord.ChannelType.text], min_values=0, max_values=1,
            default_values=[cur_arch] if cur_arch else [], row=1)
        arch_sel.callback = self._on_archive
        self.add_item(arch_sel)

        cur_log = guild.get_channel(s.get("modlog_channel_id")) if s.get("modlog_channel_id") else None
        log_sel = discord.ui.ChannelSelect(
            placeholder="📋 Protokoll-Channel für Ticket-Ereignisse …",
            channel_types=[discord.ChannelType.text], min_values=0, max_values=1,
            default_values=[cur_log] if cur_log else [], row=2)
        log_sel.callback = self._on_modlog
        self.add_item(log_sel)

    async def _on_ablage(self, interaction: discord.Interaction) -> None:
        vals = interaction.data.get("values", [])
        await self.cog.store.set_ticket_category(self.guild, int(vals[0]) if vals else None)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_archive(self, interaction: discord.Interaction) -> None:
        vals = interaction.data.get("values", [])
        await self.cog.store.set_transcript_channel(self.guild, int(vals[0]) if vals else None)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_modlog(self, interaction: discord.Interaction) -> None:
        vals = interaction.data.get("values", [])
        await self.cog.store.set_modlog_channel(self.guild, int(vals[0]) if vals else None)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Limit & Cooldown", emoji="🔢", style=discord.ButtonStyle.secondary, row=3)
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        s = await self.cog.store.get_settings(self.guild)
        await interaction.response.send_modal(LimitModal(
            self.cog, self.guild, self.author_id,
            s.get("max_open_per_user", DEFAULT_MAX_OPEN), s.get("cooldown_seconds") or 0))

    @discord.ui.button(label="Löschzeit", emoji="⏱️", style=discord.ButtonStyle.secondary, row=3)
    async def delay_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(DelayModal(self.cog, self.guild, self.author_id))

    @discord.ui.button(label="Bestätigungen", emoji="💬", style=discord.ButtonStyle.secondary, row=3)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ConfirmDeleteModal(self.cog, self.guild, self.author_id))

    @discord.ui.button(label="Auto-Schließen", emoji="⏰", style=discord.ButtonStyle.secondary, row=3)
    async def inactivity_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        s = await self.cog.store.get_settings(self.guild)
        await interaction.response.send_modal(InactivityModal(
            self.cog, self.guild, self.author_id,
            s.get("inactivity_hours") or 0, s.get("inactivity_warn_hours") or 0))

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _reopen_root(self, interaction)


# ═════════════════════════════════════════════════════════════════════════════
# PANEL-SEITE
# ═════════════════════════════════════════════════════════════════════════════
async def panel_screen(cog, guild, author_id
                       ) -> Tuple[discord.Embed, discord.ui.View]:
    s = await cog.store.get_settings(guild)
    cur = guild.get_channel(s.get("panel_channel_id")) if s.get("panel_channel_id") else None
    embed = discord.Embed(
        title="📣 Support-Panel",
        description=("Wähle den Channel, in dem das öffentliche Panel mit dem "
                     "**»Support anfragen«**-Button erscheinen soll. Beim Auswählen wird es "
                     "sofort gepostet.\n\n"
                     f"Aktuell: {cur.mention if cur else '_noch nicht gepostet_'}"),
        color=COLOR_PANEL)
    return embed, PanelView(cog, guild, author_id, cur)


class PanelView(_BaseView):
    def __init__(self, cog, guild, author_id, cur: Optional[discord.abc.GuildChannel]):
        super().__init__(cog, guild, author_id)
        ch_sel = discord.ui.ChannelSelect(
            placeholder="📣 Panel-Channel wählen (postet sofort) …",
            channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
            default_values=[cur] if cur else [], row=0)
        ch_sel.callback = self._on_channel
        self.add_item(ch_sel)

    async def _on_channel(self, interaction: discord.Interaction) -> None:
        channel = interaction.guild.get_channel(int(interaction.data["values"][0]))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Ungültiger Channel.", ephemeral=True)
            return
        try:
            await self.cog.post_panel(self.guild, channel)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Ich darf in {channel.mention} nicht schreiben.", ephemeral=True)
            return
        embed, view = await panel_screen(self.cog, self.guild, self.author_id)
        embed.description = f"✅ Panel in {channel.mention} gepostet.\n\n" + embed.description
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Aktualisieren", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ok = await self.cog.refresh_panel(self.guild)
        embed, view = await panel_screen(self.cog, self.guild, self.author_id)
        prefix = "✅ Panel aktualisiert.\n\n" if ok else "⚠️ Es ist noch kein Panel gesetzt.\n\n"
        embed.description = prefix + embed.description
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Zurück", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _reopen_root(self, interaction)


# ═════════════════════════════════════════════════════════════════════════════
# BESTÄTIGUNG (Löschen)
# ═════════════════════════════════════════════════════════════════════════════
def confirm_screen(cog, guild, author_id, text: str, path: str, back_to: Optional[str]
                   ) -> Tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title="🗑️ Löschen bestätigen", description=f"⚠️ {text}", color=COLOR_ERROR)
    return embed, ConfirmDeleteView(cog, guild, author_id, path, back_to)


class ConfirmDeleteView(_BaseView):
    def __init__(self, cog, guild, author_id, path: str, back_to: Optional[str]):
        super().__init__(cog, guild, author_id)
        self.path = path
        self.back_to = back_to

    @discord.ui.button(label="Ja, löschen", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.store.remove_node(self.guild, self.path)
        if self.back_to:
            embed, view = await group_screen(self.cog, self.guild, self.author_id, self.back_to)
        else:
            embed, view = await root_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Abbrechen", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.back_to:
            embed, view = await group_screen(self.cog, self.guild, self.author_id, self.back_to)
        elif CATEGORY_PATH_SEP in self.path or await self.cog.store.get_node(self.guild, self.path):
            # zurück zum betroffenen Element, falls es noch existiert
            node = await self.cog.store.get_node(self.guild, self.path)
            if node and self.cog.store.is_group(node):
                embed, view = await group_screen(self.cog, self.guild, self.author_id, self.path)
            elif node:
                embed, view = await leaf_screen(self.cog, self.guild, self.author_id, self.path)
            else:
                embed, view = await root_screen(self.cog, self.guild, self.author_id)
        else:
            embed, view = await root_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ═════════════════════════════════════════════════════════════════════════════
# MODALS
# ═════════════════════════════════════════════════════════════════════════════
class NodeModal(discord.ui.Modal):
    """Legt eine Gruppe/Kategorie an oder bearbeitet Name/Emoji/Beschreibung."""

    def __init__(self, cog, guild, author_id, mode: str,
                 target: Optional[str] = None, node: Optional[Dict[str, Any]] = None):
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.mode = mode          # "create_group" | "create_leaf" | "edit"
        self.target = target      # bei create_leaf: group-key oder None; bei edit: pfad

        titles = {"create_group": "Neue Gruppe", "create_leaf": "Neue Kategorie", "edit": "Bearbeiten"}
        super().__init__(title=titles.get(mode, "Element"), timeout=300)

        self.f_name = discord.ui.TextInput(
            label="Name", required=True, max_length=80,
            default=(node or {}).get("name") if node else None)
        self.f_emoji = discord.ui.TextInput(
            label="Emoji (optional)", required=False, max_length=64,
            default=emoji_for_input((node or {}).get("emoji")) if node else None,
            placeholder="z. B. ⚔️ oder :wow:")
        self.f_desc = discord.ui.TextInput(
            label="Kurzbeschreibung (optional)", required=False, max_length=200,
            style=discord.TextStyle.paragraph,
            default=(node or {}).get("description") if node else None)
        self.add_item(self.f_name)
        self.add_item(self.f_emoji)
        self.add_item(self.f_desc)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.f_name.value).strip()
        desc = str(self.f_desc.value).strip() or None
        emoji, emoji_err = resolve_emoji(self.guild, str(self.f_emoji.value))

        error: Optional[str] = None
        goto_path: Optional[str] = None
        goto_group: Optional[str] = None

        if self.mode == "create_group":
            grp = await self.cog.store.add_group(self.guild, name, emoji, desc)
            if grp is None:
                error = "Name ungültig oder Key existiert bereits."
            else:
                goto_group = grp["key"]
        elif self.mode == "create_leaf":
            leaf, result = await self.cog.store.add_leaf(self.guild, name, self.target, emoji, desc)
            if leaf is None:
                error = result
            elif self.target:
                goto_group = self.target
            else:
                goto_path = result
        else:  # edit
            await self.cog.store.edit_node(self.guild, self.target, name, emoji, desc)
            node = await self.cog.store.get_node(self.guild, self.target)
            if node and self.cog.store.is_group(node):
                goto_group = self.target
            else:
                goto_path = self.target

        if error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return

        if goto_group:
            embed, view = await group_screen(self.cog, self.guild, self.author_id, goto_group)
        elif goto_path:
            embed, view = await leaf_screen(self.cog, self.guild, self.author_id, goto_path)
        else:
            embed, view = await root_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)
        if emoji_err:
            await interaction.followup.send(f"⚠️ {emoji_err} (ohne Emoji gespeichert)", ephemeral=True)


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in ("ja", "j", "yes", "y", "true", "1", "pflicht", "x")


def _parse_style(value: str) -> str:
    v = str(value).strip().lower()
    if v in ("lang", "paragraph", "long", "l", "mehrzeilig", "mehr"):
        return "paragraph"
    return "short"


class FieldModal(discord.ui.Modal):
    """Fügt einer Kategorie ein Formular-Feld hinzu."""

    def __init__(self, cog, guild, author_id, path: str):
        super().__init__(title="Neues Formular-Feld", timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.path = path

        self.f_label = discord.ui.TextInput(label="Beschriftung", required=True, max_length=45,
                                            placeholder="z. B. Charaktername")
        self.f_type = discord.ui.TextInput(label="Typ: kurz / lang", required=False, max_length=10,
                                           default="kurz")
        self.f_required = discord.ui.TextInput(label="Pflichtfeld? ja / nein", required=False,
                                               max_length=5, default="ja")
        self.f_maxlen = discord.ui.TextInput(label="Max. Zeichen", required=False, max_length=4,
                                             default="200")
        self.f_placeholder = discord.ui.TextInput(label="Platzhalter (optional)", required=False,
                                                  max_length=100)
        for item in (self.f_label, self.f_type, self.f_required, self.f_maxlen, self.f_placeholder):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            maxlen = int(str(self.f_maxlen.value).strip() or "200")
        except ValueError:
            maxlen = 200
        err = await self.cog.store.add_field(
            self.guild, self.path, str(self.f_label.value),
            _parse_style(self.f_type.value), _parse_bool(self.f_required.value),
            str(self.f_placeholder.value) or None, maxlen)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        embed, view = await fields_screen(self.cog, self.guild, self.author_id, self.path)
        await interaction.response.edit_message(embed=embed, view=view)


class LimitModal(discord.ui.Modal, title="Tickets pro Nutzer"):
    def __init__(self, cog, guild, author_id, cur_limit: int = 2, cur_cooldown: int = 0):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.f_value = discord.ui.TextInput(
            label="Max. offene Tickets (0 = unbegrenzt)", required=True, max_length=4,
            default=str(cur_limit))
        self.f_cooldown = discord.ui.TextInput(
            label="Cooldown in Sekunden (0 = aus)", required=False, max_length=6,
            default=str(cur_cooldown))
        self.add_item(self.f_value)
        self.add_item(self.f_cooldown)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = max(0, int(str(self.f_value.value).strip()))
            cooldown = max(0, int(str(self.f_cooldown.value).strip() or "0"))
        except ValueError:
            await interaction.response.send_message("❌ Bitte Zahlen eingeben.", ephemeral=True)
            return
        await self.cog.store.set_max_open(self.guild, value)
        await self.cog.store.set_cooldown(self.guild, cooldown)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)


class DelayModal(discord.ui.Modal, title="Löschzeit nach dem Schließen"):
    def __init__(self, cog, guild, author_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.f_value = discord.ui.TextInput(
            label="Minuten · 0 = sofort · - = nie löschen", required=True, max_length=10,
            placeholder="z. B. 5, 0 oder -", default="5")
        self.add_item(self.f_value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.f_value.value).strip().lower()
        if raw in ("-", "–", "—", "nie", "never", "unendlich", "∞", "-1", "x"):
            await self.cog.store.set_delete_delay(self.guild, None)
        else:
            try:
                minutes = max(0, int(raw))
            except ValueError:
                await interaction.response.send_message(
                    "❌ Bitte eine Zahl (Minuten) oder »-« eingeben.", ephemeral=True)
                return
            await self.cog.store.set_delete_delay(self.guild, minutes * 60)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)


class ConfirmDeleteModal(discord.ui.Modal, title="Bestätigungen automatisch ausblenden"):
    def __init__(self, cog, guild, author_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.f_value = discord.ui.TextInput(
            label="Sekunden bis Ausblenden (0 = dauerhaft)", required=True, max_length=5, default="60")
        self.add_item(self.f_value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            seconds = max(0, int(str(self.f_value.value).strip()))
        except ValueError:
            await interaction.response.send_message("❌ Bitte eine Zahl eingeben.", ephemeral=True)
            return
        await self.cog.store.set_confirm_delete(self.guild, seconds)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)


class InactivityModal(discord.ui.Modal, title="Auto-Schließen bei Inaktivität"):
    def __init__(self, cog, guild, author_id, cur_hours: int, cur_warn: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.author_id = author_id
        self.f_hours = discord.ui.TextInput(
            label="Stunden bis Schließen (0 = aus)", required=True, max_length=5,
            default=str(cur_hours))
        self.f_warn = discord.ui.TextInput(
            label="Vorwarnung X Std. vorher (0 = keine)", required=False, max_length=5,
            default=str(cur_warn))
        self.add_item(self.f_hours)
        self.add_item(self.f_warn)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            hours = max(0, int(str(self.f_hours.value).strip() or "0"))
            warn = max(0, int(str(self.f_warn.value).strip() or "0"))
        except ValueError:
            await interaction.response.send_message("❌ Bitte Zahlen eingeben.", ephemeral=True)
            return
        await self.cog.store.set_inactivity_hours(self.guild, hours)
        await self.cog.store.set_inactivity_warn_hours(self.guild, warn)
        embed, view = await settings_screen(self.cog, self.guild, self.author_id)
        await interaction.response.edit_message(embed=embed, view=view)
