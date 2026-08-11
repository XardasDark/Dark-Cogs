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
    get_guild_settings,
    add_to_waitlist,
    remove_from_waitlist,
    get_next_waitlist,
    create_group,
    set_group_message_id,
)
from .group_embed import build_group_embed, build_group_action_view
from .notifications import (
    notify_group_expired,
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

            # ── 2. Gruppe ist abgelaufen ──────────────────────────────────────
            message_id = group.get("message_id")

            # a. Status setzen
            group["status"] = "expired"
            save_group(guild_id, group)

            # b. Snapshot für "Erneut suchen"
            save_expired_snapshot(group)

            # c. Benachrichtigungen
            await notify_group_expired(self.bot, group, inactivity_days)

            # d. Discord-Post löschen
            if message_id:
                await self._delete_discord_message(guild_id, group["channel_id"], message_id)

            # e. Aus JSON entfernen
            delete_group(guild_id, message_id)

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

            try:
                start_dt = _parse_dt(dt_str)
            except ValueError:
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

            try:
                start_dt = _parse_dt(dt_str)
            except ValueError:
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

                # View mit korrekter Message-ID aktualisieren
                await message.edit(view=build_group_action_view(new_group))

            except Exception as e:
                print(f"[RO GroupFinder Scheduler] Fehler beim Erstellen des Wiederholungs-Posts: {e}")
                continue

            # ── Alte Mitglieder benachrichtigen ──────────────────────────────
            await notify_recurrence_new_post(self.bot, group, channel, message.id)

            # ── Alte Gruppe schließen ─────────────────────────────────────────
            group["recurrence_handled"] = True
            group["status"]             = "closed"
            save_group(guild_id, group)

            # Alten Discord-Post schließen (Embed-Update)
            old_msg_id = group.get("message_id")
            if old_msg_id:
                await self._update_group_embed(guild_id, channel_id, old_msg_id, group)

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

    async def _delete_discord_message(
        self,
        guild_id:   int,
        channel_id: int,
        message_id: int,
    ) -> None:
        """Löscht eine Discord-Nachricht. Ignoriert Fehler (bereits gelöscht etc.)."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except Exception:
            pass

    async def _update_group_embed(
        self,
        guild_id:   int,
        channel_id: int,
        message_id: int,
        group:      Dict,
    ) -> None:
        """Aktualisiert das Embed einer bestehenden Discord-Nachricht."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        try:
            message = await channel.fetch_message(message_id)
            embed   = build_group_embed(group)
            view    = build_group_action_view(group)
            await message.edit(embed=embed, view=view)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# MODUL-HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dt(dt_str: str) -> datetime:
    """
    Parst einen Datums-String aus dem Wizard.
    Erwartet Format: "DD.MM.YYYY HH:MM" oder ISO-Format.
    Gibt ein timezone-aware datetime (UTC) zurück.
    """
    # ISO-Format (intern gespeichert)
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # Wizard-Format: "DD.MM.YYYY HH:MM"
    dt = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
    return dt.replace(tzinfo=timezone.utc)


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
