"""
scheduler.py – Hintergrund-Tasks für den RO Group Finder

Tasks (laufen in einer asyncio-Loop alle 60 Sekunden):

  task_cleanup()
      → Prüft alle Gruppen auf Ablauf (expires_at < now)
      → Markiert sie als 'expired', benachrichtigt alle Beteiligten
      → Löscht den Discord-Post

  task_reminders()
      → Prüft alle Gruppen mit gesetztem Datum
      → Sendet Erinnerungs-DMs wenn Start in <= reminder_minutes Minuten
      → Markiert reminder_sent = True um Doppel-Sends zu verhindern

  task_waitlist_timeouts()
      → Prüft Wartelisten-Spieler die benachrichtigt wurden (notified_at gesetzt)
      → Wenn Timeout abgelaufen → Spieler überspringen, nächsten benachrichtigen

  task_recurrence()
      → Prüft Gruppen mit Wiederholung die ihren Termin hatten
      → Erstellt automatisch einen neuen Post mit gleichen Einstellungen
      → Benachrichtigt alle alten Mitglieder
"""

import asyncio
import discord
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from .data_manager import (
    get_all_groups_flat,
    get_expired_groups,
    get_upcoming_reminder_groups,
    save_group,
    delete_group,
    save_expired_snapshot,
    parse_stored_datetime,
    get_guild_settings,
    add_to_waitlist,
    remove_from_waitlist,
    get_next_waitlist,
    create_group,
    set_group_message_id,
    set_group_ended,
    group_finish_deadline,
)
from .group_embed import build_group_embed, build_group_action_view
from .forum import create_forum_post, close_forum_post, delete_forum_post
from .overview import refresh_overview, finalize_group_post
from .notifications import (
    notify_group_expired,
    notify_group_finished,
    notify_expiry_warning,
    notify_reminder,
    notify_waitlist_slot_free,
    notify_waitlist_timeout,
    notify_recurrence_new_post,
)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER-KLASSE
# ─────────────────────────────────────────────────────────────────────────────

class GroupScheduler:
    """
    Verwaltet alle Hintergrund-Tasks.
    Wird in cog.py instanziiert und beim Cog-Load gestartet.

    Übergabe:
        bot  – discord.Bot Instanz (für User-Lookups und Channel-Zugriff)
    """

    TICK_INTERVAL = 60   # Sekunden zwischen jedem Durchlauf

    def __init__(self, bot: discord.Client):
        self.bot  = bot
        self._task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Startet den Scheduler-Loop als asyncio Task."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        """Stoppt den Scheduler-Loop sauber."""
        if self._task and not self._task.done():
            self._task.cancel()

    # ── Haupt-Loop ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Läuft alle TICK_INTERVAL Sekunden und führt alle Tasks aus."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._task_cleanup()
                await self._task_reminders()
                await self._task_waitlist_timeouts()
                await self._task_recurrence()
                await self._task_auto_finish()
                await self._task_lifecycle()
            except Exception as e:
                # Fehler loggen aber Loop nicht abbrechen
                print(f"[RO GroupFinder Scheduler] Fehler: {e}")
            await asyncio.sleep(self.TICK_INTERVAL)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 1 – CLEANUP (abgelaufene Gruppen)
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_cleanup(self) -> None:
        """
        Prüft alle Gruppen auf Inaktivität.

        Ablauf basiert auf 'expires_at' (= last_activity_at + cleanup_days).
        Für jede aktive Gruppe:
          1. VORWARNUNG – wenn der Ablauf in <= warning_days liegt und noch
             keine Warnung gesendet wurde: Ersteller per DM informieren
             (mit "Suche aktiv halten"-Button) und expiry_warning_sent setzen.
          2. ABLAUF – wenn expires_at erreicht ist:
             a. Als 'expired' markiert
             b. Snapshot für "Erneut suchen" gespeichert
             c. Alle Beteiligten per DM benachrichtigt (Ersteller mit
                "Erneut suchen"-Button)
             d. Der Discord-Post wird gelöscht
             e. Die Gruppe aus groups.json entfernt
        """
        now = datetime.now(timezone.utc)
        all_groups = get_all_groups_flat()

        for group in all_groups:
            if group.get("status") in ("expired", "closed", "finished"):
                continue

            expires_str = group.get("expires_at")
            if not expires_str:
                continue

            try:
                expires_dt = datetime.fromisoformat(expires_str)
            except ValueError:
                continue

            guild_id       = group["guild_id"]
            settings       = get_guild_settings(guild_id)
            inactivity_days = settings["cleanup_days"]
            warning_days    = settings["warning_days"]

            # ── 1. Vorwarnung (Gruppe läuft bald ab) ──────────────────────────
            if expires_dt > now:
                warn_at = expires_dt - timedelta(days=warning_days)
                if now >= warn_at and not group.get("expiry_warning_sent"):
                    days_left = max(1, (expires_dt - now).days)
                    await notify_expiry_warning(
                        self.bot, group, days_left, inactivity_days
                    )
                    group["expiry_warning_sent"] = True
                    save_group(guild_id, group)
                continue

            # ── 2. Gruppe ist abgelaufen (Inaktivität) ────────────────────────
            # Gruppe beenden → die Aufräum-Pipeline (_task_lifecycle) übernimmt
            # Thread schließen/löschen und das Entfernen des Records.
            set_group_ended(group, "expired")
            save_expired_snapshot(group)
            await notify_group_expired(self.bot, group, inactivity_days)

            # Post sofort finalisieren (abgelaufene Posts bleiben nicht offen);
            # Thread/Record der Pipeline überlassen.
            await finalize_group_post(self.bot, group, allow_keep=False)
            group["post_finalized"] = True
            group["post_removed"]   = True
            save_group(guild_id, group)
            await refresh_overview(self.bot, guild_id)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 2 – ERINNERUNGEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_reminders(self) -> None:
        """
        Sendet Erinnerungs-DMs an alle Mitglieder einer Gruppe wenn der
        Starttermin in <= reminder_minutes Minuten liegt.
        """
        now = datetime.now(timezone.utc)

        for group in get_all_groups_flat():
            if group.get("reminder_sent"):
                continue
            if group.get("status") in ("closed", "expired", "finished"):
                continue

            dt_str = group.get("datetime")
            if not dt_str:
                continue

            start_dt = parse_stored_datetime(dt_str, group["guild_id"])
            if start_dt is None:
                continue

            settings        = get_guild_settings(group["guild_id"])
            reminder_delta  = timedelta(minutes=settings["reminder_minutes"])

            if now >= (start_dt - reminder_delta) and now < start_dt:
                await notify_reminder(self.bot, group)
                group["reminder_sent"] = True
                save_group(group["guild_id"], group)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 3 – WARTELISTEN-TIMEOUTS
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_waitlist_timeouts(self) -> None:
        """
        Prüft ob ein benachrichtigter Wartelisten-Spieler sein Zeitfenster
        überschritten hat.

        Ablauf:
          - Wenn notified_at gesetzt und Timeout überschritten:
            → Spieler von Warteliste entfernen
            → Spieler per DM informieren (Timeout)
            → Nächsten in der Warteschlange benachrichtigen
        """
        now = datetime.now(timezone.utc)

        for group in get_all_groups_flat():
            if not group.get("waitlist"):
                continue
            if group.get("status") in ("closed", "expired", "finished"):
                continue

            settings         = get_guild_settings(group["guild_id"])
            timeout_minutes  = settings["waitlist_timeout_minutes"]
            next_entry       = get_next_waitlist(group)

            if not next_entry or not next_entry.get("notified_at"):
                continue

            try:
                notified_dt = datetime.fromisoformat(next_entry["notified_at"])
            except ValueError:
                continue

            timeout_delta = timedelta(minutes=timeout_minutes)
            if now < notified_dt + timeout_delta:
                continue

            # ── Timeout überschritten ─────────────────────────────────────────
            expired_uid = next_entry["user_id"]

            # Spieler von Warteliste entfernen
            remove_from_waitlist(group, expired_uid)
            save_group(group["guild_id"], group)

            # Spieler benachrichtigen
            await notify_waitlist_timeout(self.bot, group, expired_uid)

            # Nächsten in der Warteschlange benachrichtigen (wenn vorhanden)
            await self._notify_next_waitlist(group, timeout_minutes)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 4 – WIEDERHOLUNGEN
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_recurrence(self) -> None:
        """
        Prüft Gruppen mit Wiederholung deren Starttermin vergangen ist.
        Erstellt automatisch einen neuen Post mit dem nächsten Termin.

        Ablauf:
          1. Starttermin der Gruppe liegt in der Vergangenheit
          2. Wiederholung ist nicht 'none'
          3. Noch kein neuer Post erstellt (recurrence_handled != True)
          → Neuen Termin berechnen
          → Neuen Gruppen-Post erstellen
          → Alte Mitglieder benachrichtigen
          → Alte Gruppe als 'closed' markieren
        """
        now = datetime.now(timezone.utc)

        for group in get_all_groups_flat():
            if group.get("recurrence", "none") == "none":
                continue
            if group.get("status") in ("closed", "expired", "finished"):
                continue
            if group.get("recurrence_handled"):
                continue

            dt_str = group.get("datetime")
            if not dt_str:
                continue

            start_dt = parse_stored_datetime(dt_str, group["guild_id"])
            if start_dt is None:
                continue

            if start_dt > now:
                continue   # Termin noch nicht vergangen

            # ── Neuen Termin berechnen ────────────────────────────────────────
            recurrence = group.get("recurrence", "none")
            if recurrence == "daily":
                next_dt = start_dt + timedelta(days=1)
            elif recurrence == "weekly":
                next_dt = start_dt + timedelta(weeks=1)
            else:
                continue

            next_dt_str = next_dt.strftime("%d.%m.%Y %H:%M")

            # ── Neuen Post erstellen ──────────────────────────────────────────
            guild_id   = group["guild_id"]
            channel_id = group["channel_id"]

            new_group = create_group(
                guild_id       = guild_id,
                channel_id     = channel_id,
                creator_id     = group["creator_id"],
                creator_name   = group["creator_name"],
                creator_ingame = group.get("creator_ingame"),
                goal           = group.get("goal", ""),
                goal_custom    = group.get("goal_custom"),
                player_count   = group["player_count"],
                slots          = _reset_slots(group["slots"]),
                dt             = next_dt,
                recurrence     = recurrence,
                comment        = group.get("comment"),
                level_min      = group.get("level_min"),
                level_max      = group.get("level_max"),
            )

            # Discord-Post senden
            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception:
                    continue

            try:
                embed   = build_group_embed(new_group)
                # Temporäre Message-ID für die View nötig → danach aktualisieren
                new_group["message_id"] = 0
                view    = build_group_action_view(new_group)
                message = await channel.send(embed=embed, view=view)

                set_group_message_id(new_group, message.id)
                save_group(guild_id, new_group)

                # Forum-Diskussionspost für die neue Gruppe erstellen (best effort)
                await create_forum_post(self.bot, new_group)
                save_group(guild_id, new_group)

                # View + Embed mit korrekter Message-ID / Forum-Link aktualisieren
                await message.edit(
                    embed=build_group_embed(new_group),
                    view=build_group_action_view(new_group),
                )

                # Übersicht wieder nach unten schieben (neuer Post)
                await refresh_overview(self.bot, guild_id, move_to_bottom=True)

            except Exception as e:
                print(f"[RO GroupFinder Scheduler] Fehler beim Erstellen des Wiederholungs-Posts: {e}")
                continue

            # ── Alte Mitglieder benachrichtigen ──────────────────────────────
            await notify_recurrence_new_post(self.bot, group, channel, message.id)

            # ── Alte Gruppe beenden – Aufräum-Pipeline übernimmt Thread & Record ──
            group["recurrence_handled"] = True
            set_group_ended(group, "deleted")
            await finalize_group_post(self.bot, group, allow_keep=False)
            group["post_finalized"] = True
            group["post_removed"]   = True
            save_group(guild_id, group)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 5 – AUTO-ENDE NACH START
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_auto_finish(self) -> None:
        """
        Beendet aktive Gruppen mit Termin automatisch, sobald
        Start + group_finish_after_start_hours erreicht ist. Der Leiter kann sie
        über den "Wieder öffnen"-Button reaktivieren (setzt den Timer zurück).
        """
        now = datetime.now(timezone.utc)

        for group in get_all_groups_flat():
            if group.get("status") not in ("open", "full"):
                continue

            deadline = group_finish_deadline(group)
            if deadline is None or now < deadline:
                continue

            guild_id = group["guild_id"]
            set_group_ended(group, "finished")   # ended_at = jetzt
            save_group(guild_id, group)

            await notify_group_finished(self.bot, group)
            # Post auf Abgeschlossen-Ansicht (mit "Wieder öffnen"-Button) bringen.
            await self._edit_group_post(group)
            await refresh_overview(self.bot, guild_id)

    # ─────────────────────────────────────────────────────────────────────────
    # TASK 6 – AUFRÄUM-PIPELINE (Thread schließen/löschen, Record entfernen)
    # ─────────────────────────────────────────────────────────────────────────

    async def _task_lifecycle(self) -> None:
        """
        Räumt beendete Gruppen (ended_at gesetzt) zeitgesteuert auf:
          1. Post finalisieren – nur 'finished' erst bei Erreichen der Löschfrist
             (expired/deleted wurden bereits im jeweiligen Handler finalisiert)
          2. Thread schließen nach thread_close_hours ab Gruppen-Ende
          3. Thread löschen nach thread_delete_hours (Zusammenfassung ins Archiv)
          4. Record entfernen, sobald Thread weg UND Post entfernt ist
        """
        now = datetime.now(timezone.utc)

        for group in get_all_groups_flat():
            ended_str = group.get("ended_at")
            if not ended_str:
                continue
            try:
                ended = datetime.fromisoformat(ended_str)
            except ValueError:
                continue
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)

            guild_id   = group["guild_id"]
            settings   = get_guild_settings(guild_id)
            close_at   = ended + timedelta(hours=settings["thread_close_hours"])
            delete_at  = ended + timedelta(hours=settings["thread_delete_hours"])
            has_thread = bool(group.get("forum_thread_id"))
            changed    = False

            # 1. Post finalisieren (abgeschlossene Gruppe erst zur Löschfrist)
            if (not group.get("post_finalized")
                    and group.get("end_kind") == "finished"
                    and now >= delete_at):
                removed = await finalize_group_post(self.bot, group)
                group["post_finalized"] = True
                group["post_removed"]   = removed
                changed = True

            # 2. Thread schließen
            if has_thread and not group.get("thread_closed") and now >= close_at:
                await close_forum_post(self.bot, group, announce_deletion=True)
                group["thread_closed"] = True
                changed = True

            # 3. Thread löschen (mit Archiv-Zusammenfassung, falls Archiv-Channel)
            if has_thread and not group.get("thread_deleted") and now >= delete_at:
                await delete_forum_post(self.bot, group, archive=True)
                group["thread_deleted"] = True
                changed = True

            # 4. Record entfernen, wenn nichts mehr aussteht
            thread_done = (not has_thread) or group.get("thread_deleted")
            if thread_done and group.get("post_finalized") and group.get("post_removed"):
                delete_group(guild_id, group.get("message_id"))
                await refresh_overview(self.bot, guild_id)
                continue

            if changed:
                save_group(guild_id, group)

    async def _edit_group_post(self, group: Dict) -> None:
        """Aktualisiert Embed + View des Gruppen-Posts im Channel (best effort)."""
        channel_id = group.get("channel_id")
        msg_id     = group.get("message_id")
        if not channel_id or not msg_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        try:
            message = await channel.fetch_message(msg_id)
            await message.edit(
                embed=build_group_embed(group),
                view=build_group_action_view(group),
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # HILFSMETHODEN
    # ─────────────────────────────────────────────────────────────────────────

    async def notify_next_waitlist_public(self, group: Dict) -> None:
        """
        Öffentliche Methode – wird aus cog.py aufgerufen wenn ein Slot frei wird
        (z.B. nach manuellem Entfernen eines Spielers).
        """
        settings        = get_guild_settings(group["guild_id"])
        timeout_minutes = settings["waitlist_timeout_minutes"]
        await self._notify_next_waitlist(group, timeout_minutes)

    async def _notify_next_waitlist(self, group: Dict, timeout_minutes: int) -> None:
        """Benachrichtigt den nächsten Wartelisten-Spieler dass ein Slot frei ist."""
        next_entry = get_next_waitlist(group)
        if not next_entry:
            return

        now_str = datetime.now(timezone.utc).isoformat()
        next_entry["notified_at"] = now_str
        save_group(group["guild_id"], group)

        await notify_waitlist_slot_free(
            self.bot, group, next_entry["user_id"], timeout_minutes
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODUL-HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _reset_slots(slots: list) -> list:
    """
    Erstellt eine Kopie der Slot-Liste mit zurückgesetzten Belegungen.
    Wird für Wiederholungs-Gruppen verwendet.
    """
    reset = []
    for slot in slots:
        reset.append({
            **slot,
            "filled_by_id":     None,
            "filled_by_name":   None,
            "filled_by_ingame": None,
            "filled_class":     None,
            "filled_emoji":     None,
        })
    return reset
